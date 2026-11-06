"""The Weekly Five: which questions run which week, checked before they go live.

(T49, 5 Nov 2026.) The game itself -- the 20-second clock, one try per week, the
grading -- lives in the database (supabase/migrations/20261105_weekly_five.sql), so
a refreshed page or a hand-made request can't bend it. This module is the app's side:

- data/weekly_five.csv is the schedule: one row per question, `week_id,idx,question_id`.
  week_id is the Friday the quiz opens. Marketing picks the five from
  marketing_approved.csv (word-for-word checked against the manual) and runs the
  same-day online check, as for every Quiz Drop.
- `python weekly_five.py check` says whether the schedule is safe to load.
- `python weekly_five.py seed 2026-11-20` writes supabase/weekly_five/2026-11-20.sql,
  the small SQL file Shawn pastes into Supabase to load that week. The answer key in
  it is read from the verified bank, never typed by hand.
- The quiz shows questions only from the verified bank (data/ps_question_bank.csv,
  Quote_Verified and Judge_Verdict both PASS). Playing the game never marks anything
  verified; only the gates do.

Times: every quiz opens Friday 6:30 PM and closes Sunday 11:59 PM, US Central, the
same as the official rules page says.

Testing the SQL (Postgres 16): load supabase/tests/stub.sql, then every file in
supabase/migrations/ in order, then run supabase/tests/weekly_five.sql. Its last line
says ALL PASS.
"""

import csv
import datetime
import functools
import hashlib
import os
import sys
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
BANK_CSV = os.path.join(HERE, "data", "ps_question_bank.csv")
SCHEDULE_CSV = os.path.join(HERE, "data", "weekly_five.csv")
SEED_DIR = os.path.join(HERE, "supabase", "weekly_five")

CENTRAL = ZoneInfo("America/Chicago")
QUESTIONS_PER_WEEK = 5
SECONDS_PER_QUESTION = 20       # must match public._w5_seconds() in the migration
OPEN_TIME = datetime.time(18, 30)
CLOSE_TIME = datetime.time(23, 59)
LETTERS = ("A", "B", "C", "D")


# ── The verified bank, by ID ─────────────────────────────────────────────────
@functools.lru_cache(maxsize=1)
def verified_bank() -> dict:
    """ID -> row, for PS rows that passed both gates and have 4 answers and a key."""
    out = {}
    try:
        with open(BANK_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if (row.get("Rating") or "").strip() != "PS":
                    continue
                if (row.get("Quote_Verified") or "").strip() != "PASS":
                    continue
                if (row.get("Judge_Verdict") or "").strip() != "PASS":
                    continue
                if (row.get("Correct_Answer") or "").strip().upper() not in LETTERS:
                    continue
                if not (row.get("Question") or "").strip():
                    continue
                if not all((row.get(f"Answer_{L}") or "").strip() for L in LETTERS):
                    continue
                out[(row.get("ID") or "").strip()] = row
    except (OSError, csv.Error):
        return {}
    return out


def question(question_id: str):
    """The bank row for one ID as a plain dict, or None if it isn't verified."""
    row = verified_bank().get((question_id or "").strip())
    if not row:
        return None
    return {
        "id": row["ID"].strip(),
        "question": row["Question"].strip(),
        "answers": {L: row[f"Answer_{L}"].strip() for L in LETTERS},
        "correct": row["Correct_Answer"].strip().upper(),
        "source_manual": (row.get("Source_Manual") or "").strip(),
        "edition": (row.get("Manual_Edition") or "").strip(),
        "page": (row.get("Page") or "").strip(),
        "quote": (row.get("Verbatim_Quote") or "").strip(),
        "url": (row.get("Source_URL") or "").strip(),
        "paygrade": (row.get("Paygrade") or "").strip(),
        "topic": (row.get("Exam_Area") or "").strip(),
    }


def as_bank_row(q: dict) -> dict:
    """The shape app.render_challenge_button() expects, for the Challenge button."""
    return {
        "question": q["question"],
        **{f"answer_{L.lower()}": q["answers"][L] for L in LETTERS},
        "correct_answer": q["correct"],
        "explanation": f'Per {q["source_manual"]}: "{q["quote"]}"' if q["quote"] else "",
        "source_manual": q["source_manual"],
        "chapter_section": f'p. {q["page"]}' if q["page"] else "",
        "source_quote": q["quote"],
        "verified": "yes",
        "grounded": "yes",
    }


# ── Answer order ─────────────────────────────────────────────────────────────
def shuffled(user_id: str, week_id: str, idx: int):
    """The order this sailor sees the four answers in: [(shown letter, real letter)].

    Different sailors see different orders, so "it's C" passed around a shop is no
    help. The same sailor always sees the same order (a refresh doesn't reshuffle).
    The real letter is what gets sent to the database for grading.
    """
    seed = hashlib.sha256(f"{user_id}|{week_id}|{idx}".encode()).digest()
    order = sorted(LETTERS, key=lambda L: hashlib.sha256(seed + L.encode()).digest())
    return list(zip(LETTERS, order))


# ── Schedule ─────────────────────────────────────────────────────────────────
def window(week_id: str):
    """(opens_at, closes_at) in US Central for a week named by its Friday."""
    friday = datetime.date.fromisoformat(week_id)
    opens = datetime.datetime.combine(friday, OPEN_TIME, tzinfo=CENTRAL)
    closes = datetime.datetime.combine(friday + datetime.timedelta(days=2), CLOSE_TIME,
                                       tzinfo=CENTRAL)
    return opens, closes


def load_schedule(path: str = SCHEDULE_CSV) -> dict:
    """week_id -> [question_id for idx 1..5] (rows in idx order)."""
    weeks = {}
    if not os.path.exists(path):
        return weeks
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(line for line in f if not line.startswith("#")):
            weeks.setdefault(row["week_id"].strip(), []).append(
                (int(row["idx"]), row["question_id"].strip()))
    return {w: [q for _, q in sorted(rows)] for w, rows in weeks.items()}


def problems(schedule: dict | None = None) -> list:
    """Everything that would make a week unsafe to load. [] means safe."""
    schedule = load_schedule() if schedule is None else schedule
    bank = verified_bank()
    found, seen_anywhere = [], {}
    for week_id, ids in schedule.items():
        try:
            friday = datetime.date.fromisoformat(week_id)
        except ValueError:
            found.append(f"{week_id}: not a date (use the Friday, YYYY-MM-DD)")
            continue
        if friday.weekday() != 4:
            found.append(f"{week_id}: not a Friday")
        if len(ids) != QUESTIONS_PER_WEEK:
            found.append(f"{week_id}: has {len(ids)} questions, needs {QUESTIONS_PER_WEEK}")
        if len(set(ids)) != len(ids):
            found.append(f"{week_id}: the same question twice")
        for qid in ids:
            if qid not in bank:
                found.append(f"{week_id}: {qid} is not a verified PS question in the bank")
            if qid in seen_anywhere and seen_anywhere[qid] != week_id:
                found.append(f"{week_id}: {qid} already ran in {seen_anywhere[qid]}")
            seen_anywhere.setdefault(qid, week_id)
    return found


def _sql_text(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def seed_sql(week_id: str, schedule: dict | None = None) -> str:
    """The SQL that loads one week. Refuses if the schedule has a problem."""
    schedule = load_schedule() if schedule is None else schedule
    if week_id not in schedule:
        raise ValueError(f"{week_id} isn't in {os.path.basename(SCHEDULE_CSV)}")
    bad = [p for p in problems({week_id: schedule[week_id]})]
    if bad:
        raise ValueError("; ".join(bad))
    opens, closes = window(week_id)
    keys = [(i + 1, qid, question(qid)["correct"]) for i, qid in enumerate(schedule[week_id])]
    values = ",\n  ".join(f"({_sql_text(week_id)}, {i}, {_sql_text(q)}, {_sql_text(c)})"
                          for i, q, c in keys)
    ids = ", ".join(_sql_text(q) for _, q, _ in keys)
    return f"""-- Weekly Five, week of {week_id} (made by `python weekly_five.py seed {week_id}`).
-- Opens {opens:%a %d %b %Y %I:%M %p} Central, closes {closes:%a %d %b %Y %I:%M %p} Central.
-- Questions (verified bank): {", ".join(q for _, q, _ in keys)}.
-- Paste into Supabase > SQL Editor > Run. Safe to run twice. It never changes a week
-- that's already loaded: if this week exists with other questions it stops with an
-- error and changes nothing.
-- Needs 20261105_weekly_five.sql to have been run first.

begin;
insert into public.w5_weeks (week_id, opens_at, closes_at)
values ({_sql_text(week_id)}, {_sql_text(opens.isoformat())}, {_sql_text(closes.isoformat())})
on conflict (week_id) do nothing;

insert into public.w5_key (week_id, idx, question_id, correct) values
  {values}
on conflict (week_id, idx) do nothing;

do $$
begin
  if (select count(*) from public.w5_key
       where week_id = {_sql_text(week_id)} and question_id in ({ids})) <> {len(keys)} then
    raise exception 'Week {week_id} is already loaded with different questions. Nothing changed.';
  end if;
end $$;
commit;

-- Check: should list {len(keys)} rows.
select idx, question_id from public.w5_key where week_id = {_sql_text(week_id)} order by idx;
"""


# ── Words the app shows ──────────────────────────────────────────────────────
def when_text(ts) -> str:
    """'Sun 22 Nov, 11:59 PM Central' from an ISO timestamp or datetime."""
    if isinstance(ts, str):
        ts = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    local = ts.astimezone(CENTRAL)
    hour = local.strftime("%I:%M %p").lstrip("0")
    return f"{local:%a} {local.day} {local:%b}, {hour} Central"


def seconds_text(ms) -> str:
    return f"{(ms or 0) / 1000:.1f} s"


def main(argv):
    if len(argv) >= 2 and argv[1] == "check":
        bad = problems()
        weeks = load_schedule()
        if bad:
            print("NOT SAFE TO LOAD:\n  - " + "\n  - ".join(bad))
            return 1
        print(f"OK: {len(weeks)} week(s), every question verified: {', '.join(sorted(weeks)) or '(none yet)'}")
        return 0
    if len(argv) >= 3 and argv[1] == "seed":
        sql = seed_sql(argv[2])
        os.makedirs(SEED_DIR, exist_ok=True)
        out = os.path.join(SEED_DIR, f"{argv[2]}.sql")
        with open(out, "w", encoding="utf-8") as f:
            f.write(sql)
        print(f"wrote {os.path.relpath(out, HERE)}")
        return 0
    print(__doc__)
    print("usage: python weekly_five.py check | seed <friday YYYY-MM-DD>")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
