-- Trial starts on tap — T28, 2 Nov 2026 (approved by Shawn 29 Oct, S14).
--
-- What this does, in plain English:
--   Before: every new account was put on a 3-day trial the moment it was created
--   (the profiles table defaulted tier to 'trial' and trial_start to now()), so a
--   sailor who signed up in November to try the free tools had used up the trial
--   long before studying for a March exam.
--   After: a new account starts on the FREE plan with its trial NOT started
--   (trial_start empty). The app shows "Start my 3-day free trial"; tapping it calls
--   start_my_trial(), which starts it once and never again.
--
--   1. New rows default to tier 'free' with no trial_start. This covers the
--      dashboard-made on_auth_user_created -> handle_new_user() step, which inserts
--      only (id, email) and relies on these defaults. That function is unchanged.
--   2. profiles_guard: a new row made from the app's own session is likewise
--      'free' with no trial_start (was: trial starting now). Everything else in
--      the guard is copied unchanged from 20260930_account_locks.sql.
--   3. start_my_trial(): the ONLY way a sailor's session can go free -> trial.
--      Works once: the account must be free, never have started a trial, and have
--      no Stripe customer. Answers 'started' or 'not available'.
--
-- Not touched: anyone already on a trial or a paid plan, expired trials (their
-- trial_start stays set, so they can't restart), the Stripe webhook, usage limits.
-- No data is changed or deleted. Safe to run more than once.
--
-- To undo (puts back the old behaviour for NEW sign-ups):
--   alter table public.profiles alter column tier set default 'trial';
--   alter table public.profiles alter column trial_start set default now();
--   then re-run the profiles_guard section of 20260930_account_locks.sql.

-- ── 1. New accounts: free, trial not started ─────────────────────────────────
alter table public.profiles alter column tier set default 'free';
alter table public.profiles alter column trial_start drop default;

-- ── 2. The guard: app-made new rows start free too ───────────────────────────
create or replace function public.profiles_guard()
returns trigger
language plpgsql
as $$
declare
    privileged boolean := current_user not in ('authenticated', 'anon');
    o jsonb;
    n jsonb := to_jsonb(new);
    patch jsonb := '{}'::jsonb;
    col text;
begin
    if privileged then
        -- Webhook / dashboard: anything goes, but keep rating_set_at honest.
        if tg_op = 'UPDATE' and new.rating is distinct from old.rating then
            new.rating_set_at := now();
        end if;
        return new;
    end if;

    if tg_op = 'INSERT' then
        -- A brand-new row from the app: free, trial NOT started, no Stripe link.
        -- (T28, 2 Nov 2026: the 3-day trial starts only through start_my_trial(),
        -- when the sailor taps "Start my 3-day free trial".)
        patch := jsonb_build_object(
            'tier', 'free',
            'trial_start', null,
            'stripe_customer_id', null
        );
        if coalesce(auth.jwt()->>'email', '') <> '' then
            patch := patch || jsonb_build_object('email', auth.jwt()->>'email');
        end if;
        if n->>'rating' is not null and n->>'rating' not in ('PS', 'YN', 'NC') then
            patch := patch || jsonb_build_object('rating', null);
        end if;
        new := jsonb_populate_record(new, patch);
        new.rating_set_at := case when new.rating is null then null else now() end;
        return new;
    end if;

    -- UPDATE from the sailor's own session.
    o := to_jsonb(old);

    -- tier: unchanged, or trial -> free only.
    if (n->>'tier') is distinct from (o->>'tier')
       and not (o->>'tier' = 'trial' and n->>'tier' = 'free') then
        patch := patch || jsonb_build_object('tier', o->'tier');
    end if;

    -- Never settable by the sailor.
    if o ? 'stripe_customer_id' and (n->'stripe_customer_id') is distinct from (o->'stripe_customer_id') then
        patch := patch || jsonb_build_object('stripe_customer_id', o->'stripe_customer_id');
    end if;

    -- Settable only while still empty (email must be their own login email).
    foreach col in array array['trial_start', 'email'] loop
        if o ? col and (n->col) is distinct from (o->col) and (o->col) <> 'null'::jsonb then
            patch := patch || jsonb_build_object(col, o->col);
        end if;
    end loop;
    if o ? 'email' and (o->'email') = 'null'::jsonb and (n->>'email') is distinct from (auth.jwt()->>'email') then
        patch := patch || jsonb_build_object('email', o->'email');
    end if;

    new := jsonb_populate_record(new, patch);

    -- rating: first pick is free; after that, once per 180 days; PS/YN/NC only.
    if new.rating is distinct from old.rating then
        if new.rating is null
           or new.rating not in ('PS', 'YN', 'NC')
           or (old.rating is not null
               and old.rating_set_at is not null
               and old.rating_set_at > now() - interval '180 days') then
            new.rating := old.rating;
            new.rating_set_at := old.rating_set_at;
        else
            new.rating_set_at := now();
        end if;
    else
        new.rating_set_at := old.rating_set_at;
    end if;

    return new;
end;
$$;


-- (The profiles_guard trigger itself already points at this function.)

-- ── 3. Start the trial, once ─────────────────────────────────────────────────
create or replace function public.start_my_trial()
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
    changed int;
begin
    if auth.uid() is null then
        return 'not available';
    end if;
    update public.profiles
       set tier = 'trial', trial_start = now()
     where id = auth.uid()
       and tier = 'free'
       and trial_start is null
       and stripe_customer_id is null;
    get diagnostics changed = row_count;
    return case when changed = 1 then 'started' else 'not available' end;
end;
$$;

revoke all on function public.start_my_trial() from public, anon;
grant execute on function public.start_my_trial() to authenticated;
