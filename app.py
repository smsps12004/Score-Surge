import streamlit as st
import pandas as pd
import re
import tempfile
import os
from fpdf import FPDF
import anthropic

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

# PAGE CONFIG
st.set_page_config(page_title="Score Surge", page_icon="⚓", layout="centered")

st.title("⚓ Score Surge | by Strategic Sailor")
st.markdown("""
Your Navy advancement engine. Calculate your FMS, build your study plan, and advance.
| Cycle | Min FMS to Advance |
|-------|--------------------|
| 271   | **44.0**           |
""")

# CONSTANTS
MIN_FMS = 44.0

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
    "pma": 4.2,
    "tir": 3.0,
    "awards": 2.0,
    "education": 1.0,
    "pna": 0.5,
}

# SMART OCR PARSER
def extract_number_near_label(text, patterns):
    text_lower = text.lower()
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            after = text[match.end(): match.end() + 80]
            num_match = re.search(r"\b(\d{1,3}(?:\.\d{1,2})?)\b", after)
            if num_match:
                return float(num_match.group(1))
    return None


def parse_ocr_text(raw_text):
    results = {}
    missing = []
    for field, patterns in LABEL_PATTERNS.items():
        value = extract_number_near_label(raw_text, patterns)
        if value is not None:
            results[field] = value
        else:
            results[field] = DEFAULT_VALUES[field]
            missing.append(field)
    return results, missing


def extract_text_from_upload(uploaded_file):
    raw_text = ""
    suffix = os.path.splitext(uploaded_file.name)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.read())
        tmp_path = tmp.name

    try:
        if uploaded_file.type == "application/pdf":
            if not OCR_PDF_AVAILABLE:
                st.error("PyMuPDF not installed. Run: python3 -m pip install pymupdf")
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


# OCR UPLOAD SECTION
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
        if not missing_fields:
            st.success("✅ All fields extracted! Review and edit below if needed.")
        else:
            st.warning(
                "Could not auto-detect: **" + ", ".join(missing_fields) + "**. "
                "Default values used — please fill them in manually."
            )
        with st.expander("🔍 Show raw OCR text (for debugging)"):
            st.text(raw_text[:2000])
    else:
        st.error("Could not extract text. Try a clearer image or enter values manually.")


# INPUT FORM
st.subheader("📋 Enter or Edit Your Scores")

with st.form("fms_form"):
    sailor_name = st.text_input("Sailor Name / Rate", value="SailorX")

    col1, col2 = st.columns(2)

    with col1:
        exam_score = st.number_input(
            "Exam Standard Score",
            min_value=0.0, max_value=80.0,
            value=float(extracted_data["exam_score"]),
            step=0.5,
        )
        pma = st.number_input(
            "PMA (Eval Average)",
            min_value=0.0, max_value=5.0,
            value=float(extracted_data["pma"]),
            step=0.01,
        )
        tir = st.number_input(
            "Time in Rate (Years)",
            min_value=0.0, max_value=10.0,
            value=float(extracted_data["tir"]),
            step=0.5,
        )

    with col2:
        awards = st.number_input(
            "Awards Points",
            min_value=0.0, max_value=10.0,
            value=float(extracted_data["awards"]),
            step=0.5,
        )
        education = st.number_input(
            "Education Points",
            min_value=0.0, max_value=2.0,
            value=float(extracted_data["education"]),
            step=0.5,
        )
        pna = st.number_input(
            "PNA Points",
            min_value=0.0, max_value=9.0,
            value=float(extracted_data["pna"]),
            step=0.5,
        )

    submitted = st.form_submit_button("📊 Calculate My FMS", use_container_width=True)


# CALCULATION & RESULTS
if submitted:
    fms = round(exam_score + (pma * 9) + tir + awards + education + pna, 2)
    passed = fms >= MIN_FMS
    gap = round(MIN_FMS - fms, 2) if not passed else 0.0

    st.subheader("📊 Your Results")
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Final Multiple Score", f"{fms}")
    col_b.metric("Minimum to Advance", f"{MIN_FMS}")
    col_c.metric("Status", "✅ Eligible" if passed else "❌ Not Yet")

    if not passed:
        st.error(f"You need **{gap} more points** to reach the cutoff of {MIN_FMS}.")
    else:
        st.success("You meet the minimum FMS! Focus on maximizing your score for a better rank.")

    # Score Breakdown
    st.subheader("📉 Score Breakdown")
    breakdown = {
        "Exam Score": exam_score,
        "PMA (x9)": round(pma * 9, 2),
        "Time in Rate": tir,
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
        guide_items.append({
            "area": "PMA / Eval Performance",
            "priority": "HIGH" if pma < 4.0 else "MEDIUM",
            "current": str(pma) + " (worth " + str(round(pma * 9, 2)) + " pts)",
            "target": "4.4+ (worth " + str(round(4.4 * 9, 2)) + " pts)",
            "gain": round((4.4 - pma) * 9, 2),
            "actions": [
                "Talk to your supervisor about your eval standing.",
                "Volunteer for additional duties and qualifications.",
                "Document all accomplishments — do not wait until eval time.",
                "Pursue a warfare qualification if not already earned.",
                "Request a mid-term counseling session.",
            ],
        })

    if awards < 5:
        guide_items.append({
            "area": "Awards",
            "priority": "MEDIUM",
            "current": awards,
            "target": "5-10",
            "gain": round(5 - awards, 1),
            "actions": [
                "Talk to your LPO or Chief about submitting an award write-up.",
                "Track achievements that qualify for a NAM.",
                "Ensure all past awards are in your service record.",
                "Participate in community service events.",
            ],
        })

    if education < 2.0:
        guide_items.append({
            "area": "Education",
            "priority": "MEDIUM" if education < 1.0 else "LOW",
            "current": education,
            "target": 2.0,
            "gain": round(2.0 - education, 1),
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
    st.dataframe(
        pd.DataFrame([{
            "Sailor": sailor_name,
            "Exam": exam_score,
            "PMA": pma,
            "PMA pts": round(pma * 9, 2),
            "TIR": tir,
            "Awards": awards,
            "Education": education,
            "PNA": pna,
            "FMS": fms,
            "Status": "PASS" if passed else "FAIL",
            "Gap": gap,
        }]),
        use_container_width=True,
    )

    # PDF Report
    st.subheader("📥 Download Report")

    def generate_pdf(name, fms, passed, gap, exam_score, pma, tir, awards, education, pna, guide_items):
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", "B", 16)
        pdf.cell(0, 10, "Navy FMS Report - " + name, ln=True, align="C")
        pdf.set_font("Arial", "", 10)
        pdf.cell(0, 8, "Cycle 267 | Minimum FMS Required: " + str(MIN_FMS), ln=True, align="C")
        pdf.ln(6)
        pdf.set_font("Arial", "B", 14)
        pdf.cell(0, 10, "Final Multiple Score: " + str(fms) + "   |   Status: " + ("ELIGIBLE" if passed else "NOT YET"), ln=True)
        if not passed:
            pdf.set_font("Arial", "", 11)
            pdf.cell(0, 8, "Points needed to advance: " + str(gap), ln=True)
        pdf.ln(4)
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 8, "Score Breakdown:", ln=True)
        pdf.set_font("Arial", "", 11)
        for label, val in [
            ("Exam Standard Score", exam_score),
            ("PMA (x9)", round(pma * 9, 2)),
            ("Time in Rate", tir),
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

    pdf_path = generate_pdf(sailor_name, fms, passed, gap, exam_score, pma, tir, awards, education, pna, guide_items)
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
    sg_api_key = st.text_input("Your Claude API Key", type="password", placeholder="sk-ant-...")
    sg_submit = st.form_submit_button("Generate My Study Guide", use_container_width=True)

if sg_submit:
    if not sg_api_key:
        st.error("Enter your Claude API key to generate a guide.")
    else:
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
You know NAVADMIN 008/26 (Cycle 271) inside and out.

CYCLE 271 FACTS (NAVADMIN 008/26):
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
                client = anthropic.Anthropic(api_key=sg_api_key)
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

tutor_api_key = st.text_input("Claude API Key", type="password", key="tutor_key", placeholder="sk-ant-...")

if st.button("📖 Start Lesson", use_container_width=True):
    if not tutor_api_key:
        st.error("Enter your Claude API key.")
    else:
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
                client = anthropic.Anthropic(api_key=tutor_api_key)
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
                st.session_state.tutor_key_saved = tutor_api_key

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
                    client = anthropic.Anthropic(api_key=st.session_state.tutor_key_saved)
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
import datetime

st.subheader("⏱️ Cycle 271 Countdown")

today = datetime.date.today()
deadlines = [
    ("PMK-EE Deadline", datetime.date(2026, 1, 31)),
    ("ILDC Deadline (E6)", datetime.date(2026, 2, 28)),
    ("E6 Exam Day", datetime.date(2026, 3, 5)),
    ("E5 Exam Day", datetime.date(2026, 3, 12)),
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

# PRACTICE QUESTION MODE
st.subheader("🎯 Practice Question Mode")
st.caption("Answer like the exam is tomorrow. The Chief will grade you and explain every answer.")

with st.form("practice_form"):
    col1, col2 = st.columns(2)
    with col1:
        pq_topic = st.selectbox("Topic", list(PS_TOPICS.keys()), key="pq_topic")
    with col2:
        pq_num = st.selectbox("Number of Questions", [3, 5, 10], key="pq_num")
    pq_api_key = st.text_input("Claude API Key", type="password", key="pq_key", placeholder="sk-ant-...")
    pq_submit = st.form_submit_button("Generate Practice Questions", use_container_width=True)

if pq_submit:
    if not pq_api_key:
        st.error("Enter your Claude API key.")
    else:
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
                client = anthropic.Anthropic(api_key=pq_api_key)
                message = client.messages.create(
                    model="claude-opus-4-5",
                    max_tokens=2000,
                    messages=[{"role": "user", "content": pq_prompt}]
                )
                questions_text = message.content[0].text
                st.session_state.practice_questions = questions_text
                st.session_state.pq_key_saved = pq_api_key
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
                    client = anthropic.Anthropic(api_key=st.session_state.pq_key_saved)
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
st.divider()
st.subheader("👨‍✈️ Ask the Chief")
st.caption("Got a work question? Ask the Chief. He knows every PS instruction and has no patience for wrong answers.")

expert_api_key = st.text_input("Claude API Key", type="password", key="expert_key", placeholder="sk-ant-...")
work_question = st.text_area(
    "What's your question?",
    placeholder="e.g. A sailor wants to know if they can take leave during a PCS move. What are the rules?",
    height=100
)

if st.button("Ask the Chief", use_container_width=True):
    if not expert_api_key:
        st.error("Enter your Claude API key.")
    elif not work_question:
        st.error("Type your question first.")
    else:
        expert_prompt = f"""You are a senior PS Chief Petty Officer with 20 years of experience.
You know every Navy instruction relevant to the PS rating inside and out including:
- MILPERSMAN (all 1000, 1050, 1070, 1160, 1300, 1306, 1320, 1600, 1740, 1770, 1800, 1830, 1910 series)
- BUPERSINST 1430.16G (Advancement Manual)
- BUPERSINST 1900.8F (DD214 / Separations)
- BUPERSINST 1750.10E (ID Cards / DEERS)
- JTR (Joint Travel Regulations)
- DOD 7000.14-R Volumes 5, 7A, 9 (Pay and Travel)
- OPNAVINST 1160.8B (SRB)
- RESPERS M-1001.5 (Reserve Personnel)
- SECNAVINST 1650.1J (Awards)
- NAVSUP P-727 (Navy Cash)
- Navy DJMS Procedures Training Guide
- NAVADMIN 008/26 (Cycle 271)

A PS sailor at the front office just asked you this question:
{work_question}

Answer like you are talking directly to that sailor face to face.
Be direct and clear.
Give the correct answer first, then explain why.
Cite the exact instruction, article, or chapter that governs this.
If there are exceptions or edge cases, mention them.
No fluff. No wasted words.
If you are not certain, say so and tell them where to verify."""

        with st.spinner("Chief is looking that up..."):
            try:
                client = anthropic.Anthropic(api_key=expert_api_key)
                message = client.messages.create(
                    model="claude-opus-4-5",
                    max_tokens=1500,
                    messages=[{"role": "user", "content": expert_prompt}]
                )
                answer = message.content[0].text
                st.subheader("Chief says:")
                st.markdown(answer)
                st.download_button(
                    "📥 Save This Answer",
                    data=f"QUESTION:\n{work_question}\n\nCHIEF'S ANSWER:\n{answer}",
                    file_name="ChiefAnswer.txt",
                    mime="text/plain",
                    use_container_width=True
                )
            except Exception as e:
                st.error("Error: " + str(e))# PS HOT TOPICS
st.divider()
st.subheader("🔥 PS Hot Topics")
st.caption("Stay current. The Chief keeps you informed on everything happening in the PS world.")

hot_api_key = st.text_input("Claude API Key", type="password", key="hot_key", placeholder="sk-ant-...")

col1, col2 = st.columns(2)

with col1:
    if st.button("📰 Get Daily PS Briefing", use_container_width=True):
        if not hot_api_key:
            st.error("Enter your Claude API key.")
        else:
            briefing_prompt = """You are a senior PS Chief Petty Officer giving a morning briefing to your PS shop.

Cover the following in your briefing:
1. NWAE Cycle 271 status and what sailors should be doing RIGHT NOW
2. Any recent NAVADMIN messages relevant to PS rating (advancement, pay, personnel policy)
3. Current MNCC policy updates affecting PS work
4. Upcoming deadlines PS sailors need to know about
5. Any changes to governing instructions (MILPERSMAN, BUPERSINST, JTR, etc.)
6. One piece of advice for PS sailors this week

Format it like a real morning briefing — direct, organized, no fluff.
If something is time-sensitive, flag it clearly.
Cite specific NAVADMINs or instructions where applicable.
Keep it under 500 words — sailors are busy."""

            with st.spinner("Chief is preparing the morning briefing..."):
                try:
                    client = anthropic.Anthropic(api_key=hot_api_key)
                    message = client.messages.create(
                        model="claude-opus-4-5",
                        max_tokens=1500,
                        messages=[{"role": "user", "content": briefing_prompt}]
                    )
                    briefing = message.content[0].text
                    st.subheader("📋 Morning Briefing")
                    st.markdown(briefing)
                    st.download_button(
                        "📥 Save Briefing",
                        data=briefing,
                        file_name="PS_Morning_Briefing.txt",
                        mime="text/plain",
                        use_container_width=True
                    )
                except Exception as e:
                    st.error("Error: " + str(e))

with col2:
    hot_topics_list = [
        "Latest NAVADMIN affecting PS rating",
        "Cycle 271 advancement cutoffs",
        "MNCC recent policy updates",
        "Changes to JTR travel policy",
        "BAH rate updates",
        "SRB program changes",
        "ID card / DEERS policy updates",
        "Separation and retirement processing changes",
        "Reserve component PS updates",
        "Officer programs open to PS sailors",
    ]
    selected_topic = st.selectbox("Quick Topic Lookup", hot_topics_list, key="hot_topic_select")
    if st.button("🔍 Brief Me On This", use_container_width=True):
        if not hot_api_key:
            st.error("Enter your Claude API key.")
        else:
            topic_prompt = f"""You are a senior PS Chief Petty Officer.
A sailor just asked you to brief them on: {selected_topic}

Give them:
1. Current status — what's the latest on this topic
2. What it means for PS sailors specifically
3. Any action items or deadlines
4. Exact instructions or NAVADMINs that govern this
5. Common mistakes sailors make on this topic

Be direct. Cite your sources. No fluff."""

            with st.spinner(f"Chief is looking up {selected_topic}..."):
                try:
                    client = anthropic.Anthropic(api_key=hot_api_key)
                    message = client.messages.create(
                        model="claude-opus-4-5",
                        max_tokens=1000,
                        messages=[{"role": "user", "content": topic_prompt}]
                    )
                    topic_answer = message.content[0].text
                    st.subheader(f"📌 {selected_topic}")
                    st.markdown(topic_answer)
                except Exception as e:
                    st.error("Error: " + str(e))

st.divider()
st.subheader("🔎 Search Any PS Topic")
st.caption("Type anything — policy, instruction, process, entitlement. The Chief will find it.")

custom_topic = st.text_input("Search topic", placeholder="e.g. posthumous promotion, SDAP eligibility, erroneous enlistment")
if st.button("Search", use_container_width=True):
    if not hot_api_key:
        st.error("Enter your Claude API key.")
    elif not custom_topic:
        st.error("Type a topic to search.")
    else:
        search_prompt = f"""You are a senior PS Chief Petty Officer.
A sailor asked you about: {custom_topic}

Give them everything they need to know:
1. What this is in plain English
2. The governing instruction(s) with specific articles or chapters
3. Current policy and any recent changes
4. How it applies to their day-to-day PS work
5. Common errors or misconceptions
6. Where to go for more info (NSIPS, MNCC, MyNavyHR, etc.)

Be the expert. Cite everything. No fluff."""

        with st.spinner("Chief is on it..."):
            try:
                client = anthropic.Anthropic(api_key=hot_api_key)
                message = client.messages.create(
                    model="claude-opus-4-5",
                    max_tokens=1500,
                    messages=[{"role": "user", "content": search_prompt}]
                )
                search_result = message.content[0].text
                st.subheader(f"📖 {custom_topic}")
                st.markdown(search_result)
                st.download_button(
                    "📥 Save This",
                    data=f"TOPIC: {custom_topic}\n\n{search_result}",
                    file_name=f"PS_Topic_{custom_topic[:30].replace(' ','_')}.txt",
                    mime="text/plain",
                    use_container_width=True
                )
            except Exception as e:
                st.error("Error: " + str(e))# EVAL BULLET GENERATOR
st.divider()
st.subheader("📝 Eval Bullet Generator")
st.caption("Describe what the sailor did. The Chief will write it in proper Navy eval format.")

eval_api_key = st.text_input("Claude API Key", type="password", key="eval_key", placeholder="sk-ant-...")

with st.form("eval_form"):
    col1, col2 = st.columns(2)
    with col1:
        eval_rank = st.selectbox("Sailor's Rank", ["E1","E2","E3","E4","E5","E6","E7","E8","E9"])
        eval_rate = st.text_input("Rate", placeholder="e.g. PS2, YN1, BM3")
    with col2:
        eval_trait = st.selectbox("Eval Trait", [
            "Professional Knowledge",
            "Quality of Work",
            "Command or Organizational Climate/Equal Opportunity",
            "Military Bearing/Character",
            "Personal Job Accomplishment/Initiative",
            "Teamwork",
            "Leadership",
            "Collateral Duties",
        ])
        eval_impact = st.selectbox("Impact Level", ["Individual", "Division", "Department", "Command", "Fleet/Navy"])
    
    achievement = st.text_area(
        "Describe the achievement or performance (be specific — numbers, results, timeframes)",
        placeholder="e.g. Processed 47 PCS transfers in 30 days with zero errors during a major deployment surge. Trained 2 junior PS on NSIPS procedures.",
        height=120
    )
    eval_submitted = st.form_submit_button("Generate Eval Bullet", use_container_width=True)

if eval_submitted:
    if not eval_api_key:
        st.error("Enter your Claude API key.")
    elif not achievement:
        st.error("Describe the achievement first.")
    else:
        eval_prompt = f"""You are a senior PS Chief Petty Officer who writes exceptional Navy performance evaluations.

Write a Navy eval bullet for the following:
- Sailor: {eval_rate} ({eval_rank})
- Eval Trait: {eval_trait}
- Impact Level: {eval_impact}
- Achievement: {achievement}

Rules for Navy eval bullets:
1. Start with a strong action verb in ALL CAPS
2. Be specific — include numbers, percentages, timeframes when possible
3. Show impact at the {eval_impact} level
4. Use Navy-specific terminology and abbreviations correctly
5. Keep it to 1-2 sentences maximum
6. End with the impact on the command/Navy mission
7. Do NOT use "I" — write in third person

Write 3 versions of the bullet — ranked from good to best.
Label them VERSION 1, VERSION 2, VERSION 3.
After the bullets, explain what makes VERSION 3 the strongest."""

        with st.spinner("Chief is writing your eval bullet..."):
            try:
                client = anthropic.Anthropic(api_key=eval_api_key)
                message = client.messages.create(
                    model="claude-opus-4-5",
                    max_tokens=1000,
                    messages=[{"role": "user", "content": eval_prompt}]
                )
                eval_result = message.content[0].text
                st.subheader("✅ Your Eval Bullets")
                st.markdown(eval_result)
                st.download_button(
                    "📥 Save Eval Bullets",
                    data=f"RATE: {eval_rate}\nTRAIT: {eval_trait}\nACHIEVEMENT: {achievement}\n\n{eval_result}",
                    file_name=f"EvalBullet_{eval_rate.replace(' ','_')}.txt",
                    mime="text/plain",
                    use_container_width=True
                )
            except Exception as e:
                st.error("Error: " + str(e))# LES DECODER
st.divider()
st.subheader("💰 LES Decoder")
st.caption("Paste your LES data and the Chief will explain every line in plain English.")

les_api_key = st.text_input("Claude API Key", type="password", key="les_key", placeholder="sk-ant-...")

les_data = st.text_area(
    "Paste your LES information here (copy from MyPay)",
    placeholder="""Paste any part of your LES here. Examples:
- Your entitlements (BAH, BAS, special pays)
- Your deductions (SGLI, TSP, taxes)
- Your leave balance
- Any codes or amounts you don't understand""",
    height=200
)

if st.button("Decode My LES", use_container_width=True):
    if not les_api_key:
        st.error("Enter your Claude API key.")
    elif not les_data:
        st.error("Paste your LES data first.")
    else:
        les_prompt = f"""You are a senior PS Chief Petty Officer and pay expert.
A sailor just pasted their LES data and needs you to explain it in plain English.

LES DATA:
{les_data}

Do the following:
1. Explain each entitlement line — what it is, why they get it, and how it's calculated
2. Explain each deduction line — what it is and why it's being taken out
3. Flag anything that looks wrong, unusual, or that they should verify
4. Explain their leave balance if present
5. Give them 2-3 action items if anything needs attention

Use plain English — explain it like they've never seen a pay statement before.
Be specific about the dollar amounts they provided.
Cite the governing regulation for any pay or deduction (DOD FMR Vol 7A, JTR, etc.)
If something looks off, tell them straight up and tell them who to contact.

Format your response with clear sections so it's easy to read."""

        with st.spinner("Chief is reviewing your LES..."):
            try:
                client = anthropic.Anthropic(api_key=les_api_key)
                message = client.messages.create(
                    model="claude-opus-4-5",
                    max_tokens=2000,
                    messages=[{"role": "user", "content": les_prompt}]
                )
                les_result = message.content[0].text
                st.subheader("📋 Your LES Explained")
                st.markdown(les_result)
                st.download_button(
                    "📥 Save LES Explanation",
                    data=f"LES DATA:\n{les_data}\n\nCHIEF'S EXPLANATION:\n{les_result}",
                    file_name="LES_Explanation.txt",
                    mime="text/plain",
                    use_container_width=True
                )
            except Exception as e:
                st.error("Error: " + str(e))