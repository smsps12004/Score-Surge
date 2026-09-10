# Real profile sheet, uploaded by Shawn 23 Aug 2026

Source file: a phone photo of a physical monitor (not a screenshot) — `pdfinfo` on the
original PDF shows Title "IMG_6060.HEIC", Producer "macOS Version 15.5 Quartz PDFContext".
Zero text layer (`pdftotext -layout` returns nothing) — pure OCR case, and the extreme
end of the "sailor with only a phone, no printer" scenario. The original 24MB file lives
in this conversation's upload history, not saved to disk here (over the 20MB device-bridge
transfer cap) — this file preserves the ground-truth transcription instead, which is what
a test fixture actually needs.

**Layout: this is the Navy BOL "Exam Profile Data" web page, NOT the NETPDC 1430/3 form**
the existing mock fixtures in `test-profile-sheets/` simulate. Any real coordinate-based
parser needs to handle both layouts, or at minimum not break on this one.

## What's on the page

Green "Unclassified" banner, browser tab "Exam Profile Data". Candidate: MILLAN NICHOLAS,
ID 1596815320, PRESENT RATE PS3, EXAM RATE PS2, GROUP USNRT, CYCLE 260, SERIAL NO 2600469,
DATE SEP 23, UIC 66231, PARENT UIC 43106.

**"FINAL MULTIPLE FACTOR SCORE BREAKDOWN" table columns:**
Exam Standard Score | PMA (Eval Avg) | Serv. In Pay Grade (YYMM) | Awards | Education
Points | PNA | Your Final Multiple | Minimum Multiple Req'd

**Row 1 — the sailor's own row ("YOUR multiple broken down by each factor"):**
49.50 | 64.00 (4.00) | 00:20 (0100) | **0** | 0.00 | 0.00 | 113.70 | 20.00

**Row 2 — "AVERAGE of candidates advanced in your rate" (always below row 1):**
51.22 | 56.55 (3.91) | 00.29 (0106) | **0** | 0.0 | (blank) | (blank, "PAGE: 1" instead)

## Two facts this confirmed, not previously known from the documented mock fixtures

1. **The sailor's own row is always first, top-to-bottom; the AVERAGE row is always
   below it.** This is the rule the coordinate-parser design already assumed — this real
   sheet is a second confirmation of it, on a totally different layout than the one that
   rule was first documented against.
2. **Awards can print as a bare "0", no decimal.** Every mock fixture used "4.00"-style
   decimals for every field. Fixed 23 Aug 2026 (commit `03ff425`): `extract_number_near_label`
   now accepts an exact "0" token even without a decimal point, while still rejecting every
   other bare integer (so the original date-fragment bug — "SEP 30,2025" read as SIPG 30.0
   — stays fixed).
3. **PMA and SIPG both print two numbers per cell**: a big/points-scale figure and a
   parenthesized raw figure — e.g. `64.00 (4.00)` and `00:20 (0100)`. This matches the
   documented rule (Finding 5, `BREAK_ATTEMPT_2026-07-29.md`) that the parenthesized raw
   figure is the one to read, not the points figure — but the current parser has not yet
   been checked against this specific two-number-per-cell format. Worth a targeted test.

## What this does NOT settle

The real structural fix — reading table columns by x-position instead of label-then-scan —
still hasn't been built. This sheet is one real, genuine ground-truth sample to build and
verify it against, but is only one layout (BOL web-page style). The NETPDC 1430/3 paper-form
layout the existing mocks simulate is a second, different layout. A real coordinate parser
should be checked against both before being called done.
