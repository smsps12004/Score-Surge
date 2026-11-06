"""Checks for the iPhone app's API (api/main.py). Added 5 Nov 2026.

    python3 api_test.py        (needs: pip install -r api/requirements.txt)

The one promise this file guards: the phone app gets exactly the same FMS as the
website. Every case is run through the API AND straight through fms_core, and the
two must agree to the cent. The real-sheet cases are the two NETPDC sheets Shawn
verified (run_checks.py section 1b), so the API is also checked against the Navy.
"""

import unittest

from fastapi.testclient import TestClient

from api.main import app
from fms_core import compute_fms

client = TestClient(app)


def post(**body):
    return client.post("/v1/fms", json=body)


class HealthTest(unittest.TestCase):
    def test_health(self):
        r = client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])


class FmsMatchesWebsiteTest(unittest.TestCase):
    CASES = [
        # (paygrade, exam, pma, years, awards, education, pna, expected total or None)
        ("E6", 69.15, 3.60, 6.0, 6, 0, 4.50, 128.85),   # real GM2 sheet, cycle 259
        ("E5", 56.43, 3.80, 1.0, 0, 0, 0.00, 104.63),   # real BM3 sheet, cycle 243
        ("E6", 62, 4.06, 3.5, 4, 4, 6, 138.50),         # hand-computed from the chart
        ("E5", 70, 3.80, 2.0, 5, 2, 3, 128.40),
        ("E7", 55, 4.50, 6, 0, 0, 0, 136.00),           # E7: exam + RSCA PMA only
        ("E4", 80, 4.00, 30, 10, 4, 9, None),           # every cap at once
        ("E6", 0, 0, 0, 0, 0, 0, None),                 # all zeros
    ]

    def test_api_equals_shared_math(self):
        for pg, exam, pma, yrs, aw, edu, pna, expected in self.CASES:
            with self.subTest(case=(pg, exam, pma)):
                r = post(paygrade=pg, exam_score=exam, pma=pma, years_in_rate=yrs,
                         awards=aw, education=edu, pna=pna)
                self.assertEqual(r.status_code, 200, r.text)
                body = r.json()
                total, parts = compute_fms(pg, exam, pma, yrs, aw, edu, pna)
                self.assertAlmostEqual(body["total"], total, places=2)
                self.assertEqual(body["breakdown"], parts)
                if expected is not None:
                    self.assertAlmostEqual(body["total"], expected, places=2)

    def test_breakdown_keeps_website_order(self):
        body = post(paygrade="E6", exam_score=62, pma=4.06, years_in_rate=3.5).json()
        self.assertEqual(list(body["breakdown"]), [
            "Exam Standard Score", "PMA Points", "Time in Rate",
            "Awards", "Education", "PNA Points"])
        self.assertEqual(body["fms_max"], 222.0)
        self.assertEqual(body["pma_name"], "RSCA PMA")


class RejectsWhatTheWebsiteFormWouldNotAcceptTest(unittest.TestCase):
    def bad(self, **over):
        body = dict(paygrade="E5", exam_score=60, pma=3.6, years_in_rate=2)
        body.update(over)
        r = post(**body)
        self.assertEqual(r.status_code, 422, f"{over} should be rejected: {r.text}")

    def test_unknown_paygrade(self):
        self.bad(paygrade="E9")

    def test_negative_values(self):
        self.bad(exam_score=-1)
        self.bad(pma=-0.5)
        self.bad(years_in_rate=-2)

    def test_over_form_limits(self):
        self.bad(exam_score=80.5)
        self.bad(years_in_rate=31)
        self.bad(pna=9.5)

    def test_pma_limit_depends_on_paygrade(self):
        self.bad(paygrade="E5", pma=4.2)    # E4/E5 PMA tops out at 4.00
        r = post(paygrade="E6", exam_score=60, pma=4.2, years_in_rate=2)
        self.assertEqual(r.status_code, 200)  # E6 RSCA PMA goes to 5.80
        self.bad(paygrade="E6", pma=5.9)

    def test_awards_limit_depends_on_paygrade(self):
        self.bad(paygrade="E5", awards=11)  # E4/E5 awards cap is 10
        r = post(paygrade="E6", exam_score=60, pma=4.0, years_in_rate=2, awards=12)
        self.assertEqual(r.status_code, 200)

    def test_education_is_only_0_2_or_4(self):
        self.bad(education=3)


if __name__ == "__main__":
    unittest.main()
