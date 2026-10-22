"""The curated exam-topic maps for PS and YN.

Moved verbatim out of app.py on 22 Oct 2026 (nothing in the maps changed) so the
public welcome page -- which draws before login, long before app.py reaches these --
can say exactly which ratings and paygrades are covered, from the same lists the
app itself uses.
"""


def split_topics(topics: dict) -> dict:
    """{'E6 - Topic': {...}} -> {'E6': {'Topic': {...}}}. Same rule as app.py's _split_topics."""
    out = {}
    for key, val in topics.items():
        pg, _, name = key.partition(" - ")
        if not name:
            pg, name = "E6", key
        out.setdefault(pg.strip(), {})[name.strip()] = val
    return out

# ── PS TOPICS ─────────────────────────────────────────────────────────────────
PS_TOPICS = {
    "E6 - Customer Service Management & Processing": {
        "subtopics": ["Correspondence", "DEERS & RAPIDS Management", "Electronic Service Record", "Leave"],
        "bib": "BUPERSINST 1750.10E, MILPERSMAN 1050 series, NSIPS"
    },
    "E6 - Disbursing Operations": {
        "subtopics": ["Fiscal", "Navy Cash"],
        "bib": "DOD 7000.14-R Vol 5, NAVSUP P-727"
    },
    "E6 - Education Services": {
        "subtopics": ["Advancement", "Programs"],
        "bib": "BUPERSINST 1430.16G, NAVPERS 18068F"
    },
    "E6 - Manning & Manpower Management": {
        "subtopics": ["Administration & Management", "Roles & Responsibilities"],
        "bib": "OPNAVINST 1300.21, BUPERSINST 1080.54B, MNA Users Guide"
    },
    "E6 - MILPAY Processing": {
        "subtopics": ["Allotments", "Indebtedness", "Legal", "Pay Processing", "Report Management"],
        "bib": "DOD 7000.14-R Vol 7A, Navy DJMS Procedures Training Guide"
    },
    "E6 - Receipts Management & Processing": {
        "subtopics": ["Gains", "Procedures", "Required Documentation & Forms"],
        "bib": "NSIPS, MILPERSMAN 1300 series"
    },
    "E6 - Reenlistment & Extension Processing": {
        "subtopics": ["Administration & Procedures", "Eligibility"],
        "bib": "OPNAVINST 1160.8B, MILPERSMAN 1160 series"
    },
    "E6 - Reserve Pay, Management & Processing": {
        "subtopics": ["Electronic Drill Management", "Entitlements", "Gains",
                      "Mobilization & Demobilization", "Separations & Transfers"],
        "bib": "RESPERS M-1001.5, BUPERSINST 1001.39F"
    },
    "E6 - Separations & Retirement Processing": {
        "subtopics": ["Entitlements & Audit", "Required Documentation & Forms", "Strength Loss"],
        "bib": "BUPERSINST 1900.8F, MILPERSMAN 1910 series, 1830 series"
    },
    "E6 - Transfers Management & Processing": {
        "subtopics": ["Entitlements", "Loss", "Required Documentation & Forms"],
        "bib": "JTR Chapter 5, MILPERSMAN 1300 series"
    },
    "E6 - Travel & Transportation Processing": {
        "subtopics": ["Computations", "Requirements", "Travel Policy & Procedures"],
        "bib": "JTR Chapters 1, 2, 5, DOD 7000.14-R Vol 9"
    },
    "E5 - Customer Service Management & Processing": {
        "subtopics": ["Correspondence", "DEERS & RAPIDS Management", "Electronic Service Record", "Leave"],
        "bib": "BUPERSINST 1750.10E Ch 4, MILPERSMAN 1050 series"
    },
    "E5 - MILPAY Processing": {
        "subtopics": ["Allotments", "Pay Processing", "Special Pays"],
        "bib": "DOD 7000.14-R Vol 7A, Navy DJMS Procedures Training Guide"
    },
    "E5 - Separations & Retirement Processing": {
        "subtopics": ["Entitlements", "Required Documentation", "DD214"],
        "bib": "BUPERSINST 1900.8F, MILPERSMAN 1910 series"
    },
    "E5 - Transfers Management & Processing": {
        "subtopics": ["Entitlements", "Loss", "Required Documentation & Forms"],
        "bib": "JTR Chapters 2, 3, 5, MILPERSMAN 1300 series"
    },
    "E5 - Travel & Transportation Processing": {
        "subtopics": ["Computations", "Requirements", "Travel Policy & Procedures"],
        "bib": "JTR, DOD 7000.14-R Vol 9"
    },
    "E5 - Military Awards": {
        "subtopics": ["Award Types", "Eligibility", "Processing"],
        "bib": "SECNAVINST 1650.1J"
    },
}

# YN's own topic map, built 7 Sep 2026 the same way PS's was: real chapters from the
# rating's own training manual (NAVEDTRA 15009C, Yeoman — 6 chapters, page-indexed into
# corpus.db as yn_pages, see build_yn_corpus.py in Score Surge DB), cross-checked
# against YN's actual current Navy COOL bibliography (all 4 variants — E5/E6, Regular/
# Substitute — fetched and read 7 Sep 2026; see bibliography_YN_Sept2026.md in Score
# Surge DB for the full reference-by-reference table this was built from).
#
# Topic names follow NAVEDTRA 15009C's own 6 chapters — that's the Navy's own
# organization of what a Yeoman studies, not an invented grouping — and every "bib"
# line only names a reference this session actually confirmed governs that chapter's
# content, never a guess. Every "MILPERSMAN NNNN series" mention below is a series
# genuinely cited on a current YN bibliography (E5 and/or E6) and already fully in
# corpus.db, so it starts auto-grounding through corpus.get_series_grounding() the
# same day this ships — no separate engineering step needed for those. The DoD
# 7000.14-R Vol 7A / Vol 9 and NAVEDTRA 15009C citations are equally real and
# equally confirmed, but corpus.get_series_grounding() only knows how to retrieve
# from the MILPERSMAN pages table today — those sources are named here for honest
# attribution and are the next grounding-engine work, not yet wired to retrieve text.
# Three MILPERSMAN articles genuinely cited on a current YN bib have no corpus.db row
# at all yet (1070-290, 1300-100, 1300-202) — real Gate 0 gaps, left out of every bib
# line below rather than cited as if they were sourced.
YN_TOPICS = {
    "E6 - Navy Yeoman Fundamentals": {
        "subtopics": ["Office Procedures & Customer Service", "Career Path & Flag/Staff Support",
                      "Protocol & Social Usage"],
        "bib": "NAVEDTRA 15009C Ch 1, OPNAVINST 1000.16L, OPNAVINST 1160.6C"
    },
    "E6 - Correspondence & Records Management": {
        "subtopics": ["Naval Correspondence", "Files & Records Management",
                      "Reports & Forms Management Programs"],
        "bib": "NAVEDTRA 15009C Ch 2, MILPERSMAN 1000-021, SECNAV M-5216.5, SECNAV M-5210.1, "
               "OPNAV M-5215.1, OPNAVINST 5215.17A"
    },
    "E6 - Evaluations & Awards": {
        "subtopics": ["EVAL/FITREP Processing", "Military Awards Processing",
                      "Awards Submission to OMPF"],
        "bib": "NAVEDTRA 15009C Ch 2, 3, MILPERSMAN 1770 series, BUPERSINST 1610.10H, "
               "SECNAVINST 1650.1J, SECNAV M-1650.1"
    },
    "E6 - Pay & Personnel Administration (CPPA)": {
        "subtopics": ["Pay & Allowances", "Electronic Service Record (ESR) & OMPF",
                      "Receipts, Transfers & Advancement Processing"],
        "bib": "NAVEDTRA 15009C Ch 3, DoD 7000.14-R Vol 7A Ch 1, 10, 18, 25, 26, 27, 29, "
               "DFAS-CL DJMS MMPA Guide, MILPERSMAN 1070 series, 1300 series, 1320 series, 1600 series"
    },
    "E6 - Separations Processing": {
        "subtopics": ["Separation Documentation", "Discharge Characterization & Fleet Reserve"],
        "bib": "NAVEDTRA 15009C Ch 3, MILPERSMAN 1910 series, 1800 series, BUPERSINST 1900.8F"
    },
    "E6 - Legal & Disciplinary Processing": {
        "subtopics": ["Non-Judicial Punishment (NJP)", "Administrative Investigations & Unauthorized Absence"],
        "bib": "NAVEDTRA 15009C Ch 4, MCM 2024 Edition, JAGINST 5800.7G"
    },
    "E6 - Travel Processing": {
        "subtopics": ["Travel Orders & Allowances", "PCS & Separation Travel"],
        "bib": "NAVEDTRA 15009C Ch 5, JOINT TRAVEL REGULATIONS Ch 1, 2, 5, DoD 7000.14-R Vol 9 Ch 5, 8"
    },
    "E6 - Security Administration": {
        "subtopics": ["Personnel Security Clearances", "Classified Material Control & Physical Security"],
        "bib": "NAVEDTRA 15009C Ch 6, DODM 5200.01, DODI 5200.48, SECNAVINST 5510.30C, SECNAVINST 5510.36B"
    },
    "E5 - Navy Yeoman Fundamentals": {
        "subtopics": ["Office Procedures & Customer Service", "Career Path & Flag/Staff Support",
                      "Protocol & Social Usage"],
        "bib": "NAVEDTRA 15009C Ch 1, OPNAVINST 1000.16L"
    },
    "E5 - Correspondence & Records Management": {
        "subtopics": ["Naval Correspondence", "Files & Records Management",
                      "Reports & Forms Management Programs"],
        "bib": "NAVEDTRA 15009C Ch 2, SECNAV M-5216.5, SECNAV M-5210.1, SECNAV M-5210.2, "
               "OPNAV M-5215.1, OPNAVINST 5215.17A"
    },
    "E5 - Evaluations & Awards": {
        "subtopics": ["EVAL/FITREP Processing", "Military Awards Processing"],
        "bib": "NAVEDTRA 15009C Ch 2, 3, BUPERSINST 1610.10H, SECNAVINST 1650.1J, SECNAV M-1650.1"
    },
    "E5 - Pay & Personnel Administration (CPPA)": {
        "subtopics": ["Pay & Allowances", "Electronic Service Record (ESR) & OMPF",
                      "Receipts, Transfers & Advancement Processing"],
        "bib": "NAVEDTRA 15009C Ch 3, DoD 7000.14-R Vol 7A Ch 1, 10, 18, 25, 32, "
               "MILPERSMAN 1070 series, 1300 series, 1320 series, 1600 series"
    },
    "E5 - Separations Processing": {
        "subtopics": ["Separation Documentation", "Discharge Characterization & Fleet Reserve"],
        "bib": "NAVEDTRA 15009C Ch 3, MILPERSMAN 1910 series, 1800 series, BUPERSINST 1900.8F"
    },
    "E5 - Legal & Disciplinary Processing": {
        "subtopics": ["Non-Judicial Punishment (NJP)", "Administrative Investigations & Unauthorized Absence"],
        "bib": "NAVEDTRA 15009C Ch 4, MCM 2024 Edition, JAGINST 5800.7G"
    },
    "E5 - Travel Processing": {
        "subtopics": ["Travel Orders & Allowances", "PCS & Separation Travel"],
        "bib": "NAVEDTRA 15009C Ch 5, JOINT TRAVEL REGULATIONS Ch 5, DoD 7000.14-R Vol 9 Ch 6, 8"
    },
    "E5 - Security Administration": {
        "subtopics": ["Personnel Security Clearances", "Classified Material Control & Physical Security"],
        "bib": "NAVEDTRA 15009C Ch 6, DODM 5200.01, ICPG 704.2, SECNAVINST 5510.30C, SECNAVINST 5510.36B"
    },
}


CURATED = {"PS": split_topics(PS_TOPICS), "YN": split_topics(YN_TOPICS)}
