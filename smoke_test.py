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
from unittest.mock import patch

FAIL = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append((name, got, want))
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<52} got={got!r:<10} want={want!r}")


def build_app(tier="chief"):
    """Start app.py past the login gate with throwaway secrets.

    The default used to be "elite", which is not a tier. TIER_ORDER is
    free/petty_officer/chief, so can_access() returned False for everything and
    every check ran against the FREE app — the AI Study Guide, Tutor, Mock Exam,
    Planner and BBA Hub, i.e. everything the $12.99 and $19.99 tiers pay for, had
    never rendered in an automated check. Default is a paying user now; section 6
    asks for "free" explicitly so both sides of the paywall are covered.
    """
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("app.py", default_timeout=120)
    for key in ("ANTHROPIC_API_KEY", "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_ANON_KEY",
                "STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_WEBHOOK_SECRET"):
        at.secrets[key] = "https://test.invalid" if "URL" in key else "test-not-real"
    # The app gates on st.session_state.user; a stand-in object is enough to get
    # past it without touching Supabase.
    at.session_state["user"] = types.SimpleNamespace(id="smoke-test-user",
                                                     email="smoke@test.invalid")
    at.session_state["tier"] = tier
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

    # The default tier must actually BE a tier. A typo here ("elite") silently
    # downgrades every check below to the free app while still passing, which is
    # how the five paid tabs went untested. A locked banner means we are not the
    # paying user we think we are.
    check("default tier really has paid access",
          len([w for w in at.warning if "tier required" in w.value]), 0)
    check("all seven tabs are present", len(at.tabs), 7)

    for label in ("Your Current FMS", "Your Current or Expected FMS"):
        [n for n in at.number_input if n.label == label][0].set_value(138.5)
    at.run()
    check("planner and BBA accept an FMS above 100", len(at.exception), 0)
    check("both retain the actual FMS", [n.value for n in at.number_input if n.label in ("Your Current FMS", "Your Current or Expected FMS")], [138.5, 138.5])
    # Default E5 target with E6 date is invalid and must never call the AI.
    with patch("anthropic.Anthropic", side_effect=AssertionError("unexpected AI call")) as ai:
        [b for b in at.button if b.label == "Build My Personalized Study Plan"][0].click().run()
    check("mismatched planner paygrade/date does not call AI", ai.call_count, 0)
    check("mismatched planner paygrade/date explains the problem", any("exam date matching" in e.value for e in at.error), True)

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

    print("\n6. NO STRIPE CALL UNTIL THE SAILOR TAPS UPGRADE")
    # Regression guard. upgrade_banner() used to create a Checkout Session while the
    # page was drawing. Streamlit re-runs all seven tabs on every interaction, so a
    # free user typing in the FMS calculator created five live Stripe sessions per
    # keystroke — lag on every rerun, junk in the Stripe dashboard, and five red
    # errors on a working page whenever Stripe was slow. Sessions are created on
    # click now. If this check ever counts above zero, that regressed.
    stripe_calls = []

    def _fake_session(**kwargs):
        stripe_calls.append(kwargs)
        return types.SimpleNamespace(url="https://checkout.stripe.test/session")

    at = build_app(tier="free")
    with patch("stripe.checkout.Session.create", side_effect=_fake_session):
        at.run()
        check("free page renders", len(at.exception), 0)
        check("locked tabs show an upgrade banner",
              len([w for w in at.warning if "tier required" in w.value]), 5)
        check("ZERO Stripe sessions created on render", len(stripe_calls), 0)

        upgrades = [b for b in at.button if "Upgrade to" in b.label]
        check("every locked tab offers an upgrade button",
              len([b for b in upgrades if not b.key.endswith("_profile")]), 5)
        check("...and My Profile offers both plans to a free sailor",
              len([b for b in upgrades if b.key.endswith("_profile")]), 2)
        # AppTest does not model st.link_button, so the checkout link is checked via
        # the caption that only renders once a session URL exists.
        paid = lambda: len([c for c in at.caption if "Opens Stripe" in c.value])
        check("no checkout link before the tap", paid(), 0)

        # No billing consent means no checkout even after tapping Upgrade.
        upgrades[0].click().run()
        check("checkout is blocked without recurring-billing consent", len(stripe_calls), 0)
        [c for c in at.checkbox if "renews monthly" in c.label][0].check()
        [b for b in at.button if "Upgrade to" in b.label][0].click().run()
        check("no exception after tapping Upgrade", len(at.exception), 0)
        check("tapping Upgrade creates exactly one session", len(stripe_calls), 1)
        check("checkout link appears after the tap", paid() >= 1, True)

        # A later rerun must not quietly create another one.
        before = len(stripe_calls)
        at.selectbox[0].select("E6").run()
        check("a later rerun creates no further sessions", len(stripe_calls), before)

    print("\n7. A BAD UPLOAD NEVER TAKES THE PAGE DOWN")
    # A real profile sheet turned out to be a phone photo wrapped in a PDF: no text
    # layer at all. The PDF branch only read the text layer, so the format sheets
    # actually arrive in was the one format the uploader could not read. And
    # pytesseract is a wrapper around a system binary requirements.txt cannot
    # install — without packages.txt every image upload raised TesseractNotFoundError
    # into an empty except-less path, so the sailor got a stack trace.
    import io as _io

    import fitz as _fitz
    from PIL import Image as _Image

    _buf = _io.BytesIO()
    _Image.new("RGB", (120, 120), "white").save(_buf, format="PNG")
    _png = _buf.getvalue()

    # A PDF that is a picture, exactly like the real sheet.
    _doc = _fitz.open()
    _doc.new_page().insert_image(_fitz.Rect(0, 0, 400, 400), stream=_png)
    _scanned_pdf = _doc.tobytes()

    def upload(name, data, mime, patcher):
        at = build_app()
        from streamlit.runtime.uploaded_file_manager import UploadedFile, UploadedFileRec
        from streamlit.proto.Common_pb2 import FileURLs
        file = UploadedFile(UploadedFileRec("test-upload", name, mime, data), FileURLs())
        original_run = at.run
        def run_with_upload(*args, **kwargs):
            file.seek(0)
            def uploader(label, *a, **kw):
                return file if "Navy Profile Sheet" in label else None
            with patch("streamlit.file_uploader", side_effect=uploader), patcher:
                return original_run(*args, **kwargs)
        at.run = run_with_upload
        at.run()
        return at

    at = upload("sheet.png", _png, "image/png",
                patch("pytesseract.get_tesseract_version", side_effect=OSError("no binary")))
    check("missing OCR binary does not crash the page", len(at.exception), 0)
    check("...and says so once, not twice", len(at.error), 1)

    at = upload("sheet.png", _png, "image/png",
                patch("pytesseract.image_to_string", side_effect=RuntimeError("boom")))
    check("OCR blowing up does not crash the page", len(at.exception), 0)
    check("...and the sailor gets a message, not a traceback", len(at.error), 1)

    # The capability that was missing: a text-less PDF must fall back to OCR.
    sheet_text = ("PAYGRADE COMPETING FOR: E6\nEXAM STANDARD SCORE 62.00\n"
                  "PERFORMANCE MARK AVERAGE (RSCA PMA) 4.06\nSERVICE IN PAYGRADE 3.50\n"
                  "AWARDS POINTS 4.00\nEDUCATION POINTS 4.00\nPNA POINTS 6.00")

    # iPhone JPEGs can open as MPO images under newer Pillow releases. The
    # prepared grayscale copy is valid, but pytesseract 0.3.13 rejects the raw
    # image's MPO format label. The untouched-image fallback must neutralize that
    # label without taking down the upload.
    _mpo_image = _Image.new("RGB", (120, 80), "white")
    _mpo_image.format = "MPO"
    _mpo_image.getexif()[274] = 6  # stored landscape, displayed 90° clockwise

    def _reject_mpo(image, **_kwargs):
        if image.format == "MPO":
            raise TypeError("Unsupported image format/type")
        if image.width > image.height:
            raise RuntimeError("EXIF orientation was not applied before OCR")
        return sheet_text

    at = build_app()
    at.run()
    at.file_uploader[0].set_value(("iphone_sheet.jpeg", _png, "image/jpeg"))
    with patch("PIL.Image.open", return_value=_mpo_image), \
            patch("pytesseract.image_to_string", side_effect=_reject_mpo):
        at.run()
    check("an iPhone MPO-labelled JPEG does not crash the OCR fallback",
          len(at.exception), 0)
    check("...and applies the phone photo's EXIF orientation before OCR",
          len(at.error), 0)
    check("...and the read still reaches the score form",
          [n.value for n in at.number_input if "Exam Standard Score" in n.label], [62.0])

    at = upload("photo_of_sheet.pdf", _scanned_pdf, "application/pdf",
                patch("pytesseract.image_to_string", return_value=sheet_text))
    check("a photographed PDF is OCR'd instead of rejected", len(at.exception), 0)
    check("...with no error shown", len(at.error), 0)
    check("...and the scores actually land in the form",
          [n.value for n in at.number_input if "Exam Standard Score" in n.label], [62.0])
    check("...including the paygrade off the sheet", at.selectbox[0].value, "E6")

    # 24 Aug 2026: the reconciliation check (does the parsed read match the Final
    # Multiple the sheet itself prints?), exercised through the real rendered page,
    # not just the pure function. sheet_text above has no printed total, so this
    # reuses it with one added to check both the confirming and the contradicting path.
    reconciling_sheet = sheet_text + "\nYOUR FINAL MULTIPLE SCORE (FMS) 138.50"
    at = upload("photo_of_sheet.pdf", _scanned_pdf, "application/pdf",
                patch("pytesseract.image_to_string", return_value=reconciling_sheet))
    check("a read matching the sheet's own total shows no exception", len(at.exception), 0)
    check("...and confirms the match on screen",
          any("138.5" in c.value for c in at.caption), True)

    contradicting_sheet = sheet_text + "\nYOUR FINAL MULTIPLE SCORE (FMS) 120.00"
    at = upload("photo_of_sheet.pdf", _scanned_pdf, "application/pdf",
                patch("pytesseract.image_to_string", return_value=contradicting_sheet))
    check("a read that contradicts the sheet's total shows no exception",
          len(at.exception), 0)
    check("...and is flagged as untrustworthy on screen, not shown as clean",
          any("don't add up" in e.value for e in at.error), True)

    calc = [b for b in at.button if "Calculate My FMS" in b.label][0]
    calc.click()
    at.run()
    check("unconfirmed uploaded values cannot produce an FMS",
          any(m.label == "Final Multiple Score" for m in at.metric), False)
    check("blocked calculation explains the correction requirement",
          any("confirmation box" in e.value for e in at.error), True)
    at.checkbox[0].check()
    [b for b in at.button if "Calculate My FMS" in b.label][0].click()
    at.run()
    check("reviewed upload can calculate after explicit confirmation",
          any(m.label == "Final Multiple Score" for m in at.metric), True)
    check("reviewed upload renders without exception", len(at.exception), 0)

    # ── 8. THE LOGGED-OUT PAGE ───────────────────────────────────────────────
    #
    # Every check above runs PAST the login gate, because build_app() fakes a session.
    # Which meant the one screen a locked-out sailor can actually reach had never been
    # rendered by an automated check at all. That gap mattered on 25 Aug 2026: the
    # Forgot Password flow was added and it lives entirely on this screen. A crash
    # here locks out every user of the app, including the ones who still remember
    # their password.
    print("\n8. LOGGED-OUT PAGE (the only screen a locked-out sailor can reach)")
    at = build_app()
    at.session_state["user"] = None
    at.run()
    for exc in at.exception:
        print(f"  EXCEPTION: {str(exc.value)[:300]}")
    check("the login page renders with no uncaught exception", len(at.exception), 0)

    _blob = " ".join([m.value for m in at.markdown] + [c.value for c in at.caption])
    _labels = [t.label for t in at.text_input]
    # The heading itself is an expander label, which AppTest doesn't surface as text —
    # so assert on the copy inside it, which only renders if the flow is really there.
    check("...and offers a way back in for a forgotten password",
          "six-digit code" in _blob, True)
    # The reset widgets live inside an expander, so they are in the tree even while
    # it is collapsed. If the email field is missing, the flow is unreachable.
    check("...with somewhere to enter the account email",
          any("signed up with" in l for l in _labels), True)
    check("...and the login form is still intact underneath",
          _labels.count("Password") >= 1, True)

    # ── 9. PLACEMENT TEST (13 Oct 2026) ──────────────────────────────────────
    # Driven end to end through the real page: a FREE sailor takes the whole test,
    # grades it, gets a report card, and a paying sailor's Study Guide then opens on
    # their weakest topic. No AI call is allowed anywhere in this flow.
    print("\n9. PLACEMENT TEST (free, verified bank only, no AI)")
    with patch("anthropic.Anthropic", side_effect=AssertionError("unexpected AI call")) as ai:
        at = build_app(tier="free")
        at.run()
        check("free page renders with the placement test", len(at.exception), 0)
        pick = [s for s in at.selectbox if s.label == "Paygrade you're competing for"]
        check("placement paygrade picker is there for a free sailor", len(pick), 1)
        check("...with nothing preselected", pick[0].value, None)
        pick[0].select("E5").run()
        check("E5 says coming soon instead of running a thin test",
              any("E5 placement test is coming soon" in i.value for i in at.info), True)
        [s for s in at.selectbox if s.label == "Paygrade you're competing for"][0].select("E6").run()
        start = [b for b in at.button if b.label.startswith("Start my placement test")]
        check("E6 offers a start button", len(start), 1)
        start[0].click().run()
        radios = [r for r in at.radio if r.label.startswith("Placement question")]
        check("the test shows 40 questions", len(radios), 40)
        _qs = at.session_state["placement_qs"]
        check("...every one from the verified bank", all(q.get("verified") == "yes" for q in _qs), True)
        # Answer the first 30 right and leave 10 blank, then grade anyway.
        for i, r in enumerate(radios[:30]):
            r.set_value(_qs[i]["correct_answer"])
        [b for b in at.button if b.label == "Grade my placement test"][0].click().run()
        check("blanks are flagged before grading",
              any("haven't answered 10 questions" in w.value for w in at.warning), True)
        [b for b in at.button if b.label.startswith("Grade anyway")][0].click().run()
        for exc in at.exception:
            print(f"  EXCEPTION: {str(exc.value)[:300]}")
        check("grading renders with no exception", len(at.exception), 0)
        res = at.session_state["placement_result"]
        check("the score is 30 of 40", (res["correct"], res["total"]), (30, 40))
        check("a report card table is shown", len(at.table) >= 1, True)
        check("...listing untested topics honestly",
              "Not tested yet" in at.table[0].value.to_string(), True)
        check("results reach score_history (8 tested topics)",
              len(at.session_state["score_history"]), 8)
        check("a free sailor is pointed at the upgrade, not a guide they can't open",
              any("upgrade and the Chief builds" in i.value for i in at.info), True)
        _weak = at.session_state["sg_topic"]
        check("the Study Guide is pointed at a real weak topic",
              res["topics"][_weak]["level"] != "Strong", True)
    check("the whole placement flow made zero AI calls", ai.call_count, 0)

    at = build_app(tier="petty_officer")
    at.session_state["score_history"] = [
        {"date": "Oct 13", "topic": "Education Services", "score": 1, "total": 5, "pct": 20},
        {"date": "Oct 13", "topic": "MILPAY Processing", "score": 5, "total": 5, "pct": 100},
    ]
    at.session_state["score_history_loaded"] = True
    at.session_state["sg_paygrade"] = "E6"
    at.run()
    check("a paying sailor's Study tab renders with results on file", len(at.exception), 0)
    _sg = [s for s in at.selectbox if s.label.startswith("Topic (used by")]
    check("...and the Study Guide opens on their weakest topic",
          _sg[0].value if _sg else None, "Education Services")

    # ── 10. TRIAL SAILORS CAN PAY (22 Oct 2026) ─────────────────────────────
    # A trial account has every tab open, so no locked tab ever offered Upgrade, and
    # My Profile only had "Manage subscription / cancel", which told someone who had
    # never paid that no billing account was linked. They had no way to pay at all.
    print("\n10. A SAILOR ON THE FREE TRIAL CAN UPGRADE")
    stripe_calls = []

    def _fake_session2(**kwargs):
        stripe_calls.append(kwargs)
        return types.SimpleNamespace(url="https://checkout.stripe.test/session")

    at = build_app(tier="trial")
    at.session_state["_billing_customer"] = False
    with patch("stripe.checkout.Session.create", side_effect=_fake_session2):
        at.run()
        for exc in at.exception:
            print(f"  EXCEPTION: {str(exc.value)[:300]}")
        check("trial page renders", len(at.exception), 0)
        _banners = [m for m in at.markdown if "Free trial" in m.value and "$12.99/mo" in m.value]
        check("every tab carries the trial banner with real prices", len(_banners), 7)
        check("...naming both plans", all("$19.99/mo" in m.value for m in _banners), True)
        check("...with a way to the plans from each of the other six",
              len([b for b in at.button if b.label == "See plans & upgrade"]), 6)
        _ups = [b for b in at.button if "Upgrade to" in b.label]
        check("My Profile shows an Upgrade button for each plan", len(_ups), 2)
        check("...and NOT 'Manage subscription' to someone who never paid",
              any(b.key == "manage_subscription" for b in at.button), False)
        check("no Stripe call on render", len(stripe_calls), 0)
        [c for c in at.checkbox if "Petty Officer costs" in c.label][0].check()
        [b for b in at.button if b.key == "upgrade_petty_officer_profile"][0].click().run()
        check("tapping Upgrade opens exactly one checkout", len(stripe_calls), 1)
        check("...for the $12.99 plan's price",
              stripe_calls[0]["line_items"][0]["price"], "price_1TxgMkDP0fFhPzMl2MEk9OZk")
        check("...and comes back to the app on success",
              "stripe_success=true" in stripe_calls[0]["success_url"], True)
        check("...tagged with this sailor's account",
              stripe_calls[0]["metadata"]["user_id"], "smoke-test-user")

    at = build_app(tier="petty_officer")
    at.run()
    check("a paying sailor still gets Manage subscription",
          any(b.key == "manage_subscription" for b in at.button), True)
    check("...and no trial banner", any("Free trial" in m.value for m in at.markdown), False)

    # ── 11. PUBLIC PAGE BEFORE LOGIN (22 Oct 2026) ──────────────────────────
    print("\n11. WHAT A FIRST-TIME VISITOR SEES")
    import public_site as site
    at = build_app()
    at.session_state["user"] = None
    at.run()
    check("logged-out page renders", len(at.exception), 0)
    _all = " ".join([m.value for m in at.markdown] + [c.value for c in at.caption])
    check("the tagline is above the login", site.TAGLINE in _all, True)
    check("both prices shown, read from the app's own plan table",
          "$12.99/mo" in _all and "$19.99/mo" in _all, True)
    check("trial terms shown", "3-day free trial, no card required" in _all, True)
    check("placement coverage stated exactly", "Covers PS E6 today" in _all, True)
    check("the independence line is on the login page", site.INDEPENDENCE_LINE in _all, True)
    for _slug in ("terms", "privacy", "refunds"):
        check(f"...and so is the {_slug} link", f"?page={_slug}" in _all, True)
    _start = [b for b in at.button if b.label == "Start free trial"]
    check("a Start free trial button", len(_start), 1)
    _start[0].click().run()
    check("...which opens the Create Account tab",
          at.session_state["auth_tab"], "Create Account")
    check("signup asks how they heard about us, optionally",
          [s.value for s in at.selectbox if s.label.startswith("How did you hear")], [None])

    for _slug, (_title, _) in site.LEGAL_PAGES.items():
        at = build_app()
        at.session_state["user"] = None
        at.query_params["page"] = _slug
        at.run()
        _txt = " ".join([m.value for m in at.markdown] + [c.value for c in at.caption])
        check(f"{_title} page renders before login", len(at.exception), 0)
        check(f"...shows its own text", f"# {_title}" in _txt, True)
        check(f"...with the footer", site.INDEPENDENCE_LINE in _txt, True)
    check("refund policy states the 7-day full refund on the latest charge",
          "within 7 days of a charge and we'll refund that charge in full" in site.REFUNDS_MD
          and "your latest charge" in site.REFUNDS_MD, True)

    at = build_app()
    at.run()
    _all = " ".join([m.value for m in at.markdown] + [c.value for c in at.caption])
    check("the independence line is on the logged-in app too", site.INDEPENDENCE_LINE in _all, True)

    # ── 12. COMING SOON INSTEAD OF THIN CONTENT (22 Oct 2026) ───────────────
    print("\n12. PRACTICE QUESTIONS ONLY WHERE THE VERIFIED BANK CAN CARRY THEM")
    with patch("anthropic.Anthropic", side_effect=AssertionError("unexpected AI call")) as ai:
        at = build_app(tier="chief")
        at.session_state["locked_rating"] = "YN"
        at.run()
        check("a YN sailor's app renders", len(at.exception), 0)
        check("YN Mock Exam says coming soon",
              any("YN E5 practice questions are coming soon" in i.value
                  or "YN E6 practice questions are coming soon" in i.value for i in at.info), True)
        check("...and offers no Generate button",
              any(b.label == "Generate Mock Exam" for b in at.button), False)
        at = build_app(tier="chief")
        at.session_state["locked_rating"] = "PS"
        at.run()
        _pg = [s for s in at.selectbox if s.key == "pq_paygrade"][0]
        check("PS Mock Exam opens on a paygrade that's live (E6)", _pg.value, "E6")
        _pg.select("E5").run()
        check("PS E5 says coming soon",
              any("PS E5 practice questions are coming soon" in i.value for i in at.info), True)
    check("none of that made an AI call", ai.call_count, 0)

    # ── 13. NO PAST EXAM DATES (22 Oct 2026) ────────────────────────────────
    print("\n13. EXAM DATES BETWEEN CYCLES")
    at = build_app()
    at.run()
    _all = " ".join([m.value for m in at.markdown] + [i.value for i in at.info]
                    + [c.value for c in at.caption] + [h.value for h in at.subheader])
    check("says spring 2027 dates aren't out", "Spring 2027 exam dates not yet announced" in _all, True)
    check("no past Cycle 272 exam date on screen",
          "3 September 2026" in _all or "10 September 2026" in _all
          or "September 3, 2026" in _all or "September 10, 2026" in _all, False)

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
