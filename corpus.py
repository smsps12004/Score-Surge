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
import random
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
        # Cut at a PARAGRAPH break, not just a line break. Cutting on the nearest
        # newline still lands mid-sentence, because manual text wraps every 60-odd
        # characters — and a sentence that stops halfway is one the model finishes
        # from memory. Measured live 25 Aug 2026: a Mock Exam question quoted
        # "...must agree to serve for 3 years in the Ready Reserve" where the supplied
        # text stopped at "...in the Ready". The gate correctly threw the question
        # away, but the sailor lost a good question to a ragged edge.
        # A SENTENCE end, not a paragraph break. Backing up to the last blank line was
        # tried first and rejected the same day: on 1050-010 the nearest blank line was
        # 700 characters earlier, which threw away the 60-day ordinary accrual limit —
        # trading a ragged edge for a missing rule is a bad trade. A sentence boundary
        # is almost always within a line or two of the cap, so it costs nothing.
        cut = text[:max_chars]
        end = max(cut.rfind(". "), cut.rfind(".\n"))
        cut = cut[:end + 1] if end > max_chars * 0.85 else cut.rsplit("\n", 1)[0]
        text = cut.rstrip() + "\n...[article continues]"
    return text


# ── READING ORDER: MILPERSMAN IS FULL OF TWO-COLUMN TABLES ───────────────────────
#
# Found live 25 Aug 2026, while measuring the new Mock Exam quote check against a real
# generation run. Most MILPERSMAN rules are printed as a "WHEN... / THEN..." table, and
# the text stored in corpus.db keeps the printed layout — the two cells of a row sit
# side by side on the same physical line, padded apart with spaces:
#
#     member is hospitalized or      chargeable leave will terminate
#     SIQ,                           the day preceding and recommence
#                                    the day following such status.
#
# Read as flat text, that is "member is hospitalized or chargeable leave will terminate
# SIQ, the day preceding and..." — the two columns interleaved into nonsense. The model
# was in fact reading it correctly and reassembling the real rule, which is why nobody
# noticed: it only showed up when the Mock Exam started asking for a VERBATIM quote and
# every honest quote failed to appear in the text it was quoting from. 5 of 7 questions
# in the first real run were dropped, and all five turned out to be quoting the rule
# correctly.
#
# So the fix belongs here, not in the checker: put the text into reading order before
# anyone — model or code — is handed it. Cells are detected by the printed layout
# itself (a run of blank columns that is blank on EVERY line of the block), never by
# guessing at content, and a block with no such gap is passed through untouched.
def _column_gaps(lines: list, min_gap: int = 2, allowed: int = 0) -> list:
    """(start, end) of every column of blanks running the full height of the block.

    Leading indentation and trailing ragged edge are excluded — those are margins, not
    column separators, and treating them as separators would split ordinary prose.

    allowed is how many lines may write straight through a separator and still leave it
    a separator. It stays 0 (blank on every line) for the first pass, because loosening
    it in general re-cuts prose that was reading fine. _flatten_block only raises it as
    a second attempt on a block where the strict pass found no table at all.
    """
    width = max(len(l) for l in lines)
    padded = [l.ljust(width) for l in lines]
    blank = [sum(1 for p in padded if p[i] == " ") >= len(padded) - allowed
             for i in range(width)]
    gaps, i = [], 0
    while i < width:
        if blank[i]:
            j = i
            while j < width and blank[j]:
                j += 1
            if i > 0 and j < width and (j - i) >= min_gap:
                gaps.append((i, j))
            i = j
        else:
            i += 1
    return gaps


def _flatten_block(lines: list) -> list:
    """One block of lines, re-emitted a table row at a time instead of line by line."""
    if len(lines) < 2:
        return lines
    width = max(len(l) for l in lines)
    gaps = _column_gaps(lines)
    # Second attempt, and only when the strict pass found nothing. A line that writes
    # straight through a column separator is not a table row — it is a centred table
    # title ("ELIGIBILITY CRITERIA FOR SPECIAL LEAVE ACCRUAL (Page 1 of 2)") or a note.
    # Found live 25 Aug 2026: one such title inside the block destroyed the separator
    # and the whole table stayed interleaved, which is how two honest quotes out of
    # 1050-010 and 1050-070 were dropped. Tolerating a few crossings here, then pulling
    # those lines back out below, recovers the table. Kept as a fallback rather than the
    # rule because raising the tolerance everywhere re-cut prose that was reading fine.
    spanning = set()
    if not gaps:
        gaps = _column_gaps(lines, allowed=max(1, len(lines) // 6))
        if not gaps:
            return lines
        spanning = {i for i, l in enumerate(lines)
                    if any(l.ljust(width)[a:b].strip() for a, b in gaps)}
    if spanning:
        out, run = [], []
        for i, line in enumerate(lines):
            if i in spanning:
                if run:
                    out.extend(_flatten_block(run))
                    run = []
                out.append(line)
            else:
                run.append(line)
        if run:
            out.extend(_flatten_block(run))
        return out

    starts = [0] + [g[1] for g in gaps]
    ends = [g[0] for g in gaps] + [width]
    grid = [[l.ljust(width)[s:e].strip() for s, e in zip(starts, ends)] for l in lines]
    # Two lines that both fill more than one cell is what makes this a table. One line
    # with something out to the right of a gap is a heading with a value beside it, and
    # is left alone.
    if sum(1 for cells in grid if sum(1 for c in cells if c) > 1) < 2:
        return lines

    # Where one table row ends and the next begins, using only the printed layout.
    #
    # A row's cells wrap over several lines, and they rarely wrap to the same depth —
    # so the tail of a row has content in some columns and blanks in the others. The
    # next row is the first line after that which fills EVERY column again. Checked
    # against two real MILPERSMAN tables (1050-050's WHEN/THEN hospitalization rules
    # and 1050-090's day-of-departure rules, which wrap to different depths): this
    # finds every real row boundary in both.
    #
    # Line 0 of a block is its column headings ("WHEN ... / THEN ..."), so it is always
    # its own row. Where a table continues across a page break there is no heading and
    # this costs the first row of the continuation — a question quoting that one row is
    # dropped, which is the safe direction to be wrong in.
    # A table that runs over a page break reprints its column headings partway down.
    # Left in, those headings land in the middle of whichever row they interrupt
    # ("...normal working hours, WHEN ... the day of departure..."), which is exactly
    # the kind of seam that makes an honest quote unfindable. Only an exact repeat of
    # this block's own heading line is removed.
    #
    # "Line 0 is a heading" only holds when line 0 actually looks like one: every cell
    # it fills is a few words ("WHEN... / THEN...", "WHEN members are ... / AND ... / THEN ...").
    # Found live 25 Aug 2026: forcing it unconditionally split the first row of every
    # table that continues from a previous page, where line 0 is a data row whose second
    # cell is a full sentence — which is how the 60-day accrual-limit rule in 1050-010
    # ended up with its row label wedged into the middle of it.
    heading = bool([c for c in grid[0] if c]) and all(
        len(c.split()) <= 4 for c in grid[0] if c)
    if heading and len(grid) >= 3:
        grid = [grid[0]] + [c for c in grid[1:] if c != grid[0]]

    rows, current = [], []
    for idx, cells in enumerate(grid):
        filled = [bool(c) for c in cells]
        new_row = idx == 0 or (idx == 1 and heading and len(grid) >= 3)
        if idx > 1 and all(filled) and not all(bool(c) for c in grid[idx - 1]):
            new_row = True
        if new_row and current:
            rows.append(current)
            current = []
        current.append(cells)
    if current:
        rows.append(current)

    out = []
    for row in rows:
        columns = []
        for col in range(len(starts)):
            joined = " ".join(cells[col] for cells in row if cells[col]).strip()
            if joined:
                columns.append(joined)
        if columns:
            out.append(" ".join(columns))
    return out


def flatten_columns(text: str) -> str:
    """An article's text in the order a person would read it off the printed page."""
    out, block = [], []
    for line in (text or "").splitlines():
        if line.strip():
            block.append(line)
        else:
            if block:
                out.extend(_flatten_block(block))
                block = []
            out.append("")
    if block:
        out.extend(_flatten_block(block))
    return "\n".join(out)


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
        # Reading order, not printed order — see _column_gaps() above for why.
        text = flatten_columns(text)
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


def _spread(articles: list, want: int) -> list:
    """Sample evenly across a series instead of taking the first few.

    A series is pulled in article-number order and then capped, so before 28 Aug 2026
    "MILPERSMAN 1050 series" always meant the numerically-lowest eight. That was
    tolerable when the corpus held twenty Leave articles. The rebuild recovered the
    twenty pre-CH ones the parser had been dropping, which are all low-numbered — so
    the cap filled entirely with 1050-080 through 1050-084 and the topic silently
    stopped reaching Separation Leave, EML or anything above 1050-085.

    Spreading costs nothing (same article count, same prompt size) and means a topic's
    grounding spans its whole series rather than one end of it. As of 3 Sep 2026 each
    slice of the series also draws a random article instead of always the same one, so
    a sailor generating several exams on one topic sees a rotating source pool instead
    of the identical handful every time — still evenly spread across the series, just
    not the same pick within each slice.
    """
    if len(articles) <= want or want <= 0:
        return articles
    step = len(articles) / want
    result = []
    for i in range(want):
        lo = int(i * step)
        hi = int((i + 1) * step) if i < want - 1 else len(articles)
        hi = max(hi, lo + 1)
        result.append(articles[random.randrange(lo, min(hi, len(articles)))])
    return result


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
        per_series.append(_spread(([r[0] for r in rows]), max_articles))

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
