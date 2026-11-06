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

    def _free_with_trial(self, rpc_answer):
        # T28: a new account is free with its 3-day trial ready to start on tap.
        app = build_app(tier='free')
        app.session_state['trial_available'] = True
        app.run()
        self.assertEqual(len(app.exception), 0)
        starts = [b for b in app.button if b.label == '▶️ Start my 3-day free trial']
        self.assertTrue(starts)
        db = MagicMock()
        db.rpc.return_value.execute.return_value.data = rpc_answer
        with patch('supabase.create_client', return_value=db):
            starts[0].click().run()
        self.assertEqual(len(app.exception), 0)
        return app, db

    def test_free_sailor_starts_trial_on_tap(self):
        app, db = self._free_with_trial('started')
        self.assertEqual([c.args for c in db.rpc.call_args_list].count(('start_my_trial',)), 1)
        self.assertEqual(app.session_state['tier'], 'trial')
        self.assertFalse(app.session_state['trial_available'])
        self.assertTrue(any('free trial has started' in s.value for s in app.success))
        self.assertFalse([b for b in app.button if b.label == '▶️ Start my 3-day free trial'])

    def test_used_trial_cannot_restart(self):
        app, _ = self._free_with_trial('not available')
        self.assertEqual(app.session_state['tier'], 'free')
        self.assertTrue(any('already used its free trial' in i.value for i in app.info))
        self.assertFalse([b for b in app.button if b.label == '▶️ Start my 3-day free trial'])

    def test_no_start_button_once_the_trial_was_used(self):
        app = build_app(tier='free')
        app.session_state['trial_available'] = False
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertFalse([b for b in app.button if b.label == '▶️ Start my 3-day free trial'])

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

    def test_unconfirmed_login_can_resend_the_email(self):
        # T33: the verify email lands in spam; a sailor stuck at "not confirmed"
        # can ask for it again from the login screen, for the address they typed.
        app, db = self._login(' Sailor@Test.INVALID ', 'Email not confirmed')
        self.assertTrue(any('Spam' in e.value and 'no-reply@strategicsailor.com' in e.value
                            for e in app.error))
        with patch('supabase.create_client', return_value=db):
            [b for b in app.button if b.key == 'resend_verify_login'][0].click().run()
        self.assertEqual(len(app.exception), 0)
        db.auth.resend.assert_called_once_with({'type': 'signup', 'email': 'sailor@test.invalid'})
        self.assertTrue(any('fresh link is on its way' in s.value for s in app.success))
        # Held back for a minute so repeated taps can't burn the shared email limit.
        app.run()
        self.assertTrue([b for b in app.button if b.key == 'resend_verify_login'][0].disabled)
        self.assertTrue(any('send another in about' in c.value for c in app.caption))
        self.assertEqual(db.auth.resend.call_count, 1)

    def test_resend_rate_limit_is_explained(self):
        app, db = self._login('sailor@test.invalid', 'Email not confirmed')
        db.auth.resend.side_effect = Exception('For security purposes, you can only request this after 42 seconds.')
        with patch('supabase.create_client', return_value=db):
            [b for b in app.button if b.key == 'resend_verify_login'][0].click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any('Wait a minute' in w.value for w in app.warning))

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

    def _come_back(self, params, exchange=None, created_minutes_ago=1, rows=None, rating=None):
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
                patch('account_locks.load_rating', return_value=(rating, None)):
            app.run()
        return app, db

    def test_plank_owner_gets_chief_free(self):
        # T31: beta_until in the future means Chief, no card, with a Profile note.
        app, _ = self._come_back({'google_flow': self._start_google()[3], 'code': 'c'},
                                 rows=[{'tier': 'free', 'trial_start': '2026-10-01T00:00:00+00:00',
                                        'beta_until': '2099-03-31'}], rating='PS')
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state['tier'], 'chief')
        self.assertFalse(app.session_state['trial_available'])
        self.assertTrue(any('Plank Owner beta' in s.value for s in app.success))

    def test_finished_beta_drops_back_to_real_plan(self):
        app, _ = self._come_back({'google_flow': self._start_google()[3], 'code': 'c'},
                                 rows=[{'tier': 'free', 'trial_start': '2026-10-01T00:00:00+00:00',
                                        'beta_until': '2020-03-31'}], rating='PS')
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state['tier'], 'free')
        self.assertFalse(any('Plank Owner beta' in s.value for s in app.success))

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

    # ── The Weekly Five (T49 + T47, 5 Nov 2026) ───────────────────────────────
    W5_OPEN = [{'week_id': '2026-11-20', 'opens_at': '2026-11-20T18:30:00-06:00',
                'closes_at': '2026-11-22T23:59:00-06:00', 'status': 'open'}]

    def _w5_db(self, answers):
        """A Supabase stand-in whose rpc(name) returns answers[name] (callable or value)."""
        db = MagicMock()
        db.calls = []
        def rpc(name, params=None):
            db.calls.append((name, params or {}))
            val = answers.get(name)
            val = val(params or {}) if callable(val) else val
            return types.SimpleNamespace(execute=lambda: types.SimpleNamespace(data=val))
        db.rpc.side_effect = rpc
        return db

    def _w5_run(self, answers, state=None, tier='free', **session):
        app = build_app(tier)
        for k, v in session.items():
            app.session_state[k] = v
        db = self._w5_db(answers)
        with patch('supabase.create_client', return_value=db):
            app.run()
        self.assertEqual(len(app.exception), 0, [e.value for e in app.exception])
        return app, db

    def test_weekly_five_card_shows_when_a_week_is_open(self):
        app, _ = self._w5_run({'w5_weeks_now': self.W5_OPEN})
        self.assertTrue([b for b in app.button if b.key == 'w5_card_play'])
        self.assertTrue(any('Weekly Five is open' in m.value for m in app.markdown))

    def test_weekly_five_hidden_when_database_not_ready(self):
        # Before the migration runs every call fails: the app must look exactly as before.
        app = build_app('free'); app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertFalse([b for b in app.button if (b.key or '').startswith('w5_')])
        self.assertEqual(len(app.tabs), 7)

    def test_weekly_five_start_needs_the_rules_box_then_opens_question_1(self):
        state = {'status': 'open', 'started': False, 'questions': 5,
                 'closes_at': '2026-11-22T23:59:00-06:00'}
        answers = {'w5_weeks_now': self.W5_OPEN, 'w5_state': state,
                   'w5_start': {'status': 'open', 'started': True, 'next_idx': 1},
                   'w5_show': {'idx': 1, 'question_id': 'ADV054', 'ms_left': 20000}}
        app, db = self._w5_run(answers, w5_open=True, w5_week_id='2026-11-20')
        start = [b for b in app.button if b.key == 'w5_start'][0]
        self.assertTrue(start.disabled)
        self.assertEqual(len(app.tabs), 0)      # the game screen replaces the tabs
        [c for c in app.checkbox if c.key == 'w5_agree'][0].check()
        with patch('supabase.create_client', return_value=db):
            app.run()
            [b for b in app.button if b.key == 'w5_start'][0].click().run()
        names = [n for n, _ in db.calls]
        self.assertIn('w5_start', names)
        self.assertEqual(names[names.index('w5_start') + 1], 'w5_show')
        self.assertEqual(db.calls[names.index('w5_show')][1], {'p_week': '2026-11-20', 'p_idx': 1})

    def test_weekly_five_answer_sends_the_banks_own_letter(self):
        import weekly_five as w5
        state = {'status': 'open', 'started': True, 'finished': False, 'questions': 5,
                 'next_idx': 1, 'current': {'idx': 1, 'question_id': 'ADV054', 'ms_left': 15000}}
        answers = {'w5_weeks_now': self.W5_OPEN, 'w5_state': state, 'w5_answer': {'answered': 1}}
        app, db = self._w5_run(answers, w5_open=True, w5_week_id='2026-11-20')
        picks = [b for b in app.button if (b.key or '').startswith('w5_pick_')]
        self.assertEqual(len(picks), 4)
        q = w5.question('ADV054')
        right = [shown for shown, real in w5.shuffled('smoke-test-user', '2026-11-20', 1)
                 if real == q['correct']][0]
        self.assertTrue(any(b.label.startswith(f"{right}.") and q['answers'][q['correct']] in b.label
                            for b in picks))
        with patch('supabase.create_client', return_value=db):
            [b for b in picks if b.label.startswith(f"{right}.")][0].click().run()
        sent = [p for n, p in db.calls if n == 'w5_answer']
        self.assertEqual(sent[-1], {'p_week': '2026-11-20', 'p_idx': 1, 'p_choice': q['correct']})

    def test_weekly_five_expired_question_is_recorded_as_no_answer(self):
        state = {'status': 'open', 'started': True, 'finished': False, 'questions': 5,
                 'next_idx': 2, 'current': {'idx': 2, 'question_id': 'ADV077', 'ms_left': 0}}
        after = {'status': 'open', 'started': True, 'finished': False, 'questions': 5,
                 'next_idx': 3, 'current': None}
        calls = {'n': 0}
        def st_(p):
            calls['n'] += 1
            return state if calls['n'] == 1 else after
        app, db = self._w5_run({'w5_weeks_now': self.W5_OPEN, 'w5_state': st_,
                                'w5_answer': {'answered': 2}},
                               w5_open=True, w5_week_id='2026-11-20')
        self.assertIn(('w5_answer', {'p_week': '2026-11-20', 'p_idx': 2, 'p_choice': None}), db.calls)
        self.assertTrue([b for b in app.button if b.key == 'w5_show_3'])

    def test_weekly_five_finished_shows_score_not_answers(self):
        state = {'status': 'open', 'started': True, 'finished': True, 'questions': 5,
                 'score': 4, 'total_ms': 41200, 'closes_at': '2026-11-22T23:59:00-06:00'}
        board = [{'place': 1, 'name': '@squared.away', 'score': 4, 'seconds': 41.2, 'is_me': True}]
        app, db = self._w5_run({'w5_weeks_now': self.W5_OPEN, 'w5_state': state,
                                'w5_leaderboard': board}, w5_open=True, w5_week_id='2026-11-20')
        self.assertTrue(any('4 of 5' in s.value for s in app.success))
        self.assertNotIn('w5_reveal', [n for n, _ in db.calls])
        self.assertTrue(any('@squared.away **(you)**' in m.value for m in app.markdown))
        self.assertTrue(any('?play=w5&src=w5' in c.value for c in app.code))

    def test_weekly_five_closed_week_shows_answers_with_manual_text(self):
        import weekly_five as w5
        state = {'status': 'closed', 'started': True, 'finished': True, 'questions': 5,
                 'score': 1, 'total_ms': 30500}
        reveal = [{'idx': 1, 'question_id': 'ADV054', 'correct': 'A', 'voided': False,
                   'my_choice': 'A', 'my_correct': True, 'pct_correct': 100, 'avg_seconds': 4.4}]
        app, _ = self._w5_run({'w5_weeks_now': self.W5_OPEN, 'w5_state': state,
                               'w5_leaderboard': [], 'w5_reveal': reveal},
                              w5_open=True, w5_week_id='2026-11-20')
        self.assertTrue(any(w5.question('ADV054')['quote'] in m.value for m in app.markdown))
        self.assertTrue([b for b in app.button if b.key == 'w5_ch_ADV054'])

    def test_play_link_before_login_says_log_in_to_play(self):
        app = self._logged_out(google='off')
        app.query_params['play'] = 'w5'
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any('Weekly Five' in i.value for i in app.info))

    def test_weekly_five_prize_gives_chief_without_plank_owner_label(self):
        import datetime
        app = build_app()
        app.session_state['tier'] = None
        db = MagicMock()
        until = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {'tier': 'free', 'trial_start': None, 'beta_until': None, 'comp_until': until}]
        with patch('supabase.create_client', return_value=db):
            app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state['tier'], 'chief')
        self.assertEqual(app.session_state['comp_until'], until)
        self.assertNotIn('beta_until', app.session_state)

    # ── "Follow us" links (T48, 5 Nov 2026) ────────────────────────────────────
    def _follow_rows(self, app):
        return [m.value for m in app.markdown if 'tiktok.com/@score.surge.app' in m.value]

    def test_welcome_page_shows_follow_us_links(self):
        app = self._logged_out(google='off')
        app.run()
        self.assertEqual(len(app.exception), 0)
        rows = self._follow_rows(app)
        self.assertEqual(len(rows), 2)  # under the trial button + footer
        self.assertIn('instagram.com/scoresurgeapp', rows[0])
        self.assertIn('discord.gg/eRHKU4QKmE', rows[0])
        self.assertIn('facebook.com/profile.php?id=61596184705519', rows[0])

    def test_follow_us_links_leave_the_link_code_alone(self):
        app = self._logged_out(google='off')
        app.query_params['src'] = 'card'
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state['signup_src'], 'card')
        self.assertEqual(len(self._follow_rows(app)), 2)

    def test_logged_in_footer_shows_follow_us_links(self):
        app = build_app()
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(self._follow_rows(app)), 1)

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
        # T33: say where to look, who it's from, and offer to send it again.
        notice = [i.value for i in app.info if 'confirm your account' in i.value][0]
        for bit in ('new@test.invalid', 'Spam', 'Promotions', 'no-reply@strategicsailor.com'):
            self.assertIn(bit, notice)
        app.run()   # the notice stays put on the next rerun
        self.assertTrue(any('confirm your account' in i.value for i in app.info))
        with patch('supabase.create_client', return_value=db):
            [b for b in app.button if b.key == 'resend_verify_signup'][0].click().run()
        db.auth.resend.assert_called_once_with({'type': 'signup', 'email': 'new@test.invalid'})

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
        # T28: a new account starts free; the 3-day trial starts on tap.
        self.assertEqual((inserted['tier'], inserted['signup_src']), ('free', 'yt'))
        self.assertNotIn('trial_start', inserted)
        self.assertTrue(app2.session_state['trial_available'])
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
