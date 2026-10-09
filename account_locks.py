"""Account locks — 30 Sep 2026.

Four things that stop one paid login from becoming a whole shop's login:

  1. RATING LOCK   — a sailor picks their rating once; every rating menu in the app
                     is fixed to it. The database only lets them change it once
                     every 180 days (Shawn can change it any time in the dashboard).
  2. ONE LOGIN     — signing in on a new device marks that device as the active
                     one; the old device notices within a minute and signs out.
  3. MONTHLY LIMITS— AI-written content and downloads are counted per calendar
                     month in the database (usage_counters), against limits in
                     usage_limits. Questions served from the verified bank cost
                     nothing to run and are NOT counted.
  4. STAMPED FILES — every download carries the sailor's email.

The rules themselves are enforced in Postgres
(supabase/migrations/20260930_account_locks.sql) — this module only reads them and
draws the UI. A sailor cannot bypass a limit by editing the app, because the app is
not where the count lives.

FAIL-OPEN ON A BLIP, by design. get_user_tier() in app.py already follows the rule
"a read failure must never be treated as this person has not paid" — the same holds
here. If the usage check can't reach Supabase, the sailor is allowed through and the
failure is printed to the server log. A limit that locks paying sailors out whenever
the network hiccups is worse than a limit that occasionally lets one extra through.
"""

from __future__ import annotations

import time
import uuid

RATING_CHOICES = ["PS", "YN", "NC"]
RATING_CHANGE_DAYS = 180
SESSION_CHECK_SECONDS = 60
TUTOR_FOLLOWUPS_PER_LESSON = 10

# Mirrors the starting rows in usage_limits. Used only for wording when the
# database can't be reached — the database's own numbers always win.
DEFAULT_LIMITS = {
    "free":          {"ai": 3,  "download": 5},
    "trial":         {"ai": 10, "download": 10},
    "petty_officer": {"ai": 30, "download": 30},
    "chief":         {"ai": 75, "download": 60},
}

KIND_LABELS = {"ai": "AI generations", "download": "downloads"}


# ── 1. Rating lock ────────────────────────────────────────────────────────────
def load_rating(supabase, user_id: str):
    """(rating, rating_set_at) from the profile, or (None, None).

    Returns ("?", None) when the read itself failed, so the caller can tell
    "hasn't picked yet" (show the picker) from "couldn't find out" (don't nag
    a sailor who already picked — let them through this render).
    """
    try:
        rows = (supabase.table("profiles").select("rating, rating_set_at")
                .eq("id", user_id).limit(1).execute().data or [])
    except Exception as e:
        print(f"[account_locks] could not read rating: {e!r}")
        return "?", None
    if not rows:
        return None, None
    return rows[0].get("rating"), rows[0].get("rating_set_at")


def save_rating(supabase, user_id: str, rating: str) -> bool:
    """Ask the database to set the rating. True only if it actually took.

    The database quietly keeps the old value if the change isn't allowed yet, so
    the result is read back rather than assumed.
    """
    if rating not in RATING_CHOICES:
        return False
    try:
        supabase.table("profiles").update({"rating": rating}).eq("id", user_id).execute()
        got, _ = load_rating(supabase, user_id)
        return got == rating
    except Exception as e:
        print(f"[account_locks] could not save rating: {e!r}")
        return False


def next_rating_change(rating_set_at) -> str | None:
    """ISO date the sailor may next change rating themselves, or None if now."""
    import datetime
    if not rating_set_at:
        return None
    try:
        set_at = datetime.datetime.fromisoformat(str(rating_set_at).replace("Z", "+00:00"))
    except Exception:
        return None
    nxt = set_at + datetime.timedelta(days=RATING_CHANGE_DAYS)
    if nxt <= datetime.datetime.now(datetime.timezone.utc):
        return None
    return nxt.date().isoformat()


# ── 2. One login at a time ────────────────────────────────────────────────────
def claim_session(supabase, user_id: str, state) -> None:
    """Mark this browser session as the sailor's active one."""
    token = uuid.uuid4().hex
    state["_active_session"] = token
    state["_session_checked_at"] = time.time()
    try:
        supabase.table("profiles").update({"active_session": token}).eq("id", user_id).execute()
    except Exception as e:
        print(f"[account_locks] could not claim session: {e!r}")


def session_still_active(supabase, user_id: str, state, now: float | None = None) -> bool:
    """False only when the database positively names a DIFFERENT active session.

    Checked at most once a minute, so it costs one small read per minute rather
    than one per button tap. A failed read, or a sailor from before this feature
    (no token on either side yet), counts as still active.
    """
    now = time.time() if now is None else now
    mine = state.get("_active_session")
    if not mine:
        return True
    if now - state.get("_session_checked_at", 0) < SESSION_CHECK_SECONDS:
        return True
    state["_session_checked_at"] = now
    try:
        rows = (supabase.table("profiles").select("active_session")
                .eq("id", user_id).limit(1).execute().data or [])
    except Exception as e:
        print(f"[account_locks] could not check session: {e!r}")
        return True
    if not rows:
        return True
    theirs = rows[0].get("active_session")
    return (not theirs) or theirs == mine


# ── 3. Monthly limits ─────────────────────────────────────────────────────────
def get_usage(supabase) -> dict | None:
    """{'ai': {'used','limit','left'}, 'download': {...}, 'tier', 'period'} or None."""
    try:
        res = supabase.rpc("get_usage").execute()
        data = res.data
        return data if isinstance(data, dict) else None
    except Exception as e:
        print(f"[account_locks] could not read usage: {e!r}")
        return None


def has_left(usage: dict | None, kind: str) -> bool:
    """Whether one more of `kind` is allowed. Unknown usage → allowed (fail-open)."""
    if not usage or kind not in usage:
        return True
    try:
        return int(usage[kind].get("left", 0)) > 0
    except Exception:
        return True


def consume(supabase, kind: str) -> dict:
    """Spend one unit. Returns the database's answer, or allowed=True on a blip."""
    try:
        res = supabase.rpc("consume_usage", {"p_kind": kind}).execute()
        data = res.data
        if isinstance(data, dict) and "allowed" in data:
            return data
    except Exception as e:
        print(f"[account_locks] could not record {kind} usage: {e!r}")
    return {"allowed": True, "unknown": True}


def usage_line(usage: dict | None, kind: str) -> str:
    """'27 of 30 AI generations left this month' — or '' if unknown."""
    if not usage or kind not in usage:
        return ""
    try:
        left, limit = int(usage[kind]["left"]), int(usage[kind]["limit"])
    except Exception:
        return ""
    return f"{left} of {limit} {KIND_LABELS.get(kind, kind)} left this month"


def limit_message(kind: str, tier: str | None) -> str:
    """What a sailor sees when a limit is reached."""
    what = "AI-written study material" if kind == "ai" else "downloads"
    msg = (f"You've used this month's {what} for your plan. It resets on the 1st. ")
    if kind == "ai":
        msg += "Verified bank questions in Mock Exam and Practice Questions are still unlimited. "
    if tier in ("free", "trial", "petty_officer"):
        msg += "Upgrading raises the limit."
    return msg.strip()


# ── 4. Stamped downloads ──────────────────────────────────────────────────────
def stamp_text(text: str, email: str) -> str:
    """Put the sailor's email at the top and bottom of a text download."""
    line = f"Licensed to {email} — Score Surge by Strategic Sailor. Personal study use only; do not redistribute."
    return f"{line}\n\n{text}\n\n{line}\n"
