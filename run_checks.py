#!/usr/bin/env python3
"""Score Surge pre-push checks.

Run this before every `git push`. It answers one question: did I break the math?

    python3 run_checks.py

It loads the pure logic out of app.py (no Streamlit needed), then checks:
  1. FMS math against hand-computed values for E5, E6 and E7
  2. Every point cap actually caps
  3. The profile sheet parser against the real sample PDFs in test-profile-sheets/
  4. Paygrade detection across the wordings real sheets use
  5. That no scraped value can escape a widget's min/max and crash the page

Exit code 0 = safe to push. Exit code 1 = something is broken OR some checks did
not run, read the output. A skipped check is never treated as a pass.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app.py")
SHEETS = os.path.join(HERE, "test-profile-sheets")

PASS, FAIL, SKIP = [], [], []

# Every check this file is supposed to run when nothing is missing. If the count
# at the end doesn't match this, checks went missing and the run is NOT a pass.
EXPECTED_TOTAL = 45


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

    ns = {"re": re}
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

    print("\n5. PAYGRADE DETECTION")
    for text, want in [
        ("PAYGRADE COMPETING FOR: E6", "E6"),
        ("PAYGRADE YOU ARE COMPETING FOR:  E7", "E7"),
        ("Competing for paygrade - E5", "E5"),
        ("ADVANCEMENT TO E6", "E6"),
        ("no paygrade statement anywhere", None),
        ("E5 mentioned but not as a labelled field", None),
    ]:
        check(f"'{text[:40]}'", getpg(text), want)

    # ── 6. Nothing out of range can reach a widget ───────────────────────────
    print("\n6. CRASH GUARDS (values that used to break the page)")
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
