#!/usr/bin/env python3
"""Drive the profile-sheet UPLOAD path through AppTest.

smoke_test.py never uploads a file, so everything from the uploader down to the
"what was read from your sheet" expander is untested against the live page.
This probes it, including what happens on the rerun that every later click causes.
"""
import sys, types, os

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = "/sessions/pensive-bold-ride/mnt/score-surge"
SHEET = os.path.join(APP_DIR, "test-profile-sheets", "PROFILE_SHEET_PS2_E6_clean.pdf")


def build():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(os.path.join(APP_DIR, "app.py"), default_timeout=120)
    for k in ("ANTHROPIC_API_KEY", "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_ANON_KEY",
              "STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_WEBHOOK_SECRET"):
        at.secrets[k] = "https://test.invalid" if "URL" in k else "test-not-real"
    at.session_state["user"] = types.SimpleNamespace(id="probe", email="p@test.invalid")
    at.session_state["tier"] = "elite"
    return at


def state(at, label):
    ok = [m.value for m in at.success]
    warn = [m.value for m in at.warning]
    err = [m.value for m in at.error]
    nums = {n.label: n.value for n in at.number_input}
    pg = at.selectbox[0].value if at.selectbox else "?"
    print(f"\n--- {label} ---")
    print(f"  paygrade widget : {pg}")
    print(f"  success msgs    : {[s[:60] for s in ok]}")
    print(f"  warning msgs    : {[s[:70] for s in warn]}")
    print(f"  error msgs      : {[s[:70] for s in err]}")
    for k, v in nums.items():
        print(f"  {k[:44]:<46} = {v}")
    return nums


def main():
    os.chdir(APP_DIR)
    data = open(SHEET, "rb").read()
    print(f"sheet: {os.path.basename(SHEET)}  ({len(data)} bytes)")

    at = build()
    at.run()
    if at.exception:
        print("app did not render:", at.exception[0].value)
        return 1

    at.file_uploader[0].set_value(("PROFILE_SHEET_PS2_E6_clean.pdf", data, "application/pdf"))
    at.run()
    if at.exception:
        print("EXCEPTION on upload:", str(at.exception[0].value)[:400])
        return 1
    first = state(at, "PASS 1: right after upload")

    # Every later interaction reruns the whole script. Pick the paygrade the way a
    # sailor would if detection failed, then look at what the form still holds.
    at.selectbox[0].select("E6").run()
    if at.exception:
        print("EXCEPTION after choosing paygrade:", str(at.exception[0].value)[:400])
        return 1
    second = state(at, "PASS 2: after choosing a paygrade (one rerun later)")

    calc = [b for b in at.button if "alculate" in b.label]
    calc[0].click().run()
    if at.exception:
        print("EXCEPTION after calculate:", str(at.exception[0].value)[:400])
        return 1
    third = state(at, "PASS 3: after clicking Calculate")
    metrics = {m.label: m.value for m in at.metric}
    print(f"  METRICS: {metrics}")

    print("\n" + "=" * 70)
    drift = {k: (first.get(k), second.get(k)) for k in first
             if first.get(k) != second.get(k)}
    if drift:
        print("VALUES CHANGED BETWEEN UPLOAD AND THE NEXT RERUN — sheet data was lost:")
        for k, (a, b) in drift.items():
            print(f"   {k}: {a} -> {b}")
    else:
        print("Sheet values survived the rerun.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
