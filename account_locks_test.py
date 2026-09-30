"""Account locks — checks for account_locks.py and its wiring in app.py.

Two layers:
  * the pure helpers in account_locks.py, with a fake Supabase;
  * the real app.py driven through Streamlit's AppTest with the same fake, so the
    rating gate, the locked rating menus, the one-login sign-out and the monthly
    limits are exercised end to end, not just read.

The database side (who may change what, the counters) is proven separately
against a real Postgres by supabase/tests/behave.sql.

Run:  python3 account_locks_test.py
"""

import types
import unittest
from unittest.mock import patch

import account_locks as locks
from smoke_test import build_app


class _Res:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self.db, self.table, self.op, self.payload, self.filters = db, table, "select", None, {}

    def select(self, *_a, **_k):
        self.op = "select"; return self

    def update(self, payload):
        self.op, self.payload = "update", payload; return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload; return self

    def upsert(self, payload):
        self.op, self.payload = "upsert", payload; return self

    def eq(self, k, v):
        self.filters[k] = v; return self

    def order(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self
    def single(self): return self

    def execute(self):
        if self.db.fail:
            raise ConnectionError("supabase down")
        rows = self.db.tables.setdefault(self.table, [])
        match = [r for r in rows if all(r.get(k) == v for k, v in self.filters.items())]
        if self.op == "update":
            for r in match:
                patch_ = dict(self.payload)
                # Mirror the database rule: rating can't change within 180 days.
                if "rating" in patch_ and r.get("rating") and self.db.rating_locked:
                    patch_.pop("rating")
                r.update(patch_)
            self.db.updates.append((self.table, dict(self.payload)))
            return _Res(match)
        if self.op in ("insert", "upsert"):
            rows.append(dict(self.payload)); return _Res([self.payload])
        return _Res([dict(r) for r in match])


class FakeSupabase:
    def __init__(self, rating="PS", ai_left=5, dl_left=5, active_session=None):
        self.fail = False
        self.rating_locked = False
        self.updates, self.rpcs = [], []
        self.tables = {
            "profiles": [{"id": "smoke-test-user", "tier": "chief", "rating": rating,
                          "rating_set_at": None, "active_session": active_session,
                          "stripe_customer_id": None}],
            "score_history": [],
        }
        self.usage = {"ai": {"used": 0, "limit": ai_left, "left": ai_left},
                      "download": {"used": 0, "limit": dl_left, "left": dl_left},
                      "tier": "chief", "period": "2026-09"}
        self.auth = types.SimpleNamespace(sign_out=lambda: None,
                                          set_session=lambda *a: None)
        self.postgrest = types.SimpleNamespace(auth=lambda *a: None)

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params=None):
        db = self

        class _R:
            def execute(self_inner):
                if db.fail:
                    raise ConnectionError("supabase down")
                db.rpcs.append((name, params))
                if name == "get_usage":
                    return _Res(db.usage)
                if name == "consume_usage":
                    k = params["p_kind"]
                    u = db.usage[k]
                    if u["left"] <= 0:
                        return _Res({"allowed": False, "used": u["used"], "limit": u["limit"]})
                    u["used"] += 1; u["left"] -= 1
                    return _Res({"allowed": True, "used": u["used"], "limit": u["limit"], "left": u["left"]})
                raise AssertionError(name)
        return _R()


# ── Pure helpers ──────────────────────────────────────────────────────────────
class HelperTests(unittest.TestCase):
    def test_has_left_fails_open(self):
        self.assertTrue(locks.has_left(None, "ai"))
        self.assertTrue(locks.has_left({"ai": {"left": 1}}, "ai"))
        self.assertFalse(locks.has_left({"ai": {"left": 0}}, "ai"))

    def test_usage_line(self):
        self.assertEqual(locks.usage_line({"ai": {"left": 27, "limit": 30}}, "ai"),
                         "27 of 30 AI generations left this month")
        self.assertEqual(locks.usage_line(None, "ai"), "")

    def test_stamp_carries_email_top_and_bottom(self):
        out = locks.stamp_text("BODY", "a@b.mil")
        self.assertTrue(out.startswith("Licensed to a@b.mil"))
        self.assertIn("BODY", out)
        self.assertEqual(out.count("a@b.mil"), 2)

    def test_consume_blip_is_allowed(self):
        db = FakeSupabase(); db.fail = True
        self.assertTrue(locks.consume(db, "ai")["allowed"])

    def test_load_rating_distinguishes_unset_from_unreadable(self):
        db = FakeSupabase(rating=None)
        self.assertEqual(locks.load_rating(db, "smoke-test-user")[0], None)
        db.fail = True
        self.assertEqual(locks.load_rating(db, "smoke-test-user")[0], "?")

    def test_save_rating_reads_back(self):
        db = FakeSupabase(rating="PS"); db.rating_locked = True
        self.assertFalse(locks.save_rating(db, "smoke-test-user", "YN"))
        self.assertFalse(locks.save_rating(db, "smoke-test-user", "XX"))

    def test_next_rating_change(self):
        self.assertIsNone(locks.next_rating_change(None))
        self.assertIsNone(locks.next_rating_change("2020-01-01T00:00:00+00:00"))
        self.assertIsNotNone(locks.next_rating_change("2099-01-01T00:00:00+00:00"))

    def test_session_check_throttled_and_fail_open(self):
        db = FakeSupabase(active_session="other")
        state = {"_active_session": "mine", "_session_checked_at": 1000.0}
        self.assertTrue(locks.session_still_active(db, "smoke-test-user", state, now=1030))
        self.assertFalse(locks.session_still_active(db, "smoke-test-user", state, now=1100))
        state["_session_checked_at"] = 0
        db.fail = True
        self.assertTrue(locks.session_still_active(db, "smoke-test-user", state, now=2000))


# ── The real app ──────────────────────────────────────────────────────────────
def run_app(db, tier="chief", extra_state=None):
    app = build_app(tier=tier)
    for k, v in (extra_state or {}).items():
        app.session_state[k] = v
    with patch("supabase.create_client", return_value=db):
        app.run()
    return app


def _texts(app):
    parts = []
    for coll in (app.title, app.subheader, app.markdown, app.caption, app.info,
                 app.warning, app.success, app.error):
        parts += [getattr(e, "value", "") or "" for e in coll]
    return "\n".join(str(p) for p in parts)


class AppTests(unittest.TestCase):
    def test_new_sailor_must_pick_rating_first(self):
        db = FakeSupabase(rating=None)
        app = run_app(db)
        self.assertEqual(len(app.exception), 0)
        self.assertIn("pick your rating", _texts(app))
        self.assertEqual(len(app.tabs), 0, "no tabs until a rating is chosen")
        app.radio(key="first_rating_pick").set_value("YN")
        with patch("supabase.create_client", return_value=db):
            app.run()
        with patch("supabase.create_client", return_value=db):
            [b for b in app.button if b.label == "Lock in YN"][0].click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(db.tables["profiles"][0]["rating"], "YN")
        self.assertEqual(app.selectbox(key="pq_rating").options, ["YN"])

    def test_every_rating_menu_is_locked(self):
        app = run_app(FakeSupabase(rating="PS"))
        self.assertEqual(len(app.exception), 0)
        for key in ("sg_rating", "tutor_rating", "pq_rating", "plan_rating", "bba_rating"):
            box = app.selectbox(key=key)
            self.assertEqual(box.options, ["PS"], key)
            self.assertTrue(box.disabled, key)

    def test_unreadable_rating_does_not_block(self):
        db = FakeSupabase(rating=None); db.fail = True
        app = run_app(db)
        self.assertEqual(len(app.exception), 0)
        self.assertNotIn("pick your rating", _texts(app))

    def test_signed_in_elsewhere_signs_this_device_out(self):
        db = FakeSupabase(active_session="other-device")
        app = run_app(db, extra_state={"_active_session": "this-device",
                                       "_session_checked_at": 0})
        self.assertEqual(len(app.exception), 0)
        self.assertIn("signed in on another device", _texts(app))
        self.assertIsNone(app.session_state["user"])

    def test_first_run_claims_a_session(self):
        db = FakeSupabase()
        run_app(db)
        self.assertTrue(db.tables["profiles"][0]["active_session"])

    def _start_lesson(self, db):
        app = run_app(db)
        fake = types.SimpleNamespace(content=[types.SimpleNamespace(text="LESSON BODY")])
        with patch("supabase.create_client", return_value=db), \
             patch("anthropic.Anthropic") as ai:
            ai.return_value.messages.create.return_value = fake
            [b for b in app.button if "Start Lesson" in b.label][0].click().run()
        return app, ai

    def test_ai_limit_reached_blocks_before_any_ai_call(self):
        db = FakeSupabase(ai_left=0)
        app, ai = self._start_lesson(db)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(ai.return_value.messages.create.call_count, 0)
        self.assertIn("used this month's AI-written study material", _texts(app))

    def test_ai_use_is_counted_once(self):
        db = FakeSupabase(ai_left=5)
        app, ai = self._start_lesson(db)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(ai.return_value.messages.create.call_count, 1)
        self.assertEqual([r for r in db.rpcs if r[0] == "consume_usage"],
                         [("consume_usage", {"p_kind": "ai"})])

    def test_download_limit_hides_download(self):
        db = FakeSupabase(ai_left=5, dl_left=0)
        app, _ = self._start_lesson(db)
        self.assertEqual(len(app.exception), 0)
        self.assertIn("used this month's downloads", _texts(app))

    def test_bank_exam_is_free_even_at_zero(self):
        db = FakeSupabase(ai_left=0)
        app = run_app(db)
        app.selectbox(key="pq_paygrade").set_value("E6")
        with patch("supabase.create_client", return_value=db):
            app.run()
        app.selectbox(key="pq_topic").set_value("Transfers Management & Processing")
        app.selectbox(key="pq_num").set_value(5)
        with patch("supabase.create_client", return_value=db), \
             patch("anthropic.Anthropic", side_effect=AssertionError("no AI for bank")):
            [b for b in app.button if b.label == "Generate Mock Exam"][0].click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.session_state["exam_questions"]), 5)
        self.assertFalse(any(r[0] == "consume_usage" for r in db.rpcs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
