# Score Surge — Operations

**Owner:** Shawn (Strategic Sailor)
**Last updated:** 2026-07-31

This is the standing agreement for how Score Surge gets built. Every session inherits
it. It is not a to-do list — the to-do list lives in `todo.md` in the Score Surge DB
repo and in the Current Plan section below.

---

## Commander's Intent

**90-day goal:** Finish the core features. Not growth, not marketing — close out the
half-built pieces so the app is complete and trustworthy.

**The two risks that matter, in Shawn's own assessment:**

1. **Content accuracy.** AI-generated questions that nobody verified against official
   Navy sources. If a sailor studies a wrong answer and fails a real exam, the "built
   by a 20-year PS" credibility — the entire differentiator — is gone. This is the
   existential risk.
2. **Bugs and rough edges.** Text-box answers instead of bubble-in, results that don't
   save, untested upload paths. These make a real product feel unfinished.

Everything below is prioritized against those two.

---

## The Two Repos

Score Surge is two projects that have been running independently and need to converge.

| | **score-surge** | **Score Surge DB** |
|---|---|---|
| Path | `~/Documents/score-surge` | `~/Documents/Score Surge DB` |
| GitHub | `smsps12004/Score-Surge` | local only (1 commit) |
| What it is | The live Streamlit app | The verified question bank |
| Deployed | score-surge.streamlit.app | not deployed |
| Governed by | this file | `.ai-rules.md` + `agents/` |

**They are converging.** The question database exists to feed the app. The gate for
that connection is already defined by the Integration Watchdog: **20 verified
questions.** As of 2026-08-07 there are still 14 — read directly from `questions.db`,
not inherited from a doc. Six to go, and a packet is ready that would take it to 22.

> ⚠️ There is a third folder, `~/Documents/score surge` (with a space), containing
> another `app.py`. Its status is unknown. Resolve or delete it before it costs an
> evening of editing the wrong file.

---

## Departments

Each department owns an area, knows what it must never break, and has a test that runs
before anything ships. The agent roles already defined in `Score Surge DB/agents/`
apply here too — this extends that model from the database to the whole product.

### 1. Exam Engine
Mock exams, practice questions, grading, and the connection to the question bank.
- **Owns:** Tab 5 (Mock Exam), practice question generation, grading, score capture
- **Must never break:** the Chief's grading voice; source citations on every answer
- **Test:** a full exam can be generated, answered, submitted, and graded without an error

### 2. Question Bank (Content Verifier's department)
The 200-question PSC build and everything downstream of it.
- **Owns:** `questions.db`, `admin_app.py`, the batch build plan, verification
- **Must never break:** the `verified` flag means a human checked it against the source.
  Nothing gets marked verified by an AI.
- **Test:** every question cites a real manual and chapter that actually contains the answer

### 3. Profile Sheet & FMS
OCR extraction, the FMS math, paygrade detection.
- **Owns:** Tab 1, OCR pipeline, `BREAK_ATTEMPT_2026-07-29.md` work
- **Must never break:** FMS math is verified to the cent against two real sheets. It stays that way.
- **Test:** `run_checks.py` (154 checks)

### 4. Revenue
Tiers, Stripe, the upgrade flow.
- **Owns:** tier gating, `upgrade_banner`, checkout, the Stripe webhook function
- **Must never break:** the free profile sheet reader stays free
- **Test:** upgrade flow completes with a real card — **still unverified**

### 5. Data
Supabase, persistence, score history.
- **Owns:** auth, `score_history`, user tiers, anything that survives a logout
- **Must never break:** a sailor's saved history
- **Test:** a result saved before logout is present after login

### 6. Brand & Interface
Colors, mobile layout, the sailor-facing feel.
- **Owns:** CSS, layout, copy tone, visual identity across Score Surge and PS Agent
- **Must never break:** readability on a phone — Shawn works from his phone and so do sailors
- **Test:** every changed screen viewed at phone width

### 7. Sentinel (QA)
The gate everything passes through.
- **Owns:** `run_checks.py` (154 checks), `smoke_test.py` (33 checks)
- **Must never break:** itself. A check that gets deleted to make a build pass is a lie.
- **Test:** both suites green before any push

---

## Standing Rules

Carried over from `.ai-rules.md` and prior sessions. These apply to every agent, every
session, no exceptions.

1. **Never delete existing features, data, or working code.**
2. **Never mark something verified that a human hasn't checked.**
3. **Explain changes in plain English before showing code.** Shawn is not a coder.
4. **Flag risk, don't silently fix it.** If something looks wrong, say so.
5. **Never hardcode secrets.** `.streamlit/secrets.toml` is gitignored and stays that way.
6. **Test before calling anything done.** Sentinel green, or it isn't done.
7. **Test locally before pushing.** `python3 -m streamlit run app.py` — the live site is
   not a staging environment.
8. **One thing, finished.** Current task closes or gets explicitly parked before the next opens.
9. **Do the investigation before assigning the work.** If the answer is in the code or
   the repo, go read it. Shawn gets asked only for things that need his hands, his eyes,
   or his judgment.
10. **Every session ends with a handoff** so the next one starts warm.

### Do Not Reintroduce
Fixed once, stays fixed. See `BREAK_ATTEMPT_2026-07-29.md` for the full list.
- PMA read as a pre-converted point value instead of the raw 2.0–4.0 scale
- UIC parsed as a rate
- Division by zero on a `Final Score: 0/0` grade result
- A missing `score_history` column blanking the whole page for a logged-in sailor
- Bare `except Exception:` that throws away the reason — if it's caught, it's recorded

---

## Current Plan

In order. Each step closes before the next opens.

> **Latest handoff: `HANDOFF_2026-08-07.md`.** Read that first — it carries the
> next action, the current business state, and what was learned. Steps 1 and 2
> below are DONE as of 7 Aug 2026.

### 1. Exam Engine — structured questions ✅ DONE (7 Aug 2026)
Rewrite Tab 5 so questions are data, not a blob of text.

Use the `questions.db` field layout as the shape, so the app and the database speak the
same language from day one:

```
question | answer_a | answer_b | answer_c | answer_d | correct_answer
         | source_manual | chapter_section | verified
```

What this fixes in one change:
- A/B/C/D radio buttons instead of a text box — matches the real NWAE
- Answers and explanations hidden until submit
- Grading happens locally and instantly — the app already knows the correct letter, so
  it costs no API call and can't produce a `0/0`
- The app becomes able to read from the question bank the moment the bank is ready

Keep one small Claude call for the Chief's closing feedback paragraph. That voice is
the product.

### 2. Data — the save bug ✅ DONE (7 Aug 2026)
Error 42501, row-level security. Streamlit rebuilds an anonymous Supabase client on
every rerun and the session restore was guarded by `and not st.session_state.user`,
true only on the first pass — so every later call went out unauthenticated and
`auth.uid()` was NULL. Fixed in the app; the RLS policy stays strict. Logout now
clears all session state, which also closed a privacy leak on shared machines.

### 2b. Revenue path ✅ DONE AND PROVEN (7 Aug 2026)
Three things were unproven. All three are now tested against real Stripe events:
- **Pay without returning to the app.** Real card, Apple Pay from a phone by QR, tab
  closed at Stripe's confirmation screen. Logged in fresh elsewhere → Chief.
- **Cancel and lose access.** `customer.subscription.deleted` → tier drops to `free`
  automatically.
- **Cancel and keep access until the period ends.** A cancellation scheduled for
  period end leaves the sailor entitled. `past_due` also counts as entitled, so a
  card blip does not cut a sailor off mid-study while Stripe is still retrying.

Shipped in `33110e8` plus a dashboard deploy of the edge function. Profiles now
carry `stripe_customer_id`; subscription events match on it, not on email, which is
also what makes a hand-set tier (like Shawn's own) unreachable by any Stripe event.
Full detail in `HANDOFF_2026-08-07.md`.

### 3. Question Bank — clear the integration gate ← DO THIS FIRST
Six more verified questions reaches 20 and unlocks the Integration Watchdog's work.

**Status 7 Aug:** 14 verified, 22 unverified, 36 total — confirmed by reading
`questions.db`, not by trusting the docs. A verification packet is ready and waiting
on Shawn's judgment: `Score Surge DB/VERIFICATION_PACKET_2026-08-07.md`. Eight
existing questions were checked against the actual text of `Whole MILPERSMAN.pdf`
with the governing passage quoted beside each. Confirming them takes 14 → 22.

**One question failed the check.** ID 140 (Article 1070-270) keys an answer that
invents an "ID Card Lab within 30 days" step that appears nowhere in the article.
It stays unverified until rewritten.

**Note on sequencing:** the gate is cleared by *verifying questions that already
exist*, not by writing new ones. Batch 2 (`question_plan_PSC_200.md` → "Batch 2 —
Clustered by Theme") is approved and remains the plan for *growing* the bank toward
200, but it is not what unblocks integration.

### 4. Integration — connect bank to app
Exam Engine reads verified questions from the bank first, falls back to AI generation
when the bank has nothing for that rate and topic. **This is the fix for content
accuracy risk** — the thing Shawn named as the top threat to the product.

### 5. Brand — match PS Agent
Near-black background, blue-fill buttons, gold accents. Pull real hex values from PS
Agent's CSS, not from a screenshot. Verify verdict banners and review cards stay
readable.

### 5b. Hosting — before anyone is told the app exists
Streamlit Community Cloud sleeps after 12 quiet hours and allows no custom domain. A
sailor who pays $19.99 and returns next morning to a "Zzzz" screen assumes it is
broken. Plan: Render, ~$7/mo, needs a Dockerfile because OCR needs the `tesseract`
binary that `packages.txt` currently installs. Then point ssstrategicsolutions.com
at it. This is not a core feature, but it is a launch blocker.

### Parked (deliberately, not forgotten)
- Reading six FMS values off a real profile sheet — `BREAK_ATTEMPT_2026-07-29.md`
- Two post-deploy checks never run: JPG upload, photographed sheet paygrade
  detection. (The upgrade flow with a real card was run and passed on 7 Aug.)
- Sidebar navigation restructure — too broad to bundle safely
- PS Agent public launch, Strategic Sailor hub deployment, ad channels
- Leaderboard, badges, "This Month in Navy History" — after core features are done

---

## How Work Ships

```
Shawn sets the goal
        ↓
Plan written and approved before any code is touched
        ↓
Department does the work — one thing at a time
        ↓
Sentinel: run_checks.py + smoke_test.py, both green
        ↓
Tested locally: python3 -m streamlit run app.py
        ↓
Shawn looks at it
        ↓
Push — and only then does the live site change
        ↓
Handoff written for the next session
```

No step skipped. Work flows up through the chain before it reaches Shawn, and nothing
reaches the live site before it has run on his Mac.
