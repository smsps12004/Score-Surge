import streamlit as st
import pandas as pd
import re
import tempfile
import os
from fpdf import FPDF
import anthropic

try:
    import fitz
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

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

DEFAULT_VALUES = {
    "exam_score": 42.0,
    "pma": 4.2,
    "tir": 3.0,
    "awards": 2.0,
    "education": 1.0,
    "pna": 0.5,
}

import base64

VISION_PROMPT = (
    "This is a Navy advancement profile sheet. "
    "Extract and return ONLY a JSON object with these exact keys: "
    "exam_score, pma, tir, awards, education, pna. "
    "exam_score = Exam Standard Score (number, typically between 20 and 80). "
    "pma = Performance Mark Average (decimal between 1.0 and 5.0). "
    "tir = Time in Rate in years (decimal, e.g. 2.0 or 3.5). "
    "awards = Awards points (decimal). "
    "education = Education points (decimal). "
    "pna = Passed Not Advanced points (decimal, 0 to 9). "
    "Use null for any value you cannot clearly read. Return ONLY valid JSON, no other text."
)


def extract_fields_from_upload(uploaded_file):
    """
    Extract FMS fields from a profile sheet using Claude vision.
    PDFs are rendered to images via PyMuPDF. Image files are sent directly.
    Returns (fields_dict, method_label).
    """
    import json
    suffix = os.path.splitext(uploaded_file.name)[1].lower()
    file_bytes = uploaded_file.read()
    images_b64 = []  # list of (b64_data, media_type)

    if suffix == ".pdf":
        if not PDF_AVAILABLE:
            st.error("PyMuPDF not installed. Run: pip install pymupdf — or enter values manually.")
            return {}, "error"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            doc = fitz.open(tmp_path)
            for page in doc:
                pix = page.get_pixmap(dpi=200)
                images_b64.append((
                    base64.standard_b64encode(pix.tobytes("png")).decode(),
                    "image/png",
                ))
        finally:
            os.unlink(tmp_path)
        method = "claude-vision-pdf"
    else:
        media_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
        media_type = media_types.get(suffix, "image/jpeg")
        images_b64.append((base64.standard_b64encode(file_bytes).decode(), media_type))
        method = "claude-vision-image"

    if not images_b64:
        return {}, "error"

    content = []
    for b64_data, media_type in images_b64:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64_data},
        })
    content.append({"type": "text", "text": VISION_PROMPT})

    try:
        cl = anthropic.Anthropic()
        msg = cl.messages.create(
            model="claude-opus-4-5",
            max_tokens=256,
            messages=[{"role": "user", "content": content}]
        )
        fields = json.loads(msg.content[0].text.strip())
        return fields, method
    except Exception as e:
        st.error(f"Claude vision extraction failed: {e}")
        return {}, "error"


# OCR UPLOAD SECTION
st.subheader("📤 Upload Profile Sheet (Optional)")
uploaded_file = st.file_uploader(
    "Upload your Navy Profile Sheet — image or PDF",
    type=["png", "jpg", "jpeg", "pdf"],
    help="The app will try to read your scores automatically. You can always edit them below.",
)

extracted_data = DEFAULT_VALUES.copy()

if uploaded_file is not None:
    with st.spinner("Reading your profile sheet with Claude vision..."):
        claude_fields, read_method = extract_fields_from_upload(uploaded_file)

    if claude_fields:
        for field in DEFAULT_VALUES:
            val = claude_fields.get(field)
            if val is not None:
                try:
                    extracted_data[field] = float(val)
                except (TypeError, ValueError):
                    pass

        missing_fields = [f for f in DEFAULT_VALUES if extracted_data[f] == DEFAULT_VALUES[f]]

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
    else:
        st.error("Could not read the document. Try a clearer image or enter values manually.")


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