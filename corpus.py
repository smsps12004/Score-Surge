"""Real MILPERSMAN text, on disk, that the Tutor can quote from instead of guessing.

Score Surge ships a plain-text copy of the MILPERSMAN (corpus/milpersman.txt) inside
the app itself. This module's only job is: given an article number like "1160-040",
hand back that article's actual wording so the AI Chief can teach from a real
citation instead of memory.

Which article number goes with which Tutor topic is NOT decided here by a keyword
guess — that was tried and rejected. A blind word-overlap search over article titles
matched "Strength Loss" to an article about entry-level separations, and "Loss"
(transfers) to an article about safety transfers, on a single shared word. A false
"this is grounded" is worse than an honest "written from memory, ask PS Agent" —
so instead app.py carries a small, hand-checked TOPIC_ARTICLE_MAP: someone actually
read each candidate article before it went in the map. Growing that map is safe,
one-time curation work, not a per-question review that would need to scale with
every rating.
"""

import os
import re
import functools

CORPUS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus")
MILPERSMAN_PATH = os.path.join(CORPUS_DIR, "milpersman.txt")

# A real article header looks like "                  MILPERSMAN 1050-210" on its own
# line. Mid-paragraph cross-references ("...per MILPERSMAN 1050-210.") are never alone
# on a line like this, so this pattern does not confuse the two.
_HEADER_RE = re.compile(r'^\s*MILPERSMAN\s+(\d{4}-\d{2,4})\s*$')


@functools.lru_cache(maxsize=1)
def _load_lines():
    """The whole MILPERSMAN, as lines. Cached so this only happens once per app run."""
    if not os.path.exists(MILPERSMAN_PATH):
        return []
    with open(MILPERSMAN_PATH, encoding="utf-8", errors="replace") as f:
        return f.readlines()


@functools.lru_cache(maxsize=1)
def _build_index():
    """One entry per real article: its number, title, and the line range it spans."""
    lines = _load_lines()
    if not lines:
        return []
    headers = []
    for i, line in enumerate(lines):
        m = _HEADER_RE.match(line.rstrip("\n"))
        if m:
            headers.append((m.group(1), i))

    entries = []
    for idx, (num, start) in enumerate(headers):
        j = start + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        title = lines[j].strip() if j < len(lines) else ""
        end = headers[idx + 1][1] if idx + 1 < len(headers) else len(lines)
        entries.append({"number": num, "title": title, "start": start, "end": end})
    return entries


def corpus_available() -> bool:
    """False if the text file didn't ship with this deploy (missing, not just empty)."""
    return bool(_load_lines())


def get_article_text(number: str, max_chars: int = 4000) -> str:
    """The real wording of one MILPERSMAN article, trimmed to a safe prompt size.

    Returns "" if the article isn't found — callers must treat that as "no grounding
    available" and fall back to the memory-only lesson, never as license to guess.
    """
    lines = _load_lines()
    for entry in _build_index():
        if entry["number"] == number:
            text = "".join(lines[entry["start"]:entry["end"]]).strip()
            if len(text) > max_chars:
                text = text[:max_chars].rsplit("\n", 1)[0] + "\n...[article continues]"
            return text
    return ""


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
