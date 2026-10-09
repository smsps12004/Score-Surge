# Score Surge Privacy Notice — factual draft, not approved for publication

Controller/provider: **Strategic Solutions LLC**, **Illinois, United States**. Privacy contact: **strategicsailor@gmail.com**. Effective date: **[DATE]**.

## Data used to provide the app

Based on the reviewed application code:

- Account email, authentication/session information and account tier are handled through Supabase. The app also maintains trial and billing-customer linkage information.
- Saved practice-exam history includes practice results and topic information linked to your user account.
- Submitted challenges include your explanation, the disputed question and choices, source information and an AI opinion. These are retained for error investigation and review.
- AI prompt text, chat context, study topics and challenge explanations are sent to Anthropic for response generation. Text users voluntarily include in these fields may therefore be transmitted to that provider.
- Uploaded profile sheets are parsed on the application server. The reviewed extraction path uses a temporary file and attempts cleanup after processing. A separate audit of hosting logs, crash handling, backups and provider retention is required before making an absolute claim that uploads are never retained anywhere.
- Stripe processes subscription payments. Score Surge maintains subscription linkage and access information. Do not characterize this as the app collecting no billing-related data merely because payment cards are entered at Stripe.

## Purposes

Data is used to authenticate accounts, provide subscribed features, generate requested study material, maintain practice history, process billing and investigate reported errors. **Confirm whether analytics, advertising, additional integrations, support tools or other uses exist in production before publishing.**

## Providers and access

The reviewed code uses Supabase, Anthropic and Stripe, plus the deployed hosting provider. **Confirm the hosting provider, subprocessors, regions, contractual terms, provider retention/training settings and cross-border handling.** Do not promise that providers never train on or retain submitted data without confirming the applicable account settings and contracts.

## Retention, deletion and requests

**Required operational decision:** Define retention periods for account records, scores, challenges, logs, backups and billing records. Establish a tested account-data deletion/request process and identify records retained for legal or accounting reasons. Explain backup deletion timing accurately. Customer contact: **strategicsailor@gmail.com**.

**Do not publish:** “We retain no personal information,” “everything is immediately deleted,” “we never share data,” or “all data is fully secure.” Those statements are not established by this review.

## User precautions

Do not enter SSNs, DoD IDs, passwords, classified material or controlled unclassified information into AI prompts. Share only the information needed for your study question. Official career determinations should be handled through authorized Navy channels.

## Security and applicable rights

**Before publishing:** verify tenant isolation, least-privilege database access, secret management, deletion, incident handling and applicable state/international privacy rights. Add the rights and request mechanisms that actually apply; do not invent a universal legal deadline or promise unsupported security certifications.
