# Score Surge — adversarial test results

**Date:** 29 July 2026
**Baseline before I started:** `run_checks.py` 55/55 pass, `smoke_test.py` 15/15 pass.
Both suites are green. Everything below is something they do not look at.

I did not change `app.py`. Nothing here is fixed yet.

---

## STATUS as of 30 July 2026

Suites now: `run_checks.py` **121/121**, `smoke_test.py` **25/25**.

| # | Finding | Status |
|---|---|---|
| 1 | Stripe sessions created on every render | **FIXED** — `5ac6687` |
| 2 | E6 sheet as E5 reads higher in the 3.92–4.00 band | **FIXED** — `0aef0fc` |
| 3 | Paygrade regex misses `E-6`, `E06`, `PSC (E7)` | **FIXED** — `0ebb38a` |
| 4 | `4,06` and `4.060` silently truncate to `4.0` | **FIXED** — see below |
| 5 | Two-column layout puts the PMA value in the SIPG field | open — needs coordinate-based parsing |
| 6 | `smoke_test.py` never tested a paying user | **FIXED** — `0ebb38a` |
| 7 | Failed score save swallowed silently | **FIXED** |
| 8 | Cycle 272 dates hardcoded in four places | **FIXED** — see below |
| 9 | Smaller things | 5 of 8 fixed — see below |

Every fix carries its own regression checks, so none of these can come back quietly.
Finding 5 is real work and wants its own session.

**Finding 9, item by item:**

| Item | Status |
|---|---|
| `load_score_history` unpacking outside its `try` | **FIXED** — rows are skipped or defaulted, never fatal |
| `total` of zero divides by zero | **FIXED** |
| `compute_fms` has no lower clamp | **FIXED** — both ends, all six components |
| `over_cap_fields` skips `tir` and `education` | open — not reachable, completeness only |
| SIPG as `03 YRS 06 MOS` reads 3.0 | open — belongs with finding 5 |
| A minus sign is ignored | open — theoretical |
| Raw sheet dump shows name and DoD ID | **FIXED** — `redact_pii`, verified on the rendered page |
| All 8 AI calls hardcode the model | open |

**Rolling the app to the next cycle** is now one edit: the `CYCLE` dict at the top of
`app.py`. It feeds the countdown, the planner's exam-date picker and the AI prompts.
`CPO_EXAM` is separate, with an `announced` flag to flip when the FY27 NAVADMIN drops.
Two checks in `run_checks.py` section 8 scan `app.py` itself and fail if a date literal
or the cycle number ever reappears outside that block.

**Related, found while fixing 4 and not yet addressed:** the integer fallback in
`extract_number_near_label` still grabs stray digits when a field's real value is
absent — `SERVICE IN PAYGRADE AS OF SEP 30,2025` with no value reads SIPG as 30.0
rather than reporting the field missing. That behaviour predates all of this work
and is unchanged by it. It belongs with finding 5.

---

## 1. Every page load creates live Stripe checkout sessions — HIGH

`upgrade_banner()` calls `create_checkout_session()` while the page is drawing, not when
the sailor clicks Upgrade. Streamlit runs the code for all seven tabs on every rerun, and
a rerun happens on every single interaction.

Measured, one free user:

| Action | Stripe Checkout Sessions created |
|---|---|
| Page load | 5 |
| Pick a paygrade | 10 |
| Type an exam score | 15 |
| Type a PMA | 20 |
| Click Calculate | 25 |

Five interactions, **25 live Stripe sessions**, nobody clicked anything to buy.

Three separate consequences:

- **Latency.** Five sequential Stripe round trips block the page every rerun. At ~250ms
  each that is over a second added to every keystroke in the FMS calculator, on a phone,
  for the free users you are trying to convert.
- **Junk in your Stripe account.** Thousands of abandoned Checkout Sessions per active
  free user. Conversion and abandonment numbers in the Stripe dashboard become fiction.
- **Visible breakage when Stripe hiccups.** With a bad key I got five red
  "Could not start checkout" errors stacked on an otherwise working page. Any Stripe
  outage or rate limit does the same thing to a live free user.

**Fix:** don't create the session during render. Draw a normal `st.button("Upgrade")`,
create the session inside the `if` when it is clicked, then redirect. Cost of the fix is
small; it is `app.py` lines 164–176.

Reproduce: `tier_probe.py`

---

## 2. An E6 sheet scored as E5 can produce a HIGHER FMS with no warning — HIGH

This is the failure you already fixed once. The dropdown no longer defaults to E5, and
`over_cap_fields()` flags a PMA above the E5 cap. But that guard only fires when
`pma > 4.00`. There is a band just underneath it where nothing fires.

E5 PMA points are `pma × 80 − 256`. E6 is `pma × 30 − 60`. They cross at pma 3.92.
Above the crossover, E5 rules pay *more*:

| RSCA PMA | FMS as E6 (truth) | FMS as E5 | Difference | Warning shown? |
|---|---|---|---|---|
| 3.90 | 133.7 | 132.7 | −1.00 | no |
| 3.92 | 134.3 | 134.3 | 0.00 | no |
| 3.95 | 135.2 | 136.7 | **+1.50** | no |
| 4.00 | 136.7 | 140.7 | **+4.00** | no |

An E6 sailor with a 4.00 RSCA PMA who picks E5 by mistake gets 140.7 instead of 136.7 —
four points too high, flattering, and silent. Same shape as the bug you already killed,
just in the range the cap check can't see.

It matters more than it looks because of finding 3: paygrade auto-detection fails on
common sheet wordings, which is exactly when the sailor has to pick manually.

**Fix:** stop relying on the caps to infer paygrade. E6/E7 sheets say "RSCA" and
"RSCA PMA"; E5 sheets say "PMA". If the sheet's own wording disagrees with the dropdown,
say so plainly. That is a text check, not a numeric one, and it covers the whole range.

---

## 3. Paygrade detection misses the way Navy systems actually print paygrade — MEDIUM-HIGH

`extract_paygrade()` matches `e[4-7]`, so anything between the letter and the digit
breaks it:

| Sheet text | Detected |
|---|---|
| `PAYGRADE COMPETING FOR: E6` | E6 ✅ |
| `ADVANCEMENT TO E-6` | **None** ❌ |
| `PAYGRADE: E-7` | **None** ❌ |
| `PAYGRADE: E06` | **None** ❌ |
| `PAYGRADE COMPETING FOR: E05` | **None** ❌ |
| `PAYGRADE: E 6` | **None** ❌ |
| `CANDIDATE FOR ADVANCEMENT TO PSC (E7)` | **None** ❌ |

`E-6` and `E06` are both standard Navy renderings. Your two test PDFs happen to print
`E6` bare, which is why this has never shown up.

Not a wrong number on its own — it falls through to "Could not tell which paygrade" and
makes the sailor choose. But it hands the sailor the decision described in finding 2.

**Fix:** one regex change — `e[\s\-]?0?([4-7])`. Also accept the rate abbreviation in
parentheses.

---

## 4. The parser says "read all six fields" while silently rounding the PMA off — MEDIUM-HIGH

The number regex is `\d{1,3}(?:\.\d{1,2})?`, which only understands one or two decimal
places and a period as the separator. Anything else gets truncated to the integer, and
the field is still reported as successfully read.

| Sheet prints | PMA parsed as | Sailor is told |
|---|---|---|
| `4.06` | 4.06 ✅ | read all six fields |
| `4.060` | **4.0** ❌ | **read all six fields** |
| `4,06` | **4.0** ❌ | **read all six fields** |

`4,06` is what you get when OCR reads a period as a comma — routine on a scanned or
photographed sheet, which is the entire image-upload path. On an E6 sheet, 4.06 → 4.00
loses 1.8 FMS points, and the green "✅ Read all six fields from your profile sheet"
message tells the sailor to trust it.

**Fix:** accept `[.,]` as the separator and allow three or more decimals, or treat a
number the regex only partially consumed as not-found rather than as read.

---

## 5. A two-column sheet layout hands the PMA value to the SIPG field — MEDIUM-HIGH

Real profile sheets are tables. When PDF text extraction emits headers first and values
underneath — which is normal for table layouts — the label-then-scan approach walks off
the end of its row.

Input:

```
EXAM STANDARD SCORE   PERFORMANCE MARK AVERAGE   SERVICE IN PAYGRADE
62.00                 4.06                       3.50
AWARDS POINTS   EDUCATION POINTS   PNA POINTS
4.00            4.00               6.00
```

Result: `exam_score` and `pma` not found (placeholders 42.0 and 3.8 used),
`tir` = **4.06** — the PMA value in the years-in-paygrade field — and `pna` = 4.0, which
is the Awards number.

The sailor does get a "Read 2 of 6 fields" warning, so this is not fully silent. But
`tir` is presented as successfully read and 4.06 years is a perfectly plausible-looking
number, so there is nothing to tip them off.

Your handoff already notes the parser is verified against only two near-identical E6
sheets. This is what that gap looks like in practice.

**Fix:** for PDFs use PyMuPDF's word coordinates and match a label to the number in the
same row band, instead of scanning forward through flattened text. That is a real piece
of work, not a one-liner.

---

## 6. `smoke_test.py` has never tested a paying user — MEDIUM

Line: `at.session_state["tier"] = "elite"`.

`TIER_ORDER` is `["free", "petty_officer", "chief"]`. `can_access()` returns `False` for
any tier not in that list, so `"elite"` is treated as **free**. Confirmed by measurement —
`elite` and `free` produce byte-identical output, five locked-tab banners each.

So every smoke run has exercised the free experience. Tabs 3, 4, 5, 6 and 7 — the AI
Study Guide, Tutor, Mock Exam, Planner and BBA Hub, everything the $12.99 and $19.99
tiers are paying for — have never rendered in an automated check.

I rendered them under `chief` and under `trial`: no exceptions, and all six enabled
buttons run without throwing. So nothing is broken today. The problem is that your safety
net has a hole in it exactly where your revenue is.

**Fix:** change `"elite"` to `"chief"` in `smoke_test.py`, and add a second pass at
`"free"` so both sides of the paywall are covered.

---

## 7. A failed score save is swallowed silently — MEDIUM

`app.py` 1575–1586:

```python
try:
    supabase.table("score_history").insert({...}).execute()
except Exception:
    pass
```

If the insert fails — expired Supabase token, RLS policy, missing column — the sailor
still sees their score in the chart for the rest of the session, then it is gone at next
login with no explanation. Given the Supabase token refresh issue already on your list,
this is a plausible live cause of "the app lost my scores."

**Fix:** keep the app from crashing, but tell the truth —
`st.caption("Saved locally — could not sync to your account.")` in the `except`.

---

## 8. Cycle 272 dates are hardcoded in three places — MEDIUM

- `app.py` 1199–1202 — the countdown metrics
- `app.py` 1256 — the CPO estimate
- `app.py` 1324–1332 — **inside an AI prompt**
- `app.py` 1643–1644 — the planner's exam date

After 10 September 2026 the countdown tab reads "✅ Passed / ✅ Passed / ✅ Passed /
✅ Passed" under a heading that still says "Cycle 272 Countdown", and
"Official NAVADMIN — ⏳ Not yet released" stays on screen forever.

The copy in the AI prompt is worse: the tutor will keep confidently citing Cycle 272
dates to a sailor studying for Cycle 274.

**Fix:** move the cycle into one dict at the top of the file, feed both the metrics and
the prompt from it, and show "Cycle 272 results are final — Cycle 273 dates not yet
published" once the exam date passes rather than four green ticks. Six weeks out.

---

## 9. Smaller things

- **`load_score_history` unpacking is outside its `try`** (296–305). The function catches
  its own errors, but the comprehension that reads `r["date"]`, `r["topic"]`, `r["score"]`
  runs unprotected and executes before any tab draws. One missing column in
  `score_history` and the whole app dies at the header, for every logged-in user.
- **`total` of zero divides by zero** (1573). If the grader model ever emits
  `Final Score: 0/0`, the sailor gets "Error: division by zero" after a mock exam.
  One `if total:` guard.
- **`compute_fms` has no lower clamp.** `min(exam_score, 80)` caps the top only, so a
  negative exam score passes straight through: `compute_fms("E6", -50, 4.06, 3.5, 4, 4, 6)`
  returns 26.5. Not reachable through the widgets today, since they all have
  `min_value=0.0`. It is a trap for the next person who calls the function from somewhere
  else. Make it `min(max(exam_score, 0.0), 80.0)`.
- **`over_cap_fields` never checks `tir` or `education`.** `tir=99` under E5 is not
  flagged. Not currently reachable — `FIELD_RANGES` caps tir at 30 and education is a
  dropdown — so this is completeness, not a live bug.
- **SIPG written as `03 YRS 06 MOS` parses to 3.0, not 3.5.** Costs 0.1 FMS. Reported as
  read.
- **A minus sign is ignored** — `-62.00` parses as `62.00`. Profile sheets have no
  negatives, so this is theoretical.
- **Privacy, already on your list, now with a measurement.** The "What was read from your
  sheet" expander prints `raw_text[:2000]`. On your own sample sheet, character 250 of
  2000 is `NAME (LAST, FIRST MI): RIVERA, MARCUS T.` and `DOD ID (LAST 4): 4417`. Both
  are inside the dump, on screen, every time.
- **All eight AI calls hardcode `claude-opus-4-5`** at 795, 1358, 1432, 1464, 1532, 1558,
  1676, 1764. One constant at the top of the file makes the model swap a one-line change
  instead of eight, and makes the mock-exam cost work easier.

---

## What I'd fix first

1. **Stripe on render** (finding 1). It costs you money and speed right now, on every
   free user, and the fix is contained.
2. **The paygrade wording regex** (finding 3). One line, and it shrinks finding 2.
3. **`smoke_test.py` tier** (finding 6). One word, and it restores coverage of the paid
   tabs before anything else changes them.
4. **The decimal separator** (finding 4). Small, and it stops the parser lying about a
   successful read.
5. **The RSCA text cross-check** (finding 2). Slightly bigger, and it closes the
   flattering-wrong-number band for good.

Findings 5 and 8 are real work and want their own sessions.

---

## How to re-run this

Two probe scripts, neither of which touches `app.py`:

- `upload_flow_probe.py` — drives the profile sheet upload path through AppTest, which
  `smoke_test.py` never does, and checks the values survive the reruns that follow.
- `tier_probe.py` — renders the app under every tier and counts Stripe calls per render.

Good news from the upload probe, for the record: uploading the clean E6 sample gives
62.00 / 4.06 / 3.50 / 4.00 / 4.00 / 6.00, auto-detects E6, survives three reruns without
drift, and calculates 138.5 — the correct answer. The core path works. Both sample PDFs,
including the crash-repro one, parse identically and cleanly.
