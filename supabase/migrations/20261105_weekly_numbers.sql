-- Weekly numbers for Marketing's Monday scorecard (T29) and Shawn's private list of
-- new sign-ups (T44). 5 Nov 2026.
--
-- What this does, in plain English:
--   1. score_history gets an optional "source" label: 'placement' or 'mock'. The app
--      fills it from now on, so placement tests can be counted. Older rows stay
--      blank; the report estimates those (a placement test saves several topic rows
--      in one go, a mock exam saves one row).
--   2. weekly_numbers(days): COUNTS ONLY, safe to paste into Marketing's scorecard.
--      New accounts by "How did you hear" answer and by link code, trials started,
--      placement tests and mock exams finished, Weekly Five entries, and how many
--      accounts are on each plan right now.
--   3. weekly_signups_private(days): each new account with email, name (only Google
--      sign-ins carry one), sign-up time, rating, answers and plan. Personal data:
--      for Shawn only, never Marketing docs, the repo or anything public.
--
--   Both run in Supabase > SQL Editor:
--     select * from public.weekly_numbers(7);
--     select * from public.weekly_signups_private(7);
--   Sailors can't run either one (no Data API access).
--
--   Not in here because the database doesn't know it: new paid upgrades,
--   cancellations and refunds by date. Those come from Stripe (Payments and
--   Subscriptions pages).
--
-- Additions only. No data is changed or deleted. Safe to run more than once.

-- ── 1. score_history.source ──────────────────────────────────────────────────
alter table public.score_history add column if not exists source text;
alter table public.score_history drop constraint if exists score_history_source_check;
alter table public.score_history add constraint score_history_source_check
    check (source is null or source in ('placement', 'mock'));

-- ── 2. Counts for the Monday scorecard ───────────────────────────────────────
create or replace function public.weekly_numbers(p_days int default 7)
returns table (section text, line text, value bigint)
language sql stable security definer set search_path = public as $$
    with win as (select now() - make_interval(days => greatest(1, coalesce(p_days, 7))) as since),
    -- Shawn's own test sign-ups use "+" addresses (name+test@...); left out of counts.
    new_acc as (
        select p.* from public.profiles p, win
         where p.created_at >= win.since
           and coalesce(p.email, '') not like '%+%@%'
    ),
    sh as (
        select s.user_id, s.created_at, s.source, count(*) over (partition by s.user_id, s.created_at) as n_rows
          from public.score_history s, win
         where s.created_at >= win.since
    ),
    batches as (
        select distinct user_id, created_at,
               coalesce(source, case when n_rows > 1 then 'placement' else 'mock' end) as kind
          from sh
    )
    select 'new accounts', 'total', count(*) from new_acc
    union all
    select 'new accounts by "How did you hear"', coalesce(heard_from, '(skipped)'), count(*)
      from new_acc group by heard_from
    union all
    select 'new accounts by link code', coalesce(signup_src, '(no link code)'), count(*)
      from new_acc group by signup_src
    union all
    select 'activity', 'trials started', count(*)
      from public.profiles p, win where p.trial_start >= win.since
    union all
    select 'activity', 'placement tests finished', count(*) from batches where kind = 'placement'
    union all
    select 'activity', 'mock exams finished', count(*) from batches where kind = 'mock'
    union all
    select 'activity', 'Weekly Five entries', count(*)
      from public.w5_attempts a, win where a.started_at >= win.since
    union all
    select 'plans right now', coalesce(tier, '(none)'), count(*)
      from public.profiles group by tier
    union all
    select 'plans right now', 'Plank Owner beta (live)', count(*)
      from public.profiles where beta_until >= current_date
    union all
    select 'plans right now', 'Weekly Five prize (live)', count(*)
      from public.profiles where comp_until >= current_date
    union all
    select 'plans right now', 'linked to Stripe', count(*)
      from public.profiles where stripe_customer_id is not null
$$;

-- ── 3. Shawn's private list ──────────────────────────────────────────────────
create or replace function public.weekly_signups_private(p_days int default 7)
returns table (signed_up timestamptz, email text, name text, rating text,
               heard_from text, link_code text, plan text, test_account boolean)
language sql stable security definer set search_path = public, auth as $$
    select p.created_at, p.email,
           nullif(coalesce(u.raw_user_meta_data->>'full_name', u.raw_user_meta_data->>'name'), ''),
           p.rating, p.heard_from, p.signup_src,
           case when p.beta_until >= current_date then 'chief (Plank Owner)'
                when p.comp_until >= current_date then 'chief (Weekly Five prize)'
                else p.tier end,
           coalesce(p.email, '') like '%+%@%'
      from public.profiles p
      left join auth.users u on u.id = p.id
     where p.created_at >= now() - make_interval(days => greatest(1, coalesce(p_days, 7)))
     order by p.created_at desc
$$;

revoke all on function public.weekly_numbers(int) from public, anon, authenticated;
revoke all on function public.weekly_signups_private(int) from public, anon, authenticated;
grant execute on function public.weekly_numbers(int) to service_role;
grant execute on function public.weekly_signups_private(int) to service_role;
