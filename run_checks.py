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

Exit code 0 = safe to push. Exit code 1 = something is broken OR some checks did
not run, read the output. A skipped check is never treated as a pass.
"""

import datetime
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app.py")
SHEETS = os.path.join(HERE, "test-profile-sheets")

PASS, FAIL, SKIP = [], [], []

# Every check this file is supposed to run when nothing is missing. If the count
# at the end doesn't match this, checks went missing and the run is NOT a pass.
EXPECTED_TOTAL = 149


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
    check("a comma with three decimals is not guessed at",
          parse("PERFORMANCE MARK AVERAGE 4,060")[0]["pma"], 4.0)

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

    # ── 9. Nothing out of range can reach a widget ───────────────────────────
    print("\n9. CRASH GUARDS (values that used to break the page)")
    for raw, lo, hi in [(272.0, 0.0, 9.0), (-5.0, 0.0, 80.0), (9999.0, 0.0, 30.0),
                        (None, 0.0, 9.0), ("junk", 0.0, 80.0), (float("nan"), 0.0, 5.8)]:
        out = safe(raw, lo, hi, lo)
        ok = isinstance(out, float) and lo <= out <= hi
        (PASS if ok else FAIL).append((f"safe_value({raw!r})", out, f"{lo}..{hi}"))
        print(f"  {'PASS' if ok else 'FAIL'}  safe_value({raw!r:>10}) -> {out} "
              f"(must stay within {lo}..{hi})")

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
