"""The verified PS question bank, shipped with the app as a static file.

Built in the separate "Score Surge DB" project (real MILPERSMAN/BUPERSINST text,
quote-matched, then run past an AI judge that checks the quote actually proves the
keyed answer -- see content-reliability-process.md there). This module does none of
that verification itself; it only loads what already passed it and hands questions to
the exam engine in the shape parse_exam_json() already expects.

data/ps_question_bank.csv is a snapshot, not a live link. That other project is the
one source of truth and keeps growing; bringing in new verified questions means
re-copying its questions.csv over this file, nothing here needs to change to pick up
more rows. As of 21 Sep 2026: 695 rows, all Quote_Verified=PASS and Judge_Verdict=PASS.

Everything below is deliberately quiet on failure -- a missing or malformed CSV means
the bank is empty, not a crashed app, and the exam engine falls back to writing
questions live the way it always has (see get_bank_questions() below).
"""

import csv
import functools
import os
import random

BANK_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ps_question_bank.csv")

# The source bank's Exam_Area names line up with Score Surge's own topic names for
# every topic except this one. MILPAY, Disbursing & Receipts is one combined area in
# the source bank; Score Surge splits E6 into three (MILPAY Processing, Disbursing
# Operations, Receipts Management & Processing). Checked 21 Sep 2026: every row filed
# under this area is actually a pay-entitlement question (Concentration values are
# things like Basic Pay, Separation Payments, Housing Allowances, COLA, Garnishments)
# -- not one is about handling cash or processing an incoming sailor's paperwork -- so
# all of them route to MILPAY Processing. None route to Disbursing Operations or
# Receipts Management & Processing; those two keep falling back to the live AI path
# exactly as they do today, same as any topic the bank doesn't cover yet.
_AREA_TO_TOPIC = {
    "MILPAY, Disbursing & Receipts": "MILPAY Processing",
}

# Only ratings/paygrades Score Surge has a curated topic map for today (PS_TOPICS_BY_PAYGRADE
# in app.py). The source bank also carries E-7/E-8/E-9 and a handful of other-paygrade
# rows (built toward the separate PS Chief exam project) plus a few YN/other-rating
# stragglers -- real questions, just nowhere in this app's topic list to hand them to
# yet, so they're loaded but never matched to anything and never served.
_SUPPORTED_PAYGRADES = {"E-5": "E5", "E-6": "E6"}
_SUPPORTED_RATINGS = {"PS"}


def _row_to_question(row: dict) -> dict:
    """One CSV row -> the dict shape parse_exam_json() and the exam engine expect.

    verified="yes" is what makes exam_source_line() print "Verified source" instead of
    the grounded/unverified tiers, and what makes exam_all_verified() true for a set
    built entirely from the bank -- both already existed in app.py, unused, until this
    module started setting the field.
    """
    manual = (row.get("Source_Manual") or "").strip()
    page = (row.get("Page") or "").strip()
    quote = (row.get("Verbatim_Quote") or "").strip()
    return {
        "question": (row.get("Question") or "").strip(),
        "answer_a": (row.get("Answer_A") or "").strip(),
        "answer_b": (row.get("Answer_B") or "").strip(),
        "answer_c": (row.get("Answer_C") or "").strip(),
        "answer_d": (row.get("Answer_D") or "").strip(),
        "correct_answer": (row.get("Correct_Answer") or "").strip().upper(),
        # The bank doesn't carry a separate "why is this right" write-up -- the
        # verbatim quote that already passed the quote gate and the AI judge IS the
        # proof, and it's stronger than an explanation written from memory would be.
        "explanation": (f'Per {manual}: "{quote}"' if quote else ""),
        "source_manual": manual,
        "chapter_section": (f"p. {page}" if page else ""),
        "source_quote": quote,
        "verified": "yes",
        "grounded": "yes",
    }


@functools.lru_cache(maxsize=1)
def _load() -> dict:
    """(rating, paygrade, topic) -> tuple of question dicts. {} if the CSV didn't ship."""
    if not os.path.exists(BANK_CSV_PATH):
        return {}
    index = {}
    try:
        with open(BANK_CSV_PATH, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rating = (row.get("Rating") or "").strip()
                if rating not in _SUPPORTED_RATINGS:
                    continue
                paygrade = _SUPPORTED_PAYGRADES.get((row.get("Paygrade") or "").strip())
                if not paygrade:
                    continue
                area = (row.get("Exam_Area") or "").strip()
                topic = _AREA_TO_TOPIC.get(area, area)
                q = _row_to_question(row)
                if not q["question"] or q["correct_answer"] not in ("A", "B", "C", "D"):
                    continue
                if not all(q[f"answer_{L}"] for L in ("a", "b", "c", "d")):
                    continue
                index.setdefault((rating, paygrade, topic), []).append(q)
    except (OSError, csv.Error):
        return {}
    return {k: tuple(v) for k, v in index.items()}


def bank_available() -> bool:
    """False if the CSV is missing or empty -- callers treat that like zero coverage."""
    return bool(_load())


def bank_count(rating: str, paygrade: str, topic: str) -> int:
    """How many verified questions exist for this exact (rating, paygrade, topic)."""
    return len(_load().get((rating, paygrade, topic), ()))


def get_bank_questions(rating: str, paygrade: str, topic: str, n: int) -> list:
    """Up to n verified questions for this exact combo, or [] if the bank can't fill
    the whole request on its own.

    Deliberately all-or-nothing. A set that mixed bank questions with freshly-written
    AI ones couldn't carry one honest "verified" label for the whole set -- some rows
    would be lying about what backs them -- so a topic that's short on bank coverage
    falls all the way through to the existing live-AI path instead of blending the two.
    That's the same shape as every other gate in this app: prove the whole thing, or
    don't claim it.
    """
    if n <= 0:
        return []
    pool = _load().get((rating, paygrade, topic), ())
    if len(pool) < n:
        return []
    return [dict(q) for q in random.sample(pool, n)]
