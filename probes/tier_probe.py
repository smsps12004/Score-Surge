#!/usr/bin/env python3
"""Render app.py under every real tier and count Stripe calls per render.

smoke_test.py sets tier to "elite". TIER_ORDER is ["free","petty_officer","chief"],
so "elite" is not a tier and can_access() returns False for everything — the smoke
test has only ever rendered the FREE experience. Tabs 2-7 have never come up as a
paying user in any automated check.
"""
import sys, types, os
from unittest.mock import patch

APP_DIR = "/sessions/pensive-bold-ride/mnt/score-surge"


def build(tier):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(os.path.join(APP_DIR, "app.py"), default_timeout=180)
    for k in ("ANTHROPIC_API_KEY", "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_ANON_KEY",
              "STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_WEBHOOK_SECRET"):
        at.secrets[k] = "https://test.invalid" if "URL" in k else "test-not-real"
    at.session_state["user"] = types.SimpleNamespace(id="probe", email="p@test.invalid")
    at.session_state["tier"] = tier
    return at


def main():
    os.chdir(APP_DIR)
    for tier in ("free", "petty_officer", "chief", "trial", "elite"):
        calls = []

        def fake_create(**kw):
            calls.append(kw.get("line_items"))
            return types.SimpleNamespace(url="https://stripe.test/session")

        at = build(tier)
        with patch("stripe.checkout.Session.create", side_effect=fake_create):
            at.run()
        exc = [str(e.value)[:160] for e in at.exception]
        locked = len([w for w in at.warning if "tier required" in w.value])
        print(f"\n=== tier={tier!r} ===")
        print(f"  exceptions            : {exc if exc else 'none'}")
        print(f"  locked-tab banners    : {locked}")
        print(f"  STRIPE SESSIONS CREATED ON ONE PAGE LOAD: {len(calls)}")
        if calls:
            print(f"  (no user ever clicked Upgrade)")

    # Second half: does a paying user's page survive interaction?
    print("\n\n=== chief tier, poke the widgets ===")
    at = build("chief")
    with patch("stripe.checkout.Session.create",
               side_effect=lambda **k: types.SimpleNamespace(url="https://stripe.test/s")):
        at.run()
        print(f"  initial exceptions: {[str(e.value)[:200] for e in at.exception] or 'none'}")
        print(f"  selectboxes={len(at.selectbox)} numbers={len(at.number_input)} "
              f"buttons={len(at.button)} radios={len(at.radio)}")
        for i, b in enumerate(at.button):
            label = b.label[:44]
            if b.disabled:
                continue
            at2 = build("chief")
            with patch("stripe.checkout.Session.create",
                       side_effect=lambda **k: types.SimpleNamespace(url="https://x")):
                at2.run()
                try:
                    at2.button[i].click().run()
                except Exception as e:
                    print(f"  button[{i}] {label:<46} HARNESS ERROR {type(e).__name__}")
                    continue
                if at2.exception:
                    print(f"  button[{i}] {label:<46} EXCEPTION: {str(at2.exception[0].value)[:150]}")
                else:
                    print(f"  button[{i}] {label:<46} ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
