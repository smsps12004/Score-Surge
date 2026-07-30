# Duty Day → Score Surge Integration — Handoff

**Decision (2026-07-29):** Duty Day is a **Score Surge feature**, not a separate app.
It becomes a tab in `app.py`, not its own deployment.

---

## What exists today

`duty_day_prototype.py` — 433 lines, committed, playable standalone via
`streamlit run duty_day_prototype.py`. **Not imported by `app.py`.**

One hand-written scenario: PS / E6 / Reenlistment & Extension Processing,
"The Friday Afternoon Reenlistment." Zero API calls — everything is hardcoded
so the flow can be judged before paying for it.

**Shape of a scenario:** brief → find the governing instruction (30 pts) →
3 multiple-choice beats (20/20/10) → 1 ordering beat with partial credit (20) →
debrief. Scored on three axes: reference, procedure, sequence. 100 points total.

**Pure functions, already testable without Streamlit:** `score_order()`,
`tally()`, `rating_for()`.

---

## Blockers to integration — must be handled, in this order

**1. `st.set_page_config` collides.**
`app.py:13` and `duty_day_prototype.py:19` both call it. Streamlit allows exactly
one call per app, and it must be the first Streamlit command. The prototype's call
has to go when it becomes a tab.

**2. Session state keys collide.**
The prototype uses bare keys: `stage`, `beat_idx`, `notes`, `ref_correct`, plus
dynamic `beat_{i}_correct`, `beat_{i}_seq`, `beat_{i}_submitted`, `beat_{i}_why`.
Score Surge already owns: `user`, `tier`, `fms_paygrade`, `_pg_src`,
`practice_questions`, `tutor_history`, `score_history`, `score_history_loaded`,
`access_token`, `refresh_token`, `_payment_success`.

No direct collision *today*, but `stage` and `notes` are exactly the kind of
generic names a future feature will grab. Namespace all of them to `dd_*` during
the move.

**3. The whole UI runs at module level.**
`st.title`, the sidebar block, and the entire stage machine execute on import.
Needs wrapping in `render_duty_day()` so it can be called inside a tab.

**4. Tab count.**
`app.py:818` already builds 7 tabs (`tab1`–`tab7`). Duty Day makes 8. Worth
checking how that reads on a phone before committing to a tab — Shawn works
mostly from mobile.

---

## Open product questions

- **Which tier gets Duty Day?** Not decided. Affects whether it needs an
  `upgrade_banner()` call.
- **Scenario storage.** The prototype's comment says the real build makes ONE API
  call per scenario and caches it. That's the `scenarios` table already on the
  todo list — Duty Day integration and the Supabase token fix are the same piece
  of work, and should be done together rather than twice.
- **Free-text answers.** The prototype buffers them in `S.notes` and never grades
  them. Real build grades all of them in one call at debrief. Not built yet.
- **Weak-area routing.** The debrief already claims weak areas "flow straight into
  your Advancement Planner." Nothing is wired. Either build it or soften the copy.

---

## Citations needing Shawn's PS1 review

Full block at the bottom of `duty_day_prototype.py` under `VERIFY_THESE`.
Five references, all pulled from MyNavyHR, none confirmed by a human who knows
the job:

1. **MILPERSMAN 1160-030** (CH-88, 22 Jul 2024) — used as the eligibility gate and
   as the source for three separate claims: CO recommendation required, term not
   less than 2 years, term may not exceed HYT. **Highest risk — three assertions
   resting on one unverified citation.**
2. **MILPERSMAN 1160-120** — High Year Tenure, referenced for the HYT ceiling.
3. **MILPERSMAN 1070-240** — NAVPERS 1070/601 contract, positioned as a later step.
4. **MILPERSMAN 1306-604** — OBLISERV, used as a plausible wrong answer.
5. **OPNAVINST 1160.8B** — SRB, used as the money distractor.

**Open question for Shawn:** the scenario asserts a pending NJP effectively blocks
reenlistment because the CO won't recommend. That's practice, not necessarily a
cited rule. Decide whether to state it as practice or tie it to an article.

---

## Suggested order for the next session

1. Verify the five citations — nothing else matters if the content is wrong
2. Decide tier gating and scenario storage
3. Refactor to `render_duty_day()`, namespace session keys, drop `set_page_config`
4. Wire as tab 8, add smoke test coverage for the new tab
5. Move the scenario into Supabase, wire the single cached API call

---

## Testing before any push

Both must pass — they cover different failure modes:

```
python3 run_checks.py    # 55 checks — the math and parsing
python3 smoke_test.py    # 15 checks — the app renders and the FMS flow works
```

`score_order()`, `tally()` and `rating_for()` are pure and deserve checks in
`run_checks.py` once Duty Day is in `app.py`. Remember to bump `EXPECTED_TOTAL`.
