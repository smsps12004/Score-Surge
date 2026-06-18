import streamlit as st
import pandas as pd
import re
import json
import tempfile
import os
import base64
import datetime
import time
from fpdf import FPDF
import anthropic

try:
    import fitz
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

# PAGE CONFIG must come before any other Streamlit call.
st.set_page_config(page_title="Score Surge", page_icon="⚓", layout="centered")

# WARDROOM DUSK THEME POLISH (Batch 9) — the base palette comes from
# .streamlit/config.toml; this CSS adds shape and texture the config can't
# reach: soft pill buttons, rounded form fields, brass-accent headings.
st.markdown("""
<style>
/* Soft pill buttons — brass fill, generous padding, modern hover */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
    border-radius: 100px;
    padding: 0.55rem 1.5rem;
    font-weight: 500;
    background: #c9a96e;
    color: #142b50;
    border: 1.5px solid #c9a96e;
    transition: background 0.15s, border-color 0.15s, transform 0.1s;
}
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {
    background: #d6b87a;
    border-color: #d6b87a;
    color: #142b50;
}
.stButton > button:active, .stDownloadButton > button:active, .stFormSubmitButton > button:active {
    transform: scale(0.98);
}
.stButton > button:focus, .stDownloadButton > button:focus, .stFormSubmitButton > button:focus {
    box-shadow: 0 0 0 3px rgba(201, 169, 110, 0.25);
    color: #142b50;
}

/* Form inputs — rounded corners with subtle brass-tinted border */
.stTextInput input, .stNumberInput input, .stTextArea textarea {
    border-radius: 8px;
    border: 0.5px solid rgba(201, 169, 110, 0.25);
}
.stSelectbox div[data-baseweb="select"] > div {
    border-radius: 8px;
    border: 0.5px solid rgba(201, 169, 110, 0.25);
}
.stFileUploader section {
    border-radius: 10px;
    border: 1px dashed rgba(201, 169, 110, 0.4);
}

/* Title and section headings — brass for hierarchy */
h1, h2, h3 {
    color: #c9a96e;
    font-weight: 500;
}

/* Status / info / warning / success boxes — brass left-border accent */
.stAlert {
    border-radius: 10px;
    border-left: 3px solid #c9a96e;
}

/* Metric tiles — soft rounded surface so result numbers sit on a clean card */
[data-testid="stMetric"] {
    background: #1d3a66;
    border-radius: 10px;
    padding: 1rem;
}

/* Expander headers — brass accent on hover for the AI Tutor / PNA cards */
.streamlit-expanderHeader {
    border-radius: 8px;
}

/* === BATCH 9.1 HOTFIX === */

/* Headings — higher specificity so brass actually applies through Streamlit's wrappers */
h1, h2, h3,
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3,
.stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
    color: #c9a96e !important;
    font-weight: 500;
}

/* Alert text — force readable cream on Streamlit's blue/yellow/red tinted backgrounds */
.stAlert, [data-testid="stAlert"] {
    border-radius: 10px !important;
    border-left: 3px solid #c9a96e !important;
}
[data-testid="stAlert"] [data-testid="stMarkdownContainer"] *,
.stAlert [data-testid="stMarkdownContainer"] *,
.stAlert p, .stAlert strong, .stAlert span {
    color: #f0e6d2 !important;
}

/* File uploader drop zone — pops as a clear "drop here" target */
[data-testid="stFileUploaderDropzone"] {
    border-radius: 12px;
    border: 1.5px dashed #c9a96e;
    background: rgba(201, 169, 110, 0.05);
    padding: 1.25rem;
}
[data-testid="stFileUploaderDropzone"] button {
    border-radius: 100px;
    background: #c9a96e;
    color: #142b50;
    border: 1.5px solid #c9a96e;
    font-weight: 500;
    padding: 0.4rem 1.2rem;
}
[data-testid="stFileUploaderDropzone"] button:hover {
    background: #d6b87a;
    border-color: #d6b87a;
    color: #142b50;
}
</style>
""", unsafe_allow_html=True)

# PASSWORD GATE
if not st.session_state.get("authenticated"):
    _, col, _ = st.columns([1, 2, 1])
    with col:
        pwd = st.text_input("Password", type="password", label_visibility="collapsed", placeholder="Password")
        if st.button("Enter", use_container_width=True):
            if pwd == st.secrets["APP_PASSWORD"]:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    st.stop()

# API KEY GUARD — show a friendly message instead of a Python stack trace
# if the secret is missing or rotated. Without the key the app can't function,
# so we stop here cleanly.
try:
    _api_key = st.secrets["ANTHROPIC_API_KEY"]
except (KeyError, FileNotFoundError):
    _api_key = None

if not _api_key:
    st.title("⚓ Score Surge | by Strategic Sailor")
    st.error(
        "⚠️ **Score Surge is temporarily unavailable.** "
        "The AI service connection is missing. The site owner has been notified — "
        "please check back in a few minutes."
    )
    st.caption("(Admin: set ANTHROPIC_API_KEY in Streamlit Cloud secrets to restore service.)")
    st.stop()

client = anthropic.Anthropic(api_key=_api_key)

# CONSTANTS — FMS formula per BUPERSINST 1430.16G (E4-E6 FMS Chart)
# E5: FMS = SS + (PMA*80 - 256) + SIPG/5 (cap 2) + Awards (cap 10) + Education (0/2/4) + PNA (cap 9). Max 169.
# E6: FMS = SS + (RSCA_PMA*30 - 60) + SIPG/5 (cap 3) + Awards (cap 12) + Education (0/2/4) + PNA (cap 9). Max 222.
MAX_FMS = {"E5": 169.0, "E6": 222.0}
AWARDS_MAX = {"E5": 10.0, "E6": 12.0}
SIPG_POINTS_MAX = {"E5": 2.0, "E6": 3.0}

# NAVY RATES — active-duty enlisted ratings. PS has a hand-curated topic library;
# all others use freeform topic mode where the sailor types what they want to study.
# Format: (rate_code, full_name) so the dropdown shows "PS — Personnel Specialist".
NAVY_RATES = [
    ("AB",  "Aviation Boatswain's Mate"),
    ("ABE", "Aviation Boatswain's Mate (Equipment)"),
    ("ABF", "Aviation Boatswain's Mate (Fuels)"),
    ("ABH", "Aviation Boatswain's Mate (Handling)"),
    ("AC",  "Air Traffic Controller"),
    ("AD",  "Aviation Machinist's Mate"),
    ("AE",  "Aviation Electrician's Mate"),
    ("AG",  "Aerographer's Mate"),
    ("AM",  "Aviation Structural Mechanic"),
    ("AME", "Aviation Structural Mechanic (Safety Equipment)"),
    ("AO",  "Aviation Ordnanceman"),
    ("AS",  "Aviation Support Equipment Technician"),
    ("AT",  "Aviation Electronics Technician"),
    ("AWF", "Naval Aircrewman Mechanical"),
    ("AWO", "Naval Aircrewman Operator"),
    ("AWR", "Naval Aircrewman Tactical-Helicopter"),
    ("AWS", "Naval Aircrewman Helicopter"),
    ("AWV", "Naval Aircrewman Avionics"),
    ("AZ",  "Aviation Maintenance Administrationman"),
    ("BM",  "Boatswain's Mate"),
    ("BU",  "Builder"),
    ("CE",  "Construction Electrician"),
    ("CM",  "Construction Mechanic"),
    ("CS",  "Culinary Specialist"),
    ("CSS", "Culinary Specialist (Submarine)"),
    ("CTI", "Cryptologic Technician (Interpretive)"),
    ("CTM", "Cryptologic Technician (Maintenance)"),
    ("CTN", "Cryptologic Technician (Networks)"),
    ("CTR", "Cryptologic Technician (Collection)"),
    ("CTT", "Cryptologic Technician (Technical)"),
    ("DC",  "Damage Controlman"),
    ("EA",  "Engineering Aide"),
    ("EM",  "Electrician's Mate"),
    ("EMN", "Electrician's Mate (Nuclear)"),
    ("EN",  "Engineman"),
    ("EO",  "Equipment Operator"),
    ("EOD", "Explosive Ordnance Disposal"),
    ("ET",  "Electronics Technician"),
    ("ETN", "Electronics Technician (Nuclear)"),
    ("ETR", "Electronics Technician (Communications)"),
    ("ETV", "Electronics Technician (Submarine - Navigation)"),
    ("FC",  "Fire Controlman"),
    ("FCA", "Fire Controlman (Aegis)"),
    ("FT",  "Fire Control Technician"),
    ("GM",  "Gunner's Mate"),
    ("GSE", "Gas Turbine Systems Technician (Electrical)"),
    ("GSM", "Gas Turbine Systems Technician (Mechanical)"),
    ("HM",  "Hospital Corpsman"),
    ("HT",  "Hull Maintenance Technician"),
    ("IC",  "Interior Communications Electrician"),
    ("IS",  "Intelligence Specialist"),
    ("IT",  "Information Systems Technician"),
    ("ITS", "Information Systems Technician (Submarine)"),
    ("LN",  "Legalman"),
    ("LS",  "Logistics Specialist"),
    ("LSS", "Logistics Specialist (Submarine)"),
    ("MA",  "Master-at-Arms"),
    ("MC",  "Mass Communication Specialist"),
    ("MM",  "Machinist's Mate"),
    ("MMA", "Machinist's Mate (Auxiliary - Submarine)"),
    ("MMN", "Machinist's Mate (Nuclear)"),
    ("MMW", "Machinist's Mate (Weapons - Submarine)"),
    ("MN",  "Mineman"),
    ("MR",  "Machinery Repairman"),
    ("MT",  "Missile Technician"),
    ("MU",  "Musician"),
    ("NC",  "Navy Counselor"),
    ("ND",  "Navy Diver"),
    ("OS",  "Operations Specialist"),
    ("PR",  "Aircrew Survival Equipmentman"),
    ("PS",  "Personnel Specialist"),
    ("QM",  "Quartermaster"),
    ("RP",  "Religious Program Specialist"),
    ("RS",  "Retail Services Specialist"),
    ("SB",  "Special Warfare Boat Operator"),
    ("SO",  "Special Warfare Operator (SEAL)"),
    ("STG", "Sonar Technician (Surface)"),
    ("STS", "Sonar Technician (Submarine)"),
    ("SW",  "Steelworker"),
    ("TM",  "Torpedoman's Mate"),
    ("UT",  "Utilitiesman"),
    ("YN",  "Yeoman"),
    ("YNS", "Yeoman (Submarine)"),
]
NAVY_RATE_LABELS = [f"{code} — {name}" for code, name in NAVY_RATES]
NAVY_RATE_CODE_FROM_LABEL = {f"{code} — {name}": code for code, name in NAVY_RATES}

# Rates with hand-curated PS_TOPICS-style libraries.
# Add a rate code here when you build out its full topic dictionary.
TUTOR_CURATED_RATINGS = {"PS"}

# UPLOAD SAFETY — keep token costs predictable and prevent abuse on the public app.
MAX_UPLOAD_MB = 8       # Profile sheets fit easily under this; Streamlit default is 200 MB.
MAX_PDF_PAGES = 3       # Profile sheets are 1-2 pages. Cap protects API spend on bad uploads.

# AI RATE LIMIT — protects API spend on a free, public app.
# Counts every Claude call (vision, study guide, lesson, Q&A, practice, grading)
# in a single browser session. Resets when the sailor refreshes or returns later.
MAX_AI_CALLS_PER_SESSION = 20


def check_ai_quota():
    """
    Returns True if the sailor still has AI calls left in this session.
    Returns False AND shows a friendly warning if the cap is hit.
    Increments the counter on True so callers don't have to.
    """
    used = st.session_state.get("ai_calls_used", 0)
    if used >= MAX_AI_CALLS_PER_SESSION:
        st.warning(
            f"🛑 You've used your **{MAX_AI_CALLS_PER_SESSION} AI generations** for this session. "
            "Refresh the page to start a new session — your FMS calculator and PDF download still work."
        )
        return False
    st.session_state["ai_calls_used"] = used + 1
    return True

# CURRENT CYCLE CONFIG — single source of truth.
# When the next NAVADMIN drops, update ONLY this block. All cycle dates,
# TIR windows, PMA windows, and prompt facts derive from this dict.
CURRENT_CYCLE = {
    "number": 271,
    "navadmin": "NAVADMIN 008/26",
    "title": "March 2026",
    "deadlines": [
        ("PMK-EE Deadline",     datetime.date(2026, 1, 31)),
        ("ILDC Deadline (E6)",  datetime.date(2026, 2, 28)),
        ("E6 Exam Day",         datetime.date(2026, 3, 5)),
        ("E5 Exam Day",         datetime.date(2026, 3, 12)),
    ],
    # Eligibility windows — used by the AI Chief prompt so it never quotes stale dates.
    "ted":           datetime.date(2026, 7, 1),    # Terminal Eligibility Date
    "tir_e5":        datetime.date(2025, 7, 1),    # Min Time-in-Rate for E5
    "tir_e6":        datetime.date(2023, 7, 1),    # Min Time-in-Rate for E6
    "pma_window_e5": (datetime.date(2024, 12, 1), datetime.date(2026, 2, 28)),
    "pma_window_e6": (datetime.date(2023, 3, 1),  datetime.date(2026, 2, 28)),
    # Approximate when per-rate cut scores / quotas are expected to be released.
    # Used to drive the "awaiting results" banner so the app doesn't tell sailors
    # the cycle is "closed" the day after the last exam.
    "selection_release_estimate": datetime.date(2026, 6, 30),
    "awaiting_results_note": "Exams are done — quotas and per-rate cut scores haven't dropped yet. Hang tight, results typically post late June.",
    "next_cycle_note": "Cycle 271 has closed. Watch MyNavyHR for the next cycle's NAVADMIN.",
}


def _fmt_cycle_date(d):
    """Format a date as '5 March 2026' for use in AI prompts and headers."""
    return d.strftime("%-d %B %Y") if hasattr(datetime.date, "strftime") else str(d)


def _exam_date(label_substr):
    """Pull a deadline date out of CURRENT_CYCLE by label substring. Returns None if absent."""
    for label, date in CURRENT_CYCLE["deadlines"]:
        if label_substr.lower() in label.lower():
            return date
    return None

st.title("⚓ Score Surge | by Strategic Sailor")

with st.sidebar:
    st.markdown("### ⚓ Strategic Sailor")
    st.link_button(
        "💬 Join the Community",
        "https://discord.gg/q3advbdPf",
        use_container_width=True
    )

# Cycle status — compute once, drives the header tone and the countdown tiles below.
# Three phases:
#   "open"             — at least one official deadline still ahead (PMK-EE / ILDC / exam day).
#   "awaiting_results" — all exam dates have passed, but selection results haven't dropped yet.
#   "closed"           — selection results have been released; cycle is fully done.
today = datetime.date.today()
upcoming_deadlines = [
    (label, date) for (label, date) in CURRENT_CYCLE["deadlines"] if (date - today).days >= 0
]
selection_release = CURRENT_CYCLE.get("selection_release_estimate")

if upcoming_deadlines:
    cycle_phase = "open"
elif selection_release and today < selection_release:
    cycle_phase = "awaiting_results"
else:
    cycle_phase = "closed"

# Honest header — adapts to which phase the cycle is in.
if cycle_phase == "open":
    st.markdown(
        f"""
Your Navy advancement engine. Calculate your FMS, build your study plan, and advance.

**Cycle {CURRENT_CYCLE['number']} — {CURRENT_CYCLE['title']} ({CURRENT_CYCLE['navadmin']}).** Selection cutoffs are published per rate after each cycle. There is no fixed minimum FMS — your standing depends on your rate's specific cutoff and quotas.
"""
    )
elif cycle_phase == "awaiting_results":
    st.markdown(
        f"""
Your Navy advancement engine. Exams are in the books — now we wait on quotas and per-rate cut scores.

**Cycle {CURRENT_CYCLE['number']} ({CURRENT_CYCLE['title']}) — exams complete, selection results pending.** Keep training while you wait — the next cycle starts before the dust settles on this one.
"""
    )
else:  # closed
    st.markdown(
        f"""
Your Navy advancement engine. Calculate your FMS, build your study plan, and stay sharp between cycles.

**Cycle {CURRENT_CYCLE['number']} ({CURRENT_CYCLE['title']}) has closed.** Use this app to keep training until the next NAVADMIN drops. Selection cutoffs are published per rate after each cycle.
"""
    )

# CYCLE STATUS — surfaced at the top so sailors see where they stand immediately.
st.subheader(f"⏱️ Cycle {CURRENT_CYCLE['number']} Status")
if cycle_phase == "open":
    cols = st.columns(len(upcoming_deadlines))
    for i, (label, date) in enumerate(upcoming_deadlines):
        days_left = (date - today).days
        if days_left == 0:
            status = "🔴 EXAM DAY"
        elif days_left <= 14:
            status = f"🔴 {days_left} days"
        elif days_left <= 30:
            status = f"🟡 {days_left} days"
        else:
            status = f"🟢 {days_left} days"
        cols[i].metric(label, status)
elif cycle_phase == "awaiting_results":
    days_until_results = (selection_release - today).days
    st.info(
        f"📋 **{CURRENT_CYCLE['awaiting_results_note']}** "
        f"Estimated release: **{_fmt_cycle_date(selection_release)}** (~{days_until_results} days). "
        "Use this time to keep your edge — review weak areas in Practice Mode and prep for the next cycle."
    )
else:  # closed
    st.info(
        f"📋 **{CURRENT_CYCLE['next_cycle_note']}** "
        "In the meantime, keep using the AI Study Guide and Practice Question Mode to stay sharp."
    )

st.divider()

DEFAULT_VALUES = {
    "exam_score": 0.0,
    "pma": 0.0,
    "sipg_months": 0.0,
    "awards": 0.0,
    "education": 0.0,
    "pna": 0.0,
}

VISION_PROMPT = (
    "This is a Navy advancement profile sheet. "
    "Extract and return ONLY a JSON object with these exact keys: "
    "exam_score, pma, sipg_months, awards, education, pna. "
    "exam_score = Standard Score from the exam (number, typically between 20 and 80). "
    "pma = Performance Mark Average for E5, or Reporting Senior's Cumulative Average (RSCA PMA) for E6 (decimal between 1.0 and 5.0). "
    "sipg_months = Service in Paygrade in months. If the sheet shows years, multiply by 12. "
    "awards = Awards points (decimal). "
    "education = Education points. Should be 0, 2 (associate's degree), or 4 (bachelor's or higher). "
    "pna = PNA points. Look carefully for ANY of these label variations on the sheet: "
    "'PNA', 'PNA Points', 'PNA Pts', 'Pass Not Advanced', 'Passed Not Advanced', "
    "'PNA Score', 'P.N.A.', or any cell, column, or row with 'PNA' in the heading. "
    "It typically appears in the FMS component breakdown section near Awards, Education, and SIPG, "
    "and is a decimal number between 0.0 and 9.0. "
    "IMPORTANT: If the sheet shows '0', '0.0', or '0.00' for PNA, return 0 — zero is a valid value, NOT missing. "
    "Only return null for pna if the field is truly absent or completely unreadable. "
    "Use null for any other value you cannot clearly read. Return ONLY valid JSON, no other text."
)


def pdf_safe(s):
    """
    fpdf's default Arial font only supports Latin-1.
    Strip/replace any character outside that range so the PDF never crashes
    on smart quotes, em-dashes, accented characters, emoji, etc.
    """
    if s is None:
        return ""
    return str(s).encode("latin-1", errors="replace").decode("latin-1")


def safe_filename(s, fallback="sailor"):
    """
    Strip anything that isn't safe in a filename (slashes, quotes, control chars, etc.).
    Keeps letters, numbers, dash, underscore, period. Collapses spaces to underscores.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", (s or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("._-")
    return cleaned or fallback


def first_text_block(msg):
    """
    Safely pull the first text block out of a Claude response.
    Don't assume content[0] is text — Claude can return tool_use, thinking, etc.
    Returns the text string, or "" if no text block is present.
    """
    for block in getattr(msg, "content", []) or []:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""


def parse_claude_json(raw_text):
    """
    Pull a JSON object out of Claude's text response.
    Tolerates markdown code fences (```json ... ```) and stray prose around the JSON.
    Raises json.JSONDecodeError if no parseable JSON is found.
    """
    text = raw_text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()
    # Locate the JSON object inside any surrounding prose
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def extract_fields_from_upload(uploaded_file):
    """
    Extract FMS fields from a profile sheet using Claude vision.
    PDFs are rendered to images via PyMuPDF (capped at MAX_PDF_PAGES).
    Returns (fields_dict, missing_fields_list, method_label).
    The caller is responsible for enforcing file-size limits before calling.
    """
    all_keys = list(DEFAULT_VALUES.keys())
    suffix = os.path.splitext(uploaded_file.name)[1].lower()
    file_bytes = uploaded_file.read()
    images_b64 = []  # list of (b64_data, media_type)

    if suffix == ".pdf":
        if not PDF_AVAILABLE:
            st.error("PyMuPDF not installed. Run: pip install pymupdf — or enter values manually.")
            return {}, all_keys, "error"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            doc = fitz.open(tmp_path)
            total_pages = len(doc)
            for i, page in enumerate(doc):
                if i >= MAX_PDF_PAGES:
                    break
                pix = page.get_pixmap(dpi=200)
                images_b64.append((
                    base64.standard_b64encode(pix.tobytes("png")).decode(),
                    "image/png",
                ))
            if total_pages > MAX_PDF_PAGES:
                st.info(
                    f"📄 PDF has {total_pages} pages — only the first {MAX_PDF_PAGES} were read. "
                    "Profile sheets typically fit in 1–2 pages."
                )
        finally:
            os.unlink(tmp_path)
        method = "claude-vision-pdf"
    else:
        media_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
        media_type = media_types.get(suffix, "image/jpeg")
        images_b64.append((base64.standard_b64encode(file_bytes).decode(), media_type))
        method = "claude-vision-image"

    if not images_b64:
        return {}, all_keys, "error"

    content = []
    for b64_data, media_type in images_b64:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64_data},
        })
    content.append({"type": "text", "text": VISION_PROMPT})

    # Bail out cleanly if the sailor has hit the per-session AI cap.
    if not check_ai_quota():
        return {}, all_keys, "rate-limited"

    try:
        msg = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=512,  # Bumped from 256 — vision sometimes wraps JSON in extra prose.
            messages=[{"role": "user", "content": content}],
        )
        # Find the first text block in Claude's response (don't assume content[0] is text)
        raw_text = ""
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                raw_text = block.text
                break
        if not raw_text:
            st.error("Claude returned no readable text. Try uploading a clearer image.")
            return {}, all_keys, "error"

        fields = parse_claude_json(raw_text)

        # Track missing fields = those Claude returned as null/missing or couldn't be parsed.
        clean = {}
        missing = []
        for k in all_keys:
            v = fields.get(k)
            if v is None:
                missing.append(k)
                continue
            try:
                clean[k] = float(v)
            except (TypeError, ValueError):
                missing.append(k)
        return clean, missing, method

    except json.JSONDecodeError:
        st.error("Claude's response wasn't valid JSON. Try uploading a clearer image, or fill the fields in manually.")
        return {}, all_keys, "error"
    except Exception as e:
        st.error(f"Couldn't read the profile sheet: {e}")
        return {}, all_keys, "error"


# OCR UPLOAD SECTION
st.subheader("📤 Upload Profile Sheet (Optional)")
uploaded_file = st.file_uploader(
    "Upload your Navy Profile Sheet — image or PDF",
    type=["png", "jpg", "jpeg", "pdf"],
    help="The app will try to read your scores automatically. You can always edit them below.",
)

extracted_data = DEFAULT_VALUES.copy()

if uploaded_file is not None:
    file_size_mb = uploaded_file.size / (1024 * 1024)
    if file_size_mb > MAX_UPLOAD_MB:
        st.error(
            f"That file is {file_size_mb:.1f} MB, which is over the {MAX_UPLOAD_MB} MB limit. "
            "Please upload a smaller image or PDF, or enter your values manually below."
        )
    else:
        with st.spinner("Reading your profile sheet with Claude vision..."):
            claude_fields, missing_fields, read_method = extract_fields_from_upload(uploaded_file)

        if read_method == "rate-limited":
            # Quota helper already showed the warning. Nudge sailor to fill the form by hand.
            st.info("Profile sheet not read — please enter your scores manually below.")
        elif read_method != "error" and claude_fields is not None:
            for field, val in claude_fields.items():
                if field in DEFAULT_VALUES and val is not None:
                    extracted_data[field] = val

            method_labels = {
                "claude-vision-pdf":   "✅ Profile sheet read via Claude vision (PDF)",
                "claude-vision-image": "✅ Profile sheet read via Claude vision (image)",
            }
            st.success(
                method_labels.get(read_method, "✅ Document read successfully") +
                (" — all fields found!" if not missing_fields else "")
            )
            if missing_fields:
                st.warning(
                    "Could not auto-detect: **" + ", ".join(missing_fields) + "**. "
                    "Default values used — please fill them in manually."
                )


# PAYGRADE SELECTOR — drives which formula applies (E5 vs E6 use different weights)
st.subheader("📋 Enter or Edit Your Scores")
paygrade = st.selectbox(
    "Your Paygrade",
    ["E5", "E6"],
    help="E5 uses regular PMA. E6 uses RSCA PMA. The formula and point caps differ by paygrade.",
)

# Education dropdown helpers
EDUCATION_OPTIONS = [0, 2, 4]
EDUCATION_LABELS = {0: "None (0 pts)", 2: "Associate's degree (2 pts)", 4: "Bachelor's or higher (4 pts)"}

def education_default_index(extracted_value):
    """Map an extracted education number to the closest dropdown index."""
    try:
        v = float(extracted_value)
    except (TypeError, ValueError):
        return 0
    if v >= 3:
        return 2  # Bachelor's
    if v >= 1:
        return 1  # Associate's
    return 0

with st.form("fms_form"):
    sailor_name = st.text_input("Sailor Name / Rate", value="SailorX")

    col1, col2 = st.columns(2)

    pma_label = "RSCA PMA" if paygrade == "E6" else "PMA (Eval Average)"
    pma_help = (
        "Reporting Senior's Cumulative Average — used for E6 advancement."
        if paygrade == "E6"
        else "Performance Mark Average — your eval average."
    )
    awards_max = AWARDS_MAX[paygrade]

    with col1:
        exam_score = st.number_input(
            "Exam Standard Score",
            min_value=0.0, max_value=99.0,
            value=min(99.0, max(0.0, float(extracted_data["exam_score"]))),
            step=0.5,
            help="Your raw exam score from the profile sheet. Standard scores typically run 40–80; high performers can score higher.",
        )
        pma = st.number_input(
            pma_label,
            min_value=0.0, max_value=5.0,
            value=min(5.0, max(0.0, float(extracted_data["pma"]))),
            step=0.01,
            help=pma_help,
        )
        sipg_months = st.number_input(
            "Service in Paygrade (months)",
            min_value=0.0, max_value=240.0,
            value=min(240.0, max(0.0, float(extracted_data["sipg_months"]))),
            step=1.0,
            help="How many months you've been at your current paygrade. SIPG/5 with a cap of 2 pts (E5) or 3 pts (E6).",
        )
        # Catch the common months-vs-years mistake. A genuinely-low SIPG (1–11 months)
        # is real for fresh promotions, but it's the same range a sailor would type
        # if they confused years with months. Show a friendly heads-up either way.
        if 0 < sipg_months < 12:
            st.warning(
                f"Heads up — that's only **{sipg_months:.0f} month{'s' if sipg_months != 1 else ''}** in paygrade. "
                "If you meant *years*, multiply by 12 (e.g., 5 years = 60 months)."
            )

    with col2:
        awards = st.number_input(
            f"Awards Points (max {awards_max:.0f} for {paygrade})",
            min_value=0.0, max_value=awards_max,
            value=min(awards_max, max(0.0, float(extracted_data["awards"]))),
            step=0.5,
        )
        education = st.selectbox(
            "Education",
            EDUCATION_OPTIONS,
            index=education_default_index(extracted_data["education"]),
            format_func=lambda x: EDUCATION_LABELS[x],
            help="Per BUPERSINST 1430.16G: 0, 2 (associate's), or 4 (bachelor's or higher).",
        )
        pna = st.number_input(
            "PNA Points",
            min_value=0.0, max_value=9.0,
            value=min(9.0, max(0.0, float(extracted_data["pna"]))),
            step=0.5,
            help="Top 25% in both SS and PMA each cycle you pass but aren't advanced earns PNA points: 1.5 pts/cycle for E5, 1 pt/cycle for E6. Cap is 9.",
        )

    st.markdown("---")
    cut_score = st.number_input(
        "Enter your cycle cut score (Optional — from your rate's NAVADMIN)",
        min_value=0.0, max_value=300.0,
        value=0.0,
        step=0.5,
        help="Your rate's published cut score from a recent advancement NAVADMIN. Leave at 0 to skip — we'll only compare if you enter one.",
    )

    submitted = st.form_submit_button("📊 Calculate My FMS", use_container_width=True)


# CALCULATION & RESULTS
# Snapshot inputs on submit so results persist across reruns (e.g. when sailor
# clicks an AI button below — that triggers a Streamlit rerun and would otherwise
# wipe the results because `submitted` flips back to False).
if submitted:
    st.session_state["fms_inputs"] = {
        "sailor_name": sailor_name,
        "paygrade": paygrade,
        "exam_score": exam_score,
        "pma": pma,
        "sipg_months": sipg_months,
        "awards": awards,
        "education": education,
        "pna": pna,
        "cut_score": cut_score,
    }

if "fms_inputs" in st.session_state:
    # Restore from snapshot so results stay stable even if sailor edits the form
    # widgets without clicking Calculate again.
    _fms_snap = st.session_state["fms_inputs"]
    sailor_name = _fms_snap["sailor_name"]
    paygrade    = _fms_snap["paygrade"]
    exam_score  = _fms_snap["exam_score"]
    pma         = _fms_snap["pma"]
    sipg_months = _fms_snap["sipg_months"]
    awards      = _fms_snap["awards"]
    education   = _fms_snap["education"]
    pna         = _fms_snap["pna"]
    cut_score   = _fms_snap["cut_score"]

    if not submitted:
        st.caption("ℹ️ Showing your last calculation. Edit inputs above and hit **Calculate My FMS** to refresh.")

    # Per BUPERSINST 1430.16G FMS Chart:
    #   E5: PMA points = (PMA * 80) - 256, never below 0
    #   E6: RSCA PMA points = (RSCA_PMA * 30) - 60, never below 0
    if paygrade == "E6":
        pma_points = max(0.0, (pma * 30) - 60)
    else:
        pma_points = max(0.0, (pma * 80) - 256)

    # Service in Paygrade points = SIPG months / 5, capped per paygrade
    sipg_points = min(sipg_months / 5.0, SIPG_POINTS_MAX[paygrade])

    fms = round(exam_score + pma_points + sipg_points + awards + education + pna, 2)
    max_fms = MAX_FMS[paygrade]
    pct_of_max = round((fms / max_fms) * 100, 1) if max_fms else 0.0

    st.subheader("📊 Your Results")
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Final Multiple Score", f"{fms}")
    col_b.metric(f"Max Possible ({paygrade})", f"{max_fms:.0f}")
    col_c.metric("% of Max", f"{pct_of_max}%")

    st.info(
        f"Your FMS is **{fms}** out of a possible **{max_fms:.0f}** for {paygrade}. "
        f"Selection cutoffs are published per rate after each cycle — your standing depends on your rate's specific cutoff, not a fixed number."
    )

    # Cut Score Comparison — only if sailor entered a cut score (cut_score > 0).
    if cut_score > 0:
        diff = round(fms - cut_score, 2)
        if diff >= 0:
            st.success(
                f"💪 Your FMS of **{fms}** is **{diff} points above** the cut score of **{cut_score}** you entered. "
                "Strong position — keep pushing."
            )
        else:
            st.warning(
                f"⚠️ Your FMS of **{fms}** is **{abs(diff)} points below** the cut score of **{cut_score}** you entered. "
                "Time to close the gap — see the study guide below."
            )

    # Score Breakdown
    st.subheader("📉 Score Breakdown")
    pma_label_short = "RSCA PMA pts" if paygrade == "E6" else "PMA pts"
    breakdown = {
        "Exam Score": exam_score,
        pma_label_short: round(pma_points, 2),
        "Service in Paygrade": round(sipg_points, 2),
        "Awards": awards,
        "Education": education,
        "PNA": pna,
    }
    df_breakdown = pd.DataFrame.from_dict(
        breakdown, orient="index", columns=["Points"]
    ).reset_index().rename(columns={"index": "Component"})
    st.bar_chart(df_breakdown.set_index("Component"))

    # Personalized Study Guide
    st.subheader("📚 Personalized Study Guide")
    guide_items = []

    if exam_score < 55:
        guide_items.append({
            "area": "Exam Score",
            "priority": "HIGH" if exam_score < 45 else "MEDIUM",
            "current": exam_score,
            "target": 55.0,
            "gain": round(55 - exam_score, 1),
            "actions": [
                "Study NRTC materials for your rate daily.",
                "Use Bitvore or rate-specific Quizlet decks.",
                "Take practice exams under timed conditions.",
                "Focus on tech manual chapters with highest question frequency.",
                "Form a study group with others in your rate.",
            ],
        })

    if pma < 4.4:
        # Use the right multiplier per BUPERSINST 1430.16G: E5 = PMA*80-256, E6 = RSCA_PMA*30-60
        pma_mult = 30 if paygrade == "E6" else 80
        pma_offset = 60 if paygrade == "E6" else 256
        target_pma_pts = round(max(0.0, (4.4 * pma_mult) - pma_offset), 2)
        pma_label_long = "RSCA PMA" if paygrade == "E6" else "PMA"
        guide_items.append({
            "area": f"{pma_label_long} / Eval Performance",
            "priority": "HIGH" if pma < 4.0 else "MEDIUM",
            "current": f"{pma} (worth {round(pma_points, 2)} pts)",
            "target": f"4.4+ (worth {target_pma_pts} pts)",
            "gain": round((4.4 - pma) * pma_mult, 2),
            "actions": [
                "Talk to your supervisor about your eval standing.",
                "Volunteer for additional duties and qualifications.",
                "Document all accomplishments — do not wait until eval time.",
                "Pursue a warfare qualification if not already earned.",
                "Request a mid-term counseling session.",
            ],
        })

    awards_cap = AWARDS_MAX[paygrade]
    if awards < (awards_cap / 2):
        guide_items.append({
            "area": "Awards",
            "priority": "MEDIUM",
            "current": awards,
            "target": f"{int(awards_cap / 2)}-{int(awards_cap)} (cap is {int(awards_cap)} for {paygrade})",
            "gain": round((awards_cap / 2) - awards, 1),
            "actions": [
                "Talk to your LPO or Chief about submitting an award write-up.",
                "Track achievements that qualify for a NAM.",
                "Ensure all past awards are in your service record.",
                "Participate in community service events.",
            ],
        })

    if education < 4:
        guide_items.append({
            "area": "Education",
            "priority": "MEDIUM" if education < 2 else "LOW",
            "current": EDUCATION_LABELS.get(int(education), str(education)),
            "target": "Bachelor's or higher (4 pts)",
            "gain": 4 - education,
            "actions": [
                "Submit your JST — military skills already earn credits.",
                "Take a free CLEP exam (Modern States can help you prep free).",
                "Enroll in Navy College Program courses through NCPACE.",
                "Contact your ESO for available on-base courses.",
            ],
        })

    # PNA card always shows so sailors at every PNA level understand how the points work.
    # Per BUPERSINST 1430.16G: E5 = 1.5 pts/cycle (max 9 over 6 cycles),
    #                          E6 = 1.0 pt/cycle  (max 9 over 9 cycles).
    if paygrade == "E6":
        pna_rate_text = "1 point per cycle (capped at 9)"
        pna_max_cycles_text = "9 cycles in the top 25% maxes you out at 9 points."
        per_cycle = 1.0
    else:
        pna_rate_text = "1.5 points per cycle (capped at 9)"
        pna_max_cycles_text = "6 cycles in the top 25% maxes you out at 9 points."
        per_cycle = 1.5

    pna_remaining = max(0.0, 9.0 - pna)
    cycles_to_max = "—" if per_cycle == 0 else max(0, -(-int(pna_remaining * 10) // int(per_cycle * 10)))

    if pna >= 9:
        pna_priority = "LOW"
        pna_target_text = "Maxed out (9.0)"
        pna_gain_text = "0 — already capped"
        pna_actions = [
            "You're at the 9-point cap. PNA isn't a lever for you anymore — focus exam study, evals, awards, and education.",
            "If you stay top-25% next cycle you still get the morale boost, just no extra points.",
        ]
    elif pna == 0:
        pna_priority = "INFO"
        pna_target_text = "Accumulates automatically"
        pna_gain_text = f"up to {9.0 - pna:.1f}"
        pna_actions = [
            "PNA points are awarded each cycle you finish in the top 25% of your rate (in both SS and PMA) but aren't advanced.",
            f"For {paygrade} sailors that's {pna_rate_text}.",
            pna_max_cycles_text,
            "Keep taking and passing the exam every cycle — the points carry forward.",
        ]
    else:
        pna_priority = "INFO"
        pna_target_text = f"9.0 (max)"
        pna_gain_text = f"up to {pna_remaining:.1f}"
        pna_actions = [
            f"You currently have **{pna} PNA points**. The cap is 9.0, so you can still earn up to **{pna_remaining:.1f} more**.",
            f"For {paygrade} sailors PNA accrues at {pna_rate_text}.",
            f"At that rate, **{cycles_to_max} more top-25% cycle(s)** would max you out.",
            "Keep showing up — finish in the top 25% in both SS and PMA each cycle and they keep adding.",
        ]

    guide_items.append({
        "area": "PNA Points",
        "priority": pna_priority,
        "current": pna,
        "target": pna_target_text,
        "gain": pna_gain_text,
        "actions": pna_actions,
    })

    # PNA card always appends to guide_items (line ~932), so the list is never empty.
    # Render the prioritized study guide directly — no need for an "all clear" fallback.
    priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
    guide_items.sort(key=lambda x: priority_order.get(x["priority"], 9))
    priority_icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢", "INFO": "🔵"}

    for item in guide_items:
        icon = priority_icon.get(item["priority"], "⚪")
        label = icon + " **" + item["area"] + "** — Current: " + str(item["current"]) + " -> Target: " + str(item["target"]) + " (+" + str(item["gain"]) + " pts possible)"
        with st.expander(label):
            st.markdown("**Action Steps:**")
            for action in item["actions"]:
                st.markdown("- " + action)

    # Data Summary
    st.subheader("🧾 Full Score Summary")
    pma_col_name = "RSCA PMA" if paygrade == "E6" else "PMA"
    st.dataframe(
        pd.DataFrame([{
            "Sailor": sailor_name,
            "Paygrade": paygrade,
            "Exam SS": exam_score,
            pma_col_name: pma,
            f"{pma_col_name} pts": round(pma_points, 2),
            "SIPG (mo)": sipg_months,
            "SIPG pts": round(sipg_points, 2),
            "Awards": awards,
            "Education": education,
            "PNA": pna,
            "FMS": fms,
            "Max FMS": max_fms,
            "% of Max": pct_of_max,
        }]),
        use_container_width=True,
    )

    # PDF Report
    st.subheader("📥 Download Report")

    def generate_pdf(name, paygrade, fms, max_fms, pct_of_max, exam_score, pma, pma_points, sipg_months, sipg_points, awards, education, pna, guide_items):
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", "B", 16)
        pdf.cell(0, 10, pdf_safe("Score Surge FMS Report - " + name), ln=True, align="C")
        pdf.set_font("Arial", "", 10)
        pdf.cell(0, 8, pdf_safe(f"Cycle {CURRENT_CYCLE['number']} | Paygrade: {paygrade} | Max FMS for {paygrade}: {max_fms:.0f}"), ln=True, align="C")
        pdf.ln(6)
        pdf.set_font("Arial", "B", 14)
        pdf.cell(0, 10, pdf_safe(f"Final Multiple Score: {fms}   |   {pct_of_max}% of max"), ln=True)
        pdf.set_font("Arial", "", 10)
        pdf.multi_cell(0, 6, pdf_safe("Selection cutoffs are published per rate after each cycle. There is no fixed minimum FMS - your standing depends on your rate's specific cutoff."))
        pdf.ln(2)
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 8, pdf_safe("Score Breakdown:"), ln=True)
        pdf.set_font("Arial", "", 11)
        pma_label_pdf = "RSCA PMA" if paygrade == "E6" else "PMA"
        for label, val in [
            ("Exam Standard Score", exam_score),
            (f"{pma_label_pdf} ({pma})", round(pma_points, 2)),
            (f"Service in Paygrade ({sipg_months} mo)", round(sipg_points, 2)),
            ("Awards Points", awards),
            ("Education Points", education),
            ("PNA Points", pna),
        ]:
            pdf.cell(0, 7, pdf_safe("  " + label + ": " + str(val)), ln=True)
        pdf.ln(4)
        if guide_items:
            pdf.set_font("Arial", "B", 12)
            pdf.cell(0, 8, pdf_safe("Improvement Areas:"), ln=True)
            for item in guide_items:
                pdf.set_font("Arial", "B", 11)
                pdf.cell(0, 7, pdf_safe("[" + item["priority"] + "] " + item["area"]), ln=True)
                pdf.set_font("Arial", "", 10)
                for action in item["actions"]:
                    pdf.multi_cell(180, 6, pdf_safe("   - " + action))
                    pdf.ln(2)
        # Unique temp path so concurrent users don't overwrite each other's PDFs.
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp.close()
        pdf.output(tmp.name)
        return tmp.name

    pdf_path = generate_pdf(sailor_name, paygrade, fms, max_fms, pct_of_max, exam_score, pma, pma_points, sipg_months, sipg_points, awards, education, pna, guide_items)
    with open(pdf_path, "rb") as f:
        st.download_button(
            label="📥 Download PDF Report",
            data=f,
            file_name="FMS_Report_" + safe_filename(sailor_name) + ".pdf",
            mime="application/pdf",
            use_container_width=True,
        )


# STUDY GUIDE ENGINE
st.divider()
st.subheader("📖 AI Study Guide")
st.caption("Powered by a stern, coffee-drinking PS Chief who has no time for excuses.")

with st.form("study_guide_form"):
    col1, col2 = st.columns(2)
    with col1:
        sg_rating_label = st.selectbox(
            "Your Rating",
            NAVY_RATE_LABELS,
            index=NAVY_RATE_LABELS.index(next(lbl for lbl in NAVY_RATE_LABELS if lbl.startswith("PS —"))),
        )
        sg_rating = NAVY_RATE_CODE_FROM_LABEL[sg_rating_label]
        sg_paygrade = st.selectbox(
            "Your Paygrade",
            ["E5", "E6"],
            help="Score Surge currently supports E5 and E6 advancement. E4 and E7 use different processes.",
        )
    with col2:
        sg_gap = st.number_input(
            "How many FMS points are you below your rate's last cut score?",
            min_value=0.0, max_value=30.0, value=0.0, step=0.5,
            help=(
                "Enter 0 if you're at or above your rate's cut score. "
                "Otherwise enter the gap (e.g. cut score 56, your FMS 50 → enter 6). "
                "Find your rate's cut score in the most recent advancement NAVADMIN on MyNavyHR. "
                "The Chief uses this to decide how aggressive your study plan should be."
            ),
        )
        sg_type = st.selectbox("Guide Type", [
            "Full Rating Guide",
            "Crash Plan (3-5 days)",
            "High Yield Topics Only",
            "Single Subject Deep Dive",
            "Practice Questions"
        ])
    sg_subject = st.text_input("Subject (only for Single Subject Deep Dive)", placeholder="e.g. Military Awards, UCMJ, Evals")
    sg_submit = st.form_submit_button("Generate My Study Guide", use_container_width=True)

if sg_submit:
    if sg_gap > 10:
        strategy = "broad coverage — this sailor needs significant improvement across all areas"
    elif sg_gap > 5:
        strategy = "high-yield focus — hit the heavy hitters that appear most on the exam"
    elif sg_gap > 0:
        strategy = "precision mode — plug specific holes, every point counts"
    else:
        strategy = "rank maximization — sailor is eligible but wants to score higher"

    if sg_type == "Single Subject Deep Dive" and sg_subject:
        topic_instruction = f"Focus exclusively on: {sg_subject}"
    else:
        topic_instruction = f"Guide type: {sg_type}"

    # All cycle-specific dates derived from CURRENT_CYCLE so the prompt never goes stale.
    e5_exam   = _exam_date("E5 Exam")
    e6_exam   = _exam_date("E6 Exam")
    pmk_due   = _exam_date("PMK-EE")
    ildc_due  = _exam_date("ILDC")
    pma_e5_lo, pma_e5_hi = CURRENT_CYCLE["pma_window_e5"]
    pma_e6_lo, pma_e6_hi = CURRENT_CYCLE["pma_window_e6"]

    cycle_facts = "\n".join([
        f"- E6 exam date: {_fmt_cycle_date(e6_exam)}" if e6_exam else "",
        f"- E5 exam date: {_fmt_cycle_date(e5_exam)}" if e5_exam else "",
        f"- Terminal Eligibility Date: {_fmt_cycle_date(CURRENT_CYCLE['ted'])}",
        f"- PMK-EE deadline: {_fmt_cycle_date(pmk_due)}" if pmk_due else "",
        f"- ILDC deadline: {_fmt_cycle_date(ildc_due)} (E6 only)" if ildc_due else "",
        f"- Min TIR E6: {_fmt_cycle_date(CURRENT_CYCLE['tir_e6'])}",
        f"- Min TIR E5: {_fmt_cycle_date(CURRENT_CYCLE['tir_e5'])}",
        f"- PMA window E6: {_fmt_cycle_date(pma_e6_lo)} to {_fmt_cycle_date(pma_e6_hi)}",
        f"- PMA window E5: {_fmt_cycle_date(pma_e5_lo)} to {_fmt_cycle_date(pma_e5_hi)}",
        "- EAW is authoritative source, must be finalized in NSIPS",
        "- Most active duty E6 ratings now under BBA, advancement via A2P/CA2P",
    ]).strip()

    prompt = f"""You are a senior {sg_rating} Chief Petty Officer with 20 years of service.
You drink too much coffee, you have zero patience for excuses, and you genuinely want your sailors to advance.
You are direct, blunt, and efficient. No fluff. No wasted words.
You know {CURRENT_CYCLE['navadmin']} (Cycle {CURRENT_CYCLE['number']}) inside and out.

CYCLE {CURRENT_CYCLE['number']} FACTS ({CURRENT_CYCLE['navadmin']}):
{cycle_facts}

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

    if check_ai_quota():
        with st.spinner("Chief is reviewing your record..."):
            try:
                message = client.messages.create(
                    model="claude-opus-4-5",
                    max_tokens=1500,
                    messages=[{"role": "user", "content": prompt}]
                )
                guide_text = first_text_block(message)
                if not guide_text:
                    st.error("Chief had nothing to say. Try again in a moment.")
                else:
                    st.subheader("📋 Your Personalized Study Guide")
                    st.markdown(guide_text)
                    st.download_button(
                        "📥 Download Study Guide",
                        data=guide_text,
                        file_name=f"StudyGuide_{sg_rating}_{sg_paygrade}.txt",
                        mime="text/plain",
                        use_container_width=True
                    )
            except Exception as e:
                st.error("Something went wrong: " + str(e))

# ── INTERACTIVE AI TUTOR ──────────────────────────────────────────────────────
st.divider()
st.subheader("🎓 Interactive AI Tutor")
st.caption("Pick a topic. The Chief will teach it. Ask questions. Get answers. Pass your exam.")

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
        "subtopics": ["Electronic Drill Management", "Entitlements", "Gains", "Mobilization & Demobilization", "Separations & Transfers"],
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

# Pick the sailor's rating from the full Navy rate list. PS rates get the
# hand-curated PS_TOPICS dropdown; everyone else gets freeform topic mode.
tutor_rating_label = st.selectbox(
    "Your Rating",
    NAVY_RATE_LABELS,
    index=NAVY_RATE_LABELS.index(next(lbl for lbl in NAVY_RATE_LABELS if lbl.startswith("PS —"))),
    key="tutor_rating_label",
    help="Pick your rate. PS sailors get a curated topic library. Every other rate uses freeform mode — type any topic from your bibliography and the Chief teaches it.",
)
tutor_rating = NAVY_RATE_CODE_FROM_LABEL[tutor_rating_label]
# Mirror to a stable key the Practice section can read, regardless of label format.
st.session_state["tutor_rating"] = tutor_rating

# When the sailor switches rates, drop any old lesson history so the Q&A doesn't
# carry context from a different rate.
if st.session_state.get("tutor_history_rating") != tutor_rating:
    st.session_state["tutor_history"] = []
    st.session_state.pop("tutor_topic", None)
    st.session_state.pop("tutor_subtopic", None)
    st.session_state.pop("tutor_lesson", None)
    st.session_state["tutor_history_rating"] = tutor_rating

if tutor_rating in TUTOR_CURATED_RATINGS:
    # ── PS curated topic mode ──
    col1, col2 = st.columns(2)
    with col1:
        tutor_topic = st.selectbox("Select a Topic to Study", list(PS_TOPICS.keys()))
    with col2:
        tutor_subtopic = st.selectbox("Select a Subtopic", PS_TOPICS[tutor_topic]["subtopics"])

    if st.button("📖 Start Lesson", use_container_width=True):
        bib_refs = PS_TOPICS[tutor_topic]["bib"]
        # Use the tutor_topic prefix ("E5"/"E6") to tell the Chief which exam this is for.
        topic_prefix = tutor_topic.split(" ")[0] if " " in tutor_topic else ""
        exam_paygrade = topic_prefix if topic_prefix in ("E5", "E6") else "E5/E6"
        lesson_prompt = f"""You are a senior PS Chief Petty Officer with 20 years of experience.
You are teaching a Navy advancement exam lesson to a busy young sailor who needs to pass the PS {exam_paygrade} NWAE.
Explain everything like the sailor is smart but has never seen this material before.
Be direct, clear, and use real Navy examples.
No wasted words. No fluff.

TOPIC: {tutor_topic}
SUBTOPIC: {tutor_subtopic}
GOVERNING REFERENCES: {bib_refs}

Teach this lesson as follows:
1. What this topic is in ONE plain-English sentence
2. Why it matters on the exam and in real life
3. The key rules, procedures, or concepts they MUST know (use bullet points, plain English)
4. A real-world example of how this works in a PS shop
5. The most common exam trap on this subtopic
6. Three practice questions with answers and explanations

Keep it tight. Make it stick."""

        if check_ai_quota():
            with st.spinner("Chief is preparing your lesson..."):
                try:
                    message = client.messages.create(
                        model="claude-opus-4-5",
                        max_tokens=2000,
                        messages=[{"role": "user", "content": lesson_prompt}]
                    )
                    lesson = first_text_block(message)
                    if not lesson:
                        st.error("Chief had nothing to say. Try again in a moment.")
                    else:
                        # Persist so lesson stays visible across reruns (Q&A, downloads, etc.).
                        st.session_state["tutor_lesson"] = lesson
                        st.session_state["tutor_topic"] = tutor_topic
                        st.session_state["tutor_subtopic"] = tutor_subtopic
                        st.session_state["tutor_history"] = [
                            {"role": "user", "content": lesson_prompt},
                            {"role": "assistant", "content": lesson},
                        ]
                        st.session_state["tutor_history_rating"] = tutor_rating
                except Exception as e:
                    st.error("Error: " + str(e))

else:
    # ── Freeform mode for every non-curated rate ──
    rate_long_name = dict(NAVY_RATES).get(tutor_rating, tutor_rating)
    st.caption(
        f"💡 Type any topic from your **{tutor_rating}** advancement bibliography. "
        "The Chief will teach it like he taught his sailors. "
        "Optional: drop in the governing reference (BUPERSINST, OPNAVINST, NAVADMIN, JTR, NTRP, technical manual) "
        "if you know it — gets you a more accurate lesson."
    )

    col1, col2 = st.columns(2)
    with col1:
        free_paygrade = st.selectbox("Your Paygrade", ["E5", "E6"], key="tutor_free_paygrade")
    with col2:
        free_topic = st.text_input(
            "Topic to study",
            placeholder="e.g. evals, OPSEC, leave processing, refrigeration plant ops",
            key="tutor_free_topic",
        )
    free_subtopic = st.text_input(
        "Subtopic (optional)",
        placeholder="e.g. mid-term counseling, classification levels, special pays",
        key="tutor_free_subtopic",
    )
    free_ref = st.text_input(
        "Governing reference (optional, if you know it)",
        placeholder="e.g. BUPERSINST 1610.10E, OPNAVINST 5510.1, NTRP 3-13",
        key="tutor_free_ref",
    )

    if st.button("📖 Start Lesson", use_container_width=True, key="tutor_free_start"):
        if not (free_topic or "").strip():
            st.error("Type a topic to study before starting the lesson.")
        else:
            ref_line = (
                f"GOVERNING REFERENCE: {free_ref.strip()}"
                if (free_ref or "").strip()
                else "GOVERNING REFERENCE: Cite whatever official Navy reference (BUPERSINST, OPNAVINST, MILPERSMAN, NAVADMIN, JTR, NTRP, technical manual, or rate-specific manual) is correct for this topic. If you're not sure, say so."
            )
            subtopic_line = f"SUBTOPIC: {free_subtopic.strip()}\n" if (free_subtopic or "").strip() else ""

            lesson_prompt = f"""You are a senior {tutor_rating} ({rate_long_name}) Chief Petty Officer with 20 years of experience.
You are teaching a Navy advancement exam lesson to a busy young sailor who needs to pass the {tutor_rating} {free_paygrade} advancement exam.
Explain everything like the sailor is smart but has never seen this material before.
Be direct, clear, and use real Navy examples specific to {tutor_rating} sailors.
No wasted words. No fluff.

TOPIC: {free_topic.strip()}
{subtopic_line}{ref_line}

Teach this lesson as follows:
1. What this topic is in ONE plain-English sentence
2. Why it matters on the {tutor_rating} {free_paygrade} exam and in real life as a {tutor_rating}
3. The key rules, procedures, or concepts they MUST know (use bullet points, plain English)
4. A real-world example of how this plays out in a {tutor_rating} shop or work environment
5. The most common exam trap sailors fall into on this topic
6. Three practice questions with answers and explanations
If the topic doesn't seem relevant to {tutor_rating} advancement, say so honestly and suggest what they probably meant.

Keep it tight. Make it stick."""

            if check_ai_quota():
                with st.spinner("Chief is preparing your lesson..."):
                    try:
                        message = client.messages.create(
                            model="claude-opus-4-5",
                            max_tokens=2000,
                            messages=[{"role": "user", "content": lesson_prompt}]
                        )
                        lesson = first_text_block(message)
                        if not lesson:
                            st.error("Chief had nothing to say. Try again in a moment.")
                        else:
                            # Build a display label for the lesson header + filename.
                            display_topic = free_topic.strip()
                            if (free_subtopic or "").strip():
                                display_topic = f"{display_topic} — {free_subtopic.strip()}"
                            st.session_state["tutor_lesson"] = lesson
                            st.session_state["tutor_topic"] = f"{tutor_rating} {free_paygrade}: {free_topic.strip()}"
                            st.session_state["tutor_subtopic"] = display_topic
                            st.session_state["tutor_history"] = [
                                {"role": "user", "content": lesson_prompt},
                                {"role": "assistant", "content": lesson},
                            ]
                            st.session_state["tutor_history_rating"] = tutor_rating
                    except Exception as e:
                        st.error("Error: " + str(e))

# ── Lesson display + Q&A — shared by curated and freeform modes ──
# Reads from session_state so the lesson and Q&A persist across reruns.
if st.session_state.get("tutor_lesson"):
    lesson_subtopic = st.session_state.get("tutor_subtopic", "Lesson")
    st.subheader(f"📚 Lesson: {lesson_subtopic}")
    st.markdown(st.session_state["tutor_lesson"])
    st.download_button(
        "📥 Download This Lesson",
        data=st.session_state["tutor_lesson"],
        file_name=f"Lesson_{safe_filename(lesson_subtopic)}.txt",
        mime="text/plain",
        use_container_width=True,
    )

    st.subheader("💬 Ask the Chief a Question")
    st.caption("Type any follow-up question about this topic. The lesson stays visible while you ask.")
    sailor_question = st.text_input(
        "Your question",
        placeholder="e.g. What happens if a sailor misses the travel claim deadline?",
        key="tutor_qa_input",
    )
    if st.button("Ask", use_container_width=True, key="tutor_qa_btn"):
        if sailor_question and check_ai_quota():
            with st.spinner("Chief is thinking..."):
                try:
                    history = st.session_state.tutor_history.copy()
                    history.append({"role": "user", "content": sailor_question})
                    message = client.messages.create(
                        model="claude-opus-4-5",
                        max_tokens=1000,
                        messages=history,
                    )
                    answer = first_text_block(message)
                    if not answer:
                        st.error("Chief had nothing to say. Try again in a moment.")
                    else:
                        st.session_state.tutor_history.append({"role": "user", "content": sailor_question})
                        st.session_state.tutor_history.append({"role": "assistant", "content": answer})
                except Exception as e:
                    st.error("Error: " + str(e))

    # Render the running Q&A transcript (skip the initial lesson exchange).
    qa_pairs = st.session_state.get("tutor_history", [])[2:]
    for i in range(0, len(qa_pairs), 2):
        if i + 1 < len(qa_pairs):
            st.markdown(f"**You:** {qa_pairs[i]['content']}")
            st.markdown(f"**Chief says:** {qa_pairs[i+1]['content']}")
            st.markdown("---")
# SCORE HISTORY TRACKER
if "score_history" not in st.session_state:
    st.session_state.score_history = []

st.divider()

# PRACTICE QUESTION MODE — works for every rate. PS uses the curated topic dropdown;
# every other rate uses freeform topic input.
st.subheader("🎯 Practice Question Mode")
st.caption("Answer like the exam is tomorrow. The Chief will grade you and explain every answer.")

# Pull rating from session state — set by the Tutor's rate selector above.
_pq_rating = st.session_state.get("tutor_rating", "PS")
_pq_rate_long = dict(NAVY_RATES).get(_pq_rating, _pq_rating)
st.caption(f"⚓ Using your rating from the AI Tutor above: **{_pq_rating} — {_pq_rate_long}**")

# These get filled by either the curated form or the freeform form so the generation
# block below stays mode-agnostic.
pq_submit = False
pq_topic_label = ""   # human-readable label for the score history
pq_prompt = ""

if _pq_rating in TUTOR_CURATED_RATINGS:
    with st.form("practice_form"):
        col1, col2 = st.columns(2)
        with col1:
            pq_topic = st.selectbox("Topic", list(PS_TOPICS.keys()), key="pq_topic")
        with col2:
            pq_num = st.selectbox("Number of Questions", [3, 5, 10], key="pq_num")
        pq_submit = st.form_submit_button("Generate Practice Questions", use_container_width=True)
    if pq_submit:
        bib_refs = PS_TOPICS[pq_topic]["bib"]
        pq_topic_label = pq_topic
        pq_prompt = f"""You are a senior PS Chief Petty Officer writing a Navy advancement exam practice set.
Generate exactly {pq_num} multiple choice practice questions for:
- Topic: {pq_topic}
- Governing References: {bib_refs}
Format each question EXACTLY like this:
Q1: [Question text]
A) [Option]
B) [Option]
C) [Option]
D) [Option]
ANSWER: [Letter]
EXPLANATION: [2-3 sentences explaining why this is correct and what regulation supports it]
Make the questions realistic exam difficulty. Include tricky distractors. Reference specific regulations. No fluff."""
else:
    with st.form("practice_form_freeform"):
        col1, col2 = st.columns(2)
        with col1:
            pq_free_paygrade = st.selectbox("Your Paygrade", ["E5", "E6"], key="pq_free_paygrade")
        with col2:
            pq_num = st.selectbox("Number of Questions", [3, 5, 10], key="pq_num_free")
        pq_free_topic = st.text_input(
            "Topic",
            placeholder="e.g. evals, OPSEC, leave processing, refrigeration plant ops",
            key="pq_free_topic",
        )
        pq_free_ref = st.text_input(
            "Governing reference (optional, if you know it)",
            placeholder="e.g. BUPERSINST 1610.10E, OPNAVINST 5510.1, NTRP 3-13",
            key="pq_free_ref",
        )
        pq_submit = st.form_submit_button("Generate Practice Questions", use_container_width=True)
    if pq_submit:
        if not (pq_free_topic or "").strip():
            st.error("Type a topic before generating practice questions.")
            pq_submit = False
        else:
            ref_line = (
                f"- Governing References: {pq_free_ref.strip()}"
                if (pq_free_ref or "").strip()
                else "- Governing References: cite whatever official Navy reference (BUPERSINST, OPNAVINST, MILPERSMAN, NAVADMIN, JTR, NTRP, technical manual, or rate-specific manual) is correct for this topic."
            )
            pq_topic_label = f"{_pq_rating} {pq_free_paygrade}: {pq_free_topic.strip()}"
            pq_prompt = f"""You are a senior {_pq_rating} ({_pq_rate_long}) Chief Petty Officer writing a Navy advancement exam practice set for {_pq_rating} {pq_free_paygrade} sailors.
Generate exactly {pq_num} multiple choice practice questions for:
- Rating: {_pq_rating}
- Paygrade: {pq_free_paygrade}
- Topic: {pq_free_topic.strip()}
{ref_line}
Format each question EXACTLY like this:
Q1: [Question text]
A) [Option]
B) [Option]
C) [Option]
D) [Option]
ANSWER: [Letter]
EXPLANATION: [2-3 sentences explaining why this is correct and what regulation supports it]
Make the questions realistic {_pq_rating} {pq_free_paygrade} exam difficulty. Include tricky distractors. Reference specific regulations. No fluff."""

if pq_submit and pq_prompt:
    if check_ai_quota():
        with st.spinner("Chief is writing your exam..."):
            try:
                message = client.messages.create(
                    model="claude-opus-4-5",
                    max_tokens=2000,
                    messages=[{"role": "user", "content": pq_prompt}]
                )
                questions_text = first_text_block(message)
                if not questions_text:
                    st.error("Chief had nothing to say. Try again in a moment.")
                else:
                    st.session_state.practice_questions = questions_text
                    st.session_state.practice_topic_label = pq_topic_label
            except Exception as e:
                st.error("Error: " + str(e))

if "practice_questions" in st.session_state:
    st.subheader("📝 Your Practice Questions")
    st.markdown(st.session_state.practice_questions)
    st.subheader("✍️ Submit Your Answers")
    sailor_answers = st.text_area("Type your answers (e.g. Q1: B, Q2: A)", height=150)
    if st.button("Grade My Answers", use_container_width=True):
        if sailor_answers and check_ai_quota():
            with st.spinner("Chief is grading..."):
                try:
                    # Use the rate that generated the questions for grader voice + accuracy.
                    grader_rating = st.session_state.get("tutor_rating", "PS")
                    grader_rate_long = dict(NAVY_RATES).get(grader_rating, grader_rating)
                    grade_prompt = f"""You are a {grader_rating} ({grader_rate_long}) Chief grading a sailor's practice exam.
Questions: {st.session_state.practice_questions}
Sailor's answers: {sailor_answers}
Grade each answer. State correct or incorrect. Explain the right answer. Reference the regulation. Give final score. One line of honest feedback. Be direct. No fluff.
For each question, after showing whether the answer is correct or incorrect and explaining the correct answer, add a new line formatted exactly like this: 📖 Source: [Manual name, Chapter X] — for example: NAVEDTRA 14257, Chapter 4 or MILPERSMAN 1430-010, Section 2. Base the source on the actual Navy training manual or instruction that covers this topic for the sailor's rating and paygrade. If you are not certain of the exact chapter, provide the most accurate manual name and your best chapter estimate.
End your response with a clear final score line in this exact format: "Final Score: X/Y" (where X is correct and Y is total)."""
                    message = client.messages.create(
                        model="claude-opus-4-5",
                        max_tokens=1500,
                        messages=[{"role": "user", "content": grade_prompt}]
                    )
                    grade_result = first_text_block(message)
                    if not grade_result:
                        st.error("Chief had nothing to say. Try again in a moment.")
                        grade_result = ""
                    else:
                        st.subheader("📊 Your Grade")
                        st.markdown(grade_result)

                    # Robust score parser — try several formats the Chief might use.
                    # Patterns: "4 out of 5", "4/5", "4 of 5", "Final Score: 4/5", "Score: 4 of 5"
                    score_match = None
                    for pattern in (
                        r'(?:final\s+score|score)[:\s]+(\d+)\s*(?:out\s+of|/|of)\s*(\d+)',
                        r'(\d+)\s*(?:out\s+of|of)\s*(\d+)',
                        r'(\d+)\s*/\s*(\d+)',
                    ):
                        score_match = re.search(pattern, grade_result, re.IGNORECASE)
                        if score_match:
                            break

                    if score_match:
                        scored = int(score_match.group(1))
                        total = int(score_match.group(2))
                        # Sanity check: don't log nonsense like "100/0" or "5/3".
                        if total > 0 and scored <= total:
                            if "score_history" not in st.session_state:
                                st.session_state.score_history = []
                            # Topic label was set when questions were generated — covers PS + freeform.
                            history_topic = st.session_state.get("practice_topic_label", "Practice")
                            st.session_state.score_history.append({
                                "date": datetime.date.today().strftime("%b %d"),
                                "topic": history_topic,
                                "score": scored,
                                "total": total,
                                "pct": round((scored/total)*100)
                            })

                    # Only offer the download if there's actually a graded result.
                    if grade_result:
                        st.download_button(
                            "📥 Download Practice Results",
                            data=f"QUESTIONS:\n{st.session_state.practice_questions}\n\nANSWERS:\n{sailor_answers}\n\nGRADE:\n{grade_result}",
                            file_name="PracticeResults.txt",
                            mime="text/plain",
                            use_container_width=True
                        )
                except Exception as e:
                    st.error("Error: " + str(e))

# ── FULL MOCK EXAM ────────────────────────────────────────────────────────────
st.divider()
st.subheader("🧪 Full Mock Exam")
st.caption("Simulate the real deal. The Chief writes the exam, you answer, he grades it. Timer starts when questions are generated.")


def parse_mock_exam_questions(text):
    """
    Parse the Chief's generated exam text into a list of structured question dicts.
    Each dict has: num (int), text (str), options (dict A-D → str),
    correct (letter str), explanation (str).
    Handles multi-line question text and explanation continuations.
    Returns an empty list if nothing parseable is found.
    """
    questions = []
    # Split on lines that start a new question block (Q1:, Q2:, etc.)
    blocks = re.split(r'(?=^Q\d+:)', text.strip(), flags=re.MULTILINE)
    for block in blocks:
        block = block.strip()
        if not re.match(r'^Q\d+:', block):
            continue
        try:
            lines = block.split('\n')
            hdr = re.match(r'^Q(\d+):\s*(.*)', lines[0])
            if not hdr:
                continue
            q_num = int(hdr.group(1))
            q_text_parts = [hdr.group(2).strip()]
            options = {}
            correct = None
            explanation_parts = []
            state = 'question'

            for line in lines[1:]:
                ls = line.strip()
                if re.match(r'^[A-D]\)', ls):
                    options[ls[0]] = ls[2:].strip()
                    state = 'option'
                elif re.match(r'^ANSWER:\s*[A-Da-d]', ls, re.IGNORECASE):
                    m = re.match(r'^ANSWER:\s*([A-Da-d])', ls, re.IGNORECASE)
                    if m:
                        correct = m.group(1).upper()
                    state = 'answer'
                elif re.match(r'^EXPLANATION:', ls, re.IGNORECASE):
                    explanation_parts.append(
                        re.sub(r'^EXPLANATION:\s*', '', ls, flags=re.IGNORECASE)
                    )
                    state = 'explanation'
                elif state == 'explanation' and ls:
                    explanation_parts.append(ls)
                elif state == 'question' and ls:
                    q_text_parts.append(ls)

            q_text = ' '.join(q_text_parts).strip()
            explanation = ' '.join(explanation_parts).strip()

            if correct and len(options) >= 4:
                questions.append({
                    "num":         q_num,
                    "text":        q_text,
                    "options":     options,
                    "correct":     correct,
                    "explanation": explanation,
                })
        except Exception:
            continue
    return questions


col1, col2, col3 = st.columns(3)
with col1:
    mock_rating_label = st.selectbox(
        "Your Rating",
        NAVY_RATE_LABELS,
        index=NAVY_RATE_LABELS.index(next(lbl for lbl in NAVY_RATE_LABELS if lbl.startswith("PS —"))),
        key="mock_rating_label",
    )
    mock_rating = NAVY_RATE_CODE_FROM_LABEL[mock_rating_label]
with col2:
    mock_paygrade = st.selectbox("Your Paygrade", ["E5", "E6"], key="mock_paygrade")
with col3:
    mock_num_q = st.selectbox("Number of Questions", [10, 25, 50], key="mock_num_q")

if st.button("Generate Exam", use_container_width=True, key="mock_gen_btn"):
    if check_ai_quota():
        mock_rate_long = dict(NAVY_RATES).get(mock_rating, mock_rating)

        # AGENT: Examiner — generate all exam questions
        mock_gen_prompt = f"""You are a senior {mock_rating} ({mock_rate_long}) Chief Petty Officer writing a full Navy advancement exam simulation for a {mock_rating} {mock_paygrade} sailor.
Generate exactly {mock_num_q} multiple choice questions covering a realistic spread of topics from the {mock_rating} {mock_paygrade} advancement bibliography.
Format each question EXACTLY like this:
Q1: [Question text]
A) [Option]
B) [Option]
C) [Option]
D) [Option]
ANSWER: [Letter]
EXPLANATION: [2-3 sentences explaining why this is correct and what regulation supports it]
Make the questions realistic {mock_rating} {mock_paygrade} exam difficulty. Include tricky distractors. Cover multiple topic areas. Reference specific regulations. No fluff. No preamble — start directly with Q1."""

        with st.spinner(f"Chief is writing your {mock_num_q}-question {mock_rating} {mock_paygrade} exam..."):
            try:
                msg = client.messages.create(
                    model="claude-opus-4-5",
                    max_tokens=min(1000 + mock_num_q * 150, 8000),
                    messages=[{"role": "user", "content": mock_gen_prompt}],
                )
                exam_text = first_text_block(msg)
                if not exam_text:
                    st.error("Chief had nothing to say. Try again.")
                else:
                    sentinel_feedback = ""
                    if check_ai_quota():
                        # AGENT: Sentinel — verify questions are accurate and well-formed before showing to sailor
                        sentinel_prompt = f"""You are a Navy advancement exam quality reviewer.
Review the following {mock_rating} {mock_paygrade} exam questions. Check for:
1. Factual accuracy (correct regulations, procedures, Navy doctrine)
2. Each question has exactly 4 options (A-D), one clearly correct answer, and an EXPLANATION
3. No duplicate or ambiguous answers

If you find significant factual errors, list them briefly (one line each).
Otherwise respond with exactly: VERIFIED: Questions are accurate and well-formed.

Questions to review:
{exam_text[:3500]}"""
                        sentinel_msg = client.messages.create(
                            model="claude-opus-4-5",
                            max_tokens=400,
                            messages=[{"role": "user", "content": sentinel_prompt}],
                        )
                        sentinel_feedback = first_text_block(sentinel_msg)

                    parsed_qs = parse_mock_exam_questions(exam_text)
                    if not parsed_qs:
                        st.error("Couldn't parse exam questions — please try generating again.")
                    else:
                        st.session_state["mock_exam_text"]      = exam_text
                        st.session_state["mock_exam_questions"] = parsed_qs
                        st.session_state["mock_exam_sentinel"]  = sentinel_feedback
                        st.session_state["mock_exam_rating"]    = mock_rating
                        st.session_state["mock_exam_paygrade"]  = mock_paygrade
                        st.session_state["mock_exam_num_q"]     = mock_num_q
                        st.session_state["mock_exam_start_ts"]  = time.time()
                        st.session_state["mock_exam_active"]    = True
                        # Clear any previous attempt state
                        for _clr in ["mock_exam_result", "mock_exam_final_time",
                                     "mock_submitted_answers", "mock_exam_score_saved",
                                     "mock_exam_graded", "mock_exam_coach_summary",
                                     "mock_exam_score", "mock_exam_total", "mock_exam_pct"]:
                            st.session_state.pop(_clr, None)
                        # Clear individual radio answer keys
                        for _q in parsed_qs:
                            st.session_state.pop(f"mock_q_{_q['num']}", None)
            except Exception as e:
                st.error("Error: " + str(e))

if "mock_exam_questions" in st.session_state:
    _me_rating   = st.session_state["mock_exam_rating"]
    _me_paygrade = st.session_state["mock_exam_paygrade"]
    _me_num_q    = st.session_state["mock_exam_num_q"]
    _me_qs       = st.session_state["mock_exam_questions"]

    # Timer — shows elapsed time; updates on each user interaction.
    _timer_ph = st.empty()
    if st.session_state.get("mock_exam_active"):
        _elapsed = int(time.time() - st.session_state.get("mock_exam_start_ts", time.time()))
        _mins, _secs = divmod(_elapsed, 60)
        _timer_ph.metric("⏱️ Time Elapsed", f"{_mins:02d}:{_secs:02d}")
    elif "mock_exam_final_time" in st.session_state:
        _timer_ph.metric("⏱️ Final Time", st.session_state["mock_exam_final_time"])

    # Sentinel quality alert — only show when the Sentinel flagged real issues.
    _sentinel = st.session_state.get("mock_exam_sentinel", "")
    if _sentinel and "VERIFIED" not in _sentinel.upper() and len(_sentinel) > 30:
        with st.expander("⚠️ Sentinel Quality Notes", expanded=False):
            st.caption(_sentinel)

    st.subheader(f"📝 {_me_num_q}-Question {_me_rating} {_me_paygrade} Mock Exam")

    # ── Answer phase — radio buttons, one per question ────────────────────────
    if st.session_state.get("mock_exam_active") and not st.session_state.get("mock_exam_graded"):
        st.caption("Select your answer for each question, then hit **Submit Exam** when all are answered.")

        for q in _me_qs:
            st.markdown(f"**Q{q['num']}.** {q['text']}")
            options_list = [
                f"{letter}) {q['options'][letter]}"
                for letter in "ABCD"
                if letter in q['options']
            ]
            st.radio(
                label=f"Question {q['num']}",
                options=options_list,
                index=None,          # No default — sailor must choose explicitly
                key=f"mock_q_{q['num']}",
                label_visibility="collapsed",
            )
            st.markdown("---")

        # Tally how many questions have been answered
        answered_count = sum(
            1 for q in _me_qs
            if st.session_state.get(f"mock_q_{q['num']}") is not None
        )
        all_answered = answered_count == len(_me_qs)

        if not all_answered:
            st.caption(f"📋 {answered_count} of {len(_me_qs)} answered — select all answers to enable Submit.")

        if st.button(
            "✅ Submit Exam",
            use_container_width=True,
            key="mock_submit_btn",
            disabled=not all_answered,
        ):
            _final_elapsed = int(time.time() - st.session_state["mock_exam_start_ts"])
            _final_m, _final_s = divmod(_final_elapsed, 60)
            st.session_state["mock_exam_final_time"] = f"{_final_m:02d}:{_final_s:02d}"

            # AGENT: Grader — compare each selected answer to the parsed correct answer
            graded = []
            for q in _me_qs:
                raw_sel      = st.session_state.get(f"mock_q_{q['num']}", "") or ""
                selected     = raw_sel[0] if raw_sel else None   # first char = A/B/C/D
                selected_txt = q['options'].get(selected, "") if selected else ""
                is_correct   = (selected == q['correct'])
                graded.append({
                    "num":           q['num'],
                    "text":          q['text'],
                    "selected":      selected,
                    "selected_text": selected_txt,
                    "correct":       q['correct'],
                    "correct_text":  q['options'].get(q['correct'], ""),
                    "explanation":   q['explanation'],
                    "is_correct":    is_correct,
                })

            score = sum(1 for g in graded if g['is_correct'])
            total = len(graded)
            pct   = round((score / total) * 100) if total > 0 else 0

            st.session_state["mock_exam_graded"] = graded
            st.session_state["mock_exam_score"]  = score
            st.session_state["mock_exam_total"]  = total
            st.session_state["mock_exam_pct"]    = pct
            st.session_state["mock_exam_active"] = False

            # AGENT: Coach — identify strong and weak topic areas from graded results
            missed_qs  = [g for g in graded if not g['is_correct']]
            correct_qs = [g for g in graded if g['is_correct']]
            missed_txt  = "\n".join(f"Q{g['num']}: {g['text']}" for g in missed_qs) or "None"
            correct_txt = "\n".join(f"Q{g['num']}: {g['text']}" for g in correct_qs) or "None"

            coach_summary = ""
            if check_ai_quota():
                try:
                    coach_prompt = f"""You are a senior {_me_rating} Chief reviewing a sailor's {_me_num_q}-question mock exam.
Rating: {_me_rating} | Paygrade: {_me_paygrade} | Score: {score}/{total} ({pct}%)

Questions answered CORRECTLY:
{correct_txt}

Questions MISSED:
{missed_txt}

Provide:
1. **Strong Topics** — 2-4 specific topic areas this sailor clearly knows (inferred from correct answers)
2. **Weak Topics** — 2-4 specific topic areas needing more study (inferred from missed answers)
3. One direct, honest sentence of Chief feedback

Be specific to {_me_rating} advancement topics. Keep it under 150 words. No fluff."""
                    coach_msg = client.messages.create(
                        model="claude-opus-4-5",
                        max_tokens=400,
                        messages=[{"role": "user", "content": coach_prompt}],
                    )
                    coach_summary = first_text_block(coach_msg)
                except Exception:
                    coach_summary = ""
            st.session_state["mock_exam_coach_summary"] = coach_summary

            # PROFILE TRACKING — save detailed result to sailor_profile history
            if "sailor_profile" not in st.session_state:
                st.session_state["sailor_profile"] = {"history": []}
            if "history" not in st.session_state["sailor_profile"]:
                st.session_state["sailor_profile"]["history"] = []
            st.session_state["sailor_profile"]["history"].append({
                "date":           datetime.date.today().isoformat(),
                "rating":         _me_rating,
                "paygrade":       _me_paygrade,
                "score":          score,
                "total":          total,
                "percentage":     pct,
                "topics_missed":  [g['text'][:80] for g in missed_qs],
                "topics_correct": [g['text'][:80] for g in correct_qs],
            })

            # Also log to score_history for the progress chart
            if not st.session_state.get("mock_exam_score_saved"):
                if "score_history" not in st.session_state:
                    st.session_state.score_history = []
                st.session_state.score_history.append({
                    "date":  datetime.date.today().strftime("%b %d"),
                    "topic": f"Mock Exam — {_me_rating} {_me_paygrade}",
                    "score": score,
                    "total": total,
                    "pct":   pct,
                })
                st.session_state["mock_exam_score_saved"] = True

            st.rerun()

    # ── Results phase — shown after Submit ────────────────────────────────────
    if st.session_state.get("mock_exam_graded"):
        graded  = st.session_state["mock_exam_graded"]
        score   = st.session_state["mock_exam_score"]
        total   = st.session_state["mock_exam_total"]
        pct     = st.session_state["mock_exam_pct"]

        st.subheader("📊 Mock Exam Results")
        _rc1, _rc2, _rc3 = st.columns(3)
        _rc1.metric("Score", f"{score}/{total}")
        _rc2.metric("Percentage", f"{pct}%")
        if "mock_exam_final_time" in st.session_state:
            _rc3.metric("⏱️ Time", st.session_state["mock_exam_final_time"])

        st.markdown("---")
        st.subheader("📋 Question-by-Question Review")
        for g in graded:
            icon  = "✅" if g['is_correct'] else "❌"
            _q_preview = g['text'][:95] + ("…" if len(g['text']) > 95 else "")
            with st.expander(f"{icon} Q{g['num']}: {_q_preview}"):
                if g['is_correct']:
                    st.success(
                        f"Correct! You answered **{g['selected']}) {g['selected_text']}**"
                    )
                else:
                    st.error(
                        f"You answered **{g['selected']}) {g['selected_text']}**  \n"
                        f"Correct answer: **{g['correct']}) {g['correct_text']}**"
                    )
                st.markdown(f"**Explanation:** {g['explanation']}")

        # Strong / Weak topic summary from Coach
        if st.session_state.get("mock_exam_coach_summary"):
            st.markdown("---")
            st.subheader("🎯 Strong & Weak Topics")
            st.markdown(st.session_state["mock_exam_coach_summary"])

        # Build download text
        _dl = (
            f"MOCK EXAM — {_me_rating} {_me_paygrade}\n"
            f"Score: {score}/{total} ({pct}%)\n"
            f"Time: {st.session_state.get('mock_exam_final_time', 'N/A')}\n\n"
        )
        for g in graded:
            _dl += (
                f"Q{g['num']}: {g['text']}\n"
                f"Your Answer: {g['selected']}) {g['selected_text']}\n"
                f"Correct:     {g['correct']}) {g['correct_text']}\n"
                f"Status:      {'CORRECT' if g['is_correct'] else 'INCORRECT'}\n"
                f"Explanation: {g['explanation']}\n\n"
            )
        if st.session_state.get("mock_exam_coach_summary"):
            _dl += f"\nTOPIC SUMMARY:\n{st.session_state['mock_exam_coach_summary']}\n"

        st.download_button(
            "📥 Download Exam Results",
            data=_dl,
            file_name=f"MockExam_{_me_rating}_{_me_paygrade}.txt",
            mime="text/plain",
            use_container_width=True,
        )

# SCORE HISTORY CHART
if "score_history" in st.session_state and len(st.session_state.score_history) > 0:
    st.divider()
    st.subheader("📈 Your Score History")
    st.caption("Track your improvement over time.")
    history_df = pd.DataFrame(st.session_state.score_history)
    st.line_chart(history_df.set_index("date")["pct"])
    st.dataframe(history_df[["date", "topic", "score", "total", "pct"]].rename(columns={
        "date": "Date", "topic": "Topic", "score": "Score", "total": "Total", "pct": "% Correct"
    }), use_container_width=True)