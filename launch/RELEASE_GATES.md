# Public subscription launch: measurable release gates

A disclaimer is not the release test. The release decision should be based on the records below.

| Gate | Current evidence | Required before public paid launch |
|---|---|---|
| FMS | Existing fixtures and logic tests pass; uploads require confirmation | Record current official formula/caps and test representative genuine E5/E6/E7 sheets; document unsupported layouts |
| AI claims | Source-supplied answers and challenge opinions are labeled provisional | Remove guaranteed/official/verified claims wherever unsupported, including marketing; measure generated-answer quality against an independently reviewed sample |
| Question bank | New hash-based verification check; edits invalidate review | Review source/revision/page/quote, keyed answer and distractors, record reviewer/date/hash; legacy yes flags alone do not count |
| Billing ownership | Local account binding and current-subscription validation tested | Test live/test-mode checkout for two accounts, callback reuse, cancellation, refund, failed payment and renewal; verify webhook delivery and stale-event behavior |
| Cancellation | Local portal entry point and ownership checks tested | Enable cancellation in Stripe portal and complete a real test cancellation; provide a working support fallback |
| Data isolation | Source inspected; deployed policies unknown | Prove account A cannot read/update B's profile/history/challenges or change its own tier/customer link; inspect RLS plus column grants/service-role use |
| Challenge review | Complete question snapshot saved in existing reason_text JSON; AI never resolves automatically | Assign a reviewer, actually retrieve pending challenges, correct/quarantine confirmed errors and communicate outcomes; do not claim a review SLA before staffing it |
| Legal/privacy | Separate drafts prepared | Confirm seller identity/state/contact, refund rules, retention/deletion and provider settings; have counsel review terms and marketing |
| Operations | Local tests pass | Confirm deployed revision, secrets, dependency versions, backups/restore, alerts, support and rollback |

## Recommended launch positioning

“Independent Navy advancement exam preparation with FMS estimates, practice questions and AI-assisted study tools.”

Do not advertise guaranteed advancement, official Navy endorsement, fully verified AI answers or a measured accuracy percentage without evidence supporting that exact claim. If AI quality is not adequate for the promised feature, narrow the paid offering or label that feature experimental before charging for it; a warning is not a cure for a systematically defective answer key.

## Legal review scope

Ask a lawyer familiar with consumer subscription software in the business's jurisdiction to review the actual app, checkout flow, marketing, Terms, Privacy Notice and refund/cancellation policy together. Consider discussing appropriate business structure and technology-errors/cyber coverage; neither incorporation nor insurance is assumed to exist or to cover every claim.

FTC sources: advertising claims require substantiation and disclosures must be clear (https://www.ftc.gov/business-guidance/resources/advertising-faqs-guide-small-business); privacy promises must match practices (https://www.ftc.gov/business-guidance/privacy-security).

## Technical caveats from this pass

The return-from-checkout fallback is hardened locally. The webhook still requires production review: older checkout events match by email, event ordering can affect tier state, and multiple subscriptions/customer links need reconciliation. No production database schema, webhook deployment, billing configuration or real customer account was changed in this pass.

New challenge reason_text entries are versioned JSON containing reason and question evidence. Consumers of that field must decode the schema or treat older entries as plain text; no automated human-review workflow is implied by saving a row.

Legacy question-bank records were not rewritten. They remain historical data; documented-verification counts exclude unsupported flags. An edited question becomes an unverified draft until a fresh review is recorded.
