"""Pure validation and disclosure helpers; no network or UI side effects."""
import json

SUPPORT_EMAIL = "strategicsailor@gmail.com"

EDUCATIONAL_NOTICE = (
    "Score Surge is an independent exam-preparation tool, not an official Navy or "
    "Department of Defense service. AI lessons and practice answers may be wrong or "
    "outdated. Source excerpts are not a guarantee that an AI interpretation is correct. "
    "FMS results are estimates based on your inputs, not an official profile sheet or "
    "advancement determination. Confirm rules, eligibility and deadlines in current "
    "official publications and with your ESO or command career counselor. No exam "
    "score, selection, billet or advancement outcome is guaranteed."
)
DATA_NOTICE = (
    "AI prompts, chat messages and challenge explanations are sent to Anthropic to "
    "generate answers. Account details, saved practice scores and submitted challenges "
    "are processed through Supabase. Stripe handles subscription payments. Profile "
    "sheet parsing runs on the application server. Do not enter SSNs, DoD IDs, "
    "passwords, classified information or controlled unclassified information into "
    "AI prompts. Share only the minimum information needed for your study question."
)
QUESTION_FIELDS = (
    'question', 'answer_a', 'answer_b', 'answer_c', 'answer_d', 'correct_answer',
    'explanation', 'source_manual', 'chapter_section', 'source_quote',
)


def validated_checkout(session, user_id, price_to_tier):
    """Derive access from current Stripe subscription plus immutable account binding.

    Caller retrieves session with subscription and line_items expanded. Reject legacy
    sessions lacking an account binding; the webhook remains their fulfillment path.
    """
    if not user_id or session.get('metadata', {}).get('user_id') != str(user_id):
        return None
    if session.get('mode') != 'subscription' or session.get('status') != 'complete':
        return None
    if session.get('payment_status') not in ('paid', 'no_payment_required'):
        return None
    subscription = session.get('subscription')
    if not isinstance(subscription, dict) or subscription.get('status') not in ('active', 'trialing'):
        return None
    customer = session.get('customer')
    customer_id = customer.get('id') if isinstance(customer, dict) else customer
    sub_customer = subscription.get('customer')
    sub_customer_id = sub_customer.get('id') if isinstance(sub_customer, dict) else sub_customer
    if not customer_id or customer_id != sub_customer_id:
        return None
    items = subscription.get('items', {}).get('data', [])
    if len(items) != 1:
        return None
    price = items[0].get('price') or {}
    tier = price_to_tier.get(price.get('id'))
    if not tier:
        return None
    return {'tier': tier, 'stripe_customer_id': customer_id}


def challenge_record(row, reason, verdict):
    """Keep disputed content with the reason in the existing text column.

    Versioned JSON avoids a production schema dependency. Never copy arbitrary row
    fields (which may contain account data); only the question evidence is retained.
    """
    snapshot = {key: str(row.get(key) or '') for key in QUESTION_FIELDS}
    payload = json.dumps({'schema_version': 1, 'reason': reason.strip(), 'question': snapshot}, ensure_ascii=False)
    status = 'priority' if verdict == 'confirms-error' else 'flagged'
    return payload, status
