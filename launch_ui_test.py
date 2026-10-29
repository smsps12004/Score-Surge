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

    def _come_back(self, params, exchange=None, created_minutes_ago=1):
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
            .execute.return_value.data = [{'tier': 'trial', 'trial_start': None}]
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

if __name__ == '__main__': unittest.main()
