# Launch preparation implemented locally

Support address supplied by owner: strategicsailor@gmail.com. Owner confirms Strategic Solutions LLC is registered in Lake County, Illinois. Strategic Sailor is the brand operating Score Surge; it is not a separately registered LLC. The Stripe seller record and approved refund policy remain to be confirmed.

## App changes

- Checkout sessions now bind to the authenticated account using Stripe metadata.
- Callback checks account binding, completed subscription checkout, payment state, current subscription status, customer identity and known current subscription price. An old canceled checkout cannot restore access; the tier is not taken from an arbitrary client-reference label.
- Recurring-billing consent is unchecked by default; absent consent prevents session creation. Accepted consent/version are recorded in new session metadata. This is billing consent, not an implemented Terms acceptance log.
- My Profile includes a billing-portal entry point, customer ownership check and public support email. Portal configuration and real cancellation are not yet production-verified.
- Full question/options/source evidence are preserved with submitted disputes as versioned JSON in the existing reason_text column. No schema migration is needed. AI opinions cannot mark disputes resolved and are presented as provisional.
- Accuracy/data-use notices appear before login and in My Profile. They describe actual providers and limitations. The prior blanket liability-exclusion footer is replaced with factual educational-use language pending legal review.

## Question database editor

- Verification counts require reviewer/date/source quotation/page and a hash matching exact question content. Legacy yes flags alone are excluded.
- Editing content invalidates prior verification and marks it for review. New questions are drafts.
- A source-review form records reviewer identity, source evidence, exam type and explicit attestation. MILPERSMAN reviews require a change revision. A stale displayed version cannot be signed off after the stored content changes.
- This records human review; it does not prove that the reviewer made the correct interpretation. No existing question was marked verified or rewritten by this work.

## Tests

377 logic checks, app smoke tests, 8 billing/dispute unit tests, 2 billing-portal UI tests, 4 existing trust regressions, 4 verification tests and admin view/edit rendering checks pass in the staged local environment. Tests use mocked service responses and a disposable database copy. No payment, production account mutation, production schema change or deployment was performed.

## Still required

See RELEASE_GATES.md. Most important: production account/data isolation, webhook/cancellation end-to-end tests, independent content-accuracy measurement, a staffed dispute-review process, current authoritative Navy sources, business details and legal/privacy review. The new callback does not fix every entitlement problem in the existing webhook. Do not call the product legally protected or fully trustworthy based only on these local changes.
