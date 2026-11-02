import types
import unittest
from unittest.mock import MagicMock, patch
import stripe
from smoke_test import build_app

class LaunchUITests(unittest.TestCase):
    def billing_case(self, email):
        app = build_app()
        app.run()
        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{'stripe_customer_id':'cus_alice'}]
        customer = stripe.StripeObject.construct_from({'id':'cus_alice','email':email}, 'test')
        with patch('supabase.create_client', return_value=db), patch('stripe.Customer.retrieve', return_value=customer), patch('stripe.billing_portal.Session.create', return_value=types.SimpleNamespace(url='https://billing.stripe.test/session')) as portal:
            [b for b in app.button if b.key == 'manage_subscription'][0].click().run()
        self.assertEqual(len(app.exception), 0)
        return app, portal

    def test_owner_can_open_billing_portal(self):
        app, portal = self.billing_case('smoke@test.invalid')
        self.assertEqual(portal.call_count, 1)
        self.assertEqual(portal.call_args.kwargs['customer'], 'cus_alice')

    def test_portal_link_is_obvious_and_stays_put(self):
        # T13: the tap must produce one plainly worded button straight to Stripe,
        # and it must survive the next rerun instead of vanishing.
        app, portal = self.billing_case('smoke@test.invalid')
        links = [l for l in app.get('link_button') if 'Open Stripe' in l.proto.label]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].proto.url, 'https://billing.stripe.test/session')
        self.assertTrue(any('Tap the button below' in s.value for s in app.success))
        app.run()
        self.assertEqual(len([l for l in app.get('link_button') if 'Open Stripe' in l.proto.label]), 1)
        self.assertEqual(portal.call_count, 1)

    def test_different_customer_email_cannot_open_portal(self):
        app, portal = self.billing_case('someone-else@test.invalid')
        self.assertEqual(portal.call_count, 0)
        self.assertTrue(any('could not be opened' in e.value for e in app.error))
        self.assertEqual(len([l for l in app.get('link_button') if 'Open Stripe' in l.proto.label]), 0)

    def test_tutor_lesson_streams_and_is_saved(self):
        # T14: the lesson must come from messages.stream (text shows as it is
        # written), keep its grounded/from-memory label, and still be saved for
        # follow-up questions and the download.
        chunks = ['## How it works\n', 'First part. ', 'Second part.']

        class FakeStream:
            text_stream = iter(chunks)
            def __enter__(self): return self
            def __exit__(self, *a): return False

        client = MagicMock()
        client.messages.stream.return_value = FakeStream()
        client.messages.create.side_effect = AssertionError('lesson should stream, not wait')
        app = build_app()
        app.run()
        with patch('anthropic.Anthropic', return_value=client), \
                patch('account_locks.has_left', return_value=True), patch('account_locks.consume'):
            [b for b in app.button if 'Start Lesson' in b.label][0].click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(client.messages.stream.call_count, 1)
        self.assertEqual(client.messages.stream.call_args.kwargs['max_tokens'], 2000)
        self.assertEqual(app.session_state['tutor_history'][1]['content'], ''.join(chunks))
        labels = [e.value for e in list(app.info) + list(app.warning)]
        self.assertTrue(any('excerpts supplied' in v or 'written from memory' in v for v in labels))

    def test_e7_sees_the_published_exam_date(self):
        # T43: NAVADMIN 241/26 sets the January 2027 E7 exam for 21 Jan 2027. The
        # app said "1 Feb 2027 estimated, NAVADMIN not yet released" to E7s.
        app = build_app()
        app.session_state['fms_paygrade'] = 'E7'
        app.run()
        self.assertEqual(len(app.exception), 0)
        metrics = {m.label: m for m in app.metric}
        self.assertIn('E7 Exam', metrics)
        self.assertIn('21 January 2027', metrics['E7 Exam'].proto.delta)
        self.assertIn('30 November 2026', metrics['PMK-EE Deadline'].proto.delta)
        self.assertIn('31 December 2026', metrics['ALDC Deadline'].proto.delta)
        self.assertNotIn('E7 Exam (est.)', metrics)
        self.assertNotIn('Official NAVADMIN', metrics)
        shown = ' '.join(x.value for x in list(app.caption) + list(app.info) + list(app.success))
        self.assertIn('NAVADMIN 241/26', shown)
        self.assertIn('NAVADMIN 257/26', shown)   # SELRES are told their own window
        self.assertNotIn('1 February 2027', shown)

    def _login(self, email, error):
        app = build_app()
        app.session_state['user'] = None
        app.run()
        db = MagicMock()
        db.auth.sign_in_with_password.side_effect = Exception(error)
        with patch('supabase.create_client', return_value=db):
            fields = [t for t in app.text_input if t.label in ('Email', 'Password')]
            fields[0].input(email)
            fields[1].input('not-the-real-one')
            [b for b in app.button if b.label == 'Log In'][0].click().run()
        return app, db

    def test_login_sends_trimmed_lowercase_email(self):
        app, db = self._login('  Sailor@Test.INVALID ', 'Invalid login credentials')
        self.assertEqual(len(app.exception), 0)
        sent = db.auth.sign_in_with_password.call_args.args[0]['email']
        self.assertEqual(sent, 'sailor@test.invalid')
        self.assertTrue(any('check your email and password' in e.value for e in app.error))

    def test_unconfirmed_email_is_told_so(self):
        app, _ = self._login('sailor@test.invalid', 'Email not confirmed')
        self.assertTrue(any("isn't confirmed yet" in e.value for e in app.error))

    # ── Continue with Google (29 Oct 2026) ─────────────────────────────────────
    def _logged_out(self, google='on'):
        app = build_app()
        app.secrets['GOOGLE_SIGNIN'] = google
        app.session_state['user'] = None
        return app

    def _google_buttons(self, app):
        return [b for b in app.button if b.key == 'google_signin']

    def _start_google(self):
        """Tap the button; return (app, the Supabase authorize URL, flow id)."""
        import urllib.parse
        app = self._logged_out()
        app.run()
        self._google_buttons(app)[0].click().run()
        self.assertEqual(len(app.exception), 0)
        links = [l for l in app.get('link_button') if 'Opening Google' in l.proto.label]
        self.assertEqual(len(links), 1)
        url = links[0].proto.url
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        back = urllib.parse.parse_qs(urllib.parse.urlparse(q['redirect_to'][0]).query)
        return app, url, q, back['google_flow'][0]

    def test_google_button_hidden_when_switched_off(self):
        app = self._logged_out(google='off')
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(self._google_buttons(app), [])
        self.assertTrue([b for b in app.button if b.label == 'Log In'])

    def test_google_button_shows_beside_email_login(self):
        app = self._logged_out()
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(self._google_buttons(app)), 1)
        self.assertTrue([b for b in app.button if b.label == 'Log In'])

    def test_google_button_sends_sailor_to_supabase_with_pkce(self):
        _, url, q, flow = self._start_google()
        self.assertTrue(url.startswith('https://test.invalid/auth/v1/authorize?'))
        self.assertEqual(q['provider'], ['google'])
        self.assertEqual(q['code_challenge_method'], ['s256'])
        self.assertTrue(len(q['code_challenge'][0]) >= 43)
        self.assertTrue(len(flow) >= 20)

    @staticmethod
    def _fake_jwt():
        # Shaped like a real Supabase token with an hour left, so the app's restore
        # step treats it as good (an unreadable token would be refreshed instead).
        import base64, json, time
        body = base64.urlsafe_b64encode(json.dumps({'exp': time.time() + 3600}).encode())
        return 'h.' + body.decode().rstrip('=') + '.s'

    def _come_back(self, params, exchange=None, created_minutes_ago=1, rows=None):
        import datetime as dt
        app = self._logged_out()
        for k, v in params.items():
            app.query_params[k] = v
        db = MagicMock()
        user = types.SimpleNamespace(
            id='g-user', email='sailor@gmail.test', user_metadata={},
            created_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=created_minutes_ago))
        db.auth.exchange_code_for_session.return_value = exchange or types.SimpleNamespace(
            user=user, session=types.SimpleNamespace(access_token=self._fake_jwt(), refresh_token='rt-1'))
        db.table.return_value.select.return_value.eq.return_value.limit.return_value \
            .execute.return_value.data = [{'tier': 'trial', 'trial_start': None}] if rows is None else rows
        with patch('supabase.create_client', return_value=db), \
                patch('account_locks.claim_session'), \
                patch('account_locks.session_still_active', return_value=True), \
                patch('account_locks.load_rating', return_value=(None, None)):
            app.run()
        return app, db

    def test_coming_back_from_google_logs_the_sailor_in(self):
        _, _, q, flow = self._start_google()
        app, db = self._come_back({'google_flow': flow, 'code': 'one-time-code'})
        self.assertEqual(len(app.exception), 0)
        sent = db.auth.exchange_code_for_session.call_args.args[0]
        self.assertEqual(sent['auth_code'], 'one-time-code')
        import google_signin
        self.assertEqual(google_signin.challenge_for(sent['code_verifier']), q['code_challenge'][0])
        self.assertEqual(app.session_state['user'].id, 'g-user')
        self.assertEqual(app.session_state['refresh_token'], 'rt-1')
        self.assertEqual(app.session_state['tier'], 'trial')
        self.assertEqual(dict(app.query_params), {})
        # New account: lands on the rating lock-in, which asks the signup question once.
        self.assertTrue(any('pick your rating' in s.value for s in app.subheader))
        self.assertTrue([s for s in app.selectbox if s.key == 'google_heard_from'])

    def test_returning_google_sailor_is_not_asked_how_they_heard(self):
        _, _, _, flow = self._start_google()
        app, _ = self._come_back({'google_flow': flow, 'code': 'c'}, created_minutes_ago=600)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual([s for s in app.selectbox if s.key == 'google_heard_from'], [])

    def test_a_flow_works_only_once(self):
        _, _, _, flow = self._start_google()
        self._come_back({'google_flow': flow, 'code': 'c'})
        app, db = self._come_back({'google_flow': flow, 'code': 'c'})
        self.assertEqual(db.auth.exchange_code_for_session.call_count, 0)
        self.assertIsNone(app.session_state['user'])
        self.assertTrue(any("didn't finish" in w.value for w in app.warning))

    def test_unknown_flow_is_refused_without_calling_supabase(self):
        app, db = self._come_back({'google_flow': 'made-up', 'code': 'c'})
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(db.auth.exchange_code_for_session.call_count, 0)
        self.assertIsNone(app.session_state['user'])
        self.assertTrue(any("didn't finish" in w.value for w in app.warning))

    def test_cancelling_on_google_says_so(self):
        _, _, _, flow = self._start_google()
        app, db = self._come_back({'google_flow': flow, 'error': 'access_denied'})
        self.assertEqual(db.auth.exchange_code_for_session.call_count, 0)
        self.assertTrue(any('cancelled' in w.value for w in app.warning))
        self.assertEqual(dict(app.query_params), {})

    # ── ?src= link codes (T2, 2 Nov 2026) ──────────────────────────────────────
    def _signup_answer(self, app):
        return [s for s in app.selectbox if s.key == 'signup_heard_from'][0]

    def test_link_code_prefills_the_signup_answer(self):
        app = self._logged_out(google='off')
        app.query_params['src'] = 'ig'
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(self._signup_answer(app).value, 'instagram')
        self.assertEqual(app.session_state['signup_src'], 'ig')

    def test_unknown_link_code_is_ignored(self):
        app = self._logged_out(google='off')
        app.query_params['src'] = 'myspace'
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertIsNone(self._signup_answer(app).value)
        self.assertNotIn('signup_src', app.session_state)

    def test_link_code_survives_the_landing_page_signup_button(self):
        # ?signup=1 clears the address bar; the code must be read first.
        app = self._logged_out(google='off')
        app.query_params['signup'] = '1'
        app.query_params['src'] = 'card'
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state['signup_src'], 'card')
        self.assertEqual(self._signup_answer(app).value, 'card')

    def test_email_signup_keeps_link_code_even_if_answer_changes(self):
        app = self._logged_out(google='off')
        app.query_params['src'] = 'tt'
        app.run()
        db = MagicMock()
        db.auth.sign_up.return_value = types.SimpleNamespace(user=types.SimpleNamespace(id='new'), session=None)
        with patch('supabase.create_client', return_value=db):
            [t for t in app.text_input if t.label == 'Email'][-1].input('new@test.invalid')
            [t for t in app.text_input if t.label == 'Password'][-1].input('secret123')
            [t for t in app.text_input if t.label == 'Confirm Password'][0].input('secret123')
            self._signup_answer(app).select('friend')
            [b for b in app.button if 'Create Account' in b.label][0].click().run()
        self.assertEqual(len(app.exception), 0)
        sent = db.auth.sign_up.call_args.args[0]
        self.assertEqual(sent['options']['data'], {'heard_from': 'friend', 'signup_src': 'tt'})
        self.assertTrue(any('confirm your account' in i.value for i in app.info))

    def test_google_signup_carries_link_code_through_google(self):
        import urllib.parse
        app = self._logged_out()
        app.query_params['src'] = 'yt'
        app.run()
        self._google_buttons(app)[0].click().run()
        url = [l for l in app.get('link_button') if 'Opening Google' in l.proto.label][0].proto.url
        back = urllib.parse.urlparse(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)['redirect_to'][0])
        self.assertNotIn('yt', back.query)          # the code waits on the server, not in the URL
        flow = urllib.parse.parse_qs(back.query)['google_flow'][0]
        app2, db = self._come_back({'google_flow': flow, 'code': 'c'}, rows=[])
        self.assertEqual(len(app2.exception), 0)
        inserted = db.table.return_value.insert.call_args_list[0].args[0]
        self.assertEqual((inserted['tier'], inserted['signup_src']), ('trial', 'yt'))
        self.assertEqual([s for s in app2.selectbox if s.key == 'google_heard_from'][0].value, 'youtube')

    def test_returning_google_sailor_gets_no_link_code(self):
        app = self._logged_out()
        app.query_params['src'] = 'fb'
        app.run()
        self._google_buttons(app)[0].click().run()
        import urllib.parse
        url = [l for l in app.get('link_button') if 'Opening Google' in l.proto.label][0].proto.url
        flow = urllib.parse.parse_qs(urllib.parse.urlparse(
            urllib.parse.parse_qs(urllib.parse.urlparse(url).query)['redirect_to'][0]).query)['google_flow'][0]
        app2, db = self._come_back({'google_flow': flow, 'code': 'c'}, created_minutes_ago=600, rows=[])
        self.assertEqual(len(app2.exception), 0)
        inserted = db.table.return_value.insert.call_args_list[0].args[0]
        self.assertNotIn('signup_src', inserted)

    # ── 2 Nov 2026, found live: Supabase makes the profile row itself at signup ──
    def _filled(self, db):
        """{column: value} the app filled in, each only where still blank."""
        upd = db.table.return_value.update
        out = {}
        for c in upd.call_args_list:
            row = c.args[0]
            if set(row) <= {'heard_from', 'signup_src'}:
                out.update(row)
        is_calls = [c.args for c in upd.return_value.eq.return_value.is_.call_args_list]
        return out, is_calls

    def test_confirm_link_fills_answers_on_row_supabase_already_made(self):
        import datetime as dt
        app = self._logged_out(google='off')
        app.query_params['token_hash'] = 'th'
        app.query_params['type'] = 'email'
        db = MagicMock()
        user = types.SimpleNamespace(
            id='e-user', email='new@test.invalid', created_at=dt.datetime.now(dt.timezone.utc),
            user_metadata={'heard_from': 'friend', 'signup_src': 'ig'})
        db.auth.verify_otp.return_value = types.SimpleNamespace(
            user=user, session=types.SimpleNamespace(access_token=self._fake_jwt(), refresh_token='rt'))
        db.table.return_value.select.return_value.eq.return_value.limit.return_value \
            .execute.return_value.data = [{'tier': 'trial', 'trial_start': None}]
        with patch('supabase.create_client', return_value=db), \
                patch('account_locks.claim_session'), \
                patch('account_locks.session_still_active', return_value=True), \
                patch('account_locks.load_rating', return_value=(None, None)):
            app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(db.table.return_value.insert.call_count, 0)   # row already there
        filled, is_calls = self._filled(db)
        self.assertEqual(filled, {'heard_from': 'friend', 'signup_src': 'ig'})
        self.assertEqual(sorted(is_calls), [('heard_from', 'null'), ('signup_src', 'null')])

    def test_google_link_code_fills_row_supabase_already_made(self):
        import urllib.parse
        app = self._logged_out()
        app.query_params['src'] = 'pts'
        app.run()
        self._google_buttons(app)[0].click().run()
        url = [l for l in app.get('link_button') if 'Opening Google' in l.proto.label][0].proto.url
        flow = urllib.parse.parse_qs(urllib.parse.urlparse(
            urllib.parse.parse_qs(urllib.parse.urlparse(url).query)['redirect_to'][0]).query)['google_flow'][0]
        app2, db = self._come_back({'google_flow': flow, 'code': 'c'})   # row exists
        self.assertEqual(len(app2.exception), 0)
        filled, is_calls = self._filled(db)
        self.assertEqual(filled, {'signup_src': 'pts'})
        self.assertEqual(is_calls, [('signup_src', 'null')])

    def test_login_without_answers_writes_nothing_extra(self):
        _, _, _, flow = self._start_google()
        app, db = self._come_back({'google_flow': flow, 'code': 'c'}, created_minutes_ago=600)
        self.assertEqual(self._filled(db), ({}, []))

if __name__ == '__main__': unittest.main()
