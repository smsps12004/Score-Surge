"""
DUTY DAY — prototype (Score Surge)

Standalone. Does NOT touch app.py, your database, or the live site.
Run it with:   streamlit run duty_day_prototype.py

Purpose: let you play one scenario end to end and react to the FEEL before
any of this gets built for real. Makes ZERO API calls — the whole scenario is
hand-written below, so playing it costs nothing.

>>> EVERY MILPERSMAN CITATION IN HERE NEEDS YOUR PS1 EYES BEFORE IT SHIPS. <<<
References were pulled from MyNavyHR and are listed in VERIFY_THESE at the
bottom of this file. If a procedure is subtly wrong, you will see it and I
will not.
"""

import streamlit as st

st.set_page_config(page_title="Duty Day — Prototype", page_icon="⚓", layout="centered")

# ─────────────────────────────────────────────────────────────────────────────
# THE SCENARIO
# In the real system this whole dict is produced by ONE API call and cached.
# Everything below plays with zero further calls.
# ─────────────────────────────────────────────────────────────────────────────
SCENARIO = {
    "id": "ps_reenlist_001",
    "rate": "PS",
    "paygrade": "E6",
    "topic": "Reenlistment & Extension Processing",
    "title": "The Friday Afternoon Reenlistment",
    "brief": (
        "It's 1530 on a Friday, PS2. Your ESO already left.\n\n"
        "**SN Alvarez** is standing at your window. He wants to reenlist **today**. "
        "He's chasing a bonus he says expires Monday, and he's getting loud about it.\n\n"
        "Here's what you know:\n"
        "- He has **14 months** left on his current enlistment\n"
        "- He has a **pending NJP** — mast is scheduled for next Thursday\n"
        "- His **CO has not signed a reenlistment recommendation**\n"
        "- He's 11 years in, so HYT isn't close\n\n"
        "He's telling you every other PS in the fleet would just push it through. "
        "What do you do?"
    ),

    # Beat 0 is always the same skill: find the governing instruction.
    "reference": {
        "prompt": "Before you touch a single form — which instruction governs whether "
                  "this Sailor may reenlist at all?",
        "choices": [
            ("MILPERSMAN 1160-030 — Enlistments and Reenlistments Under Continuous "
             "Service Conditions", True),
            ("MILPERSMAN 1070-240 — NAVPERS 1070/601, Immediate Reenlistment Contract", False),
            ("OPNAVINST 1160.8B — Selective Reenlistment Bonus", False),
            ("MILPERSMAN 1306-604 — Active Obligated Service (OBLISERV)", False),
        ],
        "why": (
            "**1160-030** is the eligibility gate — citizenship, medical, quality control, "
            "term limits, and the CO's recommendation all live there. The other three are "
            "real and you will use them, but later.\n\n"
            "1070-240 is the *contract* — that's paperwork you only reach once he's "
            "eligible. 1160.8B is the *bonus* — that's money, and money never decides "
            "eligibility. 1306-604 is OBLISERV for orders, a different problem entirely.\n\n"
            "This is the trap the whole scenario is built around: Alvarez is pushing you "
            "toward the bonus instruction because that's what he cares about. Eligibility "
            "comes first, every time."
        ),
        "points": 30,
    },

    "beats": [
        {
            "type": "choice",
            "chief": "Alright. You know where to look. Now what's your **first** move?",
            "prompt": "Your first action:",
            "choices": [
                ("Verify the CO's reenlistment recommendation before anything else", True,
                 "Correct. No recommendation, no reenlistment — full stop. Everything "
                 "downstream is wasted motion until you know that answer."),
                ("Pull up the bonus message to see if it really expires Monday", False,
                 "You just spent your Friday on money for a Sailor who may not be eligible. "
                 "The bonus is irrelevant if the CO won't recommend him."),
                ("Start the NAVPERS 1070/601 so it's ready to sign", False,
                 "You're building a contract for a Sailor you haven't established is "
                 "eligible. If mast goes badly next Thursday, you've created a document "
                 "that should never have existed."),
                ("Tell him to come back after mast and close your window", False,
                 "Defensible instinct, wrong execution. You still owe him an answer on "
                 "eligibility and a documented one. 'Come back later' isn't counseling."),
            ],
            "points": 20,
        },
        {
            "type": "choice",
            "chief": "You check. **There is no CO recommendation on file** — and with mast "
                     "pending, the chain isn't going to sign one before Thursday. "
                     "Alvarez is now telling you he'll lose the bonus and it's your fault.",
            "prompt": "What do you tell him?",
            "choices": [
                ("He is not currently eligible to reenlist; the CO's recommendation is "
                 "required by 1160-030 and cannot be waived at your level", True,
                 "That's the answer. Direct, correct, and it cites the reason. You are not "
                 "the authority that waives a CO recommendation, and neither is your ESO."),
                ("You'll process it now and get the recommendation signed retroactively", False,
                 "That is a falsified record. Careers have ended over less, and the Sailor's "
                 "would be one of them."),
                ("The bonus deadline creates an exception to the recommendation requirement",
                 False,
                 "No bonus message overrides eligibility in 1160-030. Money never unlocks "
                 "a gate that policy closed."),
                ("Route it to the ESO Monday and let them decide", False,
                 "Passing a known ineligibility up the chain without telling the Sailor "
                 "leaves him planning around a reenlistment that isn't going to happen."),
            ],
            "points": 20,
        },
        {
            "type": "order",
            "chief": "Alvarez calms down. Turns out what he actually needs is to not lose "
                     "the *opportunity* — and he has 14 months of runway. Now do it right.",
            "prompt": "Put these in the order you'd actually work them. Tap them in sequence.",
            "items": [
                "Wait for NJP disposition and the CO's recommendation decision",
                "Verify HYT and remaining obligated service against MILPERSMAN 1160-120",
                "Counsel and document the Sailor on what he must do to become eligible",
                "Confirm current bonus eligibility rules once he is otherwise qualified",
                "Prepare NAVPERS 1070/601 only after eligibility is established",
            ],
            "correct_order": [2, 1, 0, 3, 4],
            "why": (
                "Counsel first — he walks away knowing where he stands, in writing. Then "
                "verify HYT and OBLISERV, because if HYT blocks him none of the rest "
                "matters. Then wait on mast, because that decides the recommendation. "
                "Bonus rules get confirmed only once he's otherwise qualified, since they "
                "change and a quote you give today may be wrong in six weeks. The contract "
                "is last — it is always last."
            ),
            "points": 20,
        },
        {
            "type": "choice",
            "chief": "Last one. Mast happens, he takes his punishment, and eight weeks later "
                     "the CO **does** recommend him. He's eligible. He wants a 2-year "
                     "reenlistment to hit a specific EAOS.",
            "prompt": "Any problem with a 2-year term?",
            "choices": [
                ("No — 2 years is the minimum allowed, so it's acceptable if it doesn't "
                 "exceed HYT", True,
                 "Right. 1160-030 sets a floor of not less than 2 years and a ceiling at "
                 "HYT. Two years clears the floor; you verify it against his HYT date."),
                ("Yes — the minimum reenlistment term is 3 years", False,
                 "The floor is 2 years, not 3."),
                ("Yes — reenlistments must be in whole 4-year increments", False,
                 "There's no whole-4-year rule. That's a recruiting-side habit, not policy."),
                ("No, and HYT doesn't need checking since he's only 11 years in", False,
                 "Half right. Two years is fine — but you check HYT every time. 'He's "
                 "probably fine' is how a bad contract gets signed."),
            ],
            "points": 10,
        },
    ],

    "closing": (
        "Here's the whole lesson, PS2. **Alvarez walked up with a money problem and you "
        "correctly treated it as an eligibility problem.** That reflex is the difference "
        "between a PS who processes paper and a PS the command trusts.\n\n"
        "The Friday-afternoon pressure was the real test. Nothing about a bonus deadline "
        "changes what 1160-030 requires. Now go get your weekend."
    ),
}

MAX_POINTS = (SCENARIO["reference"]["points"]
              + sum(b["points"] for b in SCENARIO["beats"]))


# ─────────────────────────────────────────────────────────────────────────────
# SCORING — pure functions so they can be unit-tested without Streamlit
# ─────────────────────────────────────────────────────────────────────────────
def score_order(submitted, correct):
    """Partial credit for sequencing: fraction of items in their correct position."""
    if not submitted or len(submitted) != len(correct):
        return 0.0
    hits = sum(1 for i, item in enumerate(submitted) if item == correct[i])
    return hits / len(correct)


def tally(state):
    """Return the three score axes plus a total. Each axis is 0-100."""
    ref = 100.0 if state.get("ref_correct") else 0.0

    proc_beats = [b for b in SCENARIO["beats"] if b["type"] == "choice"]
    got = sum(b["points"] for i, b in enumerate(SCENARIO["beats"])
              if b["type"] == "choice" and state.get(f"beat_{i}_correct"))
    possible = sum(b["points"] for b in proc_beats)
    proc = (got / possible * 100.0) if possible else 0.0

    seq_vals = [state.get(f"beat_{i}_seq", 0.0)
                for i, b in enumerate(SCENARIO["beats"]) if b["type"] == "order"]
    seq = (sum(seq_vals) / len(seq_vals) * 100.0) if seq_vals else 0.0

    earned = (SCENARIO["reference"]["points"] if state.get("ref_correct") else 0)
    for i, b in enumerate(SCENARIO["beats"]):
        if b["type"] == "choice" and state.get(f"beat_{i}_correct"):
            earned += b["points"]
        elif b["type"] == "order":
            earned += b["points"] * state.get(f"beat_{i}_seq", 0.0)
    return {"reference": ref, "procedure": proc, "sequence": seq,
            "earned": round(earned, 1), "max": MAX_POINTS}


def rating_for(pct):
    if pct >= 90:
        return "🏆 **Chief's Pick** — you'd run my shop."
    if pct >= 75:
        return "✅ **Solid** — you'd handle this window without supervision."
    if pct >= 50:
        return "⚠️ **Needs work** — you'd get there, but someone's checking behind you."
    return "❌ **Hit the instruction** — this one needs another pass."


# ─────────────────────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────────────────────
S = st.session_state
S.setdefault("stage", "brief")     # brief -> reference -> beat_N -> debrief
S.setdefault("beat_idx", 0)
S.setdefault("notes", [])          # free-text answers, graded in ONE call for real

st.title("⚓ Duty Day")
st.caption("Prototype · PS · E6 · Reenlistment & Extension Processing · "
           "no API calls, nothing saved")

with st.sidebar:
    st.markdown("### Prototype notes")
    st.markdown(
        "- Plays with **zero API calls** — scenario is hand-written\n"
        "- In the real build, one call generates a scenario like this and it's cached\n"
        "- Free-text answers are buffered and graded in **one** call at debrief\n"
        "- Nothing here writes to your database"
    )
    if st.button("↺ Start over", use_container_width=True):
        for k in list(S.keys()):
            del S[k]
        st.rerun()

# ── Brief ────────────────────────────────────────────────────────────────────
if S.stage == "brief":
    st.subheader(SCENARIO["title"])
    with st.container(border=True):
        st.markdown("**The Chief:**")
        st.markdown(SCENARIO["brief"])
    if st.button("🎯 Take the watch", use_container_width=True, type="primary"):
        S.stage = "reference"
        st.rerun()

# ── Beat 0: name the governing instruction ───────────────────────────────────
elif S.stage == "reference":
    ref = SCENARIO["reference"]
    st.progress(0.15, text="Step 1 of 5 — find the instruction")
    with st.container(border=True):
        st.markdown("**The Chief:**")
        st.markdown(ref["prompt"])

    labels = [c[0] for c in ref["choices"]]
    pick = st.radio("Governing instruction:", labels, index=None, key="ref_pick")
    free = st.text_input("Something else — tell the Chief (optional)", key="ref_free",
                         placeholder="Type the reference you'd use instead")

    if st.button("Lock it in", use_container_width=True, type="primary",
                 disabled=(pick is None and not free.strip())):
        S.ref_correct = bool(pick) and dict(ref["choices"])[pick]
        if free.strip():
            S.notes.append(("Governing instruction", free.strip()))
        S.stage = "feedback_ref"
        st.rerun()

elif S.stage == "feedback_ref":
    ref = SCENARIO["reference"]
    if S.ref_correct:
        st.success("✅ Correct — MILPERSMAN 1160-030.")
    else:
        st.error("❌ Not the one. The gate is **MILPERSMAN 1160-030**.")
    with st.container(border=True):
        st.markdown("**The Chief explains:**")
        st.markdown(ref["why"])
    if st.button("Keep going", use_container_width=True, type="primary"):
        S.stage = "beat"
        S.beat_idx = 0
        st.rerun()

# ── Beats ────────────────────────────────────────────────────────────────────
elif S.stage == "beat":
    i = S.beat_idx
    beat = SCENARIO["beats"][i]
    st.progress(0.15 + 0.17 * (i + 1), text=f"Step {i + 2} of 5")

    with st.container(border=True):
        st.markdown("**The Chief:**")
        st.markdown(beat["chief"])

    if beat["type"] == "choice":
        labels = [c[0] for c in beat["choices"]]
        pick = st.radio(beat["prompt"], labels, index=None, key=f"pick_{i}")
        free = st.text_input("Something else — tell the Chief (optional)",
                             key=f"free_{i}", placeholder="Describe what you'd do instead")
        if st.button("Commit", use_container_width=True, type="primary",
                     disabled=(pick is None and not free.strip())):
            correct_map = {c[0]: c[1] for c in beat["choices"]}
            why_map = {c[0]: c[2] for c in beat["choices"]}
            S[f"beat_{i}_correct"] = bool(pick) and correct_map[pick]
            S[f"beat_{i}_why"] = why_map.get(pick, "")
            if free.strip():
                S.notes.append((f"Step {i + 2}", free.strip()))
            S.stage = "feedback_beat"
            st.rerun()

    else:  # order
        picked = st.multiselect(beat["prompt"], beat["items"], key=f"ord_{i}",
                                help="Tap them in the order you'd work them.")
        st.caption(f"Selected {len(picked)} of {len(beat['items'])}.")
        if st.button("Commit", use_container_width=True, type="primary",
                     disabled=len(picked) != len(beat["items"])):
            correct = [beat["items"][k] for k in beat["correct_order"]]
            S[f"beat_{i}_seq"] = score_order(picked, correct)
            S[f"beat_{i}_submitted"] = picked
            S.stage = "feedback_beat"
            st.rerun()

elif S.stage == "feedback_beat":
    i = S.beat_idx
    beat = SCENARIO["beats"][i]

    if beat["type"] == "choice":
        if S.get(f"beat_{i}_correct"):
            st.success("✅ Good call.")
        else:
            st.error("❌ Not the move.")
        if S.get(f"beat_{i}_why"):
            with st.container(border=True):
                st.markdown("**The Chief:**")
                st.markdown(S[f"beat_{i}_why"])
    else:
        pct = S.get(f"beat_{i}_seq", 0.0)
        correct = [beat["items"][k] for k in beat["correct_order"]]
        if pct == 1.0:
            st.success("✅ Perfect sequence.")
        elif pct >= 0.5:
            st.warning(f"⚠️ Partly right — {int(pct * 100)}% of steps in the right slot.")
        else:
            st.error(f"❌ Sequence is off — {int(pct * 100)}% in the right slot.")
        with st.container(border=True):
            st.markdown("**Correct order:**")
            for n, item in enumerate(correct, 1):
                yours = S.get(f"beat_{i}_submitted", [])
                mark = "✅" if len(yours) >= n and yours[n - 1] == item else "❌"
                st.markdown(f"{mark} **{n}.** {item}")
            st.markdown("**The Chief:**")
            st.markdown(beat["why"])

    last = i >= len(SCENARIO["beats"]) - 1
    if st.button("Debrief" if last else "Next", use_container_width=True, type="primary"):
        if last:
            S.stage = "debrief"
        else:
            S.beat_idx = i + 1
            S.stage = "beat"
        st.rerun()

# ── Debrief ──────────────────────────────────────────────────────────────────
elif S.stage == "debrief":
    sc = tally(S)
    pct = sc["earned"] / sc["max"] * 100 if sc["max"] else 0

    st.subheader("📋 Chief's Debrief")
    st.markdown(rating_for(pct))

    c1, c2, c3 = st.columns(3)
    c1.metric("Found the reference", f"{sc['reference']:.0f}%")
    c2.metric("Procedure", f"{sc['procedure']:.0f}%")
    c3.metric("Sequence", f"{sc['sequence']:.0f}%")
    st.metric("Score", f"{sc['earned']} / {sc['max']}", f"{pct:.0f}%")

    with st.container(border=True):
        st.markdown("**The Chief:**")
        st.markdown(SCENARIO["closing"])

    weak = [n for n, v in [("finding the governing instruction", sc["reference"]),
                           ("procedure", sc["procedure"]),
                           ("step sequencing", sc["sequence"])] if v < 75]
    if weak:
        st.warning("**Weak areas from this run:** " + ", ".join(weak)
                   + f" — in the real build these flow straight into your Advancement "
                     f"Planner instead of you typing them from memory.")
    else:
        st.success("No weak areas flagged on this run.")

    if S.notes:
        st.divider()
        st.markdown("#### Your written answers")
        st.caption("In the real build these are graded together in ONE API call, "
                   "bundled with this debrief. Held here, ungraded, to show the flow.")
        for label, text in S.notes:
            st.markdown(f"- **{label}:** {text}")

    st.divider()
    if st.button("↺ Run it again", use_container_width=True):
        for k in list(S.keys()):
            del S[k]
        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# SHAWN — VERIFY THESE BEFORE ANY OF THIS SHIPS
# ─────────────────────────────────────────────────────────────────────────────
VERIFY_THESE = """
Pulled from MyNavyHR. You are the PS1 — confirm each before this goes live.

1. MILPERSMAN 1160-030 (CH-88, 22 Jul 2024) — Enlistments and Reenlistments Under
   Continuous Service Conditions. Used here as the eligibility gate, and as the
   source for: CO recommendation required, term not less than 2 years, term may
   not exceed HYT.
2. MILPERSMAN 1160-120 — High Year Tenure. Referenced for the HYT ceiling.
3. MILPERSMAN 1070-240 — NAVPERS 1070/601, Immediate Reenlistment Contract.
   Used as the contract document, deliberately positioned as a LATER step.
4. MILPERSMAN 1306-604 — Active Obligated Service (OBLISERV). Used as a
   plausible wrong answer in the reference step.
5. OPNAVINST 1160.8B — Selective Reenlistment Bonus. From your own PS_TOPICS
   bib for "E6 - Reenlistment & Extension Processing". Used as the money
   distractor.

OPEN QUESTION FOR YOU: the scenario asserts a pending NJP effectively blocks
reenlistment because the CO won't recommend. That's how it works in practice —
confirm whether you want it stated as practice or tied to a specific article.
"""
