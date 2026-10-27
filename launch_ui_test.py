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

if __name__ == '__main__': unittest.main()
