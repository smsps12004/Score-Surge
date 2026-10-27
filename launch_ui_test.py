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

if __name__ == '__main__': unittest.main()
