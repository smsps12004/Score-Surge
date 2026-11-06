"""The official Final Multiple Score (FMS) math — the one copy.

Moved out of app.py on 5 Nov 2026 so the website and the coming iPhone app's API
run the exact same code. app.py imports everything here; run_checks.py tests it.
Change the chart only here. Never copy these numbers back into app.py.

No Streamlit, no network, no secrets: plain Python a phone API can call safely.
"""

# Official FMS computation per the MyNavyHR "E4 Through E7 Final Multiple Score"
# chart (Cycle 104/243 forward), NAVADMIN 312/18, and BUPERSINST 1430.16G.
#
#   PMA points   E4/E5: (PMA x 80) - 256, max 64
#                E6:    (RSCA PMA x 30) - 60, max 114
#                E7:    (RSCA PMA x 30) - 54, max 120
#   SIPG points  Service in paygrade (years) / 5, max 2 (E4/E5) or 3 (E6)
#   Education    2 pts AA/AS, 4 pts BA/BS or above
#   E7 is board-selected: exam + PMA only, no awards/PNA/SIPG/education.
#
# Input ranges are NOT the same across paygrades:
#   E4/E5 PMA is the average of eval promotion-recommendation values, which can
#   only be 4.00 / 3.80 / 3.60 / 3.40 / 2.00 -- so PMA tops out at 4.00, and
#   4.00 x 80 - 256 = 64, exactly the cap. A PMA of 3.20 or below scores zero.
#   E6/E7 RSCA PMA is that eval value PLUS up to 1.80 RSCA bonus points, so it
#   tops out at 5.80, and 5.80 x 30 - 60 = 114 / 5.80 x 30 - 54 = 120.
FMS_RULES = {
    "E4": {"pma_mult": 80, "pma_sub": 256, "pma_max": 64.0, "pma_input_max": 4.00,
           "pma_name": "PMA", "tir_div": 5.0, "tir_max": 2.0, "awards_max": 10.0,
           "edu_max": 4.0, "pna_max": 9.0, "fms_max": 169.0},
    "E5": {"pma_mult": 80, "pma_sub": 256, "pma_max": 64.0, "pma_input_max": 4.00,
           "pma_name": "PMA", "tir_div": 5.0, "tir_max": 2.0, "awards_max": 10.0,
           "edu_max": 4.0, "pna_max": 9.0, "fms_max": 169.0},
    "E6": {"pma_mult": 30, "pma_sub": 60, "pma_max": 114.0, "pma_input_max": 5.80,
           "pma_name": "RSCA PMA", "tir_div": 5.0, "tir_max": 3.0, "awards_max": 12.0,
           "edu_max": 4.0, "pna_max": 9.0, "fms_max": 222.0},
    "E7": {"pma_mult": 30, "pma_sub": 54, "pma_max": 120.0, "pma_input_max": 5.80,
           "pma_name": "RSCA PMA", "tir_div": 5.0, "tir_max": 0.0, "awards_max": 0.0,
           "edu_max": 0.0, "pna_max": 0.0, "fms_max": 200.0},
}
EXAM_MAX = 80.0


def pma_points(paygrade: str, pma_value: float) -> float:
    """Convert a raw PMA / RSCA PMA into FMS points for the given paygrade."""
    r = FMS_RULES[paygrade]
    raw = (pma_value * r["pma_mult"]) - r["pma_sub"]
    return round(min(max(raw, 0.0), r["pma_max"]), 2)


def tir_points(paygrade: str, years_in_paygrade: float) -> float:
    """Service in paygrade (years) / 5, capped by paygrade, never below zero."""
    r = FMS_RULES[paygrade]
    raw = years_in_paygrade / r["tir_div"]
    return round(min(max(raw, 0.0), r["tir_max"]), 2)


def compute_fms(paygrade, exam_score, pma, years_in_rate, awards, education, pna):
    """Return (total_fms, ordered breakdown dict) using the official chart.

    Every component is clamped at BOTH ends. These used to cap the top only, which
    the widgets hid because they all carry min_value=0.0 — but the function is
    reachable from anywhere, and a negative slipping through subtracts from a score
    the sailor is trusting. No FMS component is ever worth less than zero.
    """
    r = FMS_RULES[paygrade]

    def band(value, hi):
        return round(min(max(value, 0.0), hi), 2)

    parts = {
        "Exam Standard Score": band(exam_score, EXAM_MAX),
        "PMA Points":          pma_points(paygrade, pma),
        "Time in Rate":        tir_points(paygrade, years_in_rate),
        "Awards":              band(awards, r["awards_max"]),
        "Education":           band(education, r["edu_max"]),
        "PNA Points":          band(pna, r["pna_max"]),
    }
    return round(sum(parts.values()), 2), parts
