-- Account locks — 30 Sep 2026.
--
-- What this does, in plain English:
--   1. Adds a locked RATING to every profile (picked once, changeable by the
--      sailor at most once every 180 days; Shawn can always change it here).
--   2. Adds ACTIVE_SESSION so only one device is signed in at a time.
--   3. Stops a sailor from editing the parts of their own profile that decide
--      what they have paid for (tier, stripe_customer_id, trial_start, email).
--      Before this, the app's own login let a signed-in sailor update their
--      whole profile row — including tier — straight from the browser session.
--      Only the Stripe webhook (service role) and the dashboard can change them
--      now. The one change a sailor's session may still make is trial -> free,
--      which the app does itself when a 3-day trial runs out.
--   4. Adds monthly usage limits (AI generations and downloads) that are counted
--      in the database, where a sailor cannot reset them. Limits live in the
--      usage_limits table so they can be changed without touching code.
--
-- Additions only. Nothing is dropped, nothing existing is deleted. Safe to run
-- more than once. Run it in the Supabase SQL editor as one script.

-- ── 1 & 2. New profile columns ────────────────────────────────────────────────
alter table public.profiles add column if not exists rating        text;
alter table public.profiles add column if not exists rating_set_at timestamptz;
alter table public.profiles add column if not exists active_session text;

-- ── 3. Protect paid-for fields from the sailor's own session ─────────────────
--
-- Works by quietly putting protected values back rather than raising an error,
-- so no existing app code path breaks — the change simply doesn't take.
-- Columns are handled through jsonb so this still compiles if one of them
-- (e.g. stripe_customer_id or email) doesn't exist on this database.
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
        -- A brand-new row from the app: it may only start as a trial or free,
        -- with the trial starting now and no Stripe link.
        patch := jsonb_build_object(
            'tier', case when n->>'tier' in ('trial', 'free') then n->>'tier' else 'trial' end,
            'trial_start', now(),
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

drop trigger if exists profiles_guard on public.profiles;
create trigger profiles_guard
    before insert or update on public.profiles
    for each row execute function public.profiles_guard();

-- A sailor's own session may not delete their profile (that would let them
-- re-insert a fresh trial). The dashboard and service role still can.
create or replace function public.profiles_no_self_delete()
returns trigger
language plpgsql
as $$
begin
    if current_user in ('authenticated', 'anon') then
        return null;  -- silently skip the delete
    end if;
    return old;
end;
$$;

drop trigger if exists profiles_no_self_delete on public.profiles;
create trigger profiles_no_self_delete
    before delete on public.profiles
    for each row execute function public.profiles_no_self_delete();

-- ── 4. Monthly usage limits ───────────────────────────────────────────────────
create table if not exists public.usage_limits (
    tier          text not null,
    kind          text not null check (kind in ('ai', 'download')),
    monthly_limit integer not null check (monthly_limit >= 0),
    primary key (tier, kind)
);

-- Starting numbers (30 Sep 2026). Edit these rows in the Table Editor to change
-- them — the app reads them live. Existing rows are left as they are.
insert into public.usage_limits (tier, kind, monthly_limit) values
    ('free',          'ai',        3),
    ('free',          'download',  5),
    ('trial',         'ai',       10),
    ('trial',         'download', 10),
    ('petty_officer', 'ai',       30),
    ('petty_officer', 'download', 30),
    ('chief',         'ai',       75),
    ('chief',         'download', 60)
on conflict (tier, kind) do nothing;

alter table public.usage_limits enable row level security;
do $$
begin
    if not exists (select 1 from pg_policies where schemaname = 'public'
                   and tablename = 'usage_limits' and policyname = 'anyone signed in reads limits') then
        create policy "anyone signed in reads limits" on public.usage_limits
            for select to authenticated using (true);
    end if;
end $$;

create table if not exists public.usage_counters (
    user_id uuid    not null references auth.users(id) on delete cascade,
    period  text    not null,  -- calendar month, 'YYYY-MM' (UTC)
    kind    text    not null check (kind in ('ai', 'download')),
    used    integer not null default 0,
    primary key (user_id, period, kind)
);

alter table public.usage_counters enable row level security;
do $$
begin
    if not exists (select 1 from pg_policies where schemaname = 'public'
                   and tablename = 'usage_counters' and policyname = 'sailors read own usage') then
        create policy "sailors read own usage" on public.usage_counters
            for select using (auth.uid() = user_id);
    end if;
end $$;
-- Deliberately NO insert/update/delete policy: a sailor cannot write their own
-- counters. Only the two functions below (which run with elevated rights) can.

-- The sailor's tier as the database sees it, with an expired trial counted as free.
create or replace function public._effective_tier(uid uuid)
returns text
language sql
stable
security definer
set search_path = public
as $$
    select case
             when p.tier = 'trial' and p.trial_start is not null
                  and p.trial_start < now() - interval '3 days' then 'free'
             else coalesce(p.tier, 'free')
           end
    from public.profiles p where p.id = uid
$$;

-- What's used and what's left this month. Read-only.
create or replace function public.get_usage()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
    uid uuid := auth.uid();
    t text;
    per text := to_char(now() at time zone 'utc', 'YYYY-MM');
    result jsonb := '{}'::jsonb;
    k text;
    lim integer;
    u integer;
begin
    if uid is null then
        raise exception 'not signed in';
    end if;
    t := coalesce(public._effective_tier(uid), 'free');
    foreach k in array array['ai', 'download'] loop
        select monthly_limit into lim from public.usage_limits where tier = t and kind = k;
        select used into u from public.usage_counters where user_id = uid and period = per and kind = k;
        result := result || jsonb_build_object(k, jsonb_build_object(
            'used', coalesce(u, 0), 'limit', coalesce(lim, 0),
            'left', greatest(coalesce(lim, 0) - coalesce(u, 0), 0)));
    end loop;
    return result || jsonb_build_object('tier', t, 'period', per);
end;
$$;

-- Spend one unit of `p_kind`. Returns allowed=false (and spends nothing) when the
-- month's limit is already reached. Atomic: two taps at once can't both slip past.
create or replace function public.consume_usage(p_kind text)
returns jsonb
language plpgsql
volatile
security definer
set search_path = public
as $$
declare
    uid uuid := auth.uid();
    t text;
    per text := to_char(now() at time zone 'utc', 'YYYY-MM');
    lim integer;
    new_used integer;
begin
    if uid is null then
        raise exception 'not signed in';
    end if;
    if p_kind not in ('ai', 'download') then
        raise exception 'unknown usage kind %', p_kind;
    end if;
    t := coalesce(public._effective_tier(uid), 'free');
    select monthly_limit into lim from public.usage_limits where tier = t and kind = p_kind;
    lim := coalesce(lim, 0);

    insert into public.usage_counters as c (user_id, period, kind, used)
    values (uid, per, p_kind, 1)
    on conflict (user_id, period, kind) do update
        set used = c.used + 1
        where c.used < lim
    returning used into new_used;

    if new_used is null or new_used > lim then
        -- Limit reached. (A first-ever insert with lim = 0 is rolled back below.)
        if new_used is not null then
            delete from public.usage_counters
             where user_id = uid and period = per and kind = p_kind and used = new_used and new_used = 1;
        end if;
        return jsonb_build_object('allowed', false, 'used', least(coalesce(new_used, lim), lim), 'limit', lim);
    end if;
    return jsonb_build_object('allowed', true, 'used', new_used, 'limit', lim,
                              'left', greatest(lim - new_used, 0));
end;
$$;

revoke all on function public.get_usage()            from public, anon;
revoke all on function public.consume_usage(text)    from public, anon;
revoke all on function public._effective_tier(uuid)  from public, anon, authenticated;
grant execute on function public.get_usage()         to authenticated;
grant execute on function public.consume_usage(text) to authenticated;
