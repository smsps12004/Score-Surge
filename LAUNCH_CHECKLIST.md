# Score Surge — Go-Live Checklist

Work these in order. Do not take a real card until 1–4 pass.

---

## 1. Wait for the rebuild

Streamlit Cloud → your app → it should show the new commit `cf21af4`.
Normal rebuild, no `packages.txt` change, so a few minutes.

**Pass:** the app loads and the Mock Exam tab shows A/B/C/D radio buttons.

---

## 2. Add the `email` column to profiles  ← blocks payment

Supabase → SQL Editor → New query → paste and Run.
Safe to run twice. Backfills everyone you already have.

```sql
alter table profiles add column if not exists email text;

update profiles p
set email = u.email
from auth.users u
where p.id = u.id and p.email is null;
```

**Why:** the Stripe webhook finds sailors by email. Nothing was ever writing one,
so it updated zero rows and reported success. Someone pays, nothing happens.

**Pass:** run this to confirm no one is left without an email —

```sql
select count(*) from profiles where email is null;
```

Should return `0`.

---

## 3. Confirm LIVE Stripe keys  ← blocks real money

Streamlit Cloud → your app → Settings → Secrets.

- `STRIPE_SECRET_KEY` must start with **`sk_live_`**
- If it starts with `sk_test_`, no real money can move

Also confirm the price IDs in `app.py` (`STRIPE_PRICE_IDS`) are **live-mode**
price IDs. Test-mode and live-mode price IDs are different strings, and a
live key with a test price ID fails at checkout.

**Pass:** key starts `sk_live_`, price IDs come from the live dashboard.

---

## 4. Confirm the webhook is deployed and registered

Stripe → Developers → Webhooks.

- An endpoint pointing at your Supabase edge function URL
- Listening for `checkout.session.completed`
- Its signing secret is set as `STRIPE_WEBHOOK_SECRET` on the function
- The function is deployed (Supabase → Edge Functions → `stripe-webhook`)

**Pass:** endpoint exists, recent deliveries show 200s (or no failures).

---

## 5. Buy your own product

The only test that proves the revenue path.

1. Open the live site in a private window
2. Create a **new** account with a different email
3. Wait out or skip past the trial, hit a locked tab, tap Upgrade
4. Pay with a **real card**
5. Confirm you land back on the app with the paid tier active
6. Check Supabase: that sailor's `profiles.tier` says `chief`
7. Check Stripe: the payment shows up
8. **Refund yourself**

**Pass:** tier upgraded in the database, not just on screen.

**Extra credit — the failure case that matters:** pay, then close the tab at
Stripe's confirmation screen instead of returning to the app. Open the app
fresh and log in. If the tier is still upgraded, the webhook is doing its job.
If not, only the return-to-app path works, and anyone who pays on their phone
and opens on a laptop gets nothing.

---

## 6. Smoke the live site

- Log in, log out, log back in — no leftover exam from the previous session
- Take a mock exam end to end
- Score History survives a logout
- Upload a profile sheet (JPG and a photographed sheet)
- Check it on your phone

---

## Known gaps — live with these, fix soon

- **Cancellations are not handled.** The webhook only listens for
  `checkout.session.completed`. A sailor who cancels, or whose card fails,
  keeps paid access indefinitely. Revenue leak.
- **Questions are ~1 in 3 unreliable.** Verified 2026-07-31 against the
  manuals. They are now labelled unverified throughout, but the fix is the
  question bank: 14 verified, 20 needed to clear the integration gate.
- **Upload paths never tested live:** JPG upload, photographed sheet paygrade
  detection.
- **Duplicate folder** `~/Documents/score surge` (with a space) still exists
  and its status is unknown.
