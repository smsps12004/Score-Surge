# Billing and refunds — proposed policy, owner approval required

Do not publish until business identity/support contact are supplied and Stripe behavior is verified.

## Established from local code

New-plan labels currently show Petty Officer at $12.99/month and Chief at $19.99/month. Verify the corresponding live Stripe prices, taxes, currency, legacy prices and what each tier includes before publication. These are recurring subscriptions, not a one-time purchase.

The local app now requests affirmative recurring-billing consent and records a disclosure version in new Checkout-session metadata. It includes a billing-portal entry point. The Stripe portal configuration and actual cancellation path still need live/test-mode validation.

## Recommended policy for owner review

- Cancellation stops future renewals; paid access remains through the current paid period, if confirmed by the actual Stripe configuration.
- Offer a full refund of the first subscription payment when requested within seven days of purchase. This is a proposed commercial decision, not a policy already accepted or implemented.
- Correct duplicate/unauthorized charges and service-delivery failures through a documented support process; preserve rights required by law.
- Explain whether and when renewal payments qualify for discretionary refunds. Avoid a blanket “no refunds under any circumstances.”
- Provide a working cancellation/support contact and a clear procedure when the portal is unavailable. Proposed support target: acknowledge within two business days, subject to actual staffing.

## Customer-facing draft after approval

“Your selected plan renews monthly at the price shown at checkout until you cancel. Manage or cancel from My Profile → Manage subscription / cancel. [CONFIRMED CANCELLATION-EFFECTIVE-TIME POLICY]. For billing help or refund requests, contact strategicsailor@gmail.com. [APPROVED REFUND WINDOW AND EXCEPTIONS]. Nothing in this policy limits rights that cannot be waived under applicable law.”

## Review basis

FTC guidance describes clear material terms, express informed consent and a simple cancellation mechanism under ROSCA: https://www.ftc.gov/business-guidance/blog/2016/09/negative-options-make-them-positive

Do not rely on outdated summaries presenting the 2024 amended “click-to-cancel” rule as effective law. The FTC's February 2026 notice conforms rules to court decisions: https://www.ftc.gov/legal-library/browse/federal-register-notices/revision-negative-option-rule-withdrawal-cars-rule-removal-non-compete-rule-conform-these-rules

Counsel should assess applicable federal and state renewal, cancellation, refund and notice requirements for the seller and customers.
