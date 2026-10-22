"""Which ratings/paygrades get practice questions on launch day, and which say "coming soon".

Marketing review item 6, 22 Oct 2026: "For any rating/paygrade without enough verified
questions, show 'coming soon' instead of thin content. Don't generate unverified
questions to fill gaps; the accuracy gate applies."

"Enough" uses the bar that already exists and is already tested: placement.placement_ready()
-- the verified bank can fill a full 40-question placement test across at least three of
that paygrade's topics. One rule, so the placement test, the Mock Exam, the Study Guide's
Practice Questions and the public welcome page can never disagree with each other. When
the bank grows past the bar, a rating/paygrade opens by itself -- no code change.

Questions only count if the app can actually serve them: a verified question for a
paygrade the app has no topic list for (PS E7 today) can't be routed anywhere yet.
"""

import csv

import placement
import question_bank
import rate_topics

RATINGS = ("PS", "YN", "NC")
PAYGRADES = ("E4", "E5", "E6", "E7")


def topics_for(rating: str, paygrade: str) -> list:
    return list(rate_topics.CURATED.get(rating, {}).get(paygrade, {}).keys())


def practice_open(rating: str, paygrade: str) -> bool:
    """True if this rating/paygrade has enough verified questions to serve practice sets."""
    topics = topics_for(rating, paygrade)
    return bool(topics) and placement.placement_ready(rating, paygrade, topics)


def open_paygrades(rating: str) -> list:
    return [pg for pg in PAYGRADES if practice_open(rating, pg)]


def bank_counts() -> dict:
    """(rating, 'E6') -> rows in the shipped verified bank, whatever the app can route."""
    counts = {}
    try:
        with open(question_bank.BANK_CSV_PATH, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if (row.get("Quote_Verified") or "").strip().upper() != "PASS":
                    continue
                if not (row.get("Verbatim_Quote") or "").strip():
                    continue
                key = ((row.get("Rating") or "").strip(),
                       (row.get("Paygrade") or "").strip().replace("-", ""))
                counts[key] = counts.get(key, 0) + 1
    except (OSError, csv.Error):
        return {}
    return counts


def readiness_table() -> list:
    """One row per rating/paygrade: verified in bank, servable by the app, status."""
    raw = bank_counts()
    rows = []
    for r in RATINGS:
        for pg in PAYGRADES:
            topics = topics_for(r, pg)
            servable = sum(placement.coverage(r, pg, topics).values()) if topics else 0
            rows.append({
                "rating": r, "paygrade": pg,
                "verified_in_bank": raw.get((r, pg), 0),
                "servable": servable,
                "status": "open" if practice_open(r, pg) else "coming soon",
            })
    return rows


def coming_soon_text(rating: str, paygrade: str) -> str:
    return (f"**{rating} {paygrade} practice questions are coming soon.** Score Surge only "
            "serves practice questions it can back with real manual text, and the "
            f"verified bank doesn't have enough {rating} {paygrade} questions yet to do "
            "that honestly. Your FMS calculator, the Tutor and study plans work now.")


def topic_coming_soon_text(topic: str) -> str:
    return (f"**Practice questions for {topic} are coming soon.** The verified bank "
            "doesn't cover this topic yet, and the manual it comes from isn't loaded "
            "into Score Surge — so rather than write questions from memory, the app "
            "waits. Pick another topic, or ask the Tutor about this one.")


if __name__ == "__main__":
    print(f"{'Rating':<7}{'Grade':<7}{'Verified in bank':>18}{'App can serve':>15}  Status")
    for row in readiness_table():
        print(f"{row['rating']:<7}{row['paygrade']:<7}{row['verified_in_bank']:>18}"
              f"{row['servable']:>15}  {row['status']}")
