"""Real MILPERSMAN text, on disk, that the Tutor can quote from instead of guessing.

Backed by corpus.db (built 21 Aug 2026, from ~/Documents/Score Surge DB/corpus/) as of
24 Aug 2026 -- this module used to read a plain-text milpersman.txt instead. Score
Surge had two separate text stores doing the same job; standardizing on this one,
bigger one, and retiring the other, was Shawn's explicit call. corpus.db already has
every current MILPERSMAN article, page-resolved, with five real defects (header read as
footer, duplicate superseded editions, undated continuation pages, printed typos,
partial editions) already found and fixed -- 28/28 checks passing on the machine that
built it. See milpersman-corpus.md in the project for the full story.

Two ways to ground a lesson in this text, both below: get_article_text() /
build_source_block() for a specific, hand-picked article number (unchanged contract
from before 24 Aug), and get_series_grounding() for automatic retrieval keyed off a
topic's own bibliography line -- the new piece, and the reason coverage no longer needs
someone to read a candidate article before every single topic can be trusted. Which
article number goes with which Tutor topic was NEVER decided by a keyword guess over
article TITLES here -- that was tried and rejected once already (see
get_series_grounding()'s docstring for why) and isn't being reintroduced by this
rewrite.
"""

import os
import re
import sqlite3
import functools

CORPUS_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus", "corpus.db")


@functools.lru_cache(maxsize=1)
def _connect():
    """One shared read-only connection to the bundled corpus. None if the file didn't
    ship with this deploy -- every caller below treats that exactly like an empty
    corpus, not an error."""
    if not os.path.exists(CORPUS_DB_PATH):
        return None
    try:
        # mode=ro: this file ships inside the app and nothing at runtime ever writes to
        # it. check_same_thread=False: Streamlit can call in from more than one thread
        # across reruns; a read-only connection to an unchanging file is safe to share.
        return sqlite3.connect(f"file:{CORPUS_DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    except sqlite3.Error:
        return None


def corpus_available() -> bool:
    """False if corpus.db didn't ship with this deploy (missing, not just empty)."""
    con = _connect()
    if con is None:
        return False
    try:
        return con.execute("SELECT COUNT(*) FROM pages").fetchone()[0] > 0
    except sqlite3.Error:
        return False


@functools.lru_cache(maxsize=256)
def get_article_text(number: str, max_chars: int = 4000) -> str:
    """The real wording of one MILPERSMAN article, trimmed to a safe prompt size.

    Only the CURRENT, non-cancelled edition -- corpus.db carries nine articles at two
    revisions each (an old edition superseded by a newer one, both kept for the
    substitute-exam bibliography which is locked to different dates); this always
    takes the current one, never the superseded text, for a regular-exam lesson.

    Returns "" if the article isn't found, is cancelled, or every edition on file is
    superseded -- callers must treat that as "no grounding available" and fall back to
    the memory-only lesson, never as license to guess.
    """
    con = _connect()
    if con is None:
        return ""
    rows = con.execute(
        "SELECT title, text FROM pages WHERE article = ? AND is_current = 1 "
        "AND cancelled = 0 ORDER BY page",
        (number,),
    ).fetchall()
    if not rows:
        return ""
    title = (rows[0][0] or "").strip()
    body = "\n".join(r[1] for r in rows if r[1])
    # Page 1's own stored text already starts with the printed "MILPERSMAN NNNN-NNN /
    # TITLE" header line -- corpus.db keeps it because it's real page content. Without
    # this check that header gets printed twice: once added here for a predictable
    # first two lines (build_source_block relies on line 2 being the title), and again
    # a few lines down where the page itself prints it.
    if body.strip().upper().startswith(f"MILPERSMAN {number}"):
        text = body.strip()
    else:
        text = f"MILPERSMAN {number}\n{title}\n\n{body}".strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0] + "\n...[article continues]"
    return text


def build_source_block(article_numbers: list, max_chars_each: int = 4000):
    """The real text for a lesson's mapped articles, plus which ones actually loaded.

    Returns (source_block, matched_numbers). source_block is "" when none of the
    numbers loaded — that's the caller's signal to fall back to the memory-only
    lesson, unchanged.
    """
    parts = []
    numbers = []
    for number in article_numbers or []:
        text = get_article_text(number, max_chars=max_chars_each)
        if not text:
            continue
        title = text.splitlines()[1].strip() if len(text.splitlines()) > 1 else ""
        parts.append(f"--- MILPERSMAN {number} — {title} ---\n{text}")
        numbers.append(number)
    return "\n\n".join(parts), numbers


# ── AUTOMATIC RETRIEVAL, KEYED ON A TOPIC'S OWN BIBLIOGRAPHY LINE ─────────────────
#
# TOPIC_ARTICLE_MAP in app.py only ever covered a handful of topics, because someone
# had to read a candidate article before it went in the list. That doesn't scale, and
# Shawn's call 24 Aug 2026 was direct: the business cannot run on him hand-verifying
# content one article at a time, so the machine has to be the thing supplying and
# checking the research, not a person reading ahead of it.
#
# This is NOT a keyword search over article TITLES — an earlier version of this corpus
# module tried exactly that and it was scrapped: it matched the Tutor topic "Strength
# Loss" to a real MILPERSMAN article about entry-level separations on the strength of
# one shared word, and "Loss" (under Transfers) to an article about safety transfers
# the same way. A confidently wrong "this is grounded" is worse than an honest
# "written from memory."
#
# What this trusts instead is narrower and already vetted: PS_TOPICS[...]["bib"] in
# app.py already names the specific MILPERSMAN hundred-series that governs each topic
# (e.g. "MILPERSMAN 1050 series" for Leave) — a line a person wrote and reviewed when
# the topic list itself was built, not something invented on the fly here. Pulling
# every CURRENT, non-cancelled article in that exact series is retrieval, not
# invention: every word handed to the model is real MILPERSMAN text, and the model is
# still barred from stating anything that text doesn't actually say. A topic whose
# bibliography doesn't name a MILPERSMAN series or article at all (most of MILPAY,
# Travel, Disbursing — governed by the JTR and the FMR, neither shipped in this corpus
# yet) simply returns nothing, exactly like an unmapped topic does today.
_SERIES_RE = re.compile(r"MILPERSMAN\s+(\d{4})\s+series", re.IGNORECASE)
_ARTICLE_RE = re.compile(r"MILPERSMAN\s+(\d{4}-\d{2,4})\b", re.IGNORECASE)
# A bib line sometimes lists more than one MILPERSMAN series after a single
# "MILPERSMAN" mention — "MILPERSMAN 1910 series, 1830 series" only says the word
# once. Every bib line in PS_TOPICS as of 24 Aug 2026 was checked: a bare "NNNN
# series" with no manual name of its own is always a continuation of the MILPERSMAN
# list that came before it in this codebase's convention — no other manual here is
# ever referred to that way. Only applied once _SERIES_RE has already found at least
# one explicit "MILPERSMAN NNNN series" in the same line, so a bib line that never
# mentions MILPERSMAN at all still grounds nothing, same as always.
_BARE_SERIES_RE = re.compile(r"\b(\d{4})\s+series\b", re.IGNORECASE)


def get_series_grounding(bib: str, max_articles: int = 8, max_chars_each: int = 2500):
    """Pull real text for every article a topic's own bibliography line points to.

    Returns (source_block, matched_article_numbers) — "" / [] if the bib line names no
    MILPERSMAN series or specific article, which the caller must treat exactly like an
    unmapped topic: fall back to the memory-safe lesson, same as always.

    max_articles caps how many articles from one series get pulled into a single
    lesson. Not a safety limit — every article in the series is equally real text —
    just a lid on prompt size, since a lesson grounded in twelve articles doesn't
    teach any better than one grounded in eight.
    """
    if not bib:
        return "", []
    con = _connect()
    if con is None:
        return "", []

    series_list = _SERIES_RE.findall(bib)
    if series_list:
        # Only once an explicit "MILPERSMAN NNNN series" has been seen — see
        # _BARE_SERIES_RE's comment above for why a bare mention is safe to include.
        for s in _BARE_SERIES_RE.findall(bib):
            if s not in series_list:
                series_list.append(s)

    explicit_articles = [a for a in _ARTICLE_RE.findall(bib)]

    per_series = []
    for series in series_list:
        try:
            rows = con.execute(
                "SELECT DISTINCT article FROM pages WHERE article LIKE ? "
                "AND is_current = 1 AND cancelled = 0 ORDER BY article",
                (f"{series}-%",),
            ).fetchall()
        except sqlite3.Error:
            rows = []
        per_series.append([r[0] for r in rows])

    # Interleave round-robin across series instead of filling from the first series
    # named — a bib line like "MILPERSMAN 1910 series, 1830 series" names both
    # deliberately, and 1910 alone runs past most caps, which would otherwise starve
    # 1830 out of the lesson entirely even though the bib line asked for it too.
    numbers = list(explicit_articles)
    i = 0
    while len(numbers) < max_articles and any(per_series):
        for series_articles in per_series:
            if i < len(series_articles):
                article = series_articles[i]
                if article not in numbers:
                    numbers.append(article)
                if len(numbers) >= max_articles:
                    break
        i += 1

    numbers = numbers[:max_articles]
    return build_source_block(numbers, max_chars_each=max_chars_each)
