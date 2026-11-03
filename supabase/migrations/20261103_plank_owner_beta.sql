-- Plank Owner beta access — T31, 2 Nov 2026 (approved by Shawn 29 Oct, S16).
--
-- What this does, in plain English:
--   Adds one column, profiles.beta_until (a date). While today is on or before that
--   date, the sailor gets the Chief tier: in the app and in the database's monthly
--   usage limits. No card, no Stripe. When the date passes they drop back to
--   whatever plan they actually have, automatically.
--
--   Only Shawn sets it, in the Supabase dashboard (Table editor > profiles >
--   the sailor's row > beta_until = 2027-03-31). The sailor's own session can't set
--   or change it (profiles_guard below). Empty for everyone else, so nothing changes
--   for anyone who isn't a Plank Owner.
--
--   1. New column beta_until (date, empty by default).
--   2. profiles_guard: beta_until can't be set from the app; everything else is
--      unchanged from 20261102_trial_on_tap.sql.
--   3. _effective_tier (used by the usage limits): a live beta counts as 'chief'.
--      Otherwise unchanged from 20260930_account_locks.sql.
--
-- Additions only. No data is changed or deleted. Safe to run more than once.
-- Run this BEFORE the app update that reads beta_until.
--
-- To undo: run 20261102_trial_on_tap.sql section 2 and the _effective_tier block
-- from 20260930_account_locks.sql, then (optional) drop the column.

-- ── 1. The column ─────────────────────────────────────────────────────────────
alter table public.profiles add column if not exists beta_until date;

-- ── 2. The guard: beta_until is dashboard-only ───────────────────────────────
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
            'stripe_customer_id', null,
            'beta_until', null
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
    -- Plank Owner beta date: set only by Shawn in the dashboard (T31).
    if o ? 'beta_until' and (n->'beta_until') is distinct from (o->'beta_until') then
        patch := patch || jsonb_build_object('beta_until', o->'beta_until');
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

-- ── 3. Usage limits treat a live beta as Chief ───────────────────────────────
create or replace function public._effective_tier(uid uuid)
returns text
language sql
stable
security definer
set search_path = public
as $$
    select case
             when p.beta_until is not null and p.beta_until >= current_date then 'chief'
             when p.tier = 'trial' and p.trial_start is not null
                  and p.trial_start < now() - interval '3 days' then 'free'
             else coalesce(p.tier, 'free')
           end
    from public.profiles p where p.id = uid
$$;

revoke all on function public._effective_tier(uuid) from public, anon, authenticated;
