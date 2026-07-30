#!/usr/bin/env python3
"""Score Surge smoke test — does the app actually render?

    python3 smoke_test.py

run_checks.py pulls the pure functions out of app.py and tests them in isolation.
That catches bad math. It cannot catch a bad widget argument, a missing session
key, or a Streamlit API change — those break the live page while every one of the
55 checks still passes.

This file loads app.py the way Streamlit does, with a fake logged-in user and
fake secrets, and asserts the page comes up without throwing. Run both before a
push: run_checks.py for the math, this for the screens.

Exit code 0 = the app renders and the FMS flow works. Exit code 1 = read the output.
"""

import sys
import types

FAIL = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append((name, got, want))
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<52} got={got!r:<10} want={want!r}")


def build_app():
    """Start app.py past the login gate with throwaway secrets."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("app.py", default_timeout=120)
    for key in ("ANTHROPIC_API_KEY", "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_ANON_KEY",
                "STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_WEBHOOK_SECRET"):
        at.secrets[key] = "https://test.invalid" if "URL" in key else "test-not-real"
    # The app gates on st.session_state.user; a stand-in object is enough to get
    # past it without touching Supabase.
    at.session_state["user"] = types.SimpleNamespace(id="smoke-test-user",
                                                     email="smoke@test.invalid")
    at.session_state["tier"] = "elite"
    return at


def main():
    try:
        from streamlit.testing.v1 import AppTest  # noqa: F401
    except ImportError:
        print("streamlit is not installed — cannot smoke test (pip install streamlit)")
        return 1

    print("\n1. PAGE RENDERS")
    at = build_app()
    at.run()
    for exc in at.exception:
        print(f"  EXCEPTION: {str(exc.value)[:300]}")
    check("app renders with no uncaught exception", len(at.exception), 0)
    if at.exception:
        print("\nThe page does not come up. Nothing below this will be meaningful.")
        return 1

    print("\n2. PAYGRADE MUST BE CHOSEN BEFORE SCORING")
    # Regression guard: defaulting this to E5 silently mis-scored E6 sheets.
    pg = at.selectbox[0]
    check("paygrade selector is first", pg.label, "Paygrade You Are Competing For")
    check("no paygrade is preselected", pg.value.startswith("—"), True)
    check("E4 is not offered", "E4" in list(pg.options), False)

    calc = [b for b in at.button if "alculate" in b.label]
    check("calculate button exists", len(calc), 1)
    check("calculate is disabled until a paygrade is picked", calc[0].disabled, True)

    print("\n3. CHOOSING A PAYGRADE UNLOCKS SCORING")
    at.selectbox[0].select("E6").run()
    check("no exception after choosing paygrade", len(at.exception), 0)
    calc = [b for b in at.button if "alculate" in b.label]
    check("calculate is now enabled", calc[0].disabled, False)
    labels = [n.label for n in at.number_input]
    check("PMA field re-scaled to the E6 cap",
          any("5.80" in lbl for lbl in labels), True)

    print("\n4. THE FMS FLOW COMPLETES")
    calc[0].click().run()
    for exc in at.exception:
        print(f"  EXCEPTION: {str(exc.value)[:300]}")
    check("no exception after calculating", len(at.exception), 0)
    metrics = {m.label: m.value for m in at.metric}
    check("Final Multiple Score is shown", "Final Multiple Score" in metrics, True)
    check("Max Possible reflects E6", metrics.get("Max Possible (E6)"), "222.0")

    print("\n5. SWITCHING PAYGRADE RE-SCALES THE FORM")
    at.selectbox[0].select("E7").run()
    check("no exception switching to E7", len(at.exception), 0)
    disabled_now = [n.label for n in at.number_input if n.disabled]
    check("E7 greys out the fields it does not score", len(disabled_now) >= 3, True)

    print("\n" + "=" * 68)
    if FAIL:
        print(f"{len(FAIL)} SMOKE CHECK(S) FAILED — the app is broken for users:")
        for name, got, want in FAIL:
            print(f"   - {name}: got {got!r}, expected {want!r}")
        print("=" * 68)
        return 1
    print("SMOKE TEST PASSED — the app renders and the FMS flow works.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
