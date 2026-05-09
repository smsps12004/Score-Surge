import streamlit as st
import pandas as pd
import re
import json
import tempfile
import os
import datetime
from fpdf import FPDF
import anthropic

try:
    import fitz
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])

# PAGE CONFIG
st.set_page_config(page_title="Score Surge", page_icon="⚓", layout="centered")

# CONSTANTS — FMS formula per BUPERSINST 1430.16G (E4-E6 FMS Chart)
# E5: FMS = SS + (PMA*80 - 256) + SIPG/5 (cap 2) + Awards (cap 10) + Education (0/2/4) + PNA (cap 9). Max 169.
# E6: FMS = SS + (RSCA_PMA*30 - 60) + SIPG/5 (cap 3) + Awards (cap 12) + Education (0/2/4) + PNA (cap 9). Max 222.
MAX_FMS = {"E5": 169.0, "E6": 222.0}
AWARDS_MAX = {"E5": 10.0, "E6": 12.0}
SIPG_POINTS_MAX = {"E5": 2.0, "E6": 3.0}

# UPLOAD SAFETY — keep token costs predictable and prevent abuse on the public app.
MAX_UPLOAD_MB = 8       # Profile sheets fit easily under this; Streamlit default is 200 MB.
MAX_PDF_PAGES = 3       # Profile sheets are 1-2 pages. Cap protects API spend on bad uploads.

# CURRENT CYCLE CONFIG — single source of truth.
# When the next NAVADMIN drops, update only this block.
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
    "next_cycle_note": "Cycle 271 has closed. Watch MyNavyHR for the next cycle's NAVADMIN.",
}

st.title("⚓ Score Surge | by Strategic Sailor")
st.markdown(
    f"""
Your Navy advancement engine. Calculate your FMS, build your study plan, and advance.

**Cycle {CURRENT_CYCLE['number']} — {CURRENT_CYCLE['title']}.** Selection cutoffs are published per rate after each cycle. There is no fixed minimum FMS — your standing depends on your rate's specific cutoff and quotas.
"""
)

DEFAULT_VALUES = {
    "exam_score": 0.0,
    "pma": 0.0,
    "sipg_months": 0.0,
    "awards": 0.0,
    "education": 0.0,
    "pna": 0.0,
}

import base64

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

    try:
        msg = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=256,
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

        if read_method != "error" and claude_fields is not None:
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
            min_value=0.0, max_value=80.0,
            value=min(80.0, max(0.0, float(extracted_data["exam_score"]))),
            step=0.5,
            help="Your raw exam score from the profile sheet.",
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
            help="Top 25% in SS and PMA earn PNA points each cycle they pass but aren't advanced. Cap is 9.",
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
if submitted:
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

    if pna == 0:
        guide_items.append({
            "area": "PNA Points",
            "priority": "INFO",
            "current": 0,
            "target": "Accumulates automatically",
            "gain": "up to 9",
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
        pdf.cell(0, 10, "Score Surge FMS Report - " + name, ln=True, align="C")
        pdf.set_font("Arial", "", 10)
        pdf.cell(0, 8, f"Cycle {CURRENT_CYCLE['number']} | Paygrade: {paygrade} | Max FMS for {paygrade}: {max_fms:.0f}", ln=True, align="C")
        pdf.ln(6)
        pdf.set_font("Arial", "B", 14)
        pdf.cell(0, 10, f"Final Multiple Score: {fms}   |   {pct_of_max}% of max", ln=True)
        pdf.set_font("Arial", "", 10)
        pdf.multi_cell(0, 6, "Selection cutoffs are published per rate after each cycle. There is no fixed minimum FMS - your standing depends on your rate's specific cutoff.")
        pdf.ln(2)
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 8, "Score Breakdown:", ln=True)
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
            pdf.cell(0, 7, "  " + label + ": " + str(val), ln=True)
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

    pdf_path = generate_pdf(sailor_name, paygrade, fms, max_fms, pct_of_max, exam_score, pma, pma_points, sipg_months, sipg_points, awards, education, pna, guide_items)
    with open(pdf_path, "rb") as f:
        st.download_button(
            label="📥 Download PDF Report",
            data=f,
            file_name="FMS_Report_" + sailor_name.replace(" ", "_") + ".pdf",
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
        sg_rating = st.selectbox("Your Rating", ["PS", "YN", "IT", "BM", "MM", "EM", "HM", "MA"])
        sg_paygrade = st.selectbox("Your Paygrade", ["E4", "E5", "E6", "E7"])
    with col2:
        sg_gap = st.number_input("Your FMS Gap (0 if eligible)", min_value=0.0, max_value=30.0, value=0.0, step=0.5)
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

    prompt = f"""You are a senior {sg_rating} Chief Petty Officer with 20 years of service.
You drink too much coffee, you have zero patience for excuses, and you genuinely want your sailors to advance.
You are direct, blunt, and efficient. No fluff. No wasted words.
You know {CURRENT_CYCLE['navadmin']} (Cycle {CURRENT_CYCLE['number']}) inside and out.

CYCLE {CURRENT_CYCLE['number']} FACTS ({CURRENT_CYCLE['navadmin']}):
- E6 exam date: 5 March 2026
- E5 exam date: 12 March 2026
- Terminal Eligibility Date: 1 July 2026
- PMK-EE deadline: 31 January 2026
- ILDC deadline: 28 February 2026 (E6 only)
- Min TIR E6: 1 July 2023
- Min TIR E5: 1 July 2025
- PMA window E6: 1 March 2023 to 28 February 2026
- PMA window E5: 1 December 2024 to 28 February 2026
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
            message = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}]
            )
            guide_text = message.content[0].text
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

col1, col2 = st.columns(2)
with col1:
    tutor_topic = st.selectbox("Select a Topic to Study", list(PS_TOPICS.keys()))
with col2:
    tutor_subtopic = st.selectbox("Select a Subtopic", PS_TOPICS[tutor_topic]["subtopics"])

if st.button("📖 Start Lesson", use_container_width=True):
    bib_refs = PS_TOPICS[tutor_topic]["bib"]
    lesson_prompt = f"""You are a senior PS Chief Petty Officer with 20 years of experience.
You are teaching a Navy advancement exam lesson to a busy young sailor who needs to pass the PS {tutor_topic[:2]} NWAE.
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

    with st.spinner("Chief is preparing your lesson..."):
        try:
            message = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=2000,
                messages=[{"role": "user", "content": lesson_prompt}]
            )
            lesson = message.content[0].text

            st.subheader(f"📚 Lesson: {tutor_subtopic}")
            st.markdown(lesson)

            if "tutor_history" not in st.session_state:
                st.session_state.tutor_history = []
            st.session_state.tutor_history = [
                {"role": "user", "content": lesson_prompt},
                {"role": "assistant", "content": lesson}
            ]
            st.session_state.tutor_topic = tutor_topic
            st.session_state.tutor_subtopic = tutor_subtopic

            st.download_button(
                "📥 Download This Lesson",
                data=lesson,
                file_name=f"Lesson_{tutor_subtopic.replace(' ', '_')}.txt",
                mime="text/plain",
                use_container_width=True
            )
        except Exception as e:
            st.error("Error: " + str(e))

# Follow-up Q&A
if "tutor_history" in st.session_state and len(st.session_state.tutor_history) > 0:
    st.subheader("💬 Ask the Chief a Question")
    st.caption("Type any follow-up question about this topic.")
    sailor_question = st.text_input("Your question", placeholder="e.g. What happens if a sailor misses the travel claim deadline?")
    if st.button("Ask", use_container_width=True):
        if sailor_question:
            with st.spinner("Chief is thinking..."):
                try:
                    history = st.session_state.tutor_history.copy()
                    history.append({"role": "user", "content": sailor_question})
                    message = client.messages.create(
                        model="claude-opus-4-5",
                        max_tokens=1000,
                        messages=history
                    )
                    answer = message.content[0].text
                    st.session_state.tutor_history.append({"role": "assistant", "content": answer})
                    st.markdown("**Chief says:**")
                    st.markdown(answer)
                except Exception as e:
                    st.error("Error: " + str(e))
# SCORE HISTORY TRACKER
if "score_history" not in st.session_state:
    st.session_state.score_history = []

# CYCLE COUNTDOWN
st.subheader(f"⏱️ Cycle {CURRENT_CYCLE['number']} Countdown")

today = datetime.date.today()

# Only show deadlines that haven't already passed.
upcoming = [(label, date) for (label, date) in CURRENT_CYCLE["deadlines"] if (date - today).days >= 0]

if upcoming:
    # Show one tile per upcoming deadline, color-coded by urgency.
    cols = st.columns(len(upcoming))
    for i, (label, date) in enumerate(upcoming):
        days_left = (date - today).days
        if days_left <= 14:
            status = f"🔴 {days_left} days"
        elif days_left <= 30:
            status = f"🟡 {days_left} days"
        else:
            status = f"🟢 {days_left} days"
        cols[i].metric(label, status)
else:
    # All this cycle's deadlines are in the past — say so cleanly instead of showing 4 "Passed" tiles.
    st.info(
        f"📋 **{CURRENT_CYCLE['next_cycle_note']}** "
        f"In the meantime, keep using the AI Study Guide and Practice Question Mode to stay sharp."
    )

st.divider()

# PRACTICE QUESTION MODE
st.subheader("🎯 Practice Question Mode")
st.caption("Answer like the exam is tomorrow. The Chief will grade you and explain every answer.")

with st.form("practice_form"):
    col1, col2 = st.columns(2)
    with col1:
        pq_topic = st.selectbox("Topic", list(PS_TOPICS.keys()), key="pq_topic")
    with col2:
        pq_num = st.selectbox("Number of Questions", [3, 5, 10], key="pq_num")
    pq_submit = st.form_submit_button("Generate Practice Questions", use_container_width=True)

if pq_submit:
    bib_refs = PS_TOPICS[pq_topic]["bib"]
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

    with st.spinner("Chief is writing your exam..."):
        try:
            message = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=2000,
                messages=[{"role": "user", "content": pq_prompt}]
            )
            questions_text = message.content[0].text
            st.session_state.practice_questions = questions_text
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
                    grade_prompt = f"""You are a PS Chief grading a sailor's practice exam.
Questions: {st.session_state.practice_questions}
Sailor's answers: {sailor_answers}
Grade each answer. State correct or incorrect. Explain the right answer. Reference the regulation. Give final score. One line of honest feedback. Be direct. No fluff."""
                    message = client.messages.create(
                        model="claude-opus-4-5",
                        max_tokens=1500,
                        messages=[{"role": "user", "content": grade_prompt}]
                    )
                    grade_result = message.content[0].text
                    st.subheader("📊 Your Grade")
                    st.markdown(grade_result)
                    import re as re2
                    score_match = re2.search(r'(\d+)\s*out\s*of\s*(\d+)', grade_result)
                    if score_match:
                        scored = int(score_match.group(1))
                        total = int(score_match.group(2))
                        if "score_history" not in st.session_state:
                            st.session_state.score_history = []
                        st.session_state.score_history.append({
                            "date": datetime.date.today().strftime("%b %d"),
                            "topic": pq_topic,
                            "score": scored,
                            "total": total,
                            "pct": round((scored/total)*100)
                        })
                    st.download_button(
                        "📥 Download Practice Results",
                        data=f"QUESTIONS:\n{st.session_state.practice_questions}\n\nANSWERS:\n{sailor_answers}\n\nGRADE:\n{grade_result}",
                        file_name="PracticeResults.txt",
                        mime="text/plain",
                        use_container_width=True
                    )
                except Exception as e:
                    st.error("Error: " + str(e))# SCORE HISTORY CHART
if "score_history" in st.session_state and len(st.session_state.score_history) > 0:
    st.divider()
    st.subheader("📈 Your Score History")
    st.caption("Track your improvement over time.")
    history_df = pd.DataFrame(st.session_state.score_history)
    st.line_chart(history_df.set_index("date")["pct"])
    st.dataframe(history_df[["date", "topic", "score", "total", "pct"]].rename(columns={
        "date": "Date", "topic": "Topic", "score": "Score", "total": "Total", "pct": "% Correct"
    }), use_container_width=True)# PS RATE EXPERT — ASK THE CHIEF