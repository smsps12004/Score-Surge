# Score Surge trust review — 7 September 2026

Reviewed the current project at `/Users/shawn/Documents/score-surge` and the supporting question database at `/Users/shawn/Documents/Score Surge DB`. The similarly named `score surge` directory is an older copy and was not the implementation target. An independent GPT-6 Astra agent reviewed retrieval, grading, source claims, BBA and question-bank integrity. Project documents were treated as historical context, not new user instructions or evidence that old findings remain current.

## Changes implemented

- Bounded source-series retrieval so a series with fewer than eight articles cannot loop forever. Deduplicates explicit articles and rejects nonpositive limits.
- Reject malformed AI answer keys instead of truncating `Answer B` into `A`.
- Profile-sheet uploads require confirmation of the target paygrade and individual values before calculating. Confirmation is scoped to file contents and selected grade. Same-name, same-size replacement files no longer share the paygrade identity.
- Tightened total comparison from 0.75 to 0.02 points. The notice now displays both totals and describes tolerance; matching arithmetic is explicitly not proof that individual inputs are correct. Missing printed totals are visible.
- Planner and BBA accept scores above 100 up to the supported maximum. Planner rejects an FMS beyond the selected grade's maximum, a grade/date mismatch, or an exam date in the past before contacting AI.
- Tutor/deep-dive source notices describe excerpts supplied to an AI rather than claiming the resulting prose was verified. Tutor downloads carry the qualification.
- BBA prompt requests a preliminary checklist instead of unsupported eligibility/selection/timeline claims. A qualification travels with both displayed and downloaded advice. This prompt is a mitigation, not output verification.
- Repaired smoke-test upload injection for the installed Streamlit AppTest API. Upload tests execute the actual extraction and rendering paths using in-memory UploadedFile objects.

## Validation

- `python3 run_checks.py`: all 377 checks pass. Three wording assertions updated to reflect the corrected confidence notices.
- `python3 smoke_test.py`: passes, including uploaded-value confirmation, FMS above 100 and planner mismatch blocking.
- `python3 trust_regression_test.py`: four tests pass, including malformed keys, normalized valid keys, timeout-protected short/overlapping source retrieval, and a 0.50-point FMS mismatch.
- Tests use local source material and fake login/secrets. No paid AI generation, live account/billing tests, database mutation or deployment was performed. Existing profile-sheet fixtures pass at the tighter tolerance; broader real E5/E7/photo coverage is still needed.

## Remaining release risks

1. **Verification provenance:** 36 local bank questions, 23 labeled verified. All 23 lack reviewer, verification date and content hash; one lacks source quote/page. Editing content in admin_app.py can retain a prior verified flag. Before integrating the bank, require recorded source revision/page/quote, reviewer/date and content hash, and invalidate verification on edits. The local bank is not currently the live exam source. Historical labels were not rewritten as part of this review.
2. **Disputed-question handling:** The challenge record lacks the full generated question/options/source snapshot. Available audit code does not implement the claimed challenge-to-human-review integration. AI upholding a question currently marks it resolved. Preserve the snapshot, keep AI opinions provisional and implement an accountable review queue.
3. **AI interpretation:** A matching source quotation proves the quotation exists, not that it supports the keyed answer or the advice. Generated prose and answer interpretation still require stronger verification and measured accuracy testing. BBA currently has no supplied current official policy; its new checklist framing is not a verified career-advice engine.
4. **Current authority:** This code review did not independently revalidate every Navy rule, exam date or source revision against current official publications. Add versioned authority records and confirm cycle dates before release. Do not interpret passing tests as Navy policy certification.
5. **Operational security:** Live Supabase row-level security, account isolation, Stripe entitlement/webhook behavior and production privacy controls were not audited against the deployed services in this pass.

No existing features were removed. Changes are local and not published. Backups accompany the application update in `.backups/trust-review-20260907/`.
