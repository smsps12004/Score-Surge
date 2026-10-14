"""Placement test -- where a sailor stands before the Chief writes their first study guide.

Shawn's idea, 13 Oct 2026: before a sailor gets a Study Guide, give them a placement
test so the Chief knows where they are and how to progress them. His calls: 40
questions (the real NWAE is 175), and every AI-generated question gets saved (that part
lives in app.py's save_generated_questions(), not here).

Rules this module holds to:

- **Verified bank only.** Every question comes from question_bank.py -- the rows that
  already passed the quote match and the AI judge. The Chief is going to build a study
  plan on this result, so an unchecked answer key would mean telling a sailor they are
  weak at something they actually got right. No AI call is made anywhere in here, which
  is also why the test can be free.
- **Say what wasn't tested.** A topic with no verified questions (Disbursing, Receipts,
  Travel for E6 today) is reported as "not tested yet", never as a score. A blank is not
  a zero and it is certainly not a pass.
- **Don't run a test that can't be fair.** If the bank can't fill all 40 questions across
  at least MIN_TOPICS topics, placement_ready() is False and the app says "coming soon"
  instead of running a thin test. E5 has 7 verified questions today, so E5 waits.

Everything here is a pure function so run_checks.py can test it without Streamlit.
"""

import random

import question_bank

PLACEMENT_SIZE = 40
MIN_TOPICS = 3          # a "placement" across one or two topics isn't one
MIN_PER_TOPIC = 3       # fewer than this and a per-topic level is a coin flip

# Level bands. With ~5 questions a topic: 4-5 right is Strong, 3 is Needs work,
# 0-2 is Start here.
STRONG_AT = 80
NEEDS_WORK_AT = 60

LEVEL_STRONG = "Strong"
LEVEL_NEEDS_WORK = "Needs work"
LEVEL_START_HERE = "Start here"
LEVEL_TOO_FEW = "Too few questions to judge"
LEVEL_UNTESTED = "Not tested yet"

LEVEL_ICONS = {
    LEVEL_STRONG: "🟢",
    LEVEL_NEEDS_WORK: "🟡",
    LEVEL_START_HERE: "🔴",
    LEVEL_TOO_FEW: "⚪",
    LEVEL_UNTESTED: "⚪",
}


def coverage(rating: str, paygrade: str, topics: list) -> dict:
    """topic -> how many verified bank questions exist for it."""
    return {t: question_bank.bank_count(rating, paygrade, t) for t in topics}


def allocate(counts: dict, n: int = PLACEMENT_SIZE) -> dict:
    """Spread n questions across topics as evenly as the bank allows.

    Round robin: one question per covered topic per pass, skipping any topic that has
    run out, until n is reached or every topic is empty. 40 across 8 covered E6 topics
    is 5 each; a topic with only 3 gives its share to the others.
    """
    alloc = {t: 0 for t, c in counts.items() if c > 0}
    remaining = n
    while remaining > 0:
        progressed = False
        for t in alloc:
            if remaining == 0:
                break
            if alloc[t] < counts[t]:
                alloc[t] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
    return {t: k for t, k in alloc.items() if k > 0}


def placement_ready(rating: str, paygrade: str, topics: list, n: int = PLACEMENT_SIZE) -> bool:
    """True only if the verified bank can fill the whole test across enough topics."""
    alloc = allocate(coverage(rating, paygrade, topics), n)
    judged = [t for t, k in alloc.items() if k >= MIN_PER_TOPIC]
    return sum(alloc.values()) == n and len(judged) >= MIN_TOPICS


def build_placement(rating: str, paygrade: str, topics: list,
                    n: int = PLACEMENT_SIZE, rng=None) -> tuple:
    """(questions, untested_topics).

    Each question is a bank question dict with a "topic" key added, shuffled so topics
    are mixed the way the real exam mixes them. Returns ([], untested) if the test
    isn't ready -- callers check placement_ready() first, this is the backstop.
    """
    rng = rng or random.Random()
    counts = coverage(rating, paygrade, topics)
    untested = [t for t in topics if counts.get(t, 0) == 0]
    if not placement_ready(rating, paygrade, topics, n):
        return [], untested
    questions = []
    for topic, k in allocate(counts, n).items():
        for q in question_bank.get_bank_questions(rating, paygrade, topic, k):
            questions.append({**q, "topic": topic})
    rng.shuffle(questions)
    return questions, untested


def level_for(correct: int, total: int) -> str:
    if total <= 0:
        return LEVEL_UNTESTED
    if total < MIN_PER_TOPIC:
        return LEVEL_TOO_FEW
    pct = 100 * correct / total
    if pct >= STRONG_AT:
        return LEVEL_STRONG
    if pct >= NEEDS_WORK_AT:
        return LEVEL_NEEDS_WORK
    return LEVEL_START_HERE


def grade_placement(questions: list, picks: list) -> dict:
    """Grade against the bank's own answer key.

    Returns {"correct", "total", "pct", "topics": {topic: {correct, total, pct, level}},
    "rows": [question + picked + is_right]}. A blank answer counts as wrong -- same as
    the real exam.
    """
    topics, rows, correct_all = {}, [], 0
    for q, picked in zip(questions, picks):
        is_right = bool(picked) and picked == q.get("correct_answer")
        correct_all += 1 if is_right else 0
        rows.append({**q, "picked": picked, "is_right": is_right})
        t = topics.setdefault(q.get("topic", "—"), {"correct": 0, "total": 0})
        t["total"] += 1
        t["correct"] += 1 if is_right else 0
    for t in topics.values():
        t["pct"] = round(100 * t["correct"] / t["total"]) if t["total"] else 0
        t["level"] = level_for(t["correct"], t["total"])
    total = len(questions)
    return {
        "correct": correct_all,
        "total": total,
        "pct": round(100 * correct_all / total) if total else 0,
        "topics": topics,
        "rows": rows,
    }


def history_entries(result: dict, date_str: str) -> list:
    """One score_history row per tested topic, in the shape the Mock Exam already saves.

    Same topic names as the Mock Exam uses, on purpose: My Profile's weak topics, the
    Info tab's "start here" line and the Study Guide all read score_history, so a
    placement result reaches every one of them with no extra wiring.
    """
    return [
        {"date": date_str, "topic": topic, "score": t["correct"],
         "total": t["total"], "pct": t["pct"]}
        for topic, t in result.get("topics", {}).items()
        if t.get("total", 0) > 0
    ]


def latest_by_topic(score_history: list, topics: list) -> dict:
    """topic -> the sailor's most recent pct on it (placement or Mock Exam).

    score_history is oldest-first, so the last match wins. Only topics in `topics` are
    returned -- this paygrade's list.
    """
    wanted = set(topics)
    latest = {}
    for h in score_history or []:
        if not isinstance(h, dict):
            continue
        t = str(h.get("topic") or "").strip()
        if t not in wanted:
            continue
        try:
            total = float(h.get("total") or 0)
            pct = float(h.get("pct") or 0)
        except (TypeError, ValueError):
            continue
        if total <= 0:
            continue
        latest[t] = {"pct": pct, "total": total}
    return latest


def weakest_topic(score_history: list, topics: list):
    """The topic the Chief should open on, or None if nothing has been tested.

    Lowest most-recent score among topics scored below Strong. If every tested topic is
    Strong there's no weak spot to point at, so this returns None and the Study Guide
    keeps its normal default.
    """
    latest = latest_by_topic(score_history, topics)
    weak = [(v["pct"], topics.index(t), t) for t, v in latest.items() if v["pct"] < STRONG_AT]
    if not weak:
        return None
    return min(weak)[2]


def has_placement_history(score_history: list, topics: list) -> bool:
    """True once the sailor has any graded result on this paygrade's topics."""
    return bool(latest_by_topic(score_history, topics))


def chief_brief(score_history: list, topics: list) -> str:
    """What the Chief knows about this sailor, as a block for the Study Guide prompt.

    Practice scores only -- no facts about the manuals -- so it sits safely alongside
    the accuracy rules. Empty string if nothing has been tested yet, so the prompt is
    unchanged for a sailor with no history.
    """
    latest = latest_by_topic(score_history, topics)
    if not latest:
        return ""
    lines = []
    for t in topics:
        if t in latest:
            v = latest[t]
            lvl = level_for(round(v["pct"] * v["total"] / 100), int(v["total"]))
            lines.append(f"- {t}: {round(v['pct'])}% on their latest attempt ({lvl})")
        else:
            lines.append(f"- {t}: not tested yet -- you do not know their level here")
    return (
        "\n=== WHAT YOU KNOW ABOUT THIS SAILOR ===\n\n"
        "Their most recent scored results by topic, from their placement test and mock "
        "exams in this app:\n" + "\n".join(lines) + "\n\n"
        "Use this. Put their weakest topics first in the plan and spend the most time "
        "there. Do not spend days on a topic they are already Strong in. Treat a topic "
        "marked 'not tested yet' as unknown -- not as a strength -- and tell them to test "
        "it. These are practice scores, not facts about the manuals, so they change "
        "nothing in the accuracy rules below.\n"
    )
