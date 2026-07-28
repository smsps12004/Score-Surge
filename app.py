import streamlit as st
import pandas as pd
import re
import json
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
    from PIL import Image
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
if st.session_state.access_token and not st.session_state.user:
    try:
        res = supabase.auth.set_session(
            st.session_state.access_token,
            st.session_state.refresh_token
        )
        st.session_state.user = res.user
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

def get_user_tier(user_id: str) -> str:
    try:
        result = (
            supabase.table("profiles")
            .select("tier, trial_start")
            .eq("id", user_id)
            .single()
            .execute()
        )
        profile = result.data
        if not profile:
            return "free"
        tier = profile["tier"]
        if tier == "trial":
            trial_start = datetime.datetime.fromisoformat(
                profile["trial_start"].replace("Z", "+00:00")
            )
            days_elapsed = (datetime.datetime.now(datetime.timezone.utc) - trial_start).days
            if days_elapsed >= 3:
                supabase.table("profiles").update({"tier": "free"}).eq("id", user_id).execute()
                return "free"
        return tier
    except Exception:
        try:
            supabase.table("profiles").insert({
                "id": user_id,
                "tier": "free"
            }).execute()
        except Exception:
            pass
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


def upgrade_banner(required_tier: str):
    label, price, features = UPGRADE_INFO.get(required_tier, ("", "", ""))
    st.warning(f"🔒 **{label} tier required** ({price})\n\nUnlock: {features}")
    if st.session_state.get("user"):
        url = create_checkout_session(required_tier, st.session_state.user.email)
        if url:
            st.link_button(
                f"⬆️ Upgrade to {label} — {price}",
                url=url,
                use_container_width=True,
            )
    else:
        st.info("Log in to upgrade your plan.")


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
            login_submit = st.form_submit_button("Log In", use_container_width=True)

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
                    st.session_state.tier = get_user_tier(res.user.id)
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
                "Create Account & Start Free Trial", use_container_width=True
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
                            supabase.table("profiles").upsert({
                                "id": res.user.id,
                                "tier": "trial",
                                "trial_start": datetime.datetime.utcnow().isoformat()
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
                    supabase.table("profiles").update({"tier": _new_tier}).eq(
                        "id", st.session_state.user.id
                    ).execute()
                    st.session_state.tier = _new_tier
                    st.session_state._payment_success = True
        except Exception:
            pass
    st.query_params.clear()
    st.rerun()

# ── REQUIRE LOGIN ─────────────────────────────────────────────────────────────
if not st.session_state.user:
    show_auth_page()
    st.stop()

# Load tier if missing
if not st.session_state.tier:
    st.session_state.tier = get_user_tier(st.session_state.user.id)

if st.session_state.user and "score_history_loaded" not in st.session_state:
    raw = load_score_history(st.session_state.user.id)
    st.session_state.score_history = [
        {"date": r["date"], "topic": r["topic"],
         "score": r["score"], "total": r["total"], "pct": r["pct"]}
        for r in raw
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
    if st.button("Log Out", use_container_width=True):
        supabase.auth.sign_out()
        for key in ["user", "tier", "access_token", "refresh_token"]:
            st.session_state[key] = None
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
    """Service in paygrade (years) / 5, capped by paygrade."""
    r = FMS_RULES[paygrade]
    return round(min(years_in_paygrade / r["tir_div"], r["tir_max"]), 2)


def compute_fms(paygrade, exam_score, pma, years_in_rate, awards, education, pna):
    """Return (total_fms, ordered breakdown dict) using the official chart."""
    r = FMS_RULES[paygrade]
    parts = {
        "Exam Standard Score": round(min(exam_score, EXAM_MAX), 2),
        "PMA Points":          pma_points(paygrade, pma),
        "Time in Rate":        tir_points(paygrade, years_in_rate),
        "Awards":              round(min(awards, r["awards_max"]), 2),
        "Education":           round(min(education, r["edu_max"]), 2),
        "PNA Points":          round(min(pna, r["pna_max"]), 2),
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
        for num_match in re.finditer(r"\b(\d{1,3}(?:\.\d{1,2})?)\b", segment):
            token = num_match.group(1)
            value = float(token)
            if valid_range is not None:
                lo, hi = valid_range
                if not (lo <= value <= hi):
                    continue
            (decimals if "." in token else integers).append(value)
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


def extract_text_from_upload(uploaded_file):
    raw_text = ""
    suffix = os.path.splitext(uploaded_file.name)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.read())
        tmp_path = tmp.name
    try:
        if uploaded_file.type == "application/pdf":
            if not OCR_PDF_AVAILABLE:
                st.error("PyMuPDF not installed.")
                return ""
            doc = fitz.open(tmp_path)
            for page in doc:
                raw_text += page.get_text()
        else:
            if not OCR_IMAGE_AVAILABLE:
                st.error("pytesseract or Pillow not installed.")
                return ""
            image = Image.open(tmp_path)
            raw_text = pytesseract.image_to_string(image)
    finally:
        os.unlink(tmp_path)
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

    if uploaded_file is not None:
        with st.spinner("Reading your document..."):
            raw_text = extract_text_from_upload(uploaded_file)
        if raw_text.strip():
            extracted_data, missing_fields = parse_ocr_text(raw_text)

            FIELD_LABELS = {
                "exam_score": "Exam Standard Score",
                "pma": "PMA / RSCA PMA",
                "tir": "Service in Paygrade (SIPG)",
                "awards": "Awards Points",
                "education": "Education Points",
                "pna": "PNA Points",
            }
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
                st.caption("Raw text extracted from your document:")
                st.text(raw_text[:2000])
        else:
            st.error("Could not extract text. Try a clearer image or enter values manually.")

    st.subheader("📋 Enter or Edit Your Scores")

    # Outside the form on purpose: changing paygrade must immediately re-scale the
    # PMA field and hide the fields that paygrade does not use.
    paygrade = st.selectbox(
        "Paygrade You Are Competing For", ["E5", "E6", "E7"], index=0,
        help="The PMA formula and every point cap change by paygrade.",
    )
    rules = FMS_RULES[paygrade]
    pma_cap = rules["pma_input_max"]
    pma_label = rules["pma_name"]
    is_e7 = paygrade == "E7"

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
        submitted = st.form_submit_button("📊 Calculate My FMS", use_container_width=True)

    if submitted:
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
            use_container_width=True,
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
                mime="application/pdf", use_container_width=True,
            )


# ── TAB 2: ADVANCEMENT INFO ───────────────────────────────────────────────────
with tab2:
    st.subheader("⏱️ Cycle 272 Countdown")

    today = datetime.date.today()
    deadlines = [
        ("PMK-EE Deadline",    datetime.date(2026, 7, 31)),
        ("ILDC Deadline (E6)", datetime.date(2026, 8, 31)),
        ("E6 Exam Day",        datetime.date(2026, 9, 3)),
        ("E5 Exam Day",        datetime.date(2026, 9, 10)),
    ]

    cols = st.columns(4)
    for i, (label, date) in enumerate(deadlines):
        days_left = (date - today).days
        if days_left < 0:
            status = "✅ Passed"
        elif days_left <= 14:
            status = f"🔴 {days_left} days"
        elif days_left <= 30:
            status = f"🟡 {days_left} days"
        else:
            status = f"🟢 {days_left} days"
        cols[i].metric(label, status)

    st.divider()

    st.subheader("📋 What is Billet-Based Advancement (BBA)?")
    st.caption("Most E6 sailors are now under BBA. Here's what that means for you.")

    with st.expander("Read the plain-English BBA breakdown"):
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

    st.subheader("⭐ CPO / E7 Exam Watch — FY27")
    st.caption("The FY27 CPO board exam is typically held in January–February. The official NAVADMIN has not yet been released.")

    _cpo_est_date = datetime.date(2027, 2, 1)
    _cpo_days_left = (_cpo_est_date - today).days
    if _cpo_days_left < 0:
        _cpo_status = "✅ Est. date passed"
    elif _cpo_days_left <= 14:
        _cpo_status = f"🔴 ~{_cpo_days_left} days"
    elif _cpo_days_left <= 30:
        _cpo_status = f"🟡 ~{_cpo_days_left} days"
    else:
        _cpo_status = f"🟢 ~{_cpo_days_left} days"

    cpo_col1, cpo_col2 = st.columns(2)
    cpo_col1.metric("CPO Exam (est.)", _cpo_status, delta="Feb 1, 2027 estimated")
    cpo_col2.metric("Official NAVADMIN", "⏳ Not yet released")
    st.info(
        "📋 **FY27 CPO Board Exam** — Historically announced Oct–Nov and administered Jan–Feb. "
        "Watch for the official NAVADMIN on [MyNavyHR](https://www.mynavyhr.navy.mil). "
        "This countdown will be updated once the date is confirmed."
    )


# ── TAB 3: AI STUDY GUIDE ─────────────────────────────────────────────────────
with tab3:
    st.subheader("📖 AI Study Guide")
    st.caption("Powered by a stern, coffee-drinking PS Chief who has no time for excuses.")

    if not can_access("petty_officer"):
        upgrade_banner("petty_officer")
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
            sg_submit = st.form_submit_button("Generate My Study Guide", use_container_width=True)

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

                prompt = f"""You are a senior {sg_rating} Chief Petty Officer with 20 years of service.
You drink too much coffee, you have zero patience for excuses, and you genuinely want your sailors to advance.
You are direct, blunt, and efficient. No fluff. No wasted words.
You know NAVADMIN 168/26 (Cycle 272) inside and out.

CYCLE 272 FACTS (NAVADMIN 168/26):
- E6 exam date: 3 September 2026
- E5 exam date: 10 September 2026
- Terminal Eligibility Date: 1 January 2027
- PMK-EE deadline: 31 July 2026
- ILDC deadline: 31 August 2026 (E6 only)
- Min TIR E6: 1 January 2024
- Min TIR E5: 1 January 2026
- PMA window E6: 1 September 2023 to 31 August 2026
- PMA window E5: 1 June 2025 to 31 August 2026
- EAW is authoritative source, must be finalized in NSIPS
- Most active duty E6 ratings now under BBA, advancement via A2P/CA2P

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
Keep it tight. Every sentence must earn its place."""

                with st.spinner("Chief is reviewing your record..."):
                    try:
                        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
                        message = client.messages.create(
                            model="claude-opus-4-5", max_tokens=1500,
                            messages=[{"role": "user", "content": prompt}]
                        )
                        guide_text = message.content[0].text
                        st.subheader("📋 Your Personalized Study Guide")
                        st.markdown(guide_text)
                        st.download_button(
                            "📥 Download Study Guide", data=guide_text,
                            file_name=f"StudyGuide_{sg_rating}_{sg_paygrade}.txt",
                            mime="text/plain", use_container_width=True,
                        )
                    except Exception as e:
                        st.error("Something went wrong: " + str(e))


# ── TAB 4: AI TUTOR ───────────────────────────────────────────────────────────
with tab4:
    st.subheader("🎓 Interactive AI Tutor")
    st.caption("Pick a topic. The Chief will teach it. Ask questions. Get answers. Pass your exam.")

    if not can_access("petty_officer"):
        upgrade_banner("petty_officer")
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

        if st.button("📖 Start Lesson", use_container_width=True):
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
                            mime="text/plain", use_container_width=True,
                        )
                    except Exception as e:
                        st.error("Error: " + str(e))

        if "tutor_history" in st.session_state and len(st.session_state.tutor_history) > 0:
            st.subheader("💬 Ask the Chief a Question")
            st.caption("Type any follow-up question about this topic.")
            sailor_question = st.text_input("Your question",
                                            placeholder="e.g. What happens if a sailor misses the travel claim deadline?")
            if st.button("Ask", use_container_width=True):
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


# ── TAB 5: MOCK EXAM ──────────────────────────────────────────────────────────
with tab5:
    st.subheader("🎯 Full Mock Exam")
    st.caption("Exam-style questions, graded by the Chief. Real explanations. Chief doesn't grade on a curve.")

    if "score_history" not in st.session_state:
        st.session_state.score_history = []

    if not can_access("chief"):
        upgrade_banner("chief")
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
            pq_submit = st.form_submit_button("Generate Mock Exam", use_container_width=True)

        if pq_submit:
            if True:
                if pq_topic not in pq_topics:
                    pq_topic = list(pq_topics.keys())[0]
                bib_refs = pq_topics[pq_topic]["bib"]
                pq_prompt = f"""You are a senior {pq_rating} Chief Petty Officer writing a Navy {pq_rating} {pq_paygrade} advancement exam practice set.
Generate exactly {pq_num} multiple choice practice questions for:
- Topic: {pq_topic}
- Rating / Paygrade: {pq_rating} advancing to {pq_paygrade}
- Governing References: {bib_refs}
Format each question EXACTLY like this:
Q1: [Question text]
A) [Option]
B) [Option]
C) [Option]
D) [Option]
ANSWER: [Letter]
EXPLANATION: [2-3 sentences explaining why this is correct and what regulation supports it. Then on a new line add: 📖 Source: [Manual name, Chapter/Section X] — for example: NAVEDTRA 14257, Chapter 4 or MILPERSMAN 1430-010, Section 2. Base the source on the actual Navy training manual or instruction that covers this topic for the sailor's rating and paygrade. If you are not certain of the exact chapter, provide the most accurate manual name and your best chapter estimate.]
Make the questions realistic exam difficulty. Include tricky distractors. Reference specific regulations. No fluff."""

                with st.spinner("Chief is writing your exam..."):
                    try:
                        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
                        message = client.messages.create(
                            model="claude-opus-4-5", max_tokens=2000,
                            messages=[{"role": "user", "content": pq_prompt}]
                        )
                        st.session_state.practice_questions = message.content[0].text

                    except Exception as e:
                        st.error("Error: " + str(e))

        if "practice_questions" in st.session_state:
            st.subheader("📝 Your Practice Questions")
            st.markdown(st.session_state.practice_questions)
            st.subheader("✍️ Submit Your Answers")
            sailor_answers = st.text_area("Type your answers (e.g. Q1: B, Q2: A)", height=150)
            if st.button("Grade My Answers", use_container_width=True):
                if sailor_answers:
                    with st.spinner("Chief is grading..."):
                        try:
                            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
                            grade_prompt = f"""You are a {pq_rating} Chief grading a sailor's practice exam.
Questions: {st.session_state.practice_questions}
Sailor's answers: {sailor_answers}
Grade each answer. State correct or incorrect. Explain the right answer. Reference the regulation.
For each question, after the explanation add a new line formatted exactly like this: 📖 Source: [Manual name, Chapter X] — for example: NAVEDTRA 14257, Chapter 4 or MILPERSMAN 1430-010, Section 2. Base the source on the actual Navy training manual or instruction that covers this topic. If you are not certain of the exact chapter, provide the most accurate manual name and your best chapter estimate.
One line of honest feedback. Be direct. No fluff.
End with a line in exactly this format: Final Score: X/Y"""
                            message = client.messages.create(
                                model="claude-opus-4-5", max_tokens=1500,
                                messages=[{"role": "user", "content": grade_prompt}]
                            )
                            grade_result = message.content[0].text
                            st.subheader("📊 Your Grade")
                            st.markdown(grade_result)
                            import re as re2
                            score_match = re2.search(r'Final Score:\s*(\d+)/(\d+)', grade_result) or \
                                          re2.search(r'(\d+)\s*out\s*of\s*(\d+)', grade_result)
                            if score_match:
                                scored = int(score_match.group(1))
                                total = int(score_match.group(2))
                                st.session_state.score_history.append({
                                    "date": datetime.date.today().strftime("%b %d"),
                                    "topic": pq_topic, "score": scored, "total": total,
                                    "pct": round((scored / total) * 100),
                                })
                                if st.session_state.user:
                                    try:
                                        supabase.table("score_history").insert({
                                            "user_id": st.session_state.user.id,
                                            "date": datetime.date.today().strftime("%b %d"),
                                            "topic": pq_topic,
                                            "score": scored,
                                            "total": total,
                                            "pct": round((scored / total) * 100),
                                        }).execute()
                                    except Exception:
                                        pass
                            st.download_button(
                                "📥 Download Practice Results",
                                data=f"QUESTIONS:\n{st.session_state.practice_questions}\n\nANSWERS:\n{sailor_answers}\n\nGRADE:\n{grade_result}",
                                file_name="PracticeResults.txt", mime="text/plain",
                                use_container_width=True,
                            )
                        except Exception as e:
                            st.error("Error: " + str(e))

        if len(st.session_state.score_history) > 0:
            st.divider()
            st.subheader("📈 Your Score History")
            st.caption("Track your improvement over time.")
            history_df = pd.DataFrame(st.session_state.score_history)
            st.line_chart(history_df.set_index("date")["pct"])
            st.dataframe(
                history_df[["date", "topic", "score", "total", "pct"]].rename(columns={
                    "date": "Date", "topic": "Topic", "score": "Score",
                    "total": "Total", "pct": "% Correct",
                }),
                use_container_width=True,
            )


# ── TAB 6: ADVANCEMENT PLANNER ────────────────────────────────────────────────
with tab6:
    st.subheader("📅 Smart Advancement Planner")
    st.caption("Your personalized day-by-day study roadmap based on your scores, weak areas, and time to exam.")

    if not can_access("chief"):
        upgrade_banner("chief")
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
                    ["September 3, 2026 (E6)", "September 10, 2026 (E5)"],
                    key="plan_exam")
            plan_weak = st.text_area(
                "Weak topics (from your practice history or your own knowledge):",
                placeholder="e.g. Military Awards, TIR calculations, MILPAY processing",
                key="plan_weak"
            )
            plan_submit = st.form_submit_button(
                "Build My Personalized Study Plan", use_container_width=True)

        if plan_submit:
            exam_date = (datetime.date(2026, 9, 3) if "E6" in plan_exam
                         else datetime.date(2026, 9, 10))
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
                        use_container_width=True,
                    )
                except Exception as e:
                    st.error("Error building plan: " + str(e))

    st.divider()

    st.subheader("⚓ BBA Strategy Hub")
    st.caption("AI-powered guidance for navigating Billet-Based Advancement. Built for E6s under A2P.")

    if not can_access("chief"):
        upgrade_banner("chief")
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
                "Get My BBA Strategy", use_container_width=True)

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
                        use_container_width=True,
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
        st.dataframe(pd.DataFrame(_hist_rows), use_container_width=True, hide_index=True)

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
        _trend_df = pd.DataFrame([
            {"Session": i + 1, "Score %": _entry.get("pct", 0)}
            for i, _entry in enumerate(_score_hist)
        ]).set_index("Session")
        st.line_chart(_trend_df)

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
                use_container_width=True,
            ):
                st.info(f"Head to the AI Tutor tab and select **{_rec_topic[:60]}** to start your lesson!")
