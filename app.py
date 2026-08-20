import streamlit as st
import pandas as pd
import re
import io
import json
import base64
import time
import tempfile
import os
import datetime
from fpdf import FPDF
import anthropic
import stripe

# PAGE CONFIG — must be first
st.set_page_config(page_title="Score Surge", page_icon="⚓", layout="centered")

# Optional imports
try:
    import pytesseract
    from PIL import Image, ImageFilter, ImageOps
    OCR_IMAGE_AVAILABLE = True
except ImportError:
    OCR_IMAGE_AVAILABLE = False

try:
    import fitz
    OCR_PDF_AVAILABLE = True
except ImportError:
    OCR_PDF_AVAILABLE = False

# ── SUPABASE SETUP ────────────────────────────────────────────────────────────
from supabase import create_client, Client

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

stripe.api_key = st.secrets["STRIPE_SECRET_KEY"]
STRIPE_PUBLISHABLE_KEY = st.secrets.get("STRIPE_PUBLISHABLE_KEY", "")
ANTHROPIC_API_KEY = st.secrets["ANTHROPIC_API_KEY"]

# ── SESSION STATE INIT ────────────────────────────────────────────────────────
for key, default in [
    ("user", None),
    ("tier", None),
    ("access_token", None),
    ("refresh_token", None),
    ("_payment_success", False),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── RESTORE SESSION ACROSS RERUNS ─────────────────────────────────────────────
def _token_expiring(token: str, within_seconds: int = 120) -> bool:
    """True if this access token is close enough to expiry to be worth refreshing.

    Reading the `exp` claim is local and free. Calling set_session on every rerun
    would put a network round trip behind every single radio button on a mock exam.
    Unreadable or undated tokens are treated as expiring, so the slow, safe path runs.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)  # base64url, padding stripped
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return True if not exp else (float(exp) - time.time()) < within_seconds
    except Exception:
        return True


# Streamlit re-executes this entire file on every interaction, and `create_client`
# above returns a brand new, ANONYMOUS client each time. st.session_state survives
# a rerun; the client's authentication does not.
#
# The guard here used to be `if access_token and not st.session_state.user:` — so on
# the first rerun after login, `user` was already set, the restore was skipped, and
# from then on every database call went out unauthenticated. Postgres refused the
# insert with "new row violates row-level security policy" (42501) because auth.uid()
# was NULL and could not be matched against user_id. That is the RLS policy working
# exactly as intended. Reads failed the same way and returned nothing, which is why
# score history only ever survived inside a single browser session.
#
# Loosening the policy would "fix" this by letting anyone read and write every
# sailor's scores. The fix belongs here.
if st.session_state.access_token:
    try:
        if not st.session_state.user or _token_expiring(st.session_state.access_token):
            res = supabase.auth.set_session(
                st.session_state.access_token,
                st.session_state.refresh_token
            )
            st.session_state.user = res.user
            # set_session can hand back rotated tokens. Keeping the old ones would
            # mean refreshing again on every rerun from here on.
            if getattr(res, "session", None):
                st.session_state.access_token = res.session.access_token
                st.session_state.refresh_token = res.session.refresh_token
        else:
            # The token is still good — attach it to this run's client so database
            # calls carry the sailor's identity. Local, no network round trip.
            supabase.postgrest.auth(st.session_state.access_token)
    except Exception:
        st.session_state.access_token = None
        st.session_state.refresh_token = None
        st.session_state.user = None

# ── TIER HELPERS ──────────────────────────────────────────────────────────────
TIER_ORDER = ["free", "petty_officer", "chief"]

TIER_LABELS = {
    "free":          "⚓ Seaman — Free",
    "trial":         "🎖️ Trial (3-day full access)",
    "petty_officer": "🎖️ Petty Officer — $12.99/mo",
    "chief":         "⭐ Chief — $19.99/mo",
}

UPGRADE_INFO = {
    "petty_officer": ("Petty Officer", "$12.99/mo", "AI Study Guide + Interactive AI Tutor"),
    "chief":         ("Chief", "$19.99/mo", "Full Mock Exam + Smart Advancement Planner + BBA Strategy Hub"),
}

# Prices new checkouts use. Stripe prices are immutable, so a price change means
# creating a new price and pointing here at it.
STRIPE_PRICE_IDS = {
    "petty_officer": "price_1TxgMkDP0fFhPzMl2MEk9OZk",   # $12.99/mo, created 26 Jul 2026
    "chief":         "price_1TxgLnDP0fFhPzMlRxZuLWpU",   # $19.99/mo, created 26 Jul 2026
}

# Retired prices. Nobody can check out at these any more, but sailors who
# subscribed before the change keep them, so they must still map to a tier.
LEGACY_PRICE_IDS = {
    "price_1Tw3DsDP0fFhPzMlLaeh0Ixs": "petty_officer",   # $12.00/mo
    "price_1Tw3EZDP0fFhPzMlc8E3QcY8": "chief",           # $20.00/mo
}

PRICE_TO_TIER = {v: k for k, v in STRIPE_PRICE_IDS.items()}
PRICE_TO_TIER.update(LEGACY_PRICE_IDS)

def get_user_tier(user_id: str, user_email: str = "") -> str:
    """What this sailor is entitled to see.

    This used to call `.single()`, which raises both when there is no row AND when
    the network hiccups — the same except branch handled both. So one transient
    Supabase blip took a PAYING sailor, wrote a profile row, and showed them "free"
    with their paid tabs locked. Money in, access out. A read failure must never be
    treated as "this person has not paid".

    A plain select tells the two cases apart: `[]` means genuinely new, an exception
    means we could not find out. Only the first one is allowed to write anything.
    """
    def _fetch():
        return (
            supabase.table("profiles")
            .select("tier, trial_start")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )

    try:
        try:
            result = _fetch()
        except Exception:
            result = _fetch()  # one retry — a blip should not cost someone their tier

        rows = result.data or []

        if not rows:
            # Genuinely new. Signup only creates this row when Supabase returns a
            # session immediately; with email confirmation switched on it does not,
            # and every new sailor silently lost the 3-day trial they were promised
            # on the signup screen. Creating it here closes that hole.
            new_row = {
                "id": user_id,
                "tier": "trial",
                "trial_start": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            if user_email:
                # The Stripe webhook matches profiles on email. A row without one can
                # never be upgraded by it.
                new_row["email"] = user_email
            try:
                supabase.table("profiles").insert(new_row).execute()
                return "trial"
            except Exception:
                # Most likely the row exists after all and this raced, or `email` is
                # not a column. Either way: do not invent an entitlement.
                return "free"

        profile = rows[0]
        tier = profile.get("tier") or "free"
        if tier == "trial":
            started = profile.get("trial_start")
            if not started:
                return "trial"  # no start date recorded — do not expire them early
            try:
                trial_start = datetime.datetime.fromisoformat(
                    str(started).replace("Z", "+00:00")
                )
            except Exception:
                return "trial"
            days_elapsed = (datetime.datetime.now(datetime.timezone.utc) - trial_start).days
            if days_elapsed >= 3:
                supabase.table("profiles").update({"tier": "free"}).eq("id", user_id).execute()
                return "free"
        return tier
    except Exception:
        # We could not read the tier. Show the least-privilege view for this render,
        # but write NOTHING — the next rerun tries again, and a paying sailor is not
        # demoted in the database over a failed lookup.
        return "free"


def can_access(required_tier: str) -> bool:
    user_tier = st.session_state.tier
    if user_tier == "trial":
        return True
    if user_tier not in TIER_ORDER or required_tier not in TIER_ORDER:
        return False
    return TIER_ORDER.index(user_tier) >= TIER_ORDER.index(required_tier)


def get_app_base_url() -> str:
    try:
        return st.context.url.split("?")[0].rstrip("/")
    except Exception:
        return st.secrets.get("APP_URL", "http://localhost:8501")


def create_checkout_session(tier: str, user_email: str):
    price_id = STRIPE_PRICE_IDS[tier]
    base = get_app_base_url()
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            customer_email=user_email,
            client_reference_id=tier,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{base}/?stripe_success=true&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base}/",
        )
        return session.url
    except Exception as e:
        st.error(f"Could not start checkout: {e}")
        return None


def upgrade_banner(required_tier: str, where: str):
    """Draw a locked-tab banner. `where` only has to be unique per call site.

    A Stripe Checkout Session is created when the sailor taps Upgrade — never
    while the page is drawing. This used to call create_checkout_session() inline,
    and because Streamlit re-runs the code for all seven tabs on every single
    interaction, a free user typing in the FMS calculator created five live Stripe
    sessions per keystroke: five network round trips of added lag on every rerun,
    thousands of abandoned sessions in the Stripe dashboard, and five stacked red
    errors on a working page any time Stripe was slow or the key was wrong.
    """
    label, price, features = UPGRADE_INFO.get(required_tier, ("", "", ""))
    st.warning(f"🔒 **{label} tier required** ({price})\n\nUnlock: {features}")

    if not st.session_state.get("user"):
        st.info("Log in to upgrade your plan.")
        return

    # Keyed on the tier, not the call site: the same tier is the same checkout, so
    # tapping Upgrade on one locked tab is worth one Stripe call, not five.
    url_key = f"_checkout_url_{required_tier}"
    if st.button(
        f"⬆️ Upgrade to {label} — {price}",
        key=f"upgrade_{required_tier}_{where}",
        width="stretch",
    ):
        with st.spinner("Opening secure checkout..."):
            st.session_state[url_key] = create_checkout_session(
                required_tier, st.session_state.user.email
            )

    if st.session_state.get(url_key):
        st.link_button(
            f"✅ Continue to secure checkout — {label}",
            url=st.session_state[url_key],
            width="stretch",
        )
        st.caption("Opens Stripe in a new tab. Your card details never touch Score Surge.")


def load_score_history(user_id: str) -> list:
    try:
        result = (
            supabase.table("score_history")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at")
            .execute()
        )
        return result.data or []
    except Exception:
        return []


# ── AUTH PAGE ─────────────────────────────────────────────────────────────────
def show_auth_page():
    st.title("⚓ Score Surge | by Strategic Sailor")
    st.markdown("Your Navy advancement engine. Calculate your FMS, study smarter, and advance.")
    st.divider()

    tab_login, tab_signup = st.tabs(["Log In", "Create Account"])

    with tab_login:
        st.subheader("Welcome back, Sailor.")
        with st.form("login_form"):
            login_email = st.text_input("Email")
            login_password = st.text_input("Password", type="password")
            login_submit = st.form_submit_button("Log In", width="stretch")

        if login_submit:
            if not login_email or not login_password:
                st.error("Enter your email and password.")
            else:
                try:
                    res = supabase.auth.sign_in_with_password({
                        "email": login_email,
                        "password": login_password,
                    })
                    st.session_state.user = res.user
                    st.session_state.access_token = res.session.access_token
                    st.session_state.refresh_token = res.session.refresh_token
                    st.session_state.tier = get_user_tier(res.user.id, res.user.email or "")
                    st.rerun()
                except Exception:
                    st.error("Login failed — check your email and password.")

    with tab_signup:
        st.subheader("Start your free 3-day trial.")
        st.caption("Full access for 3 days. No credit card required.")
        with st.form("signup_form"):
            signup_email = st.text_input("Email")
            signup_password = st.text_input("Password", type="password")
            signup_password2 = st.text_input("Confirm Password", type="password")
            signup_submit = st.form_submit_button(
                "Create Account & Start Free Trial", width="stretch"
            )

        if signup_submit:
            if not signup_email or not signup_password:
                st.error("Fill in all fields.")
            elif signup_password != signup_password2:
                st.error("Passwords don't match.")
            elif len(signup_password) < 6:
                st.error("Password must be at least 6 characters.")
            else:
                try:
                    res = supabase.auth.sign_up({
                        "email": signup_email,
                        "password": signup_password,
                    })
                    if res.user and res.session:
                        st.session_state.user = res.user
                        st.session_state.access_token = res.session.access_token
                        st.session_state.refresh_token = res.session.refresh_token
                        st.session_state.tier = "trial"
                        try:
                            # `email` is written because the Stripe webhook matches
                            # profiles on it. Without it, a sailor can pay and the
                            # webhook updates zero rows while reporting success.
                            supabase.table("profiles").upsert({
                                "id": res.user.id,
                                "email": res.user.email,
                                "tier": "trial",
                                "trial_start": datetime.datetime.now(
                                    datetime.timezone.utc
                                ).isoformat(),
                            }).execute()
                        except Exception:
                            # Most likely `email` is not a column on profiles yet.
                            # Fall back to the row we know the schema accepts, so a
                            # new sailor still gets the trial they were promised.
                            try:
                                supabase.table("profiles").upsert({
                                    "id": res.user.id,
                                    "tier": "trial",
                                    "trial_start": datetime.datetime.now(
                                        datetime.timezone.utc
                                    ).isoformat(),
                                }).execute()
                            except Exception:
                                pass
                        st.rerun()
                    else:
                        st.info("Check your email to confirm your account, then log in.")
                except Exception as e:
                    st.error("Sign up failed: " + str(e))


# ── STRIPE SUCCESS CALLBACK ────────────────────────────────────────────────────
_qp = st.query_params
if _qp.get("stripe_success") == "true" and st.session_state.user:
    _sid = _qp.get("session_id", "")
    if _sid:
        try:
            _cs = stripe.checkout.Session.retrieve(_sid)
            if _cs.payment_status in ("paid", "no_payment_required"):
                _new_tier = _cs.client_reference_id
                if _new_tier in TIER_ORDER:
                    _patch = {"tier": _new_tier}
                    # Record the Stripe customer id on the way past. The webhook
                    # normally does this, but if it is the one that failed and
                    # this fallback is what granted access, an unlinked profile
                    # would never hear about a later cancellation. Whichever
                    # path gets here first, the link gets made.
                    _cust = getattr(_cs, "customer", None)
                    _cust_id = _cust if isinstance(_cust, str) else getattr(_cust, "id", None)
                    if _cust_id:
                        _patch["stripe_customer_id"] = _cust_id
                    supabase.table("profiles").update(_patch).eq(
                        "id", st.session_state.user.id
                    ).execute()
                    st.session_state.tier = _new_tier
                    st.session_state._payment_success = True
        except Exception as _e:
            # Bare `except Exception: pass` here meant a sailor could pay, land
            # back on the app, and have the grant fail silently with nothing
            # anywhere to say why. If it is caught, it is recorded.
            print(f"[stripe_success] could not apply paid tier: {_e!r}")
    st.query_params.clear()
    st.rerun()

# ── REQUIRE LOGIN ─────────────────────────────────────────────────────────────
if not st.session_state.user:
    show_auth_page()
    st.stop()

# Load tier if missing
if not st.session_state.tier:
    st.session_state.tier = get_user_tier(
        st.session_state.user.id, getattr(st.session_state.user, "email", "") or ""
    )

if st.session_state.user and "score_history_loaded" not in st.session_state:
    raw = load_score_history(st.session_state.user.id)
    # load_score_history catches its own errors, but this unpacking used to sit
    # outside any guard — and it runs before a single tab draws. One missing column
    # in score_history and every logged-in sailor got a blank page at the header,
    # with a working FMS calculator sitting behind it. A row we cannot read is worth
    # skipping; it is history, not the thing they came for.
    def _hist_num(value):
        """A stored number we can compare and chart, whatever came back.

        A null `pct` in the database is not harmless: My Profile does
        `"Pass" if pct >= 70`, and None >= 70 raises. Coercing here keeps a bad row
        from taking down the tab that displays it.
        """
        try:
            n = float(value)
        except (TypeError, ValueError):
            return 0
        return 0 if n != n else n  # NaN

    st.session_state.score_history = [
        {"date": str(r.get("date") or "—"), "topic": str(r.get("topic") or "—"),
         "score": _hist_num(r.get("score")), "total": _hist_num(r.get("total")),
         "pct": _hist_num(r.get("pct"))}
        for r in raw
        if isinstance(r, dict)
    ]
    st.session_state.score_history_loaded = True

# ── HEADER ────────────────────────────────────────────────────────────────────
col_title, col_user = st.columns([3, 1])
with col_title:
    st.title("⚓ Score Surge | by Strategic Sailor")
with col_user:
    st.markdown(f"<br>", unsafe_allow_html=True)
    tier_label = TIER_LABELS.get(st.session_state.tier, st.session_state.tier)
    username = st.session_state.user.email.split("@")[0]
    st.caption(f"**{username}**\n{tier_label}")
    if st.button("Log Out", width="stretch"):
        # sign_out is a network call. If it fails we must STILL drop this sailor's
        # session locally — a logout that half-works is worse than one that errors.
        try:
            supabase.auth.sign_out()
        except Exception:
            pass
        # Clear everything, not just the four auth keys.
        #
        # This used to null out user/tier/access_token/refresh_token and leave the rest
        # of session_state standing. Three things went wrong with that:
        #   1. The last exam, the answers picked and the grade stayed on screen after
        #      the next sailor logged in.
        #   2. `score_history_loaded` survived, so load_score_history never re-ran and
        #      the new sailor was shown the previous one's scores out of memory.
        #   3. On a shared machine — a duty office computer, a squadron workstation —
        #      that is one sailor's exam performance shown to another. Not acceptable
        #      for anything with a login on it.
        # The defaults below are re-created by the init block at the top on the rerun.
        st.session_state.clear()
        st.rerun()

if st.session_state.get("_payment_success"):
    st.session_state._payment_success = False
    tier_label = TIER_LABELS.get(st.session_state.tier, st.session_state.tier)
    st.success(f"🎉 Payment successful! You now have **{tier_label}** access. Welcome aboard!")

# Trial banner
if st.session_state.tier == "trial":
    try:
        result = supabase.table("profiles").select("trial_start").eq("id", st.session_state.user.id).single().execute()
        trial_start = datetime.datetime.fromisoformat(result.data["trial_start"].replace("Z", "+00:00"))
        days_left = 3 - (datetime.datetime.now(datetime.timezone.utc) - trial_start).days
        days_left = max(0, days_left)
        st.info(f"🎖️ **Free trial active** — {days_left} day(s) remaining. Enjoy full access!")
    except Exception:
        st.info("🎖️ **Free trial active** — enjoy full access!")

st.markdown("""
Your Navy advancement engine. Calculate your FMS, build your study plan, and advance.
""")

# ── EXAM CYCLE ────────────────────────────────────────────────────────────────
# Every date this app states about the current cycle lives here.
#
# These used to be typed out in four separate places: the countdown metrics, the
# CPO estimate, the planner's exam-date picker, and the body of an AI prompt. The
# prompt copy was the dangerous one — the model repeats what it is given, so once
# the cycle passed the tutor kept confidently quoting Cycle 272 dates to a sailor
# studying for the next cycle. Nothing on screen would have looked wrong.
#
# When the next NAVADMIN drops, edit this block and nothing else.
CYCLE = {
    "number": 272,
    "navadmin": "NAVADMIN 168/26",
    "pmkee":         datetime.date(2026, 7, 31),
    "ildc_e6":       datetime.date(2026, 8, 31),
    "exam_e6":       datetime.date(2026, 9, 3),
    "exam_e5":       datetime.date(2026, 9, 10),
    "ted":           datetime.date(2027, 1, 1),
    "min_tir_e6":    datetime.date(2024, 1, 1),
    "min_tir_e5":    datetime.date(2026, 1, 1),
    "pma_window_e6": "1 September 2023 to 31 August 2026",
    "pma_window_e5": "1 June 2025 to 31 August 2026",
}

# FY27 CPO board exam. `announced` flips to True with a real date once the
# NAVADMIN is published; until then the app calls it an estimate and says so.
CPO_EXAM = {"fy": 27, "est_date": datetime.date(2027, 2, 1), "announced": False}


def _fmt_date(d):
    """'3 September 2026'. Written out rather than strftime('%-d %B %Y'), which is
    not portable off Linux."""
    return f"{d.day} {d:%B %Y}"


def deadline_tile(date, today):
    """(status, date_line) for one cycle date.

    A passed deadline used to render as "✅ Passed". Under the heading "PMK-EE
    Deadline" a green tick reads as "you passed your PMK-EE" — the opposite of what
    it means, which is that the window shut. A sailor who has not completed PMK-EE
    could scan this page and come away reassured. Nothing good is ever a green tick
    here, and the date is always shown: "27 days" builds urgency, "3 September 2026"
    is what a sailor writes on a calendar.
    """
    days_left = (date - today).days
    if days_left < 0:
        status = "⛔ Closed"
    elif days_left == 0:
        status = "🔴 Today"
    elif days_left <= 14:
        status = f"🔴 {days_left} day" + ("" if days_left == 1 else "s")
    elif days_left <= 30:
        status = f"🟡 {days_left} days"
    else:
        status = f"🟢 {days_left} days"
    return status, _fmt_date(date)


def sailor_paygrade():
    """The paygrade this sailor is competing for, or None if they haven't said.

    Read from the FMS calculator, which is where they set it (or where the profile
    sheet reader detected it). Used to show a sailor their own exam date instead of
    a wall of dates for three paygrades they have to filter themselves.
    """
    pg = st.session_state.get("fms_paygrade")
    return pg if pg in PAYGRADES else None


def cycle_expired(today=None):
    """True once the last exam day of this cycle has passed.

    The countdown had no idea a cycle could end. After 10 September 2026 it would
    have drawn four "✅ Passed" tiles under a heading still reading "Cycle 272
    Countdown", and "Official NAVADMIN — Not yet released" would have sat there
    forever. An app that looks abandoned is one a paying sailor stops trusting.
    """
    return (today or datetime.date.today()) > CYCLE["exam_e5"]


def cycle_authority_line(today=None):
    """What the AI prompts may claim to know about cycle dates."""
    if cycle_expired(today):
        return ("You do NOT have the current exam cycle's NAVADMIN. You do not know "
                "this cycle's exam dates or deadlines.")
    return f"You know {CYCLE['navadmin']} (Cycle {CYCLE['number']}) inside and out."


def cycle_facts_block(today=None):
    """The cycle dates as the AI prompts state them.

    Once the cycle is over these are not merely stale, they are wrong for the
    sailor asking now. Rather than hand the model dates it will repeat with
    confidence, tell it plainly that it does not have them.

    `today` is injectable so the expired path can be checked without waiting for
    September.
    """
    if cycle_expired(today):
        return (
            f"EXAM CYCLE DATES:\n"
            f"- Cycle {CYCLE['number']} is complete and the next cycle's NAVADMIN is "
            f"not loaded into this app.\n"
            f"- You do NOT know the current exam dates, deadlines or TIR cutoffs.\n"
            f"- If asked for a date, say you do not have the current cycle's dates and "
            f"send the sailor to MyNavyHR. NEVER state a date from a past cycle as if "
            f"it were current."
        )
    return "\n".join([
        f"CYCLE {CYCLE['number']} FACTS ({CYCLE['navadmin']}):",
        f"- E6 exam date: {_fmt_date(CYCLE['exam_e6'])}",
        f"- E5 exam date: {_fmt_date(CYCLE['exam_e5'])}",
        f"- Terminal Eligibility Date: {_fmt_date(CYCLE['ted'])}",
        f"- PMK-EE deadline: {_fmt_date(CYCLE['pmkee'])}",
        f"- ILDC deadline: {_fmt_date(CYCLE['ildc_e6'])} (E6 only)",
        f"- Min TIR E6: {_fmt_date(CYCLE['min_tir_e6'])}",
        f"- Min TIR E5: {_fmt_date(CYCLE['min_tir_e5'])}",
        f"- PMA window E6: {CYCLE['pma_window_e6']}",
        f"- PMA window E5: {CYCLE['pma_window_e5']}",
        "- EAW is authoritative source, must be finalized in NSIPS",
        "- Most active duty E6 ratings now under BBA, advancement via A2P/CA2P",
    ])


# ── CONSTANTS ─────────────────────────────────────────────────────────────────
# Official FMS computation per the MyNavyHR "E4 Through E7 Final Multiple Score"
# chart (Cycle 104/243 forward), NAVADMIN 312/18, and BUPERSINST 1430.16G.
#
#   PMA points   E4/E5: (PMA x 80) - 256, max 64
#                E6:    (RSCA PMA x 30) - 60, max 114
#                E7:    (RSCA PMA x 30) - 54, max 120
#   SIPG points  Service in paygrade (years) / 5, max 2 (E4/E5) or 3 (E6)
#   Education    2 pts AA/AS, 4 pts BA/BS or above
#   E7 is board-selected: exam + PMA only, no awards/PNA/SIPG/education.
#
# Input ranges are NOT the same across paygrades:
#   E4/E5 PMA is the average of eval promotion-recommendation values, which can
#   only be 4.00 / 3.80 / 3.60 / 3.40 / 2.00 -- so PMA tops out at 4.00, and
#   4.00 x 80 - 256 = 64, exactly the cap. A PMA of 3.20 or below scores zero.
#   E6/E7 RSCA PMA is that eval value PLUS up to 1.80 RSCA bonus points, so it
#   tops out at 5.80, and 5.80 x 30 - 60 = 114 / 5.80 x 30 - 54 = 120.
FMS_RULES = {
    "E4": {"pma_mult": 80, "pma_sub": 256, "pma_max": 64.0, "pma_input_max": 4.00,
           "pma_name": "PMA", "tir_div": 5.0, "tir_max": 2.0, "awards_max": 10.0,
           "edu_max": 4.0, "pna_max": 9.0, "fms_max": 169.0},
    "E5": {"pma_mult": 80, "pma_sub": 256, "pma_max": 64.0, "pma_input_max": 4.00,
           "pma_name": "PMA", "tir_div": 5.0, "tir_max": 2.0, "awards_max": 10.0,
           "edu_max": 4.0, "pna_max": 9.0, "fms_max": 169.0},
    "E6": {"pma_mult": 30, "pma_sub": 60, "pma_max": 114.0, "pma_input_max": 5.80,
           "pma_name": "RSCA PMA", "tir_div": 5.0, "tir_max": 3.0, "awards_max": 12.0,
           "edu_max": 4.0, "pna_max": 9.0, "fms_max": 222.0},
    "E7": {"pma_mult": 30, "pma_sub": 54, "pma_max": 120.0, "pma_input_max": 5.80,
           "pma_name": "RSCA PMA", "tir_div": 5.0, "tir_max": 0.0, "awards_max": 0.0,
           "edu_max": 0.0, "pna_max": 0.0, "fms_max": 200.0},
}
EXAM_MAX = 80.0


def pma_points(paygrade: str, pma_value: float) -> float:
    """Convert a raw PMA / RSCA PMA into FMS points for the given paygrade."""
    r = FMS_RULES[paygrade]
    raw = (pma_value * r["pma_mult"]) - r["pma_sub"]
    return round(min(max(raw, 0.0), r["pma_max"]), 2)


def tir_points(paygrade: str, years_in_paygrade: float) -> float:
    """Service in paygrade (years) / 5, capped by paygrade, never below zero."""
    r = FMS_RULES[paygrade]
    raw = years_in_paygrade / r["tir_div"]
    return round(min(max(raw, 0.0), r["tir_max"]), 2)


def compute_fms(paygrade, exam_score, pma, years_in_rate, awards, education, pna):
    """Return (total_fms, ordered breakdown dict) using the official chart.

    Every component is clamped at BOTH ends. These used to cap the top only, which
    the widgets hid because they all carry min_value=0.0 — but the function is
    reachable from anywhere, and a negative slipping through subtracts from a score
    the sailor is trusting. No FMS component is ever worth less than zero.
    """
    r = FMS_RULES[paygrade]

    def band(value, hi):
        return round(min(max(value, 0.0), hi), 2)

    parts = {
        "Exam Standard Score": band(exam_score, EXAM_MAX),
        "PMA Points":          pma_points(paygrade, pma),
        "Time in Rate":        tir_points(paygrade, years_in_rate),
        "Awards":              band(awards, r["awards_max"]),
        "Education":           band(education, r["edu_max"]),
        "PNA Points":          band(pna, r["pna_max"]),
    }
    return round(sum(parts.values()), 2), parts

LABEL_PATTERNS = {
    "exam_score": [
        r"exam\s*standard\s*score",
        r"standard\s*score",
        r"exam\s*score",
        r"written\s*exam",
    ],
    "pma": [
        r"performance\s*mark\s*average",
        r"\bpma\b",
        r"eval\s*avg",
        r"eval\s*average",
    ],
    "tir": [
        # Official profile sheets label this row "Service in Paygrade" (SIPG).
        # It is the same figure the FMS chart calls Time in Rate.
        r"service\s*in\s*pay\s*grade",
        r"\bsipg\b",
        r"time\s*in\s*rate",
        r"\btir\b",
        r"time-in-rate",
    ],
    "awards": [
        r"awards?\s*points?",
        r"\bawards?\b",
    ],
    "education": [
        r"education\s*points?",
        r"\beducation\b",
        r"\bedu\b",
    ],
    "pna": [
        r"passed\s*not\s*advanced",
        r"\bpna\b",
        r"pna\s*points?",
    ],
}

DEFAULT_VALUES = {
    "exam_score": 42.0,
    "pma": 3.8,
    "tir": 3.0,
    "awards": 2.0,
    "education": 0.0,
    "pna": 0.0,
}

# Widest plausible range for each field across every paygrade. A number scraped
# from the sheet that falls outside its field's range is not that field's value —
# it is a cycle number, a date, a question count or a column header. Filtering on
# these ranges is what stops "PNA POINTS EARNED IN CYCLES 268 / 270 / 271" from
# handing 268 to a widget whose maximum is 9.
FIELD_RANGES = {
    "exam_score": (0.0, 80.0),
    "pma":        (0.0, 5.80),
    "tir":        (0.0, 30.0),
    "awards":     (0.0, 12.0),
    "education":  (0.0, 4.0),
    "pna":        (0.0, 9.0),
}


# A number as a profile sheet prints it. The old pattern accepted a period and at
# most two decimal places, which quietly cost sailors points: "4,06" — what OCR
# returns when it reads a period as a comma, routine on a photographed sheet — was
# consumed as the integer 4, and "4.060" the same way. The field was still reported
# as successfully read, so the sailor saw "Read all six fields" above a PMA that had
# lost 1.8 FMS points.
#
# The comma form is deliberately limited to exactly two decimals, with (?!\d) to
# stop it eating a third. That is the only way "SEP 30,2025" stays a date instead of
# becoming 30.20 in the Service in Paygrade field. The period form is open-ended
# because "4.060" is unambiguous.
_NUMBER_TOKEN = re.compile(r"\b\d{1,3}(?:\.\d+|,\d{2}(?!\d))?\b")


# For each field, a regex matching every OTHER field's labels. Used to stop the
# search before the next row begins, so a field whose value is genuinely missing
# does not quietly borrow the number belonging to the row underneath it.
_OTHER_LABELS = {
    field: re.compile("|".join(p for f, ps in LABEL_PATTERNS.items() if f != field for p in ps))
    for field in LABEL_PATTERNS
}


# ── OCR HELPERS ───────────────────────────────────────────────────────────────
def extract_number_near_label(text, patterns, valid_range=None, field=None, window=110):
    """Find a label, then the most plausible number belonging to it.

    Three guards, because profile sheets are dense with numbers that are not scores:
      1. Stop at the next FMS label, so we never read the next row's value.
      2. Ignore anything outside the field's valid range (cycle numbers, question counts).
      3. Prefer a decimal (3.50) over a bare integer (01 from a date) — every real
         FMS figure on a profile sheet is printed to two decimal places.
    """
    text_lower = text.lower()
    stop_re = _OTHER_LABELS.get(field)
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if not match:
            continue
        start = match.end()
        segment = text[start: start + window]
        if stop_re:
            nxt = stop_re.search(text_lower[start: start + window])
            if nxt:
                segment = segment[: nxt.start()]

        decimals, integers = [], []
        for num_match in _NUMBER_TOKEN.finditer(segment):
            token = num_match.group(0)
            value = round(float(token.replace(",", ".")), 2)
            if valid_range is not None:
                lo, hi = valid_range
                if not (lo <= value <= hi):
                    continue
            (decimals if ("." in token or "," in token) else integers).append(value)
        if decimals:
            return decimals[0]
        if integers:
            return integers[0]
    return None


def parse_ocr_text(raw_text):
    """Return (values, missing_fields). Every value is guaranteed in-range."""
    results = {}
    missing = []
    for field, patterns in LABEL_PATTERNS.items():
        rng = FIELD_RANGES.get(field)
        value = extract_number_near_label(raw_text, patterns, valid_range=rng, field=field)
        if value is not None:
            results[field] = value
        else:
            results[field] = DEFAULT_VALUES[field]
            missing.append(field)
    return results, missing


# How a paygrade is actually written on a sheet. Navy systems print E6, E-6 and
# E06 interchangeably, and a sheet often names the rate before the paygrade:
# "ADVANCEMENT TO PSC (E7)". Matching only a bare "E6" meant detection quietly
# failed on all of those and handed the choice back to the sailor — which is the
# one decision that must not be guessed.
#
# The leading \b is load-bearing. Without it, the optional separator lets the "e"
# in an ordinary word pair up with a nearby digit: "ADVANCEMENT TO THE 5TH" would
# read as E5.
_PG_TOKEN = r"\be[\s\-\.]?0?([4-7])\b"
# An optional rate abbreviation and/or opening bracket between the wording and the
# paygrade: "TO PSC (E7)", "TO PS1 E6".
_PG_LEAD = r"(?:[a-z]{2,4}\d?\s*)?\(?\s*"


# A rate is its own paygrade: the numeral is the grade, C is Chief. Confirmed by
# Shawn (PS1, ret.) and by arithmetic on two real sheets — a GM1 sheet only
# reconciles under E6 rules, a BM2 sheet only under E5.
RATE_SUFFIX_TO_PAYGRADE = {
    "3": "E4", "2": "E5", "1": "E6",
    "CM": "E9", "CS": "E8", "C": "E7",   # longest first; C alone is Chief
}
# PS2, GM1, HM3, PSC, PSCS, PSCM. Two to four letters then the grade marker.
_RATE_TOKEN = r"[A-Z]{2,4}(?:CM|CS|C|3|2|1)"
# Lowest to highest. Used to insist a PRESENT/EXAM rate pair really is one grade
# apart, in that order.
RATE_GRADE_ORDER = ["3", "2", "1", "C", "CS", "CM"]
# Form abbreviations that happen to look like rates once OCR has had a go. "UIC"
# is "UI" + "C" and appears twice side by side in a real sheet's header row.
_NOT_RATES = {"UIC", "NEC", "SSN", "EAOS", "PRD", "USN", "USNR"}


def rate_to_paygrade(rate):
    """'GM1' -> 'E6'. None if it is not a rate we recognise."""
    if not rate:
        return None
    r = str(rate).strip().upper()
    if r in _NOT_RATES or not re.fullmatch(_RATE_TOKEN, r):
        return None
    for suffix in ("CM", "CS", "C", "3", "2", "1"):
        if r.endswith(suffix):
            return RATE_SUFFIX_TO_PAYGRADE[suffix]
    return None


def extract_exam_rate(raw_text):
    """The rate a sailor is testing INTO, read off a real profile sheet.

    Real sheets do not say "paygrade competing for" anywhere. They carry a header
    row — PRESENT RATE | EXAM RATE | GROUP | BRANCH CLASS | CYCLE ... — with the
    values in a grid underneath. All the wording detection was built against mock
    sheets that were themselves generated, so it was matching language no real
    document uses.

    Two ways in, both taken from real sheets:
      1. the EXAM RATE label with the rate near it
      2. the two rates side by side in the value row, same rating, e.g. "GM2 GM1"
         or "PS3 PS2" — the second one is the exam rate

    Returns (rate, paygrade), or (None, None).
    """
    if not raw_text:
        return None, None
    t = " ".join(raw_text.split())

    # 1. Labelled. OCR loses the column alignment, so allow some noise between the
    #    label and the value, but not so much that PRESENT RATE's value wins.
    m = re.search(rf"exam\s*rate\W{{0,4}}\s*({_RATE_TOKEN})\b", t, re.IGNORECASE)
    if m:
        pg = rate_to_paygrade(m.group(1))
        if pg:
            return m.group(1).upper(), pg

    # 2. Adjacent pair in the value row: same rating twice, present then exam.
    #    "PS3 PS2" is unambiguous in a way a lone rate is not — a sheet mentions
    #    the sailor's present rate in several places, but only once next to the
    #    rate they are testing into.
    #
    #    The grades must be CONSECUTIVE, present then exam. Without that, "UIC UIC"
    #    in the header row of a real sheet reads as "UI" + "C" twice and hands back
    #    a confident E7 for an E5 candidate. A sailor competes for the next grade
    #    up, never their own and never two at once, so this is free accuracy.
    for m in re.finditer(rf"\b([A-Z]{{2,4}}?)(CM|CS|C|3|2|1)\s+\1(CM|CS|C|3|2|1)\b", t):
        present, exam = m.group(2), m.group(3)
        if (present in RATE_GRADE_ORDER and exam in RATE_GRADE_ORDER
                and RATE_GRADE_ORDER.index(exam) == RATE_GRADE_ORDER.index(present) + 1):
            exam_rate = m.group(1) + exam
            pg = rate_to_paygrade(exam_rate)
            if pg:
                return exam_rate, pg

    return None, None


def extract_paygrade(raw_text):
    """Read the paygrade the sailor is competing for off the profile sheet.

    Matters more than it looks: the PMA formula, every point cap and the FMS
    maximum all change by paygrade. Scoring an E6 sheet with E5 rules produces a
    confident, wrong number.

    The EXAM RATE column is checked before the bare "Paygrade:" label, because a
    lone paygrade field on a sheet is usually the sailor's CURRENT one — and
    scoring a sailor at the grade they already hold is precisely the mistake this
    whole function exists to prevent.
    """
    if not raw_text:
        return None
    t = " ".join(raw_text.split()).lower()
    # Explicit wording first, so a stray "E5" elsewhere on the sheet loses.
    for pattern in (
        rf"paygrade\s*(?:you\s*are\s*)?competing\s*for\s*[:\-]?\s*\(?\s*{_PG_TOKEN}",
        rf"competing\s*for\s*(?:paygrade\s*)?[:\-]?\s*{_PG_LEAD}{_PG_TOKEN}",
        rf"advancement\s*to\s*(?:paygrade\s*)?[:\-]?\s*{_PG_LEAD}{_PG_TOKEN}",
        rf"candidate\s*for\s*[:\-]?\s*{_PG_LEAD}{_PG_TOKEN}",
    ):
        m = re.search(pattern, t)
        if m:
            return "E" + m.group(1)

    # Then how real sheets actually say it.
    _, pg = extract_exam_rate(raw_text)
    if pg:
        return pg

    # Last: a bare "Paygrade: E6" with nothing saying which paygrade it means.
    m = re.search(rf"paygrade\s*[:\-]\s*\(?\s*{_PG_TOKEN}", t)
    if m:
        return "E" + m.group(1)
    return None


def safe_value(raw, lo, hi, fallback):
    """Clamp a parsed value into a number_input's bounds.

    Streamlit raises if `value` sits outside min_value/max_value, so nothing
    reaches a widget unclamped — belt and braces on top of FIELD_RANGES.
    """
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return fallback
    if val != val:  # NaN — comparisons silently pass and Streamlit then chokes
        return fallback
    return min(max(val, lo), hi)


FIELD_TITLES = {
    "exam_score": "Exam Standard Score",
    "pma": "PMA / RSCA PMA",
    "tir": "Service in Paygrade (SIPG)",
    "awards": "Awards Points",
    "education": "Education Points",
    "pna": "PNA Points",
}


def over_cap_fields(data, paygrade, paygrade_chosen):
    """Values read off the sheet that exceed the selected paygrade's maximum.

    safe_value quietly pulls these inside the widget's bounds, which is right for
    not crashing the page and wrong for telling the truth: an E6 sheet scored as
    E5 has its PMA trimmed from 4.06 to 4.00 and produces a confident, too-high
    FMS. Anything this returns should be shown to the sailor, because the usual
    cause is that the wrong paygrade is selected.

    Returns a list of (field, value_found, cap).
    """
    if not paygrade_chosen or paygrade not in FMS_RULES:
        return []
    rules = FMS_RULES[paygrade]
    bounds = {"exam_score": EXAM_MAX, "pma": rules["pma_input_max"]}
    # Fields a paygrade does not score at all are not "over cap" — E7 ignores
    # awards, PNA and SIPG entirely, so a sheet listing them is not a conflict.
    if paygrade != "E7":
        bounds["awards"] = rules["awards_max"]
        bounds["pna"] = rules["pna_max"]
    out = []
    for field, cap in bounds.items():
        try:
            val = float(data[field])
        except (TypeError, ValueError, KeyError):
            continue
        if val != val:  # NaN
            continue
        if val > cap:
            out.append((field, val, cap))
    return out


def paygrade_conflicts(raw_text, paygrade, paygrade_chosen):
    """Where the sheet's own words disagree with the paygrade that was picked.

    over_cap_fields() catches the wrong paygrade only when a number breaks a cap,
    which means it only fires above PMA 4.00. That leaves a hole. E5 PMA points are
    (pma x 80) - 256 and E6 is (pma x 30) - 60; the two lines cross at 3.92, so
    between 3.92 and 4.00 an E6 sheet scored under E5 rules produces a HIGHER FMS
    than the truth — a 4.00 RSCA PMA reads 140.7 instead of 136.7 — and no cap is
    broken, so nothing is shown. A flattering wrong number is the one nobody
    questions.

    Text catches what the caps cannot, across the whole range:
      - the sheet names a paygrade and it is not the one selected
      - the sheet says RSCA, which only E6 and E7 sheets do, while E5 is selected

    Returns a list of plain-English strings, strongest signal first.
    """
    if not paygrade_chosen or not raw_text:
        return []
    out = []
    t = " ".join(raw_text.split()).lower()

    stated = extract_paygrade(raw_text)
    if stated and stated != paygrade:
        out.append(
            f"**Your sheet says you are competing for {stated}, but {paygrade} is "
            f"selected above.** The PMA formula and every point cap are different for "
            f"each paygrade, so one of these is producing the wrong number. The sheet "
            f"is usually right."
        )

    # RSCA PMA is the E6/E7 figure. An E5 candidate's sheet carries a plain eval
    # PMA and never mentions RSCA, so this wording under E5 is a real disagreement.
    if "rsca" in t and paygrade == "E5":
        out.append(
            "**Your sheet mentions RSCA PMA, which only appears on E6 and E7 sheets.** "
            "E5 uses a plain performance mark average capped at 4.00. If this is an E6 "
            "sheet being scored as E5, your FMS can come out several points too HIGH "
            "without anything else looking wrong."
        )

    return out


# Labels whose value identifies a person. A profile sheet carries the sailor's
# full name and the last four of their DoD ID within the first ~250 characters,
# and the ESO of record is a second named person who never consented to anything.
_PII_LABELS = re.compile(
    r"(name\s*\(last|^\s*name\s*:|member\s*name|candidate\s*name|"
    r"dod\s*id|\bdodid\b|\bssn\b|social\s*security|eso\s*of\s*record)",
    re.IGNORECASE,
)


def redact_pii(raw_text):
    """Mask identifying values in sheet text before it is put on screen.

    The "what was read from your sheet" panel prints the raw extraction so a
    sailor can see why a field was missed. On a real sheet that text opens with
    NAME (LAST, FIRST MI): RIVERA, MARCUS T. and DOD ID (LAST 4): 4417 — printed
    back to the screen every upload, for a debugging aid.

    Display only. Parsing still runs on the original text, because the paygrade
    and the six FMS values have to come off the sheet exactly as written.
    """
    if not raw_text:
        return ""
    lines = raw_text.split("\n")
    out = []
    mask_next = False
    for line in lines:
        stripped = line.strip()
        if mask_next and stripped:
            out.append("    [redacted]")
            mask_next = False
            continue
        m = _PII_LABELS.search(line)
        if m:
            label_end = line.find(":", m.start())
            if label_end == -1:
                out.append("[redacted]")
                continue
            # "NAME (LAST, FIRST MI): RIVERA" -> mask on the same line.
            # "NAME (LAST, FIRST MI):" alone -> the value is on the next line.
            if line[label_end + 1:].strip():
                out.append(line[: label_end + 1] + " [redacted]")
            else:
                out.append(line)
                mask_next = True
            continue
        out.append(line)
    return "\n".join(out)


# A phone photo of a profile sheet OCRs badly below about 200 DPI and gets slow
# above it without reading any better.
OCR_DPI = 200

# The FMS table is small text on a shaded background, which is close to the worst
# case for OCR at native size. Enlarging to roughly this width before reading is
# the single biggest accuracy win available — measured on two real sheets, the
# figures found went from 0/7 and 1/6 to 6/7 and 5/6. Past about 2x the sharpening
# starts inventing edges and accuracy falls again, so this is a target, not a
# multiplier: a 4000px phone photo is already big enough and is left alone.
OCR_TARGET_WIDTH = 2200
# --psm 6 treats the page as one uniform block, which suits a form. --psm 11 finds
# sparse text and picks up figures the first pass drops, at roughly double the time,
# so it is only used when the first pass comes back thin.
OCR_PRIMARY_CONFIG = "--psm 6"
OCR_FALLBACK_CONFIG = "--psm 11"


def prepare_for_ocr(image):
    """Grayscale, enlarge to a readable size, lift contrast, sharpen."""
    img = image.convert("L")
    scale = OCR_TARGET_WIDTH / max(img.width, 1)
    if scale > 1.05:
        scale = min(scale, 4.0)
        img = img.resize((int(img.width * scale), int(img.height * scale)),
                         Image.LANCZOS)
    return ImageOps.autocontrast(img).filter(ImageFilter.SHARPEN)


def ocr_text(image):
    """Read a sheet image, trying harder only if the first pass looks thin.

    The second pass roughly doubles the wait, which matters on a phone, so it is
    spent only when the first pass has not found a paygrade or has missed more
    than a couple of the six figures.
    """
    prepared = prepare_for_ocr(image)
    text = pytesseract.image_to_string(prepared, config=OCR_PRIMARY_CONFIG)

    def thin(t):
        try:
            return extract_paygrade(t) is None or len(parse_ocr_text(t)[1]) > 2
        except Exception:
            return True

    if thin(text):
        text += "\n" + pytesseract.image_to_string(prepared,
                                                   config=OCR_FALLBACK_CONFIG)

    # Enlarging and sharpening rescues the small figures in the FMS table, but it
    # can blur larger print that was already legible — on one real sheet it gained
    # every figure and lost the rate pair. When the paygrade is still unknown the
    # untouched image is worth one more look, because the paygrade is the one field
    # where being wrong is worse than being slow.
    if extract_paygrade(text) is None:
        try:
            text += "\n" + pytesseract.image_to_string(image)
        except Exception:
            pass
    return text


def ocr_engine_ready():
    """Is the tesseract BINARY actually here?

    OCR_IMAGE_AVAILABLE only says the Python packages imported. pytesseract is a
    wrapper around a separate system binary that requirements.txt cannot install —
    on Streamlit Cloud that needs packages.txt. Without it every image upload hit
    TesseractNotFoundError, which nothing caught, so the sailor got a raw Python
    traceback where the page should have been.
    """
    if not OCR_IMAGE_AVAILABLE:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def extract_text_from_upload(uploaded_file):
    """Text from a profile sheet, whatever form it arrives in.

    Three shapes, because sailors send all three:
      1. a PDF with a real text layer      -> read it directly
      2. a PDF that is a photo or a scan   -> render each page and OCR it
      3. a photo or screenshot             -> OCR it

    Shape 2 is the one that used to fail. The PDF branch only ever read the text
    layer, so a sheet photographed on a phone and saved as a PDF — which is how a
    real one arrives — came back empty and the sailor was told the document could
    not be read. There is nothing wrong with the document.
    """
    raw_text = ""
    suffix = os.path.splitext(uploaded_file.name)[1]
    # Browsers are inconsistent about the MIME type they attach, so the filename
    # gets a vote too.
    is_pdf = (uploaded_file.type == "application/pdf"
              or suffix.lower() == ".pdf")

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.read())
        tmp_path = tmp.name

    try:
        if is_pdf:
            if not OCR_PDF_AVAILABLE:
                st.error("Cannot read PDFs on this server (PyMuPDF is missing). "
                         "Upload a photo instead, or enter your scores by hand below.")
                return None
            doc = fitz.open(tmp_path)
            for page in doc:
                raw_text += page.get_text()

            # No text layer means it is a picture of a sheet, not a document.
            if not raw_text.strip():
                if not ocr_engine_ready():
                    st.error(
                        "**This PDF is a photo or a scan, not a text document.** "
                        "Reading pictures is not available on this server right now, "
                        "so your scores could not be read automatically. Enter them "
                        "by hand below — the calculator works exactly the same."
                    )
                    return None
                with st.spinner("This looks like a photo — reading it may take a moment..."):
                    for page in doc:
                        pix = page.get_pixmap(dpi=OCR_DPI)
                        raw_text += ocr_text(
                            Image.open(io.BytesIO(pix.tobytes("png")))
                        )
        else:
            if not ocr_engine_ready():
                st.error(
                    "**Reading photos is not available on this server right now.** "
                    "Upload your sheet as a PDF if you have one, or enter your scores "
                    "by hand below — the calculator works exactly the same."
                )
                return None
            with st.spinner("Reading your photo..."):
                raw_text = ocr_text(Image.open(tmp_path))

    except Exception as e:
        # Whatever went wrong, a sailor with a working calculator in front of them
        # should not be looking at a stack trace.
        st.error(
            "**Could not read that file.** Enter your scores by hand below — the "
            "calculator works exactly the same. "
            f"\n\nTechnical detail, if you are reporting this: `{type(e).__name__}`"
        )
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return raw_text


# ── PS TOPICS ─────────────────────────────────────────────────────────────────
PS_TOPICS = {
    "E6 - Customer Service Management & Processing": {
        "subtopics": ["Correspondence", "DEERS & RAPIDS Management", "Electronic Service Record", "Leave"],
        "bib": "BUPERSINST 1750.10E, MILPERSMAN 1050 series, NSIPS"
    },
    "E6 - Disbursing Operations": {
        "subtopics": ["Fiscal", "Navy Cash"],
        "bib": "DOD 7000.14-R Vol 5, NAVSUP P-727"
    },
    "E6 - Education Services": {
        "subtopics": ["Advancement", "Programs"],
        "bib": "BUPERSINST 1430.16G, NAVPERS 18068F"
    },
    "E6 - Manning & Manpower Management": {
        "subtopics": ["Administration & Management", "Roles & Responsibilities"],
        "bib": "OPNAVINST 1300.21, BUPERSINST 1080.54B, MNA Users Guide"
    },
    "E6 - MILPAY Processing": {
        "subtopics": ["Allotments", "Indebtedness", "Legal", "Pay Processing", "Report Management"],
        "bib": "DOD 7000.14-R Vol 7A, Navy DJMS Procedures Training Guide"
    },
    "E6 - Receipts Management & Processing": {
        "subtopics": ["Gains", "Procedures", "Required Documentation & Forms"],
        "bib": "NSIPS, MILPERSMAN 1300 series"
    },
    "E6 - Reenlistment & Extension Processing": {
        "subtopics": ["Administration & Procedures", "Eligibility"],
        "bib": "OPNAVINST 1160.8B, MILPERSMAN 1160 series"
    },
    "E6 - Reserve Pay, Management & Processing": {
        "subtopics": ["Electronic Drill Management", "Entitlements", "Gains",
                      "Mobilization & Demobilization", "Separations & Transfers"],
        "bib": "RESPERS M-1001.5, BUPERSINST 1001.39F"
    },
    "E6 - Separations & Retirement Processing": {
        "subtopics": ["Entitlements & Audit", "Required Documentation & Forms", "Strength Loss"],
        "bib": "BUPERSINST 1900.8F, MILPERSMAN 1910 series, 1830 series"
    },
    "E6 - Transfers Management & Processing": {
        "subtopics": ["Entitlements", "Loss", "Required Documentation & Forms"],
        "bib": "JTR Chapter 5, MILPERSMAN 1300 series"
    },
    "E6 - Travel & Transportation Processing": {
        "subtopics": ["Computations", "Requirements", "Travel Policy & Procedures"],
        "bib": "JTR Chapters 1, 2, 5, DOD 7000.14-R Vol 9"
    },
    "E5 - Customer Service Management & Processing": {
        "subtopics": ["Correspondence", "DEERS & RAPIDS Management", "Electronic Service Record", "Leave"],
        "bib": "BUPERSINST 1750.10E Ch 4, MILPERSMAN 1050 series"
    },
    "E5 - MILPAY Processing": {
        "subtopics": ["Allotments", "Pay Processing", "Special Pays"],
        "bib": "DOD 7000.14-R Vol 7A, Navy DJMS Procedures Training Guide"
    },
    "E5 - Separations & Retirement Processing": {
        "subtopics": ["Entitlements", "Required Documentation", "DD214"],
        "bib": "BUPERSINST 1900.8F, MILPERSMAN 1910 series"
    },
    "E5 - Transfers Management & Processing": {
        "subtopics": ["Entitlements", "Loss", "Required Documentation & Forms"],
        "bib": "JTR Chapters 2, 3, 5, MILPERSMAN 1300 series"
    },
    "E5 - Travel & Transportation Processing": {
        "subtopics": ["Computations", "Requirements", "Travel Policy & Procedures"],
        "bib": "JTR, DOD 7000.14-R Vol 9"
    },
    "E5 - Military Awards": {
        "subtopics": ["Award Types", "Eligibility", "Processing"],
        "bib": "SECNAVINST 1650.1J"
    },
}

# ── RATE / TOPIC HELPERS ──────────────────────────────────────────────────────
RATINGS = ["PS", "YN", "IT", "BM", "MM", "EM", "HM", "MA"]
PAYGRADES = ["E5", "E6", "E7"]  # No E4 NWAE — advancement to E4 is not exam-based.

# Placeholder shown until the sailor picks a paygrade. Not a valid selection.
PG_PROMPT = "— Select your paygrade —"

# Widget bounds used before a paygrade is chosen: the widest value across every
# paygrade, so nothing read off the sheet is clamped away before we know which
# rules apply. Never used to score — the form is unsubmittable until a real
# paygrade is selected.
UNSET_RULES = {
    "pma_mult": 30, "pma_sub": 60, "pma_max": 120.0,
    "pma_input_max": max(FMS_RULES[p]["pma_input_max"] for p in PAYGRADES),
    "pma_name": "PMA / RSCA PMA",
    "tir_div": 5.0,
    "tir_max": max(FMS_RULES[p]["tir_max"] for p in PAYGRADES),
    "awards_max": max(FMS_RULES[p]["awards_max"] for p in PAYGRADES),
    "edu_max": 4.0, "pna_max": 9.0,
    "fms_max": max(FMS_RULES[p]["fms_max"] for p in PAYGRADES),
}


def _split_ps_topics():
    """Reshape PS_TOPICS from {'E6 - Topic': {...}} into {'E6': {'Topic': {...}}}."""
    out = {}
    for key, val in PS_TOPICS.items():
        pg, _, name = key.partition(" - ")
        if not name:
            pg, name = "E6", key
        out.setdefault(pg.strip(), {})[name.strip()] = val
    return out


PS_TOPICS_BY_PAYGRADE = _split_ps_topics()

# Last-resort topics if the API call fails. Keeps the tab usable instead of dead.
GENERIC_TOPICS = {
    "Advancement & Evaluations": {
        "subtopics": ["Advancement Process", "Eval Cycle", "Career Milestones"],
        "bib": "BUPERSINST 1430.16G, BUPERSINST 1610.10F",
    },
    "Administration & Correspondence": {
        "subtopics": ["Naval Correspondence", "Records Management", "Reports"],
        "bib": "SECNAV M-5216.5, SECNAV M-5210.1",
    },
    "Safety & Damage Control": {
        "subtopics": ["ORM", "Firefighting", "Watchstanding"],
        "bib": "OPNAVINST 3500.39D, NSTM 555",
    },
    "Leadership & Military Requirements": {
        "subtopics": ["Chain of Command", "UCMJ Basics", "Sailor Development"],
        "bib": "NAVEDTRA 14325, JAGINST 5800.7G",
    },
}


@st.cache_data(show_spinner=False, ttl=86400)
def get_rate_topics(rating: str, paygrade: str) -> dict:
    """Curated topics for PS. AI-generated + cached for every other rate.

    Cached for 24h per (rating, paygrade), so each combo costs at most one API
    call per day per running app instance.
    """
    if rating == "PS" and paygrade in PS_TOPICS_BY_PAYGRADE:
        return PS_TOPICS_BY_PAYGRADE[paygrade]

    prompt = f"""List the major exam topic areas on the Navy-wide advancement exam (NWAE)
for a {rating} advancing to {paygrade}, based on the official NWAE bibliography for that rate.

Return ONLY valid JSON. No markdown fences, no commentary. Exact shape:
{{
  "Topic Name": {{
    "subtopics": ["Subtopic 1", "Subtopic 2", "Subtopic 3"],
    "bib": "Governing instructions and manuals, comma separated"
  }}
}}

Rules:
- 8 to 14 topics.
- 2 to 5 subtopics each.
- "bib" must cite real Navy instructions/manuals (e.g. NAVEDTRA, OPNAVINST,
  MILPERSMAN, NAVSUP, SECNAVINST) that actually govern that topic for {rating}.
- Topic names must NOT contain the paygrade."""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw).strip()
        data = json.loads(raw)

        clean = {}
        for name, val in data.items():
            subs = val.get("subtopics") or []
            if isinstance(subs, list) and subs:
                clean[str(name)] = {
                    "subtopics": [str(s) for s in subs],
                    "bib": str(val.get("bib", "")) or "Consult your rate's NWAE bibliography",
                }
        return clean if clean else GENERIC_TOPICS
    except Exception:
        return GENERIC_TOPICS


# ── TABS ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "🏠 FMS Calculator",
    "📋 Advancement Info",
    "📖 Study Guide",
    "🎓 AI Tutor",
    "🎯 Mock Exam",
    "📅 Advancement Planner",
    "👤 My Profile",
])

# ── TAB 1: FMS CALCULATOR ─────────────────────────────────────────────────────
with tab1:
    st.subheader("📤 Upload Profile Sheet (Optional)")
    uploaded_file = st.file_uploader(
        "Upload your Navy Profile Sheet — image or PDF",
        type=["png", "jpg", "jpeg", "pdf"],
        help="The app will try to read your scores automatically. You can always edit them below.",
    )

    extracted_data = DEFAULT_VALUES.copy()
    detected_paygrade = None
    # Held past the upload block: the paygrade cross-check below needs the sheet's
    # own wording, and it runs after the dropdown, not before it.
    raw_text = ""

    if uploaded_file is not None:
        with st.spinner("Reading your document..."):
            _extracted = extract_text_from_upload(uploaded_file)
        # None means extract_text_from_upload has already said what went wrong and
        # what to do about it. A second, vaguer error underneath helps nobody.
        _already_explained = _extracted is None
        raw_text = _extracted or ""
        if raw_text.strip():
            extracted_data, missing_fields = parse_ocr_text(raw_text)
            detected_paygrade = extract_paygrade(raw_text)

            # Apply the sheet's paygrade once per uploaded file. Keyed on the file
            # itself so a later manual change to the dropdown is not overwritten on
            # every rerun — the sailor always gets the last word.
            file_id = f"{uploaded_file.name}:{uploaded_file.size}"
            if detected_paygrade in PAYGRADES and st.session_state.get("_pg_src") != file_id:
                st.session_state["fms_paygrade"] = detected_paygrade
                st.session_state["_pg_src"] = file_id

            FIELD_LABELS = FIELD_TITLES
            found = [FIELD_LABELS[f] for f in extracted_data if f not in missing_fields]

            if not missing_fields:
                st.success("✅ Read all six fields from your profile sheet.")
            else:
                st.warning(
                    "⚠️ Read " + str(len(found)) + " of 6 fields. **Could not find: "
                    + ", ".join(FIELD_LABELS[f] for f in missing_fields) + "** — these are "
                    "showing typical placeholder values, NOT your numbers. Enter them by hand "
                    "below or your FMS will be wrong."
                )

            if detected_paygrade in PAYGRADES:
                _exam_rate, _ = extract_exam_rate(raw_text)
                _how = (f"your exam rate is **{_exam_rate}**" if _exam_rate
                        else "your sheet states it")
                st.info(
                    f"📌 Detected **{detected_paygrade}** — {_how}. The paygrade below is "
                    f"set to match. The PMA formula and every point cap change by paygrade, "
                    f"so this has to be right. Change it if it's wrong."
                )
            elif detected_paygrade:
                # E4 advancement is not exam-based; E8 and E9 are board-selected and
                # Score Surge does not score them. Saying "no longer has an exam" was
                # right for E4 and wrong for the Chief grades.
                st.warning(
                    f"Your sheet points to **{detected_paygrade}**, which Score Surge "
                    "does not score — the calculator covers E5, E6 and E7. Pick your "
                    "paygrade manually below."
                )
            else:
                st.warning(
                    "Could not tell which paygrade you're competing for. **Check the paygrade "
                    "dropdown below before trusting your FMS** — the formula and point caps are "
                    "different for E5, E6 and E7."
                )

            st.caption(
                "Always check these against your sheet before trusting the FMS. Your profile "
                "sheet lists **Service in Paygrade (SIPG)** — that is the same figure the FMS "
                "chart calls Time in Rate, and it is what goes in the SIPG field below."
            )

            with st.expander("🔍 What was read from your sheet"):
                for f, lbl in FIELD_LABELS.items():
                    mark = "❌ not found — placeholder" if f in missing_fields else "✅ read"
                    st.write(f"**{lbl}:** {extracted_data[f]}  ·  {mark}")
                st.divider()
                st.caption(
                    "Text extracted from your document — your name and DoD ID are "
                    "masked here. Score Surge does not store your sheet; it is read "
                    "in memory and the temporary file is deleted straight after."
                )
                st.text(redact_pii(raw_text)[:2000])
        elif not _already_explained:
            st.error(
                "**Could not read any text from that file.** If it is a photo, try "
                "again in better light with the whole sheet flat in frame — or just "
                "enter your scores by hand below."
            )

    st.subheader("📋 Enter or Edit Your Scores")

    # Outside the form on purpose: changing paygrade must immediately re-scale the
    # PMA field and hide the fields that paygrade does not use.
    #
    # There is deliberately NO default paygrade. Defaulting to E5 meant an E6 sheet
    # whose paygrade line could not be read was scored with E5 rules — and because
    # the E5 PMA cap (4.00) is lower than a real E6 PMA, the value was silently
    # clamped and the resulting FMS came out HIGHER than the truth. A flattering
    # wrong number is one nobody questions. Make the sailor choose instead.
    if "fms_paygrade" not in st.session_state:
        st.session_state["fms_paygrade"] = PG_PROMPT
    paygrade = st.selectbox(
        "Paygrade You Are Competing For", [PG_PROMPT] + PAYGRADES, key="fms_paygrade",
        help="The PMA formula and every point cap change by paygrade. Set automatically "
             "when your profile sheet states it — you can always override it here.",
    )
    paygrade_chosen = paygrade in PAYGRADES
    # Until a paygrade is picked, widget bounds use the widest value across all
    # paygrades so nothing read off the sheet gets clamped away before we know which
    # rules apply. The form cannot be submitted in this state.
    rules = FMS_RULES[paygrade] if paygrade_chosen else UNSET_RULES
    pma_cap = rules["pma_input_max"]
    pma_label = rules["pma_name"]
    is_e7 = paygrade == "E7"

    if not paygrade_chosen:
        st.warning(
            "**Pick your paygrade before calculating.** The PMA formula and every point "
            "cap are different for E5, E6 and E7 — the same profile sheet scores "
            "differently under each one, so there is no safe default to guess."
        )

    # The sheet's own wording, checked against the dropdown. This covers the range
    # the cap check cannot see — between PMA 3.92 and 4.00 an E6 sheet scored as E5
    # comes out HIGHER than the truth without breaking any cap.
    for _conflict in paygrade_conflicts(raw_text, paygrade, paygrade_chosen):
        st.error(_conflict)

    # Surface any value the sheet gave us that does not fit the chosen paygrade.
    # This is the tell that the wrong paygrade is selected.
    for field, found, cap in over_cap_fields(extracted_data, paygrade, paygrade_chosen):
        st.error(
            f"**Your sheet says {FIELD_TITLES[field]} is {found:g}, but the {paygrade} "
            f"maximum is {cap:g}.** The field below has been reduced to {cap:g}, so your "
            f"FMS will be wrong if {paygrade} is not right. Check the paygrade above — "
            f"a value this high usually means the sheet is for a higher paygrade."
        )

    with st.form("fms_form"):
        sailor_name = st.text_input("Sailor Name / Rate", value="SailorX")
        col1, col2 = st.columns(2)
        with col1:
            exam_score = st.number_input("Exam Standard Score", min_value=0.0, max_value=80.0,
                                         value=safe_value(extracted_data["exam_score"], 0.0, 80.0,
                                                          DEFAULT_VALUES["exam_score"]), step=0.5)
            pma = st.number_input(
                f"{pma_label} (max {pma_cap:.2f})",
                min_value=0.0, max_value=pma_cap,
                value=safe_value(extracted_data["pma"], 0.0, pma_cap,
                                 min(DEFAULT_VALUES["pma"], pma_cap)), step=0.01,
                help=("Average of your eval promotion recommendation values (4.00, 3.80, "
                      "3.60, 3.40 or 2.00). Tops out at 4.00."
                      if pma_cap == 4.00 else
                      "Eval value plus RSCA bonus points, from your profile sheet. "
                      "Tops out at 5.80."),
            )
            tir = st.number_input(
                "Service in Paygrade / Time in Rate (Years)", min_value=0.0, max_value=30.0,
                value=safe_value(extracted_data["tir"], 0.0, 30.0, DEFAULT_VALUES["tir"]), step=0.5,
                disabled=is_e7,
                help="Your profile sheet calls this Service in Paygrade (SIPG); the FMS chart "
                     "calls it Time in Rate. Same number. Points = years / 5, capped at "
                     f"{rules['tir_max']:.0f}." if not is_e7 else "Not used for E7.",
            )
        with col2:
            awards = st.number_input(
                f"Awards Points (max {rules['awards_max']:.0f})",
                min_value=0.0, max_value=max(rules["awards_max"], 0.5),
                value=safe_value(extracted_data["awards"], 0.0,
                                 max(rules["awards_max"], 0.5), 0.0), step=0.5,
                disabled=is_e7,
            )
            education = st.selectbox(
                "Highest Education",
                ["None (0 pts)", "Associate's — AA/AS (2 pts)", "Bachelor's or above (4 pts)"],
                index=(2 if float(extracted_data["education"]) >= 3
                       else 1 if float(extracted_data["education"]) >= 2 else 0),
                disabled=is_e7,
            )
            pna = st.number_input(
                "PNA Points (max 9)", min_value=0.0, max_value=9.0,
                value=safe_value(extracted_data["pna"], 0.0, 9.0, DEFAULT_VALUES["pna"]), step=0.5,
                disabled=is_e7,
                help="Top 25% of candidates earn these. Last 3 exam cycles only.",
            )
        if is_e7:
            st.caption(
                "E7 FMS is exam standard score + RSCA PMA only. Awards, PNA, service in "
                "paygrade and education do not add points, so those fields are greyed out."
            )
        submitted = st.form_submit_button(
            "📊 Calculate My FMS" if paygrade_chosen else "📊 Select a paygrade above to calculate",
            width="stretch", disabled=not paygrade_chosen,
        )

    # paygrade_chosen is re-checked here, not just on the button: a disabled button
    # is a UI courtesy, not a guarantee, and scoring under a guessed paygrade is the
    # exact failure this is here to prevent.
    if submitted and paygrade_chosen:
        education = {"None (0 pts)": 0.0,
                     "Associate's — AA/AS (2 pts)": 2.0,
                     "Bachelor's or above (4 pts)": 4.0}[education]

        fms, breakdown = compute_fms(paygrade, exam_score, pma, tir, awards, education, pna)
        fms_max = FMS_RULES[paygrade]["fms_max"]
        pct = round((fms / fms_max) * 100, 1)

        st.subheader("📊 Your Results")
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Final Multiple Score", f"{fms}")
        col_b.metric(f"Max Possible ({paygrade})", f"{fms_max}")
        col_c.metric("Percent of Max", f"{pct}%")

        st.info(
            "There is no single passing FMS. Advancement cutoffs are set separately for "
            "**every rating, every cycle**, based on how many billets are available and how "
            "everyone else in your rate scored. Use your FMS to see where your points are "
            "leaking, and check the published quota results for your rating's actual cutoff."
        )

        st.subheader("📉 Score Breakdown")
        df_breakdown = (
            pd.DataFrame.from_dict(breakdown, orient="index", columns=["Points"])
            .reset_index()
            .rename(columns={"index": "Component"})
        )
        st.bar_chart(df_breakdown.set_index("Component"))

        st.subheader("📚 Personalized Study Guide")
        guide_items = []

        if exam_score < 55:
            guide_items.append({
                "area": "Exam Score",
                "priority": "HIGH" if exam_score < 45 else "MEDIUM",
                "current": exam_score, "target": 55.0,
                "gain": round(55 - exam_score, 1),
                "actions": [
                    "Study NRTC materials for your rate daily.",
                    "Use Bitvore or rate-specific Quizlet decks.",
                    "Take practice exams under timed conditions.",
                    "Focus on tech manual chapters with highest question frequency.",
                    "Form a study group with others in your rate.",
                ],
            })

        _pma_target = 4.00 if pma_cap == 4.00 else 5.00
        if pma < _pma_target:
            _now_pts = pma_points(paygrade, pma)
            _tgt_pts = pma_points(paygrade, _pma_target)
            guide_items.append({
                "area": pma_label + " / Eval Performance",
                "priority": "HIGH" if (_tgt_pts - _now_pts) >= 15 else "MEDIUM",
                "current": str(pma) + " (worth " + str(_now_pts) + " pts)",
                "target": str(_pma_target) + " (worth " + str(_tgt_pts) + " pts)",
                "gain": round(_tgt_pts - _now_pts, 2),
                "actions": [
                    "Talk to your supervisor about your eval standing.",
                    "Volunteer for additional duties and qualifications.",
                    "Document all accomplishments — do not wait until eval time.",
                    "Pursue a warfare qualification if not already earned.",
                    "Request a mid-term counseling session.",
                ],
            })

        if awards < 5 and not is_e7:
            guide_items.append({
                "area": "Awards", "priority": "MEDIUM",
                "current": awards, "target": f"5-{rules['awards_max']:.0f}",
                "gain": round(5 - awards, 1),
                "actions": [
                    "Talk to your LPO or Chief about submitting an award write-up.",
                    "Track achievements that qualify for a NAM.",
                    "Ensure all past awards are in your service record.",
                    "Participate in community service events.",
                ],
            })

        if education < 4.0 and not is_e7:
            guide_items.append({
                "area": "Education",
                "priority": "MEDIUM" if education < 2.0 else "LOW",
                "current": str(education) + " pts", "target": "4 pts (BA/BS or above)",
                "gain": round(4.0 - education, 1),
                "actions": [
                    "Submit your JST — military skills already earn credits.",
                    "Take a free CLEP exam (Modern States can help you prep free).",
                    "Enroll in Navy College Program courses through NCPACE.",
                    "Contact your ESO for available on-base courses.",
                ],
            })

        if pna == 0 and not is_e7:
            guide_items.append({
                "area": "PNA Points", "priority": "INFO",
                "current": 0, "target": "Accumulates automatically", "gain": "up to 9",
                "actions": [
                    "PNA points are awarded each cycle you pass but are not advanced.",
                    "Keep taking and passing the exam every cycle.",
                    "Max is 3 cycles x 3 pts = 9 points.",
                ],
            })

        if not guide_items:
            st.success("Your scores are strong across the board. Keep it up!")
        else:
            priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
            guide_items.sort(key=lambda x: priority_order.get(x["priority"], 9))
            priority_icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢", "INFO": "🔵"}
            for item in guide_items:
                icon = priority_icon.get(item["priority"], "⚪")
                label = (icon + " **" + item["area"] + "** — Current: " + str(item["current"])
                         + " -> Target: " + str(item["target"]) + " (+" + str(item["gain"]) + " pts possible)")
                with st.expander(label):
                    st.markdown("**Action Steps:**")
                    for action in item["actions"]:
                        st.markdown("- " + action)

        st.subheader("🧾 Full Score Summary")
        st.dataframe(
            pd.DataFrame([{
                "Sailor": sailor_name, "Paygrade": paygrade,
                "Exam": breakdown["Exam Standard Score"], "PMA": pma,
                "PMA pts": breakdown["PMA Points"],
                "SIPG yrs": tir, "SIPG pts": breakdown["Time in Rate"],
                "Awards": breakdown["Awards"], "Education": breakdown["Education"],
                "PNA": breakdown["PNA Points"],
                "FMS": fms, "Max": fms_max, "% of Max": pct,
            }]),
            width="stretch",
        )

        st.subheader("📥 Download Report")

        def generate_pdf(name, paygrade, fms, fms_max, pct, breakdown, pma, tir, guide_items):
            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Arial", "B", 16)
            pdf.cell(0, 10, "Navy FMS Report - " + name, ln=True, align="C")
            pdf.set_font("Arial", "", 10)
            pdf.cell(0, 8, "Competing for " + paygrade
                     + " | Max possible FMS: " + str(fms_max), ln=True, align="C")
            pdf.ln(6)
            pdf.set_font("Arial", "B", 14)
            pdf.cell(0, 10, "Final Multiple Score: " + str(fms)
                     + "   (" + str(pct) + "% of max)", ln=True)
            pdf.set_font("Arial", "", 9)
            pdf.multi_cell(180, 5,
                           "Advancement cutoffs are set per rating, per cycle. There is no single "
                           "passing FMS. Check the published quota results for your rating.")
            pdf.ln(4)
            pdf.set_font("Arial", "B", 12)
            pdf.cell(0, 8, "Score Breakdown:", ln=True)
            pdf.set_font("Arial", "", 11)
            for lbl, val in breakdown.items():
                extra = ""
                if lbl == "PMA Points":
                    extra = "   (from PMA " + str(pma) + ")"
                elif lbl == "Time in Rate":
                    extra = "   (from " + str(tir) + " yrs in paygrade)"
                pdf.cell(0, 7, "  " + lbl + ": " + str(val) + extra, ln=True)
            pdf.ln(4)
            if guide_items:
                pdf.set_font("Arial", "B", 12)
                pdf.cell(0, 8, "Improvement Areas:", ln=True)
                for item in guide_items:
                    pdf.set_font("Arial", "B", 11)
                    pdf.cell(0, 7, "[" + item["priority"] + "] " + item["area"], ln=True)
                    pdf.set_font("Arial", "", 10)
                    for action in item["actions"]:
                        safe = action.encode("latin-1", errors="replace").decode("latin-1")
                        pdf.multi_cell(180, 6, "   - " + safe)
                        pdf.ln(2)
            out_path = os.path.join(tempfile.gettempdir(), "fms_report.pdf")
            pdf.output(out_path)
            return out_path

        pdf_path = generate_pdf(sailor_name, paygrade, fms, fms_max, pct, breakdown, pma, tir, guide_items)
        with open(pdf_path, "rb") as f:
            st.download_button(
                label="📥 Download PDF Report", data=f,
                file_name="FMS_Report_" + sailor_name.replace(" ", "_") + ".pdf",
                mime="application/pdf", width="stretch",
            )


# ── TAB 2: ADVANCEMENT INFO ───────────────────────────────────────────────────
with tab2:
    today = datetime.date.today()

    if cycle_expired(today):
        # Four "✅ Passed" tiles under a live countdown heading is how an app starts
        # looking abandoned. Say the cycle is done and stop pretending to count.
        st.subheader(f"⏱️ Cycle {CYCLE['number']} — Complete")
        st.info(
            f"**Cycle {CYCLE['number']} is finished.** The E5 exam was held "
            f"{_fmt_date(CYCLE['exam_e5'])} and the E6 exam "
            f"{_fmt_date(CYCLE['exam_e6'])}.\n\n"
            "The next cycle's dates are not loaded into Score Surge yet — check the "
            "NAVADMIN on [MyNavyHR](https://www.mynavyhr.navy.mil) for them. Your FMS "
            "calculator, study tools and score history all still work; only the "
            "countdown above is waiting on the new NAVADMIN."
        )
        deadlines = []
    else:
        _pg = sailor_paygrade()

        # ── The sailor's own exam, front and centre ──────────────────────────
        # This tab used to show ILDC, the E6 exam, the E5 exam and the FY27 CPO
        # estimate all at once, and leave the sailor to work out which two lines
        # were theirs. The app already knows what they are competing for.
        if _pg == "E5":
            _my_exam_label, _my_exam_date = "Your E5 Exam Day", CYCLE["exam_e5"]
        elif _pg == "E6":
            _my_exam_label, _my_exam_date = "Your E6 Exam Day", CYCLE["exam_e6"]
        elif _pg == "E7":
            _my_exam_label, _my_exam_date = "Your CPO Board Exam (est.)", CPO_EXAM["est_date"]
        else:
            _my_exam_label = _my_exam_date = None

        if _my_exam_date:
            st.subheader(f"⏱️ Cycle {CYCLE['number']} — Your Countdown")
            _status, _date_line = deadline_tile(_my_exam_date, today)
            st.metric(_my_exam_label, _status)
            st.caption(f"📅 {_date_line}")

            _days_out = (_my_exam_date - today).days

            # ── Something to actually do about it ────────────────────────────
            # Four countdowns and no next step is a poster, not a product.
            if _days_out >= 0:
                _weak = sorted(
                    [h for h in st.session_state.get("score_history", [])
                     if isinstance(h, dict) and float(h.get("pct") or 0) < 70],
                    key=lambda h: float(h.get("pct") or 0),
                )
                _weeks = max(_days_out // 7, 0)
                _pace = (f"about {_weeks} week" + ("" if _weeks == 1 else "s")
                         if _weeks else "less than a week")
                if _weak:
                    st.info(
                        f"**{_days_out} days out — {_pace} of study time left.** "
                        f"Your weakest topic so far is **{_weak[0]['topic']}** "
                        f"({int(float(_weak[0].get('pct') or 0))}%). Start there: open the "
                        "**Study Guide** tab for that topic, then re-test it under **Mock Exam**."
                    )
                else:
                    st.info(
                        f"**{_days_out} days out — {_pace} of study time left.** "
                        "You have no graded topics yet. Take a short exam under **Mock Exam** "
                        "to find your weak spots before you spend study time guessing."
                    )
        else:
            st.subheader(f"⏱️ Cycle {CYCLE['number']} Countdown")
            st.info(
                "**Set the paygrade you're competing for** on the **FMS Calculator** tab "
                "(or upload your profile sheet) and this page will show your exam date and "
                "your deadlines instead of everyone's."
            )

        # ── Everything else, out of the way but one tap down ─────────────────
        deadlines = [
            ("PMK-EE Deadline",    CYCLE["pmkee"]),
            ("ILDC Deadline (E6)", CYCLE["ildc_e6"]),
            ("E6 Exam Day",        CYCLE["exam_e6"]),
            ("E5 Exam Day",        CYCLE["exam_e5"]),
        ]

    if deadlines:
        _all_open = st.expander(
            "All Cycle {} dates".format(CYCLE["number"]),
            expanded=sailor_paygrade() is None,
        )
        with _all_open:
            # Two columns, not four. At four, "🟡 25 days" truncated to "🟡 25 d…" on a
            # laptop and would have been worse on the phone most sailors use. The date
            # sits in a caption rather than st.metric's delta, which draws a ↑ arrow
            # that means nothing next to a calendar date.
            for i in range(0, len(deadlines), 2):
                cols = st.columns(2)
                for col, (label, date) in zip(cols, deadlines[i:i + 2]):
                    _status, _date_line = deadline_tile(date, today)
                    col.metric(label, _status)
                    col.caption(f"📅 {_date_line}")
            st.caption(f"Source: {CYCLE['navadmin']} (Cycle {CYCLE['number']}).")

        # A closed PMK-EE window is not good news, and the old "✅ Passed" tile
        # implied the sailor had passed the test rather than missed the door.
        if (CYCLE["pmkee"] - today).days < 0:
            # The ⚠️ is written into the text on purpose. Under this app's dark navy
            # theme st.warning renders olive, which reads closer to green than amber —
            # and the entire point of this message is that it is not good news.
            st.warning(
                f"⚠️ **The PMK-EE deadline closed on {_fmt_date(CYCLE['pmkee'])}.** That is the "
                "deadline passing, not you passing anything. If you have not completed your "
                f"PMK-EE, you are not eligible for the Cycle {CYCLE['number']} exam — check "
                "your status with your ESO now."
            )

    st.divider()

    _pg_now = sailor_paygrade()
    st.subheader("📋 What is Billet-Based Advancement (BBA)?")
    if _pg_now == "E6":
        st.caption("You're competing for E6 — BBA is how you advance. Read this one.")
    elif _pg_now == "E5":
        st.caption("You're competing for E5, so BBA doesn't gate you yet — but it's the "
                   "system waiting at E6. Worth knowing before you get there.")
    elif _pg_now == "E7":
        st.caption("You're competing for E7 via the CPO board, not A2P — but you'll be "
                   "advising E6s through this.")
    else:
        st.caption("Most E6 sailors are now under BBA. Here's what that means for you.")

    # Opened by default for the sailors it actually gates. This is the thing that
    # separates Score Surge from generic exam prep — sailors who pass and still do
    # not advance — and it was a collapsed grey row.
    with st.expander("Read the plain-English BBA breakdown", expanded=(_pg_now == "E6")):
        st.markdown("""
**The old system:** Pass the exam + high enough FMS = you advance.

**The new system (BBA):** Pass the exam + apply for a specific open
billet + get selected = you advance. Your FMS still matters — it
tells the Navy how competitive you are for that billet — but hitting
a cutoff number alone won't do it anymore.

**What this means for you:**
- Taking and passing the exam is still step one. You can't be
  considered for a billet without a passing score.
- Your FMS shows how strong your application looks compared to
  other sailors applying for the same billet.
- You apply through the A2P (Advancement-to-Position) process in
  NSIPS. Open billets are posted and you submit a preference card.
- If you pass but don't get selected for a billet, you are
  Pass-Not-Advanced (PNA). You earn PNA points and should apply again
  next cycle.
- Being proactive matters — know which billets are open in your
  rate, where you want to go, and have your record clean and
  up to date.

**Chief tier members** get access to the BBA Strategy Hub —
personalized AI guidance on how to navigate A2P, strengthen your
billet application, and what to do if you passed but weren't selected.
""")

    st.divider()

    # Prominent for the sailors it belongs to, one tap down for everyone else.
    # An E5 does not need the FY27 CPO estimate competing with their own exam date.
    _cpo_title = f"⭐ CPO / E7 Exam Watch — FY{CPO_EXAM['fy']}"
    if _pg_now == "E7":
        st.subheader(_cpo_title)
        _cpo_box = st.container()
    else:
        _cpo_box = st.expander(_cpo_title)

    with _cpo_box:
        st.caption(
            f"The FY{CPO_EXAM['fy']} CPO board exam is typically held in January–February. "
            + ("The date below is confirmed."
               if CPO_EXAM["announced"] else
               "The official NAVADMIN has not yet been released.")
        )

        _cpo_est_date = CPO_EXAM["est_date"]
        _cpo_days_left = (_cpo_est_date - today).days
        if _cpo_days_left < 0:
            _cpo_status = "⛔ Est. date passed"
        elif _cpo_days_left <= 14:
            _cpo_status = f"🔴 ~{_cpo_days_left} days"
        elif _cpo_days_left <= 30:
            _cpo_status = f"🟡 ~{_cpo_days_left} days"
        else:
            _cpo_status = f"🟢 ~{_cpo_days_left} days"

        cpo_col1, cpo_col2 = st.columns(2)
        cpo_col1.metric(
            "CPO Exam" if CPO_EXAM["announced"] else "CPO Exam (est.)",
            _cpo_status,
            delta=f"{_fmt_date(_cpo_est_date)}"
                  + ("" if CPO_EXAM["announced"] else " estimated"),
            delta_color="off",
        )
        cpo_col2.metric("Official NAVADMIN",
                        "✅ Released" if CPO_EXAM["announced"] else "⏳ Not yet released")
        st.info(
            f"📋 **FY{CPO_EXAM['fy']} CPO Board Exam** — Historically announced Oct–Nov and "
            "administered Jan–Feb. "
            + ("The date above is from the published NAVADMIN."
               if CPO_EXAM["announced"] else
               "Watch for the official NAVADMIN on "
               "[MyNavyHR](https://www.mynavyhr.navy.mil). "
               "This countdown will be updated once the date is confirmed.")
        )


# ── TAB 3: AI STUDY GUIDE ─────────────────────────────────────────────────────
with tab3:
    st.subheader("📖 AI Study Guide")
    st.caption("Powered by a stern, coffee-drinking PS Chief who has no time for excuses.")

    if not can_access("petty_officer"):
        upgrade_banner("petty_officer", "study_guide")
    else:
        with st.form("study_guide_form"):
            col1, col2 = st.columns(2)
            with col1:
                sg_rating = st.selectbox("Your Rating", ["PS", "YN", "IT", "BM", "MM", "EM", "HM", "MA"])
                sg_paygrade = st.selectbox("Your Paygrade", ["E5", "E6", "E7"])
            with col2:
                sg_gap = st.number_input("Your FMS Gap (0 if eligible)", min_value=0.0, max_value=30.0,
                                         value=0.0, step=0.5)
                sg_type = st.selectbox("Guide Type", [
                    "Full Rating Guide", "Crash Plan (3-5 days)",
                    "High Yield Topics Only", "Single Subject Deep Dive", "Practice Questions"
                ])
            sg_subject = st.text_input("Subject (only for Single Subject Deep Dive)",
                                       placeholder="e.g. Military Awards, UCMJ, Evals")
            sg_submit = st.form_submit_button("Generate My Study Guide", width="stretch")

        if sg_submit:
            if True:
                if sg_gap > 10:
                    strategy = "broad coverage — this sailor needs significant improvement across all areas"
                elif sg_gap > 5:
                    strategy = "high-yield focus — hit the heavy hitters that appear most on the exam"
                elif sg_gap > 0:
                    strategy = "precision mode — plug specific holes, every point counts"
                else:
                    strategy = "rank maximization — sailor is eligible but wants to score higher"

                topic_instruction = (
                    f"Focus exclusively on: {sg_subject}"
                    if sg_type == "Single Subject Deep Dive" and sg_subject
                    else f"Guide type: {sg_type}"
                )

                # "Practice Questions" is the one guide type that CANNOT obey the blanket
                # no-facts rule below — a question with no fact in it is not a question.
                # So that mode gets a narrower rule instead of a broken one, and the sailor
                # gets the same unverified-content warning the Mock Exam tab already shows.
                sg_is_questions = (sg_type == "Practice Questions")
                sg_questions_carve_out = """

=== EXCEPTION FOR THIS GUIDE TYPE ===

This guide type is practice questions, which cannot be written without stating facts. So
the rules above are replaced by these, and only for the questions themselves:

- Ask about topics and judgment, not about numbers you are unsure of. Prefer "who owns
  this decision" and "what happens when this fails" over "how many days."
- Spread the correct answer across A, B, C and D. Do not favour one letter, and do not
  make the correct answer the longest option.
- Do not put always, never or only in a wrong answer. Sailors are taught to eliminate
  those, so the distractor does no work.
- Do not name the article number in the question itself. The real exam is closed book.
- Under each question, name the manual to confirm it in. If you are not certain an
  article number is current, name the manual and stop there rather than inventing one.
"""

                prompt = f"""You are a senior {sg_rating} Chief Petty Officer with 20 years of service.
You drink too much coffee, you have zero patience for excuses, and you genuinely want your sailors to advance.
You are direct, blunt, and efficient. No fluff. No wasted words.
{cycle_authority_line()}

{cycle_facts_block()}

Generate a personalized Navy advancement study guide for:
- Rating: {sg_rating}
- Paygrade: {sg_paygrade}
- FMS Gap: {sg_gap} points
- Strategy: {strategy}
- {topic_instruction}

Structure the guide as follows:
1. ONE sentence of honest assessment of their situation
2. Top study topics for {sg_rating} advancement exam (with brief explanation of why each matters)
3. Key concepts they must understand (not memorize — understand)
4. Common exam traps and mistakes sailors make
5. Exactly what to do each day for the next 5 days
6. One closing line — make it motivating but stern

Use plain English. Write like you're talking to the sailor face to face.
Keep it tight. Every sentence must earn its place.

=== ACCURACY RULES — THESE OVERRIDE EVERYTHING ABOVE ===

You are writing a STUDY PLAN, not a reference. You do not have the manuals in front of
you. A sailor will act on what you write. If you state a regulation from memory and you
are wrong, they learn it wrong and carry it into a closed-book exam.

So: NEVER state any of the following anywhere in this guide.

- A deadline, time limit, or number of days, months or years
- A dollar amount, pay rate, percentage, point value or score threshold
- A form number (NAVPERS, DD, NAVSUP, OPNAV) or a system field name
- An article, chapter, paragraph, instruction or NAVADMIN number
- Eligibility criteria, thresholds, or any "you must have X to qualify"
- A specific procedure, routing chain, or approval authority

Naming a manual or a topic area is fine. Stating what it says is not.

Instead, name the thing to learn and send them to the source:

  DO NOT WRITE: "You have 30 days to submit the NAVPERS 1070/613."
  WRITE INSTEAD: "Know the submission window for administrative remarks — pull the
                  exact number from the MILPERSMAN article on your bibliography and
                  memorize it. This one shows up."

Apply this to every section, including exam traps and the daily plan. If a sentence
would teach a fact a sailor could be tested on, rewrite it as an instruction to go look
that fact up in their bibliography. A guide that says "learn this, here is where it
lives, here is why it matters" is more useful than one that guesses the number — and it
cannot be wrong.{sg_questions_carve_out if sg_is_questions else ""}"""

                with st.spinner("Chief is reviewing your record..."):
                    try:
                        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
                        message = client.messages.create(
                            model="claude-opus-4-5", max_tokens=1500,
                            messages=[{"role": "user", "content": prompt}]
                        )
                        guide_text = message.content[0].text
                        st.subheader("📋 Your Personalized Study Guide")
                        # The guide is a plan, not a reference. The prompt forbids it from
                        # stating deadlines, amounts, form numbers or article numbers,
                        # because it is written from the model's memory with no manual in
                        # front of it. Say so on screen — a sailor who treats a study plan
                        # as an authority is the failure this tab has to avoid.
                        if sg_is_questions:
                            st.warning(
                                "**Unverified practice questions.** These are written by AI "
                                "from the NWAE bibliography and have not been checked against "
                                "the official manuals. Use them to practise the format — "
                                "confirm anything you learn here against your bib before "
                                "test day."
                            )
                        else:
                            st.caption(
                                "This is a **study plan, not a reference.** It tells you what "
                                "to learn and where it lives — always confirm the actual "
                                "numbers, deadlines and form numbers against your bibliography."
                            )
                        st.markdown(guide_text)
                        st.download_button(
                            "📥 Download Study Guide", data=guide_text,
                            file_name=f"StudyGuide_{sg_rating}_{sg_paygrade}.txt",
                            mime="text/plain", width="stretch",
                        )
                    except Exception as e:
                        st.error("Something went wrong: " + str(e))


# ── TAB 4: AI TUTOR ───────────────────────────────────────────────────────────
with tab4:
    st.subheader("🎓 Interactive AI Tutor")
    st.caption("Pick a topic. The Chief will teach it. Ask questions. Get answers. Pass your exam.")

    if not can_access("petty_officer"):
        upgrade_banner("petty_officer", "ai_tutor")
    else:
        col1, col2 = st.columns(2)
        with col1:
            tutor_rating = st.selectbox("Your Rating", RATINGS, key="tutor_rating")
        with col2:
            tutor_paygrade = st.selectbox("Your Paygrade", PAYGRADES,
                                          index=PAYGRADES.index("E5"), key="tutor_paygrade")

        with st.spinner(f"Loading {tutor_rating} {tutor_paygrade} topics..."):
            tutor_topics = get_rate_topics(tutor_rating, tutor_paygrade)

        if not (tutor_rating == "PS" and tutor_paygrade in PS_TOPICS_BY_PAYGRADE):
            st.caption("Topics for this rate are AI-generated from the NWAE bibliography. "
                       "Verify against your official bib before test day.")

        col3, col4 = st.columns(2)
        with col3:
            tutor_topic = st.selectbox("Select a Topic to Study", list(tutor_topics.keys()),
                                       key="tutor_topic")
        with col4:
            tutor_subtopic = st.selectbox("Select a Subtopic",
                                          tutor_topics[tutor_topic]["subtopics"],
                                          key="tutor_subtopic")

        if st.button("📖 Start Lesson", width="stretch"):
            if True:
                if tutor_topic not in tutor_topics:
                    tutor_topic = list(tutor_topics.keys())[0]
                bib_refs = tutor_topics[tutor_topic]["bib"]
                lesson_prompt = f"""You are a senior {tutor_rating} Chief Petty Officer with 20 years of experience.
You are teaching a Navy advancement exam lesson to a busy young sailor who needs to pass the {tutor_rating} {tutor_paygrade} NWAE.
Explain everything like the sailor is smart but has never seen this material before.
Be direct, clear, and use real Navy examples. No wasted words. No fluff.

TOPIC: {tutor_topic}
SUBTOPIC: {tutor_subtopic}
GOVERNING REFERENCES: {bib_refs}

Teach this lesson as follows:
1. What this topic is in ONE plain-English sentence
2. Why it matters on the exam and in real life
3. The key rules, procedures, or concepts they MUST know (use bullet points, plain English)
4. A real-world example of how this works in a {tutor_rating} shop or workcenter
5. The most common exam trap on this subtopic
6. Three practice questions with answers and explanations

Keep it tight. Make it stick."""

                with st.spinner("Chief is preparing your lesson..."):
                    try:
                        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
                        message = client.messages.create(
                            model="claude-opus-4-5", max_tokens=2000,
                            messages=[{"role": "user", "content": lesson_prompt}]
                        )
                        lesson = message.content[0].text
                        st.subheader(f"📚 Lesson: {tutor_subtopic}")
                        st.markdown(lesson)
                        st.session_state.tutor_history = [
                            {"role": "user", "content": lesson_prompt},
                            {"role": "assistant", "content": lesson}
                        ]

                        st.download_button(
                            "📥 Download This Lesson", data=lesson,
                            file_name=f"Lesson_{tutor_subtopic.replace(' ', '_')}.txt",
                            mime="text/plain", width="stretch",
                        )
                    except Exception as e:
                        st.error("Error: " + str(e))

        if "tutor_history" in st.session_state and len(st.session_state.tutor_history) > 0:
            st.subheader("💬 Ask the Chief a Question")
            st.caption("Type any follow-up question about this topic.")
            sailor_question = st.text_input("Your question",
                                            placeholder="e.g. What happens if a sailor misses the travel claim deadline?")
            if st.button("Ask", width="stretch"):
                if sailor_question:
                    with st.spinner("Chief is thinking..."):
                        try:
                            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
                            history = st.session_state.tutor_history.copy()
                            history.append({"role": "user", "content": sailor_question})
                            message = client.messages.create(
                                model="claude-opus-4-5", max_tokens=1000,
                                messages=history
                            )
                            answer = message.content[0].text
                            st.session_state.tutor_history.append({"role": "assistant", "content": answer})
                            st.markdown("**Chief says:**")
                            st.markdown(answer)
                        except Exception as e:
                            st.error("Error: " + str(e))


def parse_exam_json(raw: str) -> list:
    """Turn the Chief's exam into rows the app can actually work with.

    Questions used to arrive as one blob of markdown that got printed straight to the
    page. Nothing could be attached to an individual question — so answers were
    bubble-in nowhere, the answer key printed alongside the questions, and grading had
    to be shipped back to Claude to re-read its own output.

    The field names here are deliberately the ones in `questions.db` (Score Surge DB
    repo) so the exam engine and the verified question bank speak the same language.
    A question from either source is the same shape to everything downstream.

    Returns [] rather than raising: a malformed exam should ask the sailor to hit
    Generate again, not take down the tab.
    """
    text = (raw or "").strip()
    # Models fence JSON more often than not, and the fence is not JSON.
    if text.startswith("```"):
        text = re.sub(r"^```[A-Za-z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except Exception:
        return []
    if not isinstance(data, list):
        return []

    clean = []
    for row in data:
        if not isinstance(row, dict):
            continue
        q = {
            "question": str(row.get("question") or "").strip(),
            "answer_a": str(row.get("answer_a") or "").strip(),
            "answer_b": str(row.get("answer_b") or "").strip(),
            "answer_c": str(row.get("answer_c") or "").strip(),
            "answer_d": str(row.get("answer_d") or "").strip(),
            "correct_answer": str(row.get("correct_answer") or "").strip().upper()[:1],
            "explanation": str(row.get("explanation") or "").strip(),
            "source_manual": str(row.get("source_manual") or "").strip(),
            "chapter_section": str(row.get("chapter_section") or "").strip(),
        }
        # A question missing an option, or keyed to an answer that isn't one of the
        # four, cannot be graded. Drop it rather than show a sailor something unanswerable.
        if not q["question"] or q["correct_answer"] not in ("A", "B", "C", "D"):
            continue
        if not all(q[f"answer_{L}"] for L in ("a", "b", "c", "d")):
            continue
        # Two options that say the same thing make a question unanswerable — there are
        # then two right answers and only one is keyed. This catches the crude case
        # only. It will NOT catch two different names for the same document
        # ("NAVPERS 1070/602" vs "Page 2 Dependency Application"), which is a Navy fact,
        # not a string fact. That class needs a verified question bank, not a parser.
        norm = [re.sub(r"[^a-z0-9]", "", q[f"answer_{L}"].lower()) for L in ("a", "b", "c", "d")]
        if len(set(norm)) < 4:
            continue
        clean.append(q)
    return clean


def exam_source_line(q: dict) -> str:
    """The reference under an answer.

    This line used to read "📖 Source: ...", which presents an AI-generated guess with
    the authority of a citation. Verification found questions citing cancelled articles
    and the wrong reference entirely — a wrong answer wearing a uniform. Until a
    question comes from the verified bank, its reference is a lead to check, not a
    source to trust, and it says so.
    """
    ref = ", ".join(p for p in (q.get("source_manual", ""), q.get("chapter_section", "")) if p)
    if not ref:
        return "⚠️ No reference given — treat this one with caution."
    if str(q.get("verified", "")).strip().lower() in ("yes", "true", "1"):
        return f"📖 Verified source: {ref}"
    return f"🔎 Unverified lead: {ref} — confirm in your bib before you trust it."


def exam_all_verified(questions: list) -> bool:
    """True only if every question came from the verified bank."""
    return bool(questions) and all(
        str(q.get("verified", "")).strip().lower() in ("yes", "true", "1")
        for q in questions
    )


def score_bars(entries, show_topic=True):
    """Score history as plain bars anyone can read at a glance.

    This replaced st.line_chart, which drew an interactive Vega chart: hover
    tooltips, click-drag zoom, a fullscreen button and a download menu. A sailor
    checking their scores would nudge the trackpad and zoom the axis. Worse, with
    two sittings both at 33% the y-axis auto-scaled to 4–28 — a percentage chart
    that never showed 0 or 100 and looked like nonsense.

    A bar per sitting, a fixed 0–100 scale, and a gold line at the 70% pass mark.
    No chart element means no toolbar to fight with, and nothing to interpret:
    longer bar is better, past the gold line is a pass.
    """
    if not entries:
        return
    blocks = []
    for e in entries:
        try:
            pct = max(0, min(100, int(round(float(e.get("pct") or 0)))))
        except (TypeError, ValueError):
            pct = 0
        if pct >= 80:
            fill, verdict = "#2E9E5B", "✅ Solid"
        elif pct >= 70:
            fill, verdict = "#C8A02C", "✅ Pass"
        else:
            fill, verdict = "#B3453C", "❌ Needs work"
        head = str(e.get("date") or "—")
        if show_topic:
            head += f" — {e.get('topic') or '—'}"
        blocks.append(
            f"<div style='margin:0 0 16px 0;'>"
            f"<div style='font-size:0.88rem;opacity:.85;margin-bottom:5px;'>{head}</div>"
            f"<div style='position:relative;height:30px;width:100%;border-radius:6px;"
            f"background:rgba(255,255,255,0.10);overflow:hidden;'>"
            f"<div style='position:absolute;inset:0 auto 0 0;width:{pct}%;"
            f"background:{fill};'></div>"
            f"<div style='position:absolute;left:70%;top:0;bottom:0;width:2px;"
            f"background:#F5C518;'></div>"
            f"<div style='position:absolute;left:12px;top:0;height:30px;line-height:30px;"
            f"font-weight:700;color:#fff;font-size:0.95rem;'>"
            f"{int(e.get('score') or 0)} of {int(e.get('total') or 0)} &nbsp;·&nbsp; {pct}%"
            f"</div></div>"
            f"<div style='font-size:0.82rem;margin-top:4px;opacity:.9;'>{verdict}</div>"
            f"</div>"
        )
    st.markdown("".join(blocks), unsafe_allow_html=True)
    st.caption("The gold line is the 70% pass mark. Longer bar is better.")


# ── TAB 5: MOCK EXAM ──────────────────────────────────────────────────────────
with tab5:
    st.subheader("🎯 Full Mock Exam")
    st.caption("Exam-style questions, graded by the Chief. Real explanations. Chief doesn't grade on a curve.")

    if "score_history" not in st.session_state:
        st.session_state.score_history = []

    if not can_access("chief"):
        upgrade_banner("chief", "mock_exam")
    else:
        colA, colB = st.columns(2)
        with colA:
            pq_rating = st.selectbox("Your Rating", RATINGS, key="pq_rating")
        with colB:
            pq_paygrade = st.selectbox("Your Paygrade", PAYGRADES,
                                       index=PAYGRADES.index("E5"), key="pq_paygrade")

        with st.spinner(f"Loading {pq_rating} {pq_paygrade} topics..."):
            pq_topics = get_rate_topics(pq_rating, pq_paygrade)

        if not (pq_rating == "PS" and pq_paygrade in PS_TOPICS_BY_PAYGRADE):
            st.caption("Topics for this rate are AI-generated from the NWAE bibliography. "
                       "Verify against your official bib before test day.")

        with st.form("practice_form"):
            col1, col2 = st.columns(2)
            with col1:
                pq_topic = st.selectbox("Topic", list(pq_topics.keys()), key="pq_topic")
            with col2:
                pq_num = st.selectbox("Number of Questions", [3, 5, 10], key="pq_num")
            pq_submit = st.form_submit_button("Generate Mock Exam", width="stretch")

        if pq_submit:
            if pq_topic not in pq_topics:
                pq_topic = list(pq_topics.keys())[0]
            bib_refs = pq_topics[pq_topic]["bib"]
            pq_prompt = f"""You are a senior {pq_rating} Chief Petty Officer writing a Navy {pq_rating} {pq_paygrade} advancement exam practice set.

Write exactly {pq_num} NWAE-style multiple choice questions for:
- Topic: {pq_topic}
- Rating / Paygrade: {pq_rating} advancing to {pq_paygrade}
- Governing References: {bib_refs}

Return ONLY a JSON array. No preamble, no markdown fences, no commentary.
Each element must have exactly these keys:

[
  {{
    "question": "The question text. Do not include a 'Q1:' prefix.",
    "answer_a": "First option, text only. Do not include an 'A)' prefix.",
    "answer_b": "Second option.",
    "answer_c": "Third option.",
    "answer_d": "Fourth option.",
    "correct_answer": "A single letter: A, B, C, or D",
    "explanation": "2-3 sentences on why the correct answer is right and which regulation supports it.",
    "source_manual": "The governing manual or instruction, e.g. MILPERSMAN or NAVEDTRA 14257",
    "chapter_section": "e.g. Chapter 4 or Article 1430-010"
  }}
]

Rules:
- Realistic exam difficulty, with tricky but plausible distractors.
- Spread the correct answer across A, B, C and D. Do not favour one letter.
- All four options must be genuinely different answers. Never write two options that name
  the same form, document, regulation or concept in different words — for example
  "NAVPERS 1070/602" and "Page 2 Dependency Application" are the same document, so they
  must never appear as two separate choices. Exactly one option can be correct.
- Do not conflate related but distinct concepts (for example excess leave, advance leave,
  separation leave and terminal leave are four different things). If a question would
  require blurring them, write a different question.
- Cite only references you are confident are current and in force. Do not cite articles
  that have been cancelled or superseded. If you are not certain an article number is
  current, name the manual and omit a specific article rather than inventing one.
- Prefer the governing publication for the subject matter. Do not cite an eligibility or
  ID-card manual as the authority for a pay or allowance transaction.
- No fluff."""

            with st.spinner("Chief is writing your exam..."):
                try:
                    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
                    message = client.messages.create(
                        model="claude-opus-4-5", max_tokens=4000,
                        messages=[{"role": "user", "content": pq_prompt}]
                    )
                    parsed = parse_exam_json(message.content[0].text)
                    if not parsed:
                        st.error("Chief's exam came back in a format the app couldn't read. "
                                 "Hit Generate Mock Exam again.")
                    else:
                        st.session_state.exam_questions = parsed
                        st.session_state.exam_topic = pq_topic
                        st.session_state.exam_rating = pq_rating
                        # Clear anything left over from a previous sitting, including the
                        # old free-text format, so a new exam never opens pre-graded.
                        for stale in ("exam_result", "exam_blank_warning", "practice_questions"):
                            st.session_state.pop(stale, None)
                        for i in range(50):
                            st.session_state.pop(f"exam_pick_{i}", None)
                except Exception as e:
                    st.error("Error: " + str(e))

        exam_qs = st.session_state.get("exam_questions") or []
        if exam_qs:
            exam_topic = st.session_state.get("exam_topic", pq_topic)
            exam_rating = st.session_state.get("exam_rating", pq_rating)
            exam_result = st.session_state.get("exam_result")

            def _grade_exam(picks):
                """Grade against the answer key we already hold.

                The grader used to be a second Claude call that re-read its own exam
                text and reported a score in prose, which then had to be regex'd back
                out. That round trip cost money, could disagree with itself, and once
                returned "Final Score: 0/0". The correct letter is right here.
                """
                rows, correct = [], 0
                for q, picked in zip(exam_qs, picks):
                    is_right = bool(picked) and picked == q["correct_answer"]
                    correct += 1 if is_right else 0
                    rows.append({**q, "picked": picked, "is_right": is_right})

                total = len(exam_qs)
                pct = round((correct / total) * 100) if total else 0

                # The Chief's voice is the product, so this one call stays — but it is
                # only ever the closing remark. A failure here must not cost the sailor
                # a graded exam.
                feedback = ""
                missed = [r["question"] for r in rows if not r["is_right"]]
                try:
                    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
                    fb_prompt = (
                        f"You are a {exam_rating} Chief giving a sailor one short, direct "
                        f"closing remark on their practice exam.\n"
                        f"Topic: {exam_topic}\nScore: {correct} out of {total} ({pct}%).\n"
                        + ("Questions missed:\n- " + "\n- ".join(missed) if missed
                           else "They answered every question correctly.")
                        + "\n\nTwo to four sentences. Be honest and specific about what to "
                          "study next. No fluff, no praise they did not earn."
                        + ("" if exam_all_verified(exam_qs) else
                           "\n\nIMPORTANT: these questions were AI-generated and have NOT been "
                           "verified against the official manuals — the answer key itself may be "
                           "wrong. Verification has caught cancelled article numbers and wrong "
                           "references. So point the sailor at the governing manual to confirm "
                           "what they missed. Do not tell them they do not know their job over a "
                           "question you cannot vouch for. Stay direct, but aim them at the "
                           "reference rather than judging them on this key.")
                    )
                    fb = client.messages.create(
                        model="claude-opus-4-5", max_tokens=400,
                        messages=[{"role": "user", "content": fb_prompt}]
                    )
                    feedback = fb.content[0].text.strip()
                except Exception:
                    feedback = ""

                entry = {
                    "date": datetime.date.today().strftime("%b %d"),
                    "topic": exam_topic, "score": correct, "total": total,
                    "pct": pct,
                }
                st.session_state.score_history.append(entry)
                save_error = ""
                if st.session_state.user:
                    # Never crash the page over history, but never pretend it saved
                    # either. Swallowing this meant a sailor watched their score appear,
                    # then found it gone at next login with no explanation.
                    try:
                        supabase.table("score_history").insert(
                            {"user_id": st.session_state.user.id, **entry}
                        ).execute()
                    except Exception as e:
                        save_error = f"{type(e).__name__}: {e}"

                st.session_state.exam_result = {
                    "rows": rows, "score": correct, "total": total, "pct": pct,
                    "topic": exam_topic, "feedback": feedback, "save_error": save_error,
                }
                st.session_state.pop("exam_blank_warning", None)

            # ── ANSWERING ────────────────────────────────────────────────────
            if not exam_result:
                st.divider()
                st.subheader("📝 Your Mock Exam")
                st.caption(f"{len(exam_qs)} questions · {exam_topic} · "
                           "answers stay hidden until you submit.")
                if not exam_all_verified(exam_qs):
                    st.warning(
                        "**Unverified practice questions.** These are written by AI from the "
                        "NWAE bibliography and have not been checked against the official "
                        "manuals. Spot-checking has found wrong article numbers and cancelled "
                        "references. Use them to practise the format — confirm anything you "
                        "learn here against your bib before test day."
                    )

                with st.form("exam_answer_form"):
                    for i, q in enumerate(exam_qs):
                        st.markdown(f"**Q{i + 1}. {q['question']}**")
                        st.radio(
                            f"Question {i + 1}",
                            options=["A", "B", "C", "D"],
                            index=None,  # nothing pre-selected, same as a blank answer sheet
                            format_func=lambda L, q=q: f"{L})  {q['answer_' + L.lower()]}",
                            key=f"exam_pick_{i}",
                            label_visibility="collapsed",
                        )
                        st.write("")
                    exam_submitted = st.form_submit_button("Submit Exam", width="stretch")

                current_picks = [st.session_state.get(f"exam_pick_{i}")
                                 for i in range(len(exam_qs))]

                if exam_submitted:
                    blanks = [i + 1 for i, p in enumerate(current_picks) if p is None]
                    if blanks:
                        st.session_state.exam_blank_warning = blanks
                    else:
                        with st.spinner("Chief is grading..."):
                            _grade_exam(current_picks)
                        st.rerun()

                blanks = st.session_state.get("exam_blank_warning")
                if blanks:
                    st.warning(
                        f"You haven't answered {'question' if len(blanks) == 1 else 'questions'} "
                        f"{', '.join(str(b) for b in blanks)}. On the real NWAE a blank counts "
                        "as wrong — go back and answer, or submit as-is."
                    )
                    if st.button("Submit anyway — blanks count as wrong", width="stretch"):
                        with st.spinner("Chief is grading..."):
                            _grade_exam(current_picks)
                        st.rerun()

            # ── REVIEW ───────────────────────────────────────────────────────
            else:
                score = exam_result["score"]
                total = exam_result["total"]
                pct = exam_result["pct"]

                st.divider()
                st.subheader("📊 Your Grade")
                verdict = f"**{score} / {total} — {pct}%**"
                if pct >= 80:
                    st.success(f"{verdict} · Solid. That's advancement-standard work.")
                elif pct >= 70:
                    st.info(f"{verdict} · Passing, but there's room between you and the cut.")
                else:
                    st.error(f"{verdict} · Below standard. This topic needs real study time.")

                if not exam_all_verified(exam_result["rows"]):
                    st.warning(
                        "**This score is practice, not truth.** These questions were "
                        "AI-generated and have not been verified against the official manuals. "
                        "If you're confident an answer marked wrong was actually right, back "
                        "yourself and check the manual — the key may be the thing that's wrong."
                    )

                for i, r in enumerate(exam_result["rows"]):
                    st.markdown(f"**Q{i + 1}. {r['question']}**")
                    picked = r["picked"]
                    correct = r["correct_answer"]
                    picked_text = r.get(f"answer_{picked.lower()}") if picked else None

                    if r["is_right"]:
                        st.markdown(f"✅ **Correct** — {picked}) {picked_text}")
                    elif picked:
                        st.markdown(f"❌ **Incorrect** — you chose {picked}) {picked_text}")
                        st.markdown(f"**Correct answer: {correct}) "
                                    f"{r['answer_' + correct.lower()]}**")
                    else:
                        st.markdown("❌ **No answer given**")
                        st.markdown(f"**Correct answer: {correct}) "
                                    f"{r['answer_' + correct.lower()]}**")

                    if r.get("explanation"):
                        st.markdown(r["explanation"])
                    src = exam_source_line(r)
                    if src:
                        st.caption(src)
                    st.divider()

                if exam_result.get("feedback"):
                    st.markdown("### Chief's Feedback")
                    st.markdown(exam_result["feedback"])

                if exam_result.get("save_error"):
                    st.caption(
                        "⚠️ Scored, but could not save to your account "
                        "— this result will disappear when you log out. "
                        "Download it below if you want to keep it."
                    )
                    # The reason stays visible, just not shouted at the sailor. A bare
                    # `except Exception:` here is what hid error 42501 for weeks: the
                    # RLS policy was rejecting the write because the Supabase client had
                    # lost its session on rerun, and nothing anywhere recorded why.
                    # Whatever breaks this next, it will say so.
                    with st.expander("Technical detail"):
                        st.code(exam_result["save_error"])

                download_lines = [f"{exam_topic} — {score}/{total} ({pct}%)", ""]
                for i, r in enumerate(exam_result["rows"]):
                    download_lines.append(f"Q{i + 1}. {r['question']}")
                    for L in ("a", "b", "c", "d"):
                        download_lines.append(f"   {L.upper()}) {r['answer_' + L]}")
                    download_lines.append(f"   Your answer: {r['picked'] or '(blank)'}")
                    download_lines.append(f"   Correct answer: {r['correct_answer']}")
                    if r.get("explanation"):
                        download_lines.append(f"   {r['explanation']}")
                    src = exam_source_line(r)
                    if src:
                        download_lines.append(f"   {src}")
                    download_lines.append("")
                if exam_result.get("feedback"):
                    download_lines += ["Chief's Feedback:", exam_result["feedback"]]

                col_dl, col_again = st.columns(2)
                with col_dl:
                    st.download_button(
                        "📥 Download Results",
                        data="\n".join(download_lines),
                        file_name="PracticeResults.txt", mime="text/plain",
                        width="stretch",
                    )
                with col_again:
                    if st.button("🔄 Take Another Exam", width="stretch"):
                        for stale in ("exam_questions", "exam_result", "exam_blank_warning"):
                            st.session_state.pop(stale, None)
                        for i in range(50):
                            st.session_state.pop(f"exam_pick_{i}", None)
                        st.rerun()

        if len(st.session_state.score_history) > 0:
            st.divider()
            st.subheader("📈 Your Score History")
            st.caption("Track your improvement over time.")
            # Bars, not a chart, and no separate table underneath — the bars already
            # carry the date, topic, score and percentage.
            score_bars(st.session_state.score_history)


# ── TAB 6: ADVANCEMENT PLANNER ────────────────────────────────────────────────
with tab6:
    st.subheader("📅 Smart Advancement Planner")
    st.caption("Your personalized day-by-day study roadmap based on your scores, weak areas, and time to exam.")

    if not can_access("chief"):
        upgrade_banner("chief", "planner")
    else:
        with st.form("planner_form"):
            col1, col2 = st.columns(2)
            with col1:
                plan_rating = st.selectbox("Your Rating",
                    ["PS", "YN", "IT", "BM", "MM", "EM", "HM", "MA"],
                    key="plan_rating")
                plan_paygrade = st.selectbox("Target Paygrade",
                    ["E5", "E6"], key="plan_paygrade")
            with col2:
                plan_fms = st.number_input("Your Current FMS",
                    min_value=0.0, max_value=100.0, value=45.0, step=0.1,
                    key="plan_fms")
                plan_exam = st.selectbox("Your Exam Date",
                    [f"{CYCLE['exam_e6']:%B} {CYCLE['exam_e6'].day}, "
                     f"{CYCLE['exam_e6']:%Y} (E6)",
                     f"{CYCLE['exam_e5']:%B} {CYCLE['exam_e5'].day}, "
                     f"{CYCLE['exam_e5']:%Y} (E5)"],
                    key="plan_exam")
            plan_weak = st.text_area(
                "Weak topics (from your practice history or your own knowledge):",
                placeholder="e.g. Military Awards, TIR calculations, MILPAY processing",
                key="plan_weak"
            )
            plan_submit = st.form_submit_button(
                "Build My Personalized Study Plan", width="stretch")

        if plan_submit:
            exam_date = (CYCLE["exam_e6"] if "E6" in plan_exam else CYCLE["exam_e5"])
            days_left = (exam_date - datetime.date.today()).days
            days_left = max(days_left, 1)

            planner_prompt = f"""You are a senior {plan_rating} Chief Petty Officer
and advanced exam preparation coach.
You are direct, efficient, and 100% focused on getting this sailor advanced.

Build a personalized day-by-day Navy advancement study plan for:
- Rating: {plan_rating}
- Target Paygrade: {plan_paygrade}
- Current FMS: {plan_fms}
- Days until exam: {days_left}
- Exam date: {plan_exam}
- Known weak areas: {plan_weak if plan_weak else "Not specified — build a balanced plan"}

Structure the plan as follows:
1. ONE honest sentence about where this sailor stands right now
2. Their #1 priority focus area and why
3. A day-by-day study schedule for ALL {days_left} days remaining
   (group into weekly blocks if more than 14 days).
   Each day: specific topic to study, what to do, how long.
4. The top 3 exam traps to avoid for {plan_rating} {plan_paygrade}
5. Final advice for exam week (days -7 through exam day)

Be specific. Reference real {plan_rating} study materials where relevant.
No fluff. Every line earns its place."""

            with st.spinner("Chief is building your study plan..."):
                try:
                    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
                    message = client.messages.create(
                        model="claude-opus-4-5", max_tokens=2500,
                        messages=[{"role": "user", "content": planner_prompt}]
                    )
                    plan_text = message.content[0].text
                    st.subheader("📅 Your Personalized Study Plan")
                    st.markdown(plan_text)
                    st.download_button(
                        "📥 Download My Study Plan",
                        data=plan_text,
                        file_name=f"StudyPlan_{plan_rating}_{plan_paygrade}.txt",
                        mime="text/plain",
                        width="stretch",
                    )
                except Exception as e:
                    st.error("Error building plan: " + str(e))

    st.divider()

    st.subheader("⚓ BBA Strategy Hub")
    st.caption("AI-powered guidance for navigating Billet-Based Advancement. Built for E6s under A2P.")

    if not can_access("chief"):
        upgrade_banner("chief", "bba_hub")
    else:
        st.markdown("""
Use this to get personalized strategy on:
- How to make your billet application competitive
- What to do if you passed but weren't selected
- How to use your preference card wisely
- A2P and CA2P timelines and what to expect
- Anything else about navigating BBA
""")
        with st.form("bba_hub_form"):
            col1, col2 = st.columns(2)
            with col1:
                bba_rating = st.selectbox("Your Rating",
                    ["PS", "YN", "IT", "BM", "MM", "EM", "HM", "MA"],
                    key="bba_rating")
                bba_fms = st.number_input("Your Current or Expected FMS",
                    min_value=0.0, max_value=100.0, value=50.0, step=0.1,
                    key="bba_fms")
            with col2:
                bba_situation = st.selectbox("Your Situation", [
                    "First time going through BBA/A2P",
                    "Passed the exam but not selected for a billet",
                    "Preparing my preference card / billet application",
                    "Trying to understand my competitiveness",
                    "Other / I have a specific question",
                ], key="bba_situation")
            bba_question = st.text_area(
                "Describe your situation or ask your question:",
                placeholder="e.g. I passed E6 last cycle with an FMS of 52 but didn't get a billet. What should I do differently this cycle?",
                key="bba_question"
            )
            bba_submit = st.form_submit_button(
                "Get My BBA Strategy", width="stretch")

        if bba_submit:
            bba_prompt = f"""You are a senior Navy Personnel Specialist (PS) Chief
with 20 years of service and deep expertise in the Billet-Based Advancement
(BBA) system, A2P, and CA2P processes.

You are advising a {bba_rating} sailor on BBA strategy.

Sailor's profile:
- Rating: {bba_rating}
- Current/Expected FMS: {bba_fms}
- Situation: {bba_situation}
- Their question/details: {bba_question}

Provide specific, actionable BBA strategy guidance:
1. Honest assessment of their situation (one sentence)
2. Exactly what they should do RIGHT NOW (this week)
3. How to strengthen their billet application profile
   (record, NEC, quals, eval marks, geography flexibility)
4. What the A2P/CA2P selection process actually looks at
5. Specific next steps with a rough timeline
6. One thing most sailors get wrong about BBA that this sailor
   should avoid

Be direct. Be specific to {bba_rating} rate where possible.
Reference NSIPS, MyNavyHR, and relevant milestones where applicable.
This sailor is counting on you — give them the real talk."""

            with st.spinner("Chief is reviewing your BBA situation..."):
                try:
                    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
                    message = client.messages.create(
                        model="claude-opus-4-5", max_tokens=2000,
                        messages=[{"role": "user", "content": bba_prompt}]
                    )
                    bba_advice = message.content[0].text
                    st.subheader("⚓ Your BBA Strategy")
                    st.markdown(bba_advice)
                    st.download_button(
                        "📥 Download BBA Strategy",
                        data=bba_advice,
                        file_name=f"BBA_Strategy_{bba_rating}.txt",
                        mime="text/plain",
                        width="stretch",
                    )
                except Exception as e:
                    st.error("Error: " + str(e))


# ── TAB 7: MY PROFILE ─────────────────────────────────────────────────────────
with tab7:
    st.subheader("👤 My Profile")
    st.caption("Your personal score history, weak spots, and what to study next.")

    _score_hist = st.session_state.get("score_history", [])

    st.markdown("#### 📋 Exam History")
    if not _score_hist:
        st.info("No exam history yet — complete a Mock Exam session to get started!")
    else:
        _hist_rows = []
        for _entry in _score_hist:
            _pct = _entry.get("pct", 0)
            _hist_rows.append({
                "Date":      _entry.get("date", "—"),
                "Topic":     _entry.get("topic", "—"),
                "Score":     f"{_entry.get('score', 0)}/{_entry.get('total', 0)}",
                "% Correct": _pct,
                "Result":    "✅ Pass" if _pct >= 70 else "❌ Needs Work",
            })
        # st.table, not st.dataframe: a static table with no hover toolbar, no
        # sort handles and nothing to accidentally zoom or fullscreen.
        st.table(pd.DataFrame(_hist_rows))

    st.markdown("---")

    st.markdown("#### ⚠️ Recurring Weak Topics")
    _weak_topics = {}
    for _entry in _score_hist:
        if _entry.get("pct", 100) < 70:
            _t = _entry.get("topic", "").strip()
            if _t:
                _weak_topics[_t] = _weak_topics.get(_t, 0) + 1

    if not _weak_topics:
        if _score_hist:
            st.success("No recurring weak topics — you're performing well across the board!")
        else:
            st.info("Complete a graded session to identify weak topics.")
    else:
        _top_weak = sorted(_weak_topics.items(), key=lambda x: x[1], reverse=True)[:5]
        for _topic, _count in _top_weak:
            st.markdown(f"- **{_topic[:80]}** — scored below 70% {_count} time{'s' if _count > 1 else ''}")

    st.markdown("---")

    st.markdown("#### 📈 Score Trend")
    if not _score_hist:
        st.info("Complete a session to see your score trend.")
    else:
        # Oldest at the top, newest at the bottom, so improvement reads downward.
        score_bars(
            [{**_e, "date": f"Session {i + 1} · {_e.get('date', '—')}"}
             for i, _e in enumerate(_score_hist)]
        )

    st.markdown("---")

    st.markdown("#### 🎯 Recommended Next Study Topics")
    if not _weak_topics:
        st.info("No recommendations yet — finish a graded session first.")
    else:
        _top3 = [t for t, _ in sorted(_weak_topics.items(), key=lambda x: x[1], reverse=True)[:3]]
        for _rec_topic in _top3:
            if st.button(
                f"📚 Study: {_rec_topic[:60]}",
                key=f"profile_study_{hash(_rec_topic) % 99999}",
                width="stretch",
            ):
                st.info(f"Head to the AI Tutor tab and select **{_rec_topic[:60]}** to start your lesson!")
