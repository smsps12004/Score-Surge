# Score Surge — Cold-start handoff
**Written:** 7 August 2026, evening
**For:** the next conversation, starting from nothing

---

## ▶ First move

**Ask Shawn to confirm the verification packet.** Everything else is behind it.

Open `~/Documents/Score Surge DB/VERIFICATION_PACKET_2026-08-07.md`. Eight existing
questions have been checked against the actual text of `Whole MILPERSMAN.pdf`, with
the governing passage quoted next to each. He reads, he agrees or disagrees, and only
then does anything get marked `verified='yes'`.

Confirming them moves the bank from **14 → 22 verified**, past the Integration
Watchdog's gate of 20, which unblocks the single most valuable piece of work left:
connecting the verified bank to the exam engine.

**Do not mark anything verified yourself.** Standing rule 2. An AI cannot verify a
question — that is the entire meaning of the flag.

One question, **ID 140**, failed the check and must stay unverified: its keyed answer
invents an "ID Card Lab within 30 days" step that appears nowhere in MILPERSMAN
1070-270. Offer to rewrite the option.

---

## Orientation in 60 seconds

Score Surge is a Navy advancement-exam study tool built by a 20-year Personnel
Specialist. It reads a Sailor's profile sheet, calculates their Final Multiple Score,
and gives them NWAE-format practice exams. It is live, deployed, and can take money.

**Shawn is not a coder.** Plain English, one recommendation instead of a menu, say
what to do next. He works from his phone, so check anything visual at phone width.

**Read `OPERATIONS.md` before touching anything** — it is the standing agreement, not
a to-do list. `PROJECT_BRIEFING_FOR_AIDO.md` in the same folder is the fuller
orientation: every folder, every service, the whole evolution.

| | Path |
|---|---|
| The app | `~/Documents/score-surge` → `github.com/smsps12004/Score-Surge` |
| The question bank | `~/Documents/Score Surge DB` (local only) |
| ⚠️ Unknown third folder | `~/Documents/score surge` (with a space) — do not edit |

---

## Where things stand

**Working and proven:** signup with a 3-day trial, profile sheet upload with OCR, FMS
calculator verified to the cent, NWAE-format mock exam, score history that survives
logout, and — as of tonight — the complete payment path.

**The revenue path is finished.** Three things were unproven this morning and all
three now hold, tested against real Stripe events for $0:

- A Sailor can pay on a phone, close the tab, never return, and still have access
- A Sailor who cancels loses access automatically
- A Sailor who cancels with time left keeps access until the period actually ends

Shipped in commit `33110e8` plus a Supabase dashboard deploy of the edge function.

**Not done:** the bank is not connected to the exam. Hosting sleeps after 12 quiet
hours. Colours don't match PS Agent yet.

---

## The risk that governs the work

Exam questions are AI-generated and mostly unverified. Shawn checked a batch and found
roughly **one in three unusable**. If a Sailor studies a wrong answer and fails a real
exam, the credibility that is the entire product is gone.

Right now the app is honest rather than correct — unverified questions are labelled
"🔎 Unverified lead — confirm in your bib" and the Chief points at the governing manual
instead of grading against a key nobody vouched for. That is a bandage. The fix is the
verified bank, which is why the packet is the first move.

This risk is not theoretical. Tonight's check caught a fabricated regulatory deadline
in a question already sitting in the database.

---

## Order of work after the packet

1. **Solve `questions.db` having no `explanation` column.** The app shows an
   explanation under every graded answer; bank questions would arrive with nothing to
   teach from. Blocks step 2.
2. **Connect the bank to the Exam Engine** — verified questions first, AI generation
   only where the bank has nothing for that rate and topic.
3. **Move off Streamlit hosting** — Render, ~$7/mo, needs a Dockerfile because OCR
   needs the `tesseract` binary. Then point ssstrategicsolutions.com at it. A paying
   Sailor meeting a "Zzzz" screen assumes the app is broken.
4. **Brand** — match PS Agent, using real hex values from its CSS.
5. Launch.

---

## Things that will bite you

- **Git from Claude's side strands lock files.** Do git work from Shawn's Terminal.
- **`streamlit` is not on his PATH** — use `python3 -m streamlit run app.py`.
- **The edge function deploys via the Supabase dashboard**, not git. The repo copy is
  the source of truth; keep them in sync by hand.
- **Supabase's SQL editor** often will not render in a background browser tab.
  Re-navigate and wait ~15 s.
- **Sentinel needs its dependencies** — `run_checks.py` silently *skips* blocks when
  PyMuPDF is missing and reports "2 blocks skipped, NOT safe to push". A skipped check
  is not a passed check.
- **Stripe price IDs live in two places** — `STRIPE_PRICE_IDS` in `app.py` and
  `PRICE_TO_TIER` in the webhook. They must stay in sync.
- **Never merge the two MILPERSMAN sources.** Regular exam is CH-94, substitute is
  CH-91. Different bibliographies, different change revisions.

---

## Standing rules, short form

1. Never delete working code, features, or data
2. Never mark something verified that a human hasn't checked
3. Plain English before code
4. Flag risk, don't silently fix it
5. Never hardcode secrets
6. Sentinel green or it isn't done
7. Test locally before pushing
8. One thing, finished
9. Do the investigation before assigning Shawn work
10. End every session with a handoff
