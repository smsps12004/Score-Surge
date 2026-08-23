#!/usr/bin/env python3
"""Score Surge pre-push checks.

Run this before every `git push`. It answers one question: did I break the math?

    python3 run_checks.py

It loads the pure logic out of app.py (no Streamlit needed), then checks:
  1. FMS math against hand-computed values for E5, E6 and E7
  1b. The same math against two VERIFIED REAL sheets, to the cent
  2. Every point cap actually caps
  3. The profile sheet parser against the real sample PDFs in test-profile-sheets/
  4. Label handling on the wordings real sheets use
  5. Paygrade detection across those wordings, and that it stays quiet on prose
  6. Over-cap detection, so a wrong paygrade is visible rather than clamped away
  7. Wording conflicts, which cover the PMA band the caps cannot see
  7b. That no name or DoD ID from a sheet is printed back to the screen
  8. That a finished exam cycle is never quoted as if it were current
  9. That no scraped value can escape a widget's min/max and crash the page
 10. That the Study Guide prompt cannot state a regulation from memory
 11. That EVERY fact-stating prompt is actually wired to the guardrails —
     section 8 proves they work, section 11 proves they are plugged in
 12. Where an unanswerable fact gets sent (PS Agent, never Google)
 13. That every topic mapped to real MILPERSMAN text actually resolves to it,
     and that a mapped lesson is still barred from stating anything the
     retrieved text doesn't actually say
 14. The coordinate-based profile-sheet reader (the root-cause fix): reads a
     real grid-style sheet's own printed values correctly, refuses to guess
     when a column doesn't reconcile, and is a true no-op on the NETPDC-form
     layout that already works
 15. Automatic Tutor grounding (corpus.get_series_grounding): finds real
     articles from a topic's own bibliography line, stays empty for a
     non-MILPERSMAN topic, respects the article cap, and never serves a
     superseded edition as current text

Exit code 0 = safe to push. Exit code 1 = something is broken OR some checks did
not run, read the output. A skipped check is never treated as a pass.
"""

import datetime
import os
import re
import sys
import fitz

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app.py")
SHEETS = os.path.join(HERE, "test-profile-sheets")

PASS, FAIL, SKIP = [], [], []

# Every check this file is supposed to run when nothing is missing. If the count
# at the end doesn't match this, checks went missing and the run is NOT a pass.
EXPECTED_TOTAL = 271


def skip(reason):
    """Record a block we could not run. A skipped check is not a passed check."""
    SKIP.append(reason)
    print(f"  SKIP  {reason}")


def check(name, got, want, tol=0.005):
    ok = (abs(got - want) < tol) if isinstance(want, float) else (got == want)
    (PASS if ok else FAIL).append((name, got, want))
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<52} got={got!r:<10} want={want!r}")
    return ok


def load_logic():
    """Pull the pure functions out of app.py without importing Streamlit."""
    src = open(APP, encoding="utf-8").read()

    def slab(start_marker, end_marker):
        a = src.index(start_marker)
        return src[a: src.index(end_marker, a)]

    ns = {"re": re, "datetime": datetime}
    exec(slab("CYCLE = {", "# ── CONSTANTS"), ns)
    exec(slab("FMS_RULES = {", "EXAM_MAX = 80.0"), ns)
    ns["EXAM_MAX"] = 80.0
    exec(slab("def pma_points", "LABEL_PATTERNS"), ns)
    exec(slab("LABEL_PATTERNS = {", "def extract_text_from_upload"), ns)
    return ns


def main():
    if not os.path.exists(APP):
        print("Could not find app.py next to this script.")
        return 1

    L = load_logic()
    fms, parse, getpg, safe = (L["compute_fms"], L["parse_ocr_text"],
                               L["extract_paygrade"], L["safe_value"])
    DEFAULT_VALUES = L["DEFAULT_VALUES"]
    reconciles = L["reading_reconciles"]
    extract_fm = L["extract_final_multiple"]

    # ── 1. FMS math, hand-computed from the MyNavyHR E4-E7 FMS chart ──────────
    print("\n1. FMS MATH")
    # E6: 62 + ((4.06*30)-60=61.80) + (3.5/5=0.70) + 4 + 4 + 6
    check("E6 typical sheet", fms("E6", 62, 4.06, 3.5, 4, 4, 6)[0], 138.50)
    # E5: 70 + ((3.80*80)-256=48.00) + (2/5=0.40) + 5 + 2 + 3
    check("E5 typical sheet", fms("E5", 70, 3.80, 2.0, 5, 2, 3)[0], 128.40)
    # E7: exam + RSCA PMA only; SIPG, awards, education and PNA are all worth 0
    check("E7 exam + PMA only", fms("E7", 55, 4.50, 6, 8, 4, 5)[0], 136.00)
    check("E7 ignores awards/edu/PNA", fms("E7", 55, 4.50, 0, 0, 0, 0)[0], 136.00)

    # ── 1b. Against REAL profile sheets ──────────────────────────────────────
    # Two genuine NETPDC sheets Shawn verified, 30 Jul 2026. Every other check in
    # this file is hand-computed from the published chart — which proves the code
    # matches my reading of the chart, not that either matches the Navy. These two
    # do, and they reproduce to the cent.
    #
    # Reading a real sheet: the FMS table prints POINTS, with the raw figure in
    # parentheses underneath. "PMA (Eval Avg) 48.00 (3.60)" is 48.00 points from an
    # eval average of 3.60. "Serv. In Pay Grade (YYMM) 01.20 (0600)" is 1.20 points
    # from 6 years 0 months. The app's inputs are the RAW figures, not the points.
    print("\n1b. REAL SHEETS (verified)")

    # GM2 competing for GM1 -> E6. Cycle 259, MAR 23.
    # Exam 69.15 | PMA 48.00 (3.60) | SIPG 01.20 (0600) | Awards 6 | Edu 0.00 | PNA 4.50
    a_total, a = fms("E6", 69.15, 3.60, 6.0, 6, 0.00, 4.50)
    check("real E6 sheet: PMA 3.60 -> 48.00 points", a["PMA Points"], 48.00)
    check("real E6 sheet: 6 yrs SIPG -> 1.20 points", a["Time in Rate"], 1.20)
    check("real E6 sheet: FMS matches the sheet exactly", a_total, 128.85)

    # BM3 competing for BM2 -> E5. Cycle 243, MAR 19.
    # Exam 56.43 | PMA 48.00 (3.80) | SIPG 00.20 (0100) | Awards 0 | Edu 0.00 | PNA 0.00
    b_total, b = fms("E5", 56.43, 3.80, 1.0, 0, 0.00, 0.00)
    check("real E5 sheet: PMA 3.80 -> 48.00 points", b["PMA Points"], 48.00)
    check("real E5 sheet: 1 yr SIPG -> 0.20 points", b["Time in Rate"], 0.20)
    check("real E5 sheet: FMS matches the sheet exactly", b_total, 104.63)

    # The same eval average is worth the same points at E5 and E6 only by
    # coincidence at these two values. Scoring either sheet under the other's rules
    # gets a different answer, which is the whole reason the paygrade must be right.
    check("real E6 sheet scored as E5 is NOT the same number",
          fms("E5", 69.15, 3.60, 6.0, 6, 0.00, 4.50)[0] == 128.85, False)

    # ── 2. Caps ──────────────────────────────────────────────────────────────
    print("\n2. POINT CAPS")
    check("E6 PMA caps at 114", fms("E6", 0, 5.80, 0, 0, 0, 0)[1]["PMA Points"], 114.00)
    check("E5 PMA caps at 64", fms("E5", 0, 4.00, 0, 0, 0, 0)[1]["PMA Points"], 64.00)
    check("Exam caps at 80", fms("E6", 999, 0, 0, 0, 0, 0)[1]["Exam Standard Score"], 80.00)
    check("E6 SIPG caps at 3", fms("E6", 0, 0, 100, 0, 0, 0)[1]["Time in Rate"], 3.00)
    check("E5 SIPG caps at 2", fms("E5", 0, 0, 100, 0, 0, 0)[1]["Time in Rate"], 2.00)
    check("Awards cap at 12 (E6)", fms("E6", 0, 0, 0, 999, 0, 0)[1]["Awards"], 12.00)
    check("PNA caps at 9", fms("E6", 0, 0, 0, 0, 0, 999)[1]["PNA Points"], 9.00)
    check("PMA below floor is never negative",
          fms("E5", 0, 1.00, 0, 0, 0, 0)[1]["PMA Points"], 0.00)

    # Every component clamps at BOTH ends. These used to cap the top only. The
    # widgets all carry min_value=0.0 so nothing negative could arrive through the
    # form, which is exactly why it went unnoticed — but compute_fms is reachable
    # from anywhere, and a negative subtracts from a number the sailor is trusting.
    neg = fms("E6", -50, 4.06, -100, -50, -99, -99)[1]
    check("negative exam score floors at 0", neg["Exam Standard Score"], 0.00)
    check("negative SIPG floors at 0", neg["Time in Rate"], 0.00)
    check("negative awards floors at 0", neg["Awards"], 0.00)
    check("negative education floors at 0", neg["Education"], 0.00)
    check("negative PNA floors at 0", neg["PNA Points"], 0.00)
    check("an all-negative sheet scores 0, not a negative FMS",
          fms("E6", -50, 0, -100, -50, -99, -99)[0], 0.00)

    # ── 3. Parser against the real sample sheets ─────────────────────────────
    print("\n3. PROFILE SHEET PARSER")
    try:
        import fitz  # PyMuPDF
    except ImportError:
        skip("PyMuPDF not installed — parser checks did NOT run (pip install pymupdf)")
    else:
        truth = {"exam_score": 62.0, "pma": 4.06, "tir": 3.5,
                 "awards": 4.0, "education": 4.0, "pna": 6.0}
        for tag in ("clean", "crash-repro"):
            path = os.path.join(SHEETS, f"PROFILE_SHEET_PS2_E6_{tag}.pdf")
            if not os.path.exists(path):
                skip(f"sample sheet missing: {tag} — parser checks did NOT run for it")
                continue
            raw = "".join(p.get_text() for p in fitz.open(path))
            got, missing = parse(raw)
            for field, want in truth.items():
                check(f"{tag}: {field}", got[field], want)
            check(f"{tag}: nothing fell back to a placeholder", missing, [])
            check(f"{tag}: paygrade detected", getpg(raw), "E6")

    # ── 4. Label handling on wordings real sheets use ────────────────────────
    print("\n4. LABEL HANDLING")
    sipg_only = ("EXAM STANDARD SCORE 62.00\nPERFORMANCE MARK AVERAGE 4.06\n"
                 "SERVICE IN PAYGRADE 3.50\nAWARDS POINTS 4.00\n"
                 "EDUCATION POINTS 4.00\nPASSED NOT ADVANCED (PNA) POINTS 6.00")
    check("'Service in Paygrade' reads as SIPG/TIR", parse(sipg_only)[0]["tir"], 3.5)

    noisy = ("EXAM STANDARD SCORE (CYCLE 272, MAR 2026) 62.00\n"
             "RSCA PMA AS OF 30 SEP 2025 4.06\n"
             "SERVICE IN PAYGRADE AS OF 01 SEP 2026 3.50\n"
             "AWARDS POINTS PER SECNAVINST 1650 4.00\n"
             "EDUCATION POINTS PER NAVADMIN 121 4.00\n"
             "PNA POINTS CYCLES 268 270 271 6.00")
    g = parse(noisy)[0]
    check("cycle number not read as exam score", g["exam_score"], 62.0)
    check("date not read as SIPG", g["tir"], 3.5)
    check("cycle list not read as PNA", g["pna"], 6.0)

    absent = ("EXAM STANDARD SCORE 62.00\nRSCA PMA 4.06\nSERVICE IN PAYGRADE\n"
              "AWARDS POINTS 4.00\nEDUCATION POINTS 4.00\nPNA POINTS 6.00")
    check("missing value reported, not borrowed from next row",
          "tir" in parse(absent)[1], True)

    # The number pattern used to accept a period and at most two decimals. "4,06" —
    # what OCR returns when it reads a period as a comma, routine on a photographed
    # sheet — was consumed as the integer 4, and the field was still reported as
    # successfully read. The sailor saw "Read all six fields" above a PMA that had
    # quietly lost 1.8 FMS points.
    commas = ("EXAM STANDARD SCORE 62,00\nPERFORMANCE MARK AVERAGE 4,06\n"
              "SERVICE IN PAYGRADE 3,50\nAWARDS POINTS 4,00\n"
              "EDUCATION POINTS 4,00\nPNA POINTS 6,00")
    c = parse(commas)
    check("OCR comma decimal: PMA", c[0]["pma"], 4.06)
    check("OCR comma decimal: SIPG", c[0]["tir"], 3.5)
    check("OCR comma decimal: exam score", c[0]["exam_score"], 62.0)
    check("OCR comma decimal: nothing falls back to a placeholder", c[1], [])

    three_dp = ("EXAM STANDARD SCORE 62.000\nPERFORMANCE MARK AVERAGE 4.060\n"
                "SERVICE IN PAYGRADE 3.500\nAWARDS POINTS 4.000\n"
                "EDUCATION POINTS 4.000\nPNA POINTS 6.000")
    t = parse(three_dp)[0]
    check("three decimal places: PMA", t["pma"], 4.06)
    check("three decimal places: SIPG", t["tir"], 3.5)

    # Accepting the comma must not let dates and thousands separators in. This is
    # why the comma form takes exactly two decimals and no more.
    comma_noise = ("EXAM STANDARD SCORE (CYCLE 272) 62.00\n"
                   "RSCA PMA AS OF SEP 30,2025 4.06\n"
                   "SERVICE IN PAYGRADE AS OF JAN 01,2026 3.50\n"
                   "AWARDS POINTS PER SECNAVINST 1,650 4.00\n"
                   "EDUCATION POINTS 4.00\nPNA POINTS 6.00")
    n = parse(comma_noise)[0]
    check("'SEP 30,2025' is not read as a PMA", n["pma"], 4.06)
    check("'JAN 01,2026' is not read as SIPG", n["tir"], 3.5)
    check("'SECNAVINST 1,650' is not read as awards", n["awards"], 4.0)
    # A malformed comma-decimal like "4,060" is genuinely ambiguous — is it 4.06 with
    # a stray trailing digit, or 4.60, or something else entirely? Guessing either way
    # risks reporting a wrong PMA as successfully read. 24 Aug 2026: this used to fall
    # back to the bare truncated integer (4.0) and call that "read" — safer than
    # guessing the decimals, but still a guess dressed up as a reading. Now it's
    # reported honestly as not found, same as any other unreadable field.
    _three_dp_result, _three_dp_missing = parse("PERFORMANCE MARK AVERAGE 4,060")
    check("a comma with three decimals is reported missing, not guessed at",
          "pma" in _three_dp_missing, True)
    check("...and shows the placeholder, not a truncated guess",
          _three_dp_result["pma"], DEFAULT_VALUES["pma"])

    # 24 Aug 2026, real sheet: a real BOL "Exam Profile Data" sheet printed Awards as
    # a bare "0", no decimal at all — Fix A above (require a decimal) would have
    # reported a true zero as missing and substituted the 2.0 placeholder instead,
    # which is worse than the bug it fixed. A lone "0" is now accepted as itself;
    # every OTHER bare integer must still be rejected, same as before.
    bare_zero_awards = ("EXAM STANDARD SCORE 49.50\nPMA (EVAL AVG) 64.00 (4.00)\n"
                         "SERV. IN PAY GRADE 00:20 (0100)\nAWARDS 0\n"
                         "EDUCATION POINTS 0.00\nPNA 0.00")
    z = parse(bare_zero_awards)
    check("a bare '0' Awards is read as a true zero, not missing", z[0]["awards"], 0.0)
    check("...not silently swapped for the placeholder default",
          "awards" not in z[1], True)
    check("a bare non-zero integer near a label is still rejected",
          "tir" in parse("SERVICE IN PAYGRADE AS OF SEP 30,2025\nAWARDS 0")[1], True)

    print("\n5. PAYGRADE DETECTION")
    # Navy systems print the same paygrade as E6, E-6 and E06, and often name the
    # rate first: "ADVANCEMENT TO PSC (E7)". Matching only a bare "E6" meant
    # detection silently failed on all of those and pushed the choice back onto the
    # sailor — the one decision that must never be guessed, because an E6 sheet
    # scored under E5 rules can come out HIGHER than the truth.
    for text, want in [
        ("PAYGRADE COMPETING FOR: E6", "E6"),
        ("PAYGRADE YOU ARE COMPETING FOR:  E7", "E7"),
        ("Competing for paygrade - E5", "E5"),
        ("ADVANCEMENT TO E6", "E6"),
        ("ADVANCEMENT TO E-6", "E6"),
        ("PAYGRADE: E-7", "E7"),
        ("PAYGRADE: E06", "E6"),
        ("PAYGRADE COMPETING FOR: E05", "E5"),
        ("PAYGRADE: E 6", "E6"),
        ("CANDIDATE FOR ADVANCEMENT TO PSC (E7)", "E7"),
        ("ADVANCEMENT TO PS1 E6", "E6"),
        ("PAYGRADE COMPETING FOR: (E6)", "E6"),
        ("CURRENT PAYGRADE: E6 PAYGRADE COMPETING FOR: E7", "E7"),
    ]:
        check(f"reads '{text[:44]}'", getpg(text), want)

    # How real sheets ACTUALLY state it. Two genuine sheets and one photographed
    # one carry no "paygrade competing for" wording at all — they have a header row
    # PRESENT RATE | EXAM RATE | GROUP | BRANCH CLASS | CYCLE ... with the values in
    # a grid underneath. A rate is its own paygrade: the numeral is the grade and C
    # is Chief. Confirmed by Shawn, and by arithmetic — the GM1 sheet reconciles
    # only under E6 rules, the BM2 sheet only under E5.
    r2p = L["rate_to_paygrade"]
    for rate, want in [("HM3", "E4"), ("BM3", "E4"), ("PS2", "E5"), ("BM2", "E5"),
                       ("PS1", "E6"), ("GM1", "E6"), ("PSC", "E7"), ("GMC", "E7"),
                       ("PSCS", "E8"), ("PSCM", "E9")]:
        check(f"rate {rate} is {want}", r2p(rate), want)
    for junk in ("SN", "PS", "PS4", "", None):
        check(f"{junk!r} is not a rate", r2p(junk), None)

    # Value rows lifted from the real sheets, including OCR output from a photo.
    for name, text, want in [
        ("GM sheet value row",
         "PRESENT RATE EXAM RATE GROUP BRANCH CLASS CYCLE SERIAL NO. DATE UIC "
         "PARENT UIC GM2 GM1 USN 259 2590023 MAR 23", "E6"),
        ("BM sheet value row",
         "PRESENT EXAM BRANCH SERIAL RATE RATE GROUP CLASS CYCLE NO. DATE UIC "
         "BM3 BM2 USN 243 2430329 MAR 19 21825", "E5"),
        ("photographed sheet, OCR'd",
         "3 AN NICHOLAS 596815320 PS3 PS2 USNRT 266 2600469 SEP 23 66231 43106", "E5"),
        ("labelled exam rate", "EXAM RATE: PSC", "E7"),
    ]:
        check(f"{name} -> {want}", getpg(text), want)

    # PRESENT and EXAM rate are always one grade apart, present first. Without that
    # rule the header row of a real sheet reads "UIC UIC" as "UI"+"C" twice and
    # hands back a confident E7 to an E5 candidate — found on Shawn's BM sheet,
    # which is exactly the wrong-paygrade failure everything else here guards.
    check("'UIC UIC' in a header row is not a rate pair",
          getpg("PRESENT EXAM RATE RATE UIC UIC PARENT"), None)
    check("UIC is not a rate", r2p("UIC"), None)
    check("a two-grade jump is not a rate pair", getpg("PS2 PSC"), None)
    check("a backwards pair is not a rate pair", getpg("GM1 GM2"), None)
    check("PS1 PSC is a real progression", getpg("RATE RATE PS1 PSC USN"), "E7")

    # The prose on a real sheet must not be mistaken for a paygrade statement.
    check("'CANNOT BE ADVANCED TO THE NEXT HIGHER PAY GRADE' is not a paygrade",
          getpg("SUBJECT CANDIDATE PASSED THE EXAMINATION BUT DUE TO QUOTA "
                "LIMITATIONS CANNOT BE ADVANCED TO THE NEXT HIGHER PAY GRADE"), None)
    check("a topic score row is not a rate pair",
          getpg("TOPIC 1. WEAPONS SYSTEMS FUNDAMENTALS 28 16 69"), None)

    # Loosening the pattern must not make it fire on ordinary prose. The word
    # boundary before the "e" is what stops "THE 5TH" reading as E5.
    for text in [
        "no paygrade statement anywhere",
        "E5 mentioned but not as a labelled field",
        "ADVANCEMENT TO THE 5TH DIVISION",
        "ADVANCEMENT TO THE 6 PILLARS OF LEADERSHIP",
        "PAYGRADE: E60",
        "CANDIDATE FOR THE 7 SEAS AWARD",
    ]:
        check(f"ignores '{text[:44]}'", getpg(text), None)

    # ── 6. Paygrade/value conflicts are reported, not silently clamped ───────
    # Regression guard for the bug where an E6 sheet read under E5 rules had its
    # PMA trimmed 4.06 -> 4.00 and scored 140.7 instead of 138.5 — higher than the
    # truth, so nothing looked wrong.
    print("\n6. OVER-CAP DETECTION (wrong paygrade must be visible)")
    over = L["over_cap_fields"]
    e6_sheet = {"exam_score": 62.0, "pma": 4.06, "tir": 3.5,
                "awards": 4.0, "education": 4.0, "pna": 6.0}

    flagged = over(e6_sheet, "E5", True)
    check("E6 sheet under E5 rules flags PMA", [f[0] for f in flagged], ["pma"])
    check("...and reports the value found", flagged[0][1] if flagged else None, 4.06)
    check("...and reports the cap it broke", flagged[0][2] if flagged else None, 4.00)
    check("E6 sheet under E6 rules flags nothing", over(e6_sheet, "E6", True), [])
    check("nothing flagged before a paygrade is picked", over(e6_sheet, "E5", False), [])
    check("E7 does not flag awards/PNA it never scores", over(e6_sheet, "E7", True), [])
    check("exam score over 80 is flagged",
          [f[0] for f in over({**e6_sheet, "exam_score": 99.0}, "E6", True)], ["exam_score"])
    check("awards over the E5 cap are flagged",
          [f[0] for f in over({**e6_sheet, "pma": 3.8, "awards": 99.0}, "E5", True)], ["awards"])
    check("junk values do not crash the reporter",
          over({**e6_sheet, "pma": "junk"}, "E5", True), [])
    check("missing field does not crash the reporter",
          over({"pma": 4.06}, "E5", True), [("pma", 4.06, 4.00)])

    # ── 7. The sheet's wording vs the dropdown ───────────────────────────────
    # over_cap_fields only fires when a number breaks a cap, so it cannot see
    # anything at or below PMA 4.00. E5 PMA points are (pma x 80) - 256 and E6 is
    # (pma x 30) - 60; they cross at 3.92, so an E6 sheet scored as E5 with a PMA
    # between 3.92 and 4.00 reads HIGHER than the truth with no cap broken. These
    # checks cover that band using the sheet's own text.
    print("\n7. PAYGRADE CONFLICTS (what the caps cannot see)")
    conflicts = L["paygrade_conflicts"]
    e6_text = ("PAYGRADE COMPETING FOR: E6\n"
               "PERFORMANCE MARK AVERAGE (RSCA PMA) 3.95\n"
               "SERVICE IN PAYGRADE 3.50")
    e5_text = ("PAYGRADE COMPETING FOR: E5\n"
               "PERFORMANCE MARK AVERAGE 3.95\n"
               "SERVICE IN PAYGRADE 2.00")

    # The exact band the caps miss. Nothing is over cap here, by construction.
    check("no cap is broken in the 3.92-4.00 band",
          over({"exam_score": 62.0, "pma": 3.95, "tir": 3.5,
                "awards": 4.0, "education": 4.0, "pna": 6.0}, "E5", True), [])
    check("...and scoring it as E5 really does read higher",
          fms("E5", 62, 3.95, 3.5, 4, 4, 6)[0] > fms("E6", 62, 3.95, 3.5, 4, 4, 6)[0], True)
    check("...but the wording conflict IS caught",
          len(conflicts(e6_text, "E5", True)) >= 1, True)

    check("E6 sheet under E6 rules is quiet", conflicts(e6_text, "E6", True), [])
    check("E5 sheet under E5 rules is quiet", conflicts(e5_text, "E5", True), [])
    check("stated paygrade disagreeing with the dropdown is caught",
          len(conflicts(e5_text, "E7", True)) >= 1, True)
    check("RSCA wording under E5 is caught even with no stated paygrade",
          len(conflicts("PERFORMANCE MARK AVERAGE (RSCA PMA) 3.95", "E5", True)), 1)
    check("RSCA wording under E6 is not a conflict",
          conflicts("PERFORMANCE MARK AVERAGE (RSCA PMA) 3.95", "E6", True), [])
    check("nothing is claimed before a paygrade is picked",
          conflicts(e6_text, "E5", False), [])
    check("no sheet uploaded means nothing to disagree with",
          conflicts("", "E5", True), [])
    check("both signals at once are both reported",
          len(conflicts(e6_text, "E5", True)), 2)

    # ── 7b. Nothing identifying goes back on screen ──────────────────────────
    # The "what was read from your sheet" panel printed 2000 raw characters so a
    # sailor could see why a field was missed. On a real sheet the first 250 of
    # those are their full name and the last four of their DoD ID, plus the ESO of
    # record — a second named person who never consented to anything.
    print("\n7b. PII REDACTION")
    redact = L["redact_pii"]
    try:
        import fitz  # noqa: F811
    except ImportError:
        skip("PyMuPDF not installed — PII redaction not checked against a real sheet")
    else:
        sheet = os.path.join(SHEETS, "PROFILE_SHEET_PS2_E6_clean.pdf")
        if not os.path.exists(sheet):
            skip("sample sheet missing — PII redaction not checked against a real sheet")
        else:
            raw = "".join(p.get_text() for p in fitz.open(sheet))
            shown = redact(raw)
            for secret in ("RIVERA", "MARCUS", "4417", "HAWKINS"):
                check(f"'{secret}' never reaches the screen", secret in shown, False)
            # Redaction is display-only. Parsing must still see the real sheet.
            check("redaction does not touch the parsed values",
                  parse(raw)[0]["pma"], 4.06)
            check("redaction does not touch paygrade detection", getpg(raw), "E6")
            # ...and it must not eat the things a sailor needs to debug with.
            for keep in ("EXAM STANDARD SCORE", "PAYGRADE COMPETING FOR", "62.00", "4.06"):
                check(f"'{keep}' is still shown", keep in shown, True)

    check("inline 'NAME: value' is masked",
          "RIVERA" in redact("NAME (LAST, FIRST MI): RIVERA, MARCUS T."), False)
    check("a rate is not mistaken for a name",
          redact("PRESENT RATE:\nPS2"), "PRESENT RATE:\nPS2")
    check("empty text does not crash the redactor", redact(""), "")
    check("None does not crash the redactor", redact(None), "")

    # ── 8. The app must not keep quoting a finished cycle ────────────────────
    # The countdown had no concept of a cycle ending: after the last exam day it
    # would have shown four "✅ Passed" tiles under a live-countdown heading, and
    # "Official NAVADMIN — Not yet released" forever. Worse, the same dates were
    # typed into an AI prompt, so the tutor would have gone on quoting Cycle 272
    # dates to a sailor studying for the next cycle — confidently, with nothing on
    # screen looking wrong.
    print("\n8. STALE CYCLE DATES")
    expired = L["cycle_expired"]
    facts = L["cycle_facts_block"]
    authority = L["cycle_authority_line"]
    CYC = L["CYCLE"]

    day_before = CYC["exam_e5"] - datetime.timedelta(days=1)
    day_after = CYC["exam_e5"] + datetime.timedelta(days=1)

    check("cycle is live the day before the last exam", expired(day_before), False)
    check("cycle is live ON the last exam day", expired(CYC["exam_e5"]), False)
    check("cycle is over the day after", expired(day_after), True)

    check("live prompt states the cycle dates",
          "E6 exam date" in facts(day_before), True)
    check("live prompt names the NAVADMIN",
          CYC["navadmin"] in authority(day_before), True)

    # The important half. A model repeats what it is handed.
    stale = facts(day_after)
    check("expired prompt states NO exam date", "E6 exam date" in stale, False)
    check("expired prompt gives no year at all",
          any(str(y) in stale for y in (2024, 2025, 2026, 2027)), False)
    check("expired prompt says it does not know",
          "do NOT know" in stale or "not loaded" in stale, True)
    check("expired prompt forbids quoting an old date",
          "NEVER state a date from a past cycle" in stale, True)
    check("expired authority line drops the NAVADMIN claim",
          CYC["navadmin"] in authority(day_after), False)

    # Every date the app states must come from CYCLE, so one edit moves them all.
    src = open(APP, encoding="utf-8").read()
    body = src[src.index("# ── CONSTANTS"):]
    check("no hardcoded date literal survives outside the CYCLE block",
          "datetime.date(20" in body, False)
    check("no hardcoded cycle number survives outside the CYCLE block",
          str(CYC["number"]) in body, False)

    # ── 8b. A misread can't reconcile with the sheet's own printed total ─────
    #
    # 24 Aug 2026, per the 29-30 Jul adversarial finding: a real, densely tabled
    # profile sheet can hand the parser six individually plausible numbers that are
    # still wrong (a value from the wrong column, or the AVERAGE-of-candidates row
    # instead of the sailor's own). "6 of 6 fields found" looks identical either way.
    # This is the safety net that catches that: check the parsed six against the
    # Final Multiple the sheet ALREADY prints. A bad read essentially can't produce
    # a total that happens to match by accident.
    print("\n8b. RECONCILIATION (a misread can't match the sheet's own printed total)")

    good_e6 = ("EXAM STANDARD SCORE 62.00\nRSCA PMA 4.06\nSERVICE IN PAYGRADE 3.50\n"
               "AWARDS POINTS 4.00\nEDUCATION POINTS 4.00\nPNA POINTS 6.00\n"
               "YOUR FINAL MULTIPLE 138.50")
    good_data, _ = parse(good_e6)
    ok, printed, computed = reconciles("E6", good_data, good_e6)
    check("a correct read reconciles with the sheet's own total", ok, True)
    check("...and reports the printed figure it matched", printed, 138.50)
    check("...and reports the same figure it computed", computed, 138.50)

    # Same six numbers, but as if the PMA value had landed in the SIPG column and
    # 3.50 had landed in PMA's — the exact column-swap failure mode a real sheet's
    # layout invites. The total no longer matches what the sheet prints, even though
    # every individual number still looks perfectly plausible on its own.
    swapped_e6 = ("EXAM STANDARD SCORE 62.00\nRSCA PMA 3.50\nSERVICE IN PAYGRADE 4.06\n"
                  "AWARDS POINTS 4.00\nEDUCATION POINTS 4.00\nPNA POINTS 6.00\n"
                  "YOUR FINAL MULTIPLE 138.50")
    swapped_data, _ = parse(swapped_e6)
    ok2, printed2, computed2 = reconciles("E6", swapped_data, swapped_e6)
    check("a column-swapped read does NOT reconcile", ok2, False)
    check("...even though the sheet's total is still visible", printed2, 138.50)

    # A sheet that never prints a Final Multiple (or one too garbled to OCR) must not
    # be treated as a failed check — there is nothing to check against.
    no_total = ("EXAM STANDARD SCORE 62.00\nRSCA PMA 4.06\nSERVICE IN PAYGRADE 3.50\n"
                "AWARDS POINTS 4.00\nEDUCATION POINTS 4.00\nPNA POINTS 6.00")
    no_total_data, _ = parse(no_total)
    ok3, printed3, computed3 = reconciles("E6", no_total_data, no_total)
    check("no printed total means nothing to check, not a failure", ok3, None)
    check("...and says so by reporting no printed figure", printed3, None)

    check("extract_final_multiple ignores a page number near unrelated text",
          extract_fm("Page 3 of 8\nFinal Multiple Score chart, see appendix"), None)

    # ── 9. Nothing out of range can reach a widget ───────────────────────────
    print("\n9. CRASH GUARDS (values that used to break the page)")
    for raw, lo, hi in [(272.0, 0.0, 9.0), (-5.0, 0.0, 80.0), (9999.0, 0.0, 30.0),
                        (None, 0.0, 9.0), ("junk", 0.0, 80.0), (float("nan"), 0.0, 5.8)]:
        out = safe(raw, lo, hi, lo)
        ok = isinstance(out, float) and lo <= out <= hi
        (PASS if ok else FAIL).append((f"safe_value({raw!r})", out, f"{lo}..{hi}"))
        print(f"  {'PASS' if ok else 'FAIL'}  safe_value({raw!r:>10}) -> {out} "
              f"(must stay within {lo}..{hi})")

    # ── 10. The Study Guide cannot state a regulation from memory ────────────
    #
    # This tab is the only place a paying sailor is handed free-form AI text with no
    # source behind it. The prompt forbids the model from stating deadlines, dollar
    # amounts, form numbers, article numbers or eligibility thresholds — it names the
    # topic and sends them to the bibliography instead. A sailor cannot tell a
    # remembered regulation from a real one, so the rule has to live in the prompt and
    # the prompt has to be checked, or it quietly gets edited away.
    #
    # The prompt block is built from app.py as TEXT and executed with dummy values, the
    # same trick the rest of this file uses — no Streamlit, no API call.
    print("\n10. STUDY GUIDE PROMPT (must not state regulations from memory)")
    src10 = open(APP, encoding="utf-8").read()
    _a = src10.index("                topic_instruction = (")
    _b = src10.index('                with st.spinner("Chief is reviewing your record...")')
    prompt_block = compile("if True:\n" + src10[_a:_b], "app.py-studyguide", "exec")

    for guide_type, wants_carve_out in (("Crash Plan (3-5 days)", False),
                                        ("Single Subject Deep Dive", False),
                                        ("Practice Questions", True)):
        ns = {
            "sg_type": guide_type, "sg_subject": "Military Awards",
            "sg_rating": "PS", "sg_paygrade": "E7", "sg_gap": 4.0,
            "strategy": "precision mode",
            # The cycle helpers are the app's own; stub them so this check is about the
            # accuracy rules and not about dates, which section 8 already covers.
            "cycle_authority_line": lambda: "AUTHORITY LINE",
            "cycle_facts_block": lambda: "CYCLE FACTS",
        }
        exec(prompt_block, ns)
        p = ns["prompt"]
        tag = guide_type.split(" (")[0]
        check(f"[{tag}] prompt carries the accuracy rules",
              "ACCURACY RULES" in p, True)
        check(f"[{tag}] prompt forbids stating form numbers",
              "form number" in p, True)
        check(f"[{tag}] prompt forbids stating article numbers",
              "instruction or NAVADMIN number" in p, True)
        check(f"[{tag}] no unrendered placeholder left in the prompt",
              "{sg_" in p, False)
        # Practice Questions is the one type that cannot obey a blanket no-facts rule,
        # so it gets a narrower rule instead. Every other type must NOT get that
        # carve-out — if it leaks, the no-facts rule is off for a plain study guide.
        check(f"[{tag}] questions carve-out present only where it belongs",
              "EXCEPTION FOR THIS GUIDE TYPE" in p, wants_carve_out)

    # ── 11. Every prompt that states Navy facts must carry the guardrails ────
    #
    # Section 8 proves cycle_authority_line() and cycle_facts_block() BEHAVE correctly.
    # It never checked which prompts actually call them — and on 20 August 2026 the
    # answer was one out of three. The Study Guide had them; the AI Tutor and the Mock
    # Exam did not. A whole safety system, tested and passing, wired into a third of
    # the app. This section checks the wiring, not the wire.
    print("\n11. GUARDRAIL WIRING (which prompts actually carry the protections)")
    src11 = open(APP, encoding="utf-8").read()

    def slab_between(start_marker, end_marker):
        a = src11.index(start_marker)
        return src11[a: src11.index(end_marker, a)]

    PROMPTS = [
        ("study guide", 'prompt = f"""You are a senior {sg_rating}', 'with st.spinner("Chief is reviewing'),
        ("AI tutor",    'lesson_body = f"""You are a senior {tutor_rating}', 'with st.spinner("Chief is preparing'),
        ("mock exam",   'pq_prompt = f"""You are a senior {pq_rating}', 'with st.spinner("Chief is writing'),
    ]
    for name, start, end in PROMPTS:
        body = slab_between(start, end)
        check(f"{name} prompt calls cycle_authority_line()",
              "cycle_authority_line()" in body, True)
        check(f"{name} prompt calls cycle_facts_block()",
              "cycle_facts_block()" in body, True)

    tutor = slab_between("# ── TAB 4: AI TUTOR", "def parse_exam_json")
    check("tutor lesson prompt carries the accuracy rules",
          "ACCURACY RULES" in tutor, True)
    check("tutor forbids naming an approving authority as fact",
          "named approving authority" in tutor, True)
    check("tutor follow-up restates the rules on every turn",
          "STANDING RULES" in tutor, True)
    check("tutor follow-up tells the Chief not to defend an earlier claim",
          "do NOT defend it" in tutor, True)
    check("tutor lesson is labelled on screen as written from memory",
          "written from memory, not from the manual" in tutor.lower(), True)
    check("downloaded lesson carries its own caveat",
          "WRITTEN FROM MEMORY, NOT FROM THE MANUAL" in tutor, True)
    # The API requires roles to alternate. Saving only the assistant reply put two
    # assistant turns back to back, so the SECOND follow-up a sailor asked always
    # failed. Both roles must be persisted.
    check("tutor history saves the sailor's turn, not just the Chief's",
          'tutor_history.append(\n                                {"role": "user"' in tutor, True)

    # ── 12. Where an unanswerable fact gets sent ─────────────────────────────
    #
    # The accuracy rules stop the AI stating a regulation from memory. That leaves the
    # sailor holding a gap, and the referral is what fills it. "Go look it up" sends them
    # to Google; PS Agent answers out of the manual with the citation attached, so that
    # is where they go. This is also the first seam joining the two apps, which makes it
    # exactly the kind of wording a later prompt edit would quietly undo.
    #
    # Two halves, both required. The referral has to NAME PS Agent, and it has to carry
    # enough to ask a good question — the manual and the subject. It must also keep the
    # ban on inventing an article number, or the referral becomes a new way for the model
    # to guess a citation, which is the failure the accuracy rules were built to stop.
    print("\n12. PS AGENT REFERRAL (unanswerable facts go to PS Agent, not 'look it up')")

    sg_prompt = slab_between('prompt = f"""You are a senior {sg_rating}',
                             'with st.spinner("Chief is reviewing')

    for name, body in (("study guide", sg_prompt), ("tutor lesson", tutor)):
        check(f"{name} sends the sailor to PS Agent",
              "PS Agent" in body, True)
        check(f"{name} rules out Google and a bare look-up",
              'never a bare "look it up."' in body, True)
        check(f"{name} referral names the manual and the subject",
              "name the manual and the exact subject" in body, True)
        check(f"{name} referral may not invent an article number",
              "invent an article" in body, True)

    check("tutor follow-up sends the value question to PS Agent",
          "PS Agent for the value" in tutor, True)
    check("tutor follow-up settles a challenge via PS Agent",
          "ask PS Agent about that subject" in tutor, True)

    # ── 13. Tutor corpus grounding (real MILPERSMAN text, not a guess) ───────
    #
    # Item 3 wires a shipped, plain-text copy of the MILPERSMAN into the Tutor, so a
    # mapped topic teaches from the article's actual wording instead of memory. A
    # keyword-guess version of this was tried and rejected — it matched "Strength
    # Loss" to an article about entry-level separations on one shared word. So the
    # topic-to-article map (TOPIC_ARTICLE_MAP in app.py) is hand-checked, one article
    # at a time, and this section proves every entry in it still resolves to real
    # text — not that every topic has been mapped, which is intentionally not true yet.
    print("\n13. TUTOR CORPUS GROUNDING (mapped topics teach from real MILPERSMAN text)")
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import corpus as _corpus
    import importlib
    importlib.reload(_corpus)

    check("corpus text shipped with the app", _corpus.corpus_available(), True)

    src13 = open(APP, encoding="utf-8").read()
    map_ns = {}
    _m = src13.index("TOPIC_ARTICLE_MAP = {")
    _map_end = src13.index("\n}\n", _m) + 3
    exec(src13[_m:_map_end], map_ns)
    topic_map = map_ns["TOPIC_ARTICLE_MAP"]

    check("at least one topic is mapped to real MILPERSMAN text",
          len(topic_map) > 0, True)
    for (topic, subtopic), articles in topic_map.items():
        tag = f"{topic.split(' - ')[-1]}/{subtopic}"
        block, matched = _corpus.build_source_block(articles)
        check(f"[{tag}] resolves to real article text", bool(block), True)
        check(f"[{tag}] every mapped article number actually loaded",
              matched, articles)

    check("an unmapped article number returns nothing, never a guess",
          _corpus.get_article_text("9999-999"), "")

    check("tutor lesson looks up TOPIC_ARTICLE_MAP before falling back to memory",
          "TOPIC_ARTICLE_MAP.get((tutor_topic, tutor_subtopic)" in tutor, True)
    check("grounded lesson must name the article when stating a fact",
          "you must name the article number when you do" in tutor, True)
    check("grounded lesson still forbids guessing what the text doesn't cover",
          "is NOT in the text above, do not guess it" in tutor, True)
    check("grounded practice questions are limited to the source text",
          "Base them only on what is in the text above" in tutor, True)
    check("grounded follow-up may cite the source text, unlike memory mode",
          "You MAY state a specific fact if, and only" in tutor, True)
    check("on-screen banner says when a lesson is grounded",
          "grounded in the real text of milpersman" in tutor.lower(), True)
    check("downloaded grounded lesson names the article in its caveat",
          "GROUNDED IN THE REAL TEXT OF MILPERSMAN" in tutor, True)

    # ── 14. COORDINATE-BASED PARSING (the root-cause fix, BREAK_ATTEMPT finding 5) ──
    #
    # The label-based reader above (sections 1b, 3, 4) is proven against the two real
    # NETPDC-form sheets, where each FMS field is its own printed row and the value
    # sits right after its own label — label-then-scan-forward works fine there. The
    # failure mode this section targets is the OTHER real layout: a header row of
    # column titles once, then a GRID of candidate rows underneath (the GM sheet
    # worked 29-30 Jul, and the BOL "Exam Profile Data" sheet Shawn supplied 23 Aug).
    # Flattened text throws away which row and column a number in that grid belongs
    # to. extract_fields_by_position() reads the grid by word coordinates instead.
    #
    # No real photo of a grid-style sheet is available on disk to OCR end to end in
    # this environment (test-profile-sheets/ only has the two NETPDC-form PDFs and a
    # transcription of the BOL sheet's ground-truth values, not the original photo).
    # So this section proves the coordinate LOGIC itself is correct — reconstructing
    # both real sheets' actual printed values as word boxes, exactly as fitz or
    # pytesseract would hand them back — plus proves the new function is a true no-op
    # on the sheet type that already works, using REAL word boxes read by REAL fitz
    # off the real NETPDC PDF fixtures. What is NOT proven here, and needs a live
    # upload to confirm: that pytesseract's *actual* word coordinates off a real
    # phone photo cluster the way this section's hand-built coordinates assume.
    print("\n14. COORDINATE-BASED PARSING (root-cause fix for a real grid-style sheet)")
    yymm = L["_yymm_to_years"]
    posfields = L["extract_fields_by_position"]
    fitzboxes = L["_boxes_from_fitz_words"]

    check("YYMM 0600 (6y 0m)", yymm("0600"), 6.0)
    check("YYMM 0106 (1y 6m)", yymm("0106"), 1.5)
    check("YYMM 0100 (1y 0m)", yymm("0100"), 1.0)
    check("YYMM with an impossible month count is rejected, not guessed",
          yymm("0699"), None)

    def box(text, x0, y0, w=42):
        return {"x0": x0, "y0": y0, "x1": x0 + w, "y1": y0 + 12, "text": text}

    # Reconstructs the GM sheet (BREAK_ATTEMPT_2026-07-29.md): E6, Exam 69.15 |
    # PMA 48.00 (3.60) | SIPG 01.20 (0600) | Awards 6 | Edu 0.00 | PNA 4.50, whose
    # FMS reproduces to 128.85 -- the same real sheet section 1b already checks.
    gm_boxes = [
        box("69.15", 100, 100), box("48.00", 300, 100), box("(3.60)", 345, 100),
        box("01.20", 500, 100), box("(0600)", 545, 100), box("6", 700, 100, 14),
        box("0.00", 850, 100), box("4.50", 1000, 100), box("128.85", 1150, 100),
        # AVERAGE-of-candidates row, below the sailor's own -- must be ignored.
        box("51.00", 100, 140), box("45.00", 300, 140), box("(3.30)", 345, 140),
        box("00.80", 500, 140), box("(0400)", 545, 140), box("5", 700, 140, 14),
        box("0.00", 850, 140), box("3.00", 1000, 140), box("120.00", 1150, 140),
    ]
    fields_gm = posfields(gm_boxes, "E6") or {}
    check("GM sheet (grid layout): position read reconciles", bool(fields_gm), True)
    check("GM sheet: exam_score, takes the sailor's row not the average's",
          fields_gm.get("exam_score"), 69.15)
    check("GM sheet: pma, the parenthesized raw figure",
          fields_gm.get("pma"), 3.60)
    check("GM sheet: tir, YYMM (0600) converted to 6.0 years",
          fields_gm.get("tir"), 6.0)
    check("GM sheet: awards", fields_gm.get("awards"), 6.0)
    check("GM sheet: education", fields_gm.get("education"), 0.0)
    check("GM sheet: pna", fields_gm.get("pna"), 4.5)

    # Reconstructs the BOL sheet (test-profile-sheets/BOL_exam_profile_data_real_
    # 20260823.md): PS3->PS2 is E5, row 49.50 | 64.00 (4.00) | 00:20 (0100) |
    # 0 | 0.00 | 0.00 | 113.70 | 20.00 (the 8th column, Minimum Multiple Req'd, is
    # deliberately present here to prove it's ignored, not just absent). Also
    # exercises a colon as the OCR misread of a decimal point, and Awards printing
    # as a bare "0" -- the exact real-sheet quirk that had to be fixed in the
    # label-based reader 23 Aug (commit 03ff425).
    bol_boxes = [
        box("49.50", 100, 200), box("64.00", 300, 200), box("(4.00)", 345, 200),
        box("00:20", 500, 200), box("(0100)", 545, 200), box("0", 700, 200, 10),
        box("0.00", 850, 200), box("0.00", 1000, 200), box("113.70", 1150, 200),
        box("20.00", 1300, 200),
        box("51.22", 100, 240), box("56.55", 300, 240), box("(3.91)", 345, 240),
        box("00.29", 500, 240), box("(0106)", 545, 240), box("0", 700, 240, 10),
        box("0.0", 850, 240),
    ]
    fields_bol = posfields(bol_boxes, "E5") or {}
    check("BOL sheet (grid layout): position read reconciles", bool(fields_bol), True)
    check("BOL sheet: exam_score", fields_bol.get("exam_score"), 49.50)
    check("BOL sheet: pma, the parenthesized raw figure", fields_bol.get("pma"), 4.00)
    check("BOL sheet: tir, YYMM (0100) converted to 1.0 years", fields_bol.get("tir"), 1.0)
    check("BOL sheet: awards read as an exact bare zero, not 'missing'",
          fields_bol.get("awards"), 0.0)
    check("BOL sheet: education", fields_bol.get("education"), 0.0)
    check("BOL sheet: pna", fields_bol.get("pna"), 0.0)

    # Change one cell so the six values no longer reproduce the sheet's own
    # printed 113.70 -- a stand-in for a column mix-up. Must come back empty,
    # never a plausible-looking wrong answer.
    bad_boxes = [b if b["text"] != "0.00" or b["x0"] != 850 else box("4.00", 850, 200)
                 for b in bol_boxes]
    check("a column that no longer reconciles is refused, not guessed",
          posfields(bad_boxes, "E5"), None)

    check("too few columns to be this table shape returns None, not a partial guess",
          posfields(gm_boxes[:8], "E6"), None)

    # The layout that already works must see NO change. Real word boxes, read by
    # REAL fitz, off the actual NETPDC-form fixture -- not reconstructed by hand.
    # That layout never places a parenthesized raw figure next to PMA or SIPG (it
    # uses separate YOUR VALUE / POINTS / MAX / PEER AVG columns instead), so this
    # must always come back empty and leave the label-based read as the only one.
    _mock_pdf = os.path.join(SHEETS, "PROFILE_SHEET_PS2_E6_clean.pdf")
    if os.path.exists(_mock_pdf):
        _doc = fitz.open(_mock_pdf)
        _real_words = []
        for _page in _doc:
            _real_words += fitzboxes(_page.get_text("words"))
        check("NETPDC-form sheet: position reader is a true no-op on this layout",
              posfields(_real_words, "E6"), None)
    else:
        skip("NETPDC-form fixture missing — could not prove the no-op on a real PDF")

    # ── 15. AUTOMATIC TUTOR GROUNDING (corpus.get_series_grounding) ──────────────
    #
    # Shawn's call, 24 Aug 2026: TOPIC_ARTICLE_MAP stops growing by hand. Coverage
    # grows instead from an automatic lookup keyed on each topic's own bibliography
    # line — every CURRENT, non-cancelled MILPERSMAN article in the hundred-series
    # that line already names. This section proves that lookup against the real,
    # bundled corpus.db: it finds real articles for a real bib line, stays empty for
    # a bib line that never mentions MILPERSMAN (a JTR/FMR-only topic — must fall
    # back to the memory-safe lesson exactly like an unmapped topic does today),
    # respects the article cap, and never hands back a superseded edition.
    print("\n15. AUTOMATIC TUTOR GROUNDING (corpus.get_series_grounding)")

    block, matched = _corpus.get_series_grounding("MILPERSMAN 1050 series, NSIPS")
    check("1050 series: finds real articles from the topic's own bib line",
          "1050-010" in matched, True)
    check("1050 series: more than just the one hand-mapped article",
          len(matched) > 1, True)
    check("1050 series: every matched number's text actually loaded",
          bool(block), True)

    block2, matched2 = _corpus.get_series_grounding(
        "JTR Chapters 1, 2, 5, DOD 7000.14-R Vol 9")
    check("a bib line with no MILPERSMAN mention grounds nothing, not a guess",
          matched2, [])
    check("...and hands back no text either", block2, "")

    block3, matched3 = _corpus.get_series_grounding(
        "See MILPERSMAN 1910-806 specifically")
    check("a bib line naming one specific article finds exactly that one",
          matched3, ["1910-806"])

    _, matched4 = _corpus.get_series_grounding("MILPERSMAN 1050 series", max_articles=3)
    check("the article cap is respected", len(matched4) <= 3, True)

    # A bib line naming two series ("1910 series, 1830 series" — a real PS_TOPICS
    # line) must not let the first, bigger series starve the second one out of the
    # cap entirely. Found live 24 Aug 2026: 1910 alone has 20+ articles, more than
    # the default cap, so a first-come-first-served fill never reached 1830 at all.
    _, matched5 = _corpus.get_series_grounding(
        "BUPERSINST 1900.8F, MILPERSMAN 1910 series, 1830 series")
    check("a second named series isn't starved out by a bigger first one",
          any(m.startswith("1830") for m in matched5), True)

    # 1300-1400 is one of nine articles corpus.db carries at two revisions — an old,
    # superseded edition (CH-76, 18 pages) kept alongside the current one (CH-91, 20
    # pages) because the substitute exam's bibliography is locked to the older dates.
    # get_article_text() must only ever return the current edition's own text, never
    # both editions run together.
    import sqlite3 as _sqlite3
    _con = _sqlite3.connect(f"file:{_corpus.CORPUS_DB_PATH}?mode=ro", uri=True)
    _current_chars = sum(
        len(r[0] or "") for r in _con.execute(
            "SELECT text FROM pages WHERE article='1300-1400' AND is_current=1"))
    _all_chars = sum(
        len(r[0] or "") for r in _con.execute(
            "SELECT text FROM pages WHERE article='1300-1400'"))
    check("1300-1400 really does carry two editions in the raw corpus (test is live)",
          _all_chars > _current_chars, True)
    _current_text = _corpus.get_article_text("1300-1400", max_chars=10**7)
    # Within a couple hundred characters of the current edition alone (the small gap
    # is the "MILPERSMAN 1300-1400" line get_article_text can add) -- nowhere close
    # to the combined two-edition length, which is what a is_current filter bug would
    # produce.
    check("a superseded edition is never handed to the model as current text",
          abs(len(_current_text) - _current_chars) < 300, True)

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    if FAIL:
        print(f"{len(FAIL)} CHECK(S) FAILED — do not push until these are fixed:")
        for name, got, want in FAIL:
            print(f"   - {name}: got {got!r}, expected {want!r}")
        print("=" * 68)
        return 1
    if SKIP:
        print(f"{len(PASS)} passed, but {len(SKIP)} BLOCK(S) WERE SKIPPED — NOT safe to push:")
        for reason in SKIP:
            print(f"   - {reason}")
        print("\nA skipped check is not a passed check. Fix the cause and re-run.")
        print("=" * 68)
        return 1

    if len(PASS) != EXPECTED_TOTAL:
        print(f"EXPECTED {EXPECTED_TOTAL} CHECKS, ONLY {len(PASS)} RAN — NOT safe to push.")
        print("Checks went missing without being reported as skipped. Investigate before pushing.")
        print("(If you deliberately added or removed checks, update EXPECTED_TOTAL.)")
        print("=" * 68)
        return 1

    print(f"ALL {len(PASS)} CHECKS PASSED — safe to push.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
