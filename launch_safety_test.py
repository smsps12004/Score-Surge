import copy
import json
import unittest
from launch_safety import validated_checkout, challenge_record

class LaunchSafetyTests(unittest.TestCase):
    def setUp(self):
        self.session = dict(metadata={'user_id': 'alice'}, mode='subscription', status='complete',
            payment_status='paid', customer='cus_alice', client_reference_id='chief',
            subscription=dict(status='active', customer='cus_alice', items={'data':[{'price':{'id':'price_po'}}]}))
        self.prices = {'price_po': 'petty_officer'}

    def test_tier_comes_from_current_price_not_client_reference(self):
        self.assertEqual(validated_checkout(self.session, 'alice', self.prices),
                         {'tier':'petty_officer', 'stripe_customer_id':'cus_alice'})

    def test_checkout_cannot_be_replayed_by_another_account(self):
        self.assertIsNone(validated_checkout(self.session, 'bob', self.prices))

    def test_legacy_session_without_binding_fails_closed(self):
        self.session['metadata'] = {}
        self.assertIsNone(validated_checkout(self.session, 'alice', self.prices))

    def test_old_checkout_does_not_restore_canceled_subscription(self):
        for state in ['canceled', 'unpaid', 'incomplete', 'past_due']:
            self.session['subscription']['status'] = state
            self.assertIsNone(validated_checkout(self.session, 'alice', self.prices))

    def test_unknown_or_multiple_prices_are_rejected(self):
        self.session['subscription']['items']['data'].append({'price': {'id': 'price_po'}})
        self.assertIsNone(validated_checkout(self.session, 'alice', self.prices))
        self.session['subscription']['items']['data'] = [{'price': {'id': 'unknown'}}]
        self.assertIsNone(validated_checkout(self.session, 'alice', self.prices))

    def test_unpaid_incomplete_wrong_mode_or_customer_rejected(self):
        for field, value in [('payment_status','unpaid'),('status','open'),('mode','payment'),('customer','cus_bob')]:
            candidate = copy.deepcopy(self.session)
            candidate[field] = value
            with self.subTest(field=field):
                self.assertIsNone(validated_checkout(candidate, 'alice', self.prices))

    def test_stripe_sdk_conversion(self):
        import stripe
        session = stripe.StripeObject.construct_from(self.session, 'test-not-real')
        self.assertEqual(validated_checkout(session.to_dict(), 'alice', self.prices)['tier'], 'petty_officer')

    def test_dispute_keeps_question_and_never_auto_resolves(self):
        question = dict(question='Which rule?',answer_a='A',answer_b='B',correct_answer='B',
                        source_quote='A real passage', user_email='private@example.test')
        for verdict in ['upholds','inconclusive','confirms-error']:
            evidence, status = challenge_record(question, '  I disagree  ', verdict)
            payload = json.loads(evidence)
            self.assertEqual(payload['question']['correct_answer'], 'B')
            self.assertEqual(payload['question']['source_quote'], 'A real passage')
            self.assertEqual(payload['reason'], 'I disagree')
            self.assertNotIn('user_email', payload['question'])
            self.assertNotEqual(status, 'resolved')
            self.assertEqual(status, 'priority' if verdict == 'confirms-error' else 'flagged')

if __name__ == '__main__':
    unittest.main()
