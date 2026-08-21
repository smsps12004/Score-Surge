# Score Surge — Project Briefing
**Prepared for:** Aido
**Prepared by:** Claude, working with Shawn (Strategic Sailor)
**As of:** 7 August 2026, evening
**Purpose:** full orientation — where everything lives, what exists, and where the
product is in its evolution.

---

## 1. What Score Surge is

A Navy advancement-exam study tool for enlisted Sailors, built by a 20-year Navy
Personnel Specialist (PS). It calculates a Sailor's Final Multiple Score (FMS) from
their profile sheet, builds a study plan, and gives them practice exams in the format
of the real Navy-Wide Advancement Exam (NWAE).

**The differentiator is credibility** — built by someone who has actually run the
advancement process. That is also the source of the project's single biggest risk
(see §6).

**Business:** Strategic Solutions LLC. Live Stripe account. Tiers are Free /
Petty Officer ($12.99/mo) / Chief ($19.99/mo), with a 3-day full-access trial on
signup. The profile-sheet reader stays free permanently — that is a standing rule,
not a marketing decision.

**Owner:** Shawn — non-technical. He sets direction and makes judgment calls; the
technical work is delegated entirely. Write to him in plain English, recommend one
option rather than listing four, and tell him what to do next. He works from his
phone often, so anything visual must be checked at phone width.

---

## 2. Where everything lives

All paths are on Shawn's Mac under `~/Documents/`.

| Path | What it is | Git |
|---|---|---|
| `~/Documents/score-surge` | **The live Streamlit app.** This is the main repo. | `github.com/smsps12004/Score-Surge`, branch `main` |
| `~/Documents/Score Surge DB` | **The verified question bank** + admin tooling + source PDFs | local only, 1 commit, not pushed |
| `~/Documents/score surge` *(with a space)* | ⚠️ **Unknown third folder** containing another `app.py`. Status unresolved. Do not edit anything here until it is identified. | unknown |

### Inside `score-surge`

| File | What it is |
|---|---|
| `app.py` | The entire app — ~3,000 lines, 7 tabs. Single file by design. |
| `OPERATIONS.md` | **The standing agreement.** Departments, rules, current plan. Read this first. |
| `HANDOFF_2026-08-07.md` | Latest session handoff — most recent state of play |
| `HANDOFF_NEXT_SESSION.md` | Cold-start brief for a new conversation |
| `run_checks.py` | Sentinel suite — 154 checks |
| `smoke_test.py` | Renders the app and walks the FMS flow — 33 checks |
| `supabase/functions/stripe-webhook/index.ts` | Stripe webhook (deployed via Supabase dashboard, not via git) |
| `BREAK_ATTEMPT_2026-07-29.md` | Register of bugs fixed once that must never return |
| `LAUNCH_CHECKLIST.md` | Pre-launch items |
| `.streamlit/secrets.toml` | Secrets — **gitignored, and stays that way** |

### Inside `Score Surge DB`

| File | What it is |
|---|---|
| `questions.db` | SQLite question bank. 36 rows: 14 verified, 22 not. |
| `questions.db.bak-20260729-203313` | Backup |
| `Whole MILPERSMAN.pdf` | 27 MB, 3,881 pages — the actual source manual. Verification is done against this. |
| `bibliography_PSC_Jan2027.md` | The official exam bibliography, regular and substitute |
| `question_plan_PSC_200.md` | The 200-question build plan |
| `VERIFICATION_PACKET_2026-08-07.md` | 8 questions checked against the manual, awaiting Shawn's confirmation |
| `admin_app.py` | Admin UI for the bank |
| `agents/` | 8 role definitions (builder, content verifier, QA, integration watchdog, etc.) |
| `.ai-rules.md` | Rules governing this repo |
| `changelog.md`, `todo.md` | History and task list |

### `questions.db` schema

```
id | rating | paygrade | topic | question
   | answer_a | answer_b | answer_c | answer_d | correct_answer
   | source_manual | chapter_section | verified | date_added
```

`app.py` already speaks these exact field names — the app and the bank were
deliberately aligned so integration is a read, not a translation.

---

## 3. External services

| Service | Detail |
|---|---|
| **Hosting** | Streamlit Community Cloud — `score-surge.streamlit.app`. Free tier. **Sleeps after 12 quiet hours.** |
| **Database / auth** | Supabase, project ref `regxvdcyrcztzbcwdtmb`. Postgres with row-level security on. |
| **Payments** | Stripe live mode, account `acct_1Tj91ADP0fFhPzMl`. Webhook destination `we_1Tx8u4DP0fFhPzMluPZSrT2e`. |
| **Payouts** | Navy Federal ••••3475, daily automatic (changed 7 Aug) |
| **Domain** | ssstrategicsolutions.com — owned, not yet pointed at anything |
| **AI** | Anthropic API for question generation and the "Chief" feedback voice |

**Supabase tables that matter:** `profiles` (id, email, tier, trial_start,
`stripe_customer_id`) and `score_history`.

**Stripe price IDs** live in two places that must stay in sync: `STRIPE_PRICE_IDS`
in `app.py` and `PRICE_TO_TIER` in the webhook. Stripe prices are immutable, so a
price change means creating a new price and updating both.

---

## 4. Evolution — how it got here

| When | What happened |
|---|---|
| Apr–Jun 2026 | Initial build. FMS calculator, profile sheet upload, study guide, AI tutor. |
| Jun 2026 | Question bank repo started as a separate project with its own agent roles |
| 18 Jul | MILPERSMAN loaded; first questions built |
| 21–25 Jul | Supabase auth and tiers; Stripe goes live; Shawn subscribes to his own product to test checkout |
| 26–27 Jul | Price change to $12.99 / $19.99; earlier prices retired but kept in the mapping |
| 29 Jul | Big correctness push — FMS math verified to the cent against two real profile sheets; OCR fixed to read a real sheet; PMA, UIC and divide-by-zero bugs fixed and logged as never-again |
| 29 Jul | Verified question count reaches 14. Batch 2 planned and approved. |
| 6 Aug | Entitlement hardening |
| **7 Aug (day)** | Mock exam rewritten to real NWAE format — A/B/C/D radio buttons, local grading, no more `0/0`. Score-saving bug (RLS error 42501) fixed. Logout now wipes session state, closing a privacy leak on shared duty-office computers. Unverified questions labelled honestly. |
| **7 Aug (evening)** | **Revenue path proven end to end.** See §5. |

---

## 5. Current state — what works

| Area | State |
|---|---|
| Sign-up, login, 3-day trial | Working |
| Profile sheet upload + OCR | Working (PDF and photographed sheets) |
| FMS calculator | Working, verified to the cent |
| Mock exam | Working, real NWAE format |
| Score history | Working, persists across logout |
| Payments and access | **Proven 7 Aug** |
| Cancellation removes access | **Proven 7 Aug** |
| Question bank → exam | **Not connected.** Blocked on the 20-verification gate. |
| Hosting that stays awake | **No.** Sleeps after 12 hours. |
| Brand match with PS Agent | Not started |

### What was proven on 7 August

The revenue path had never been exercised. Three tests, total cost $0:

1. **Pay and never return.** New account, forced to `free`, upgraded through real
   checkout, paid by **Apple Pay from a phone via QR**, tab closed at Stripe's
   confirmation screen, app never revisited. Logged in fresh on another device →
   Chief. Stripe: 200 OK, 796 ms.
2. **Cancel loses access.** A free-trial subscription (bills $0.00) was created and
   cancelled. `customer.subscription.deleted` → profile dropped to `free`
   automatically, confirmed in the function logs.
3. **Cancelling early does not lose access early.** A cancellation scheduled for
   period end left the Sailor entitled. `past_due` is deliberately treated as still
   entitled, because Stripe retries a failed card for days and cutting someone off on
   the first failure takes access from a Sailor who is about to pay successfully.

**Revenue collected to date: $0.** The July $20 was Shawn's own money and is
cancelled; the August $19.99 test was refunded; the trial never billed.

---

## 6. The two risks that govern everything

Shawn named these himself. Every priority is judged against them.

**1. Content accuracy — existential.** Exam questions are AI-generated and mostly
unverified. Shawn checked a batch against the manuals and found roughly **one in
three unusable**: a cancelled article cited confidently, excess leave conflated with
separation leave, two answer options naming the same form. If a Sailor studies a
wrong answer and fails a real exam, the "built by a 20-year PS" credibility — the
entire differentiator — is gone.

The current mitigation is honesty, not correctness: unverified questions are labelled
"🔎 Unverified lead — confirm in your bib", banners appear before and after the exam,
and the Chief points at the governing manual rather than grading against a key nobody
vouched for. **The real fix is connecting the verified bank to the exam engine.**

This risk is live. On 7 Aug a fresh check of one question (ID 140) found an invented
"ID Card Lab within 30 days" requirement that appears nowhere in the cited article.

**2. Rough edges.** Half-finished behaviour that makes a real product feel unfinished.
Largely addressed over 29 Jul – 7 Aug, but hosting that falls asleep is the remaining
one a paying customer would actually hit.

---

## 7. Rules any agent must follow

From `OPERATIONS.md` and `.ai-rules.md`. These are not suggestions.

1. Never delete existing features, data, or working code.
2. **Never mark something verified that a human hasn't checked.** An AI cannot set
   `verified='yes'`. This is what the flag means.
3. Explain changes in plain English before showing code. Shawn is not a coder.
4. Flag risk, don't silently fix it.
5. Never hardcode secrets. `.streamlit/secrets.toml` is gitignored and stays that way.
6. Test before calling anything done — `run_checks.py` and `smoke_test.py` both green.
7. Test locally before pushing. The live site is not a staging environment.
8. One thing, finished. Close it or park it explicitly before opening the next.
9. **Do the investigation before assigning Shawn work.** If the answer is in the code
   or the repo, go read it. He gets asked only for things needing his hands, his eyes,
   or his judgment.
10. Every session ends with a handoff.

### Practical notes

- **Git from Claude's side strands lock files.** Do git work from Shawn's Terminal.
- **Run the app:** `python3 -m streamlit run app.py` — bare `streamlit` is not on his PATH.
- **The edge function deploys via the Supabase dashboard**, not via git push. The repo
  copy is the source of truth; keep them in sync manually.
- **Supabase's SQL editor** sometimes will not render in a background browser tab.
  Navigate again and wait ~15 s.

---

## 8. What is next, in order

1. **Confirm the verification packet** — `Score Surge DB/VERIFICATION_PACKET_2026-08-07.md`.
   Takes the bank from 14 to 22 verified, clearing the gate of 20. Needs Shawn's
   judgment, nobody else's.
2. **Connect the bank to the Exam Engine.** Verified questions first, AI generation as
   fallback where the bank has nothing. *This is the actual fix for risk 1.*
3. **Solve `questions.db` having no `explanation` column** — the app shows an
   explanation under every graded answer, so bank questions would arrive with nothing
   to teach from. Must be handled before or with step 2.
4. **Move off Streamlit hosting** to Render (~$7/mo, needs a Dockerfile for
   `tesseract`), then point ssstrategicsolutions.com at it. Launch blocker.
5. **Handle cancellations at the edges** — the webhook covers the main paths now, but
   the `explanation` gap and hosting come first.
6. **Brand** — match PS Agent's palette, using real hex values from its CSS.
7. Then launch.

Parked deliberately: reading six FMS values off a real profile sheet, sidebar
restructure, PS Agent public launch, leaderboard and badges.
