-- The Weekly Five: a timed 5-question quiz inside Score Surge (T49 + T47, 5 Nov 2026).
--
-- What this does, in plain English:
--   Every week Marketing picks 5 verified PS questions. Sailors with any account,
--   free included, get ONE try per week. Each question has 20 seconds, timed by the
--   DATABASE clock (now()), not the phone, so refreshing the page can't restart it.
--   An answer that arrives late counts as wrong. Score = correct answers; a tie goes
--   to the shorter total time, then the earlier finish.
--
--   The answer key lives in a table sailors can't read. A question's number is only
--   handed out when its 20 seconds start. Nobody sees which answers were right until
--   the week closes, so a finished player can't pass the letters to a friend.
--
--   Also (T47): profiles.comp_until, a dashboard-only date. While today is on or
--   before it, the sailor gets the Chief plan free, with no "Plank Owner" label.
--   That's how Shawn hands out the prize: Table editor > profiles > the winner's
--   row > comp_until = a date 30 days out.
--   And the sign-up code ?src=w5 ("Weekly Five challenge") is added to the two
--   lists the database checks.
--
-- Additions only. No existing data is changed or deleted. Safe to run more than once.
-- Run this BEFORE the app update that shows the Weekly Five.
--
-- Weeks and their questions are NOT in this file. Each week's set is its own small
-- file in supabase/weekly_five/ (made by `python weekly_five.py seed`), pasted and
-- run the same way.
--
-- New tables follow the Supabase GRANT rule (30 Oct 2026): access is granted
-- explicitly below, and taken away explicitly where sailors must not have it.

-- ── 1. Weeks ─────────────────────────────────────────────────────────────────
create table if not exists public.w5_weeks (
    week_id   text primary key,              -- the Friday it opens, e.g. '2026-11-20'
    opens_at  timestamptz not null,
    closes_at timestamptz not null,
    created_at timestamptz not null default now(),
    constraint w5_weeks_window check (closes_at > opens_at)
);
alter table public.w5_weeks enable row level security;
drop policy if exists w5_weeks_read on public.w5_weeks;
create policy w5_weeks_read on public.w5_weeks for select to authenticated using (true);
revoke all on public.w5_weeks from anon, authenticated;
grant select on public.w5_weeks to authenticated;
grant select, insert, update, delete on public.w5_weeks to service_role;

-- ── 2. The answer key (no sailor can read this table) ────────────────────────
create table if not exists public.w5_key (
    week_id     text not null references public.w5_weeks on delete cascade,
    idx         int  not null check (idx between 1 and 5),
    question_id text not null,               -- the bank's ID, e.g. 'ADV002'
    correct     text not null check (correct in ('A', 'B', 'C', 'D')),
    -- A question proven wrong is thrown out for everyone and the week re-scored
    -- (official rules): Shawn ticks this box in the dashboard.
    voided      boolean not null default false,
    primary key (week_id, idx),
    unique (week_id, question_id)
);
alter table public.w5_key enable row level security;   -- and no policies at all
revoke all on public.w5_key from anon, authenticated;
grant select, insert, update, delete on public.w5_key to service_role;

-- ── 3. Attempts and answers (written only by the functions below) ────────────
create table if not exists public.w5_attempts (
    user_id     uuid not null references auth.users on delete cascade,
    week_id     text not null references public.w5_weeks on delete cascade,
    started_at  timestamptz not null default now(),
    finished_at timestamptz,
    handle      text,                         -- leaderboard name, only if show_handle
    show_handle boolean not null default false,
    primary key (user_id, week_id)
);
alter table public.w5_attempts enable row level security;
drop policy if exists w5_attempts_own on public.w5_attempts;
create policy w5_attempts_own on public.w5_attempts for select to authenticated
    using (user_id = auth.uid());
revoke all on public.w5_attempts from anon, authenticated;
grant select on public.w5_attempts to authenticated;
grant select, insert, update, delete on public.w5_attempts to service_role;

create table if not exists public.w5_answers (
    user_id     uuid not null,
    week_id     text not null,
    idx         int  not null check (idx between 1 and 5),
    question_id text not null,
    shown_at    timestamptz not null default now(),
    answered_at timestamptz,
    choice      text check (choice in ('A', 'B', 'C', 'D')),
    is_correct  boolean,
    ms          int,
    primary key (user_id, week_id, idx),
    foreign key (user_id, week_id) references public.w5_attempts on delete cascade
);
alter table public.w5_answers enable row level security;   -- no policies: reading
revoke all on public.w5_answers from anon, authenticated;   -- it mid-week would
grant select, insert, update, delete on public.w5_answers to service_role; -- leak answers

-- ── 4. The rules, in one place ───────────────────────────────────────────────
create or replace function public._w5_seconds() returns int
language sql immutable as $$ select 20 $$;

-- Phones and networks take a moment; an answer up to 1.5 s past the 20 still counts.
create or replace function public._w5_grace_ms() returns int
language sql immutable as $$ select 1500 $$;

-- The open week right now, if any.
create or replace function public._w5_open_week() returns text
language sql stable security definer set search_path = public as $$
    select week_id from public.w5_weeks
     where opens_at <= now() and closes_at > now()
     order by opens_at desc limit 1
$$;

-- Score for one attempt: voided questions don't count; unanswered or late = wrong;
-- time = each answered question's time (capped at 20 s), 20 s for any not answered.
create or replace function public._w5_score(p_user uuid, p_week text)
returns table (score int, total_ms int, answered int)
language sql stable security definer set search_path = public as $$
    with k as (select idx from public.w5_key where week_id = p_week and not voided),
    a as (select k.idx, w.is_correct, w.ms, w.answered_at
            from k left join public.w5_answers w
              on w.user_id = p_user and w.week_id = p_week and w.idx = k.idx)
    select count(*) filter (where is_correct)::int,
           coalesce(sum(case when answered_at is null then public._w5_seconds() * 1000
                             else least(ms, public._w5_seconds() * 1000) end), 0)::int,
           count(*) filter (where answered_at is not null)::int
      from a
$$;

-- ── 5. What the app calls ────────────────────────────────────────────────────
-- Weeks worth showing: open now, opening soon, or closed in the last 8 days.
create or replace function public.w5_weeks_now()
returns table (week_id text, opens_at timestamptz, closes_at timestamptz, status text)
language sql stable security definer set search_path = public as $$
    select w.week_id, w.opens_at, w.closes_at,
           case when now() < w.opens_at then 'upcoming'
                when now() < w.closes_at then 'open' else 'closed' end
      from public.w5_weeks w
     where w.closes_at > now() - interval '8 days'
       and w.opens_at < now() + interval '8 days'
     order by w.opens_at
$$;

-- Where this sailor stands in a week. Never says whether an answer was right
-- before the week closes.
create or replace function public.w5_state(p_week text)
returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
    uid uuid := auth.uid();
    wk public.w5_weeks;
    att public.w5_attempts;
    cur public.w5_answers;
    n_answered int;
    n_questions int;
    s record;
    st text;
begin
    if uid is null then return jsonb_build_object('status', 'login'); end if;
    select * into wk from public.w5_weeks where week_id = p_week;
    if not found then return jsonb_build_object('status', 'none'); end if;
    st := case when now() < wk.opens_at then 'upcoming'
               when now() < wk.closes_at then 'open' else 'closed' end;
    select count(*) into n_questions from public.w5_key where week_id = p_week;
    select * into att from public.w5_attempts where user_id = uid and week_id = p_week;
    if not found then
        return jsonb_build_object('status', st, 'started', false,
                                  'questions', n_questions,
                                  'opens_at', wk.opens_at, 'closes_at', wk.closes_at);
    end if;
    select count(*) into n_answered from public.w5_answers
     where user_id = uid and week_id = p_week and answered_at is not null;
    select * into cur from public.w5_answers
     where user_id = uid and week_id = p_week and answered_at is null
     order by idx limit 1;
    select * into s from public._w5_score(uid, p_week);
    return jsonb_build_object(
        'status', st, 'started', true, 'questions', n_questions,
        'opens_at', wk.opens_at, 'closes_at', wk.closes_at,
        'finished', att.finished_at is not null,
        'answered', n_answered,
        'next_idx', case when cur.idx is not null then cur.idx else n_answered + 1 end,
        'current', case when cur.idx is null then null else jsonb_build_object(
            'idx', cur.idx, 'question_id', cur.question_id,
            'ms_left', greatest(0, public._w5_seconds() * 1000
                - (extract(epoch from (now() - cur.shown_at)) * 1000)::int)) end,
        -- the number right is shown once they finish; WHICH ones only after close
        'score', case when att.finished_at is not null then s.score end,
        'total_ms', case when att.finished_at is not null then s.total_ms end
    );
end;
$$;

-- Start this week's one attempt (agreeing to the rules is checked in the app).
create or replace function public.w5_start(p_week text, p_handle text, p_show_handle boolean)
returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare
    uid uuid := auth.uid();
    h text := nullif(btrim(coalesce(p_handle, '')), '');
begin
    if uid is null then return jsonb_build_object('status', 'login'); end if;
    if public._w5_open_week() is distinct from p_week then
        return public.w5_state(p_week);
    end if;
    if h is not null and h !~ '^@?[A-Za-z0-9_.]{2,30}$' then h := null; end if;
    insert into public.w5_attempts (user_id, week_id, handle, show_handle)
    values (uid, p_week, h, coalesce(p_show_handle, false) and h is not null)
    on conflict (user_id, week_id) do nothing;          -- one attempt, ever
    return public.w5_state(p_week);
end;
$$;

-- Show question p_idx: this is the moment its 20 seconds start. Asking again
-- (a refresh) returns the same start time, so the clock keeps running.
create or replace function public.w5_show(p_week text, p_idx int)
returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare
    uid uuid := auth.uid();
    att public.w5_attempts;
    k public.w5_key;
    expected int;
begin
    if uid is null then return jsonb_build_object('error', 'login'); end if;
    select * into att from public.w5_attempts where user_id = uid and week_id = p_week;
    if not found then return jsonb_build_object('error', 'not started'); end if;
    if att.finished_at is not null then return jsonb_build_object('error', 'finished'); end if;
    -- Questions go strictly in order: the next one is the first not yet answered.
    select coalesce(min(idx), 0) into expected from public.w5_answers
     where user_id = uid and week_id = p_week and answered_at is null;
    if expected = 0 then
        select count(*) + 1 into expected from public.w5_answers
         where user_id = uid and week_id = p_week;
        -- A new question may only start while the week is open.
        if public._w5_open_week() is distinct from p_week then
            return jsonb_build_object('error', 'closed');
        end if;
    end if;
    if p_idx is distinct from expected then
        return jsonb_build_object('error', 'out of order', 'next_idx', expected);
    end if;
    select * into k from public.w5_key where week_id = p_week and idx = p_idx;
    if not found then return jsonb_build_object('error', 'no such question'); end if;
    insert into public.w5_answers (user_id, week_id, idx, question_id)
    values (uid, p_week, p_idx, k.question_id)
    on conflict (user_id, week_id, idx) do nothing;
    return public.w5_state(p_week) -> 'current';
end;
$$;

-- Lock in an answer (p_choice null = ran out of time). Late = wrong. Says nothing
-- about whether it was right.
create or replace function public.w5_answer(p_week text, p_idx int, p_choice text)
returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare
    uid uuid := auth.uid();
    a public.w5_answers;
    k public.w5_key;
    took int;
    late boolean;
    c text := upper(nullif(btrim(coalesce(p_choice, '')), ''));
    n_questions int;
    n_answered int;
begin
    if uid is null then return jsonb_build_object('error', 'login'); end if;
    select * into a from public.w5_answers
     where user_id = uid and week_id = p_week and idx = p_idx for update;
    if not found then return jsonb_build_object('error', 'not shown'); end if;
    if a.answered_at is null then
        if c is not null and c not in ('A', 'B', 'C', 'D') then c := null; end if;
        took := (extract(epoch from (now() - a.shown_at)) * 1000)::int;
        late := took > public._w5_seconds() * 1000 + public._w5_grace_ms();
        select * into k from public.w5_key where week_id = p_week and idx = p_idx;
        update public.w5_answers
           set answered_at = now(),
               choice = case when late then null else c end,
               is_correct = (not late) and c is not null and c = k.correct,
               ms = took
         where user_id = uid and week_id = p_week and idx = p_idx;
    end if;
    select count(*) into n_questions from public.w5_key where week_id = p_week;
    select count(*) into n_answered from public.w5_answers
     where user_id = uid and week_id = p_week and answered_at is not null;
    if n_answered >= n_questions then
        update public.w5_attempts set finished_at = coalesce(finished_at, now())
         where user_id = uid and week_id = p_week;
    end if;
    return public.w5_state(p_week);
end;
$$;

-- The leaderboard: names only for sailors who said yes, otherwise "Anonymous
-- sailor". While the week is open it lists finished attempts only.
create or replace function public.w5_leaderboard(p_week text, p_limit int default 10)
returns table (place int, name text, score int, seconds numeric, is_me boolean)
language sql stable security definer set search_path = public as $$
    with closed as (select coalesce((select closes_at <= now() from public.w5_weeks
                                      where week_id = p_week), false) as c),
    r as (
        select t.user_id, t.finished_at,
               case when t.show_handle and t.handle is not null then t.handle
                    else 'Anonymous sailor' end as name,
               s.score, s.total_ms
          from public.w5_attempts t
          cross join lateral public._w5_score(t.user_id, t.week_id) s
         where t.week_id = p_week
           and (t.finished_at is not null or (select c from closed))
    )
    select (row_number() over (order by score desc, total_ms asc,
                                        finished_at asc nulls last))::int,
           name, score, round(total_ms / 1000.0, 1), user_id = auth.uid()
      from r
     order by 1
     limit greatest(1, least(coalesce(p_limit, 10), 50))
$$;

-- After the week closes: the right answers, this sailor's picks, and how everyone
-- did on each question.
create or replace function public.w5_reveal(p_week text)
returns table (idx int, question_id text, correct text, voided boolean,
               my_choice text, my_correct boolean, pct_correct int, avg_seconds numeric)
language sql stable security definer set search_path = public as $$
    select k.idx, k.question_id, k.correct, k.voided,
           mine.choice, mine.is_correct,
           (100.0 * count(*) filter (where a.is_correct)
                  / nullif(count(*) filter (where a.answered_at is not null), 0))::int,
           round(avg(least(a.ms, public._w5_seconds() * 1000))
                 filter (where a.answered_at is not null) / 1000.0, 1)
      from public.w5_key k
      join public.w5_weeks w on w.week_id = k.week_id and w.closes_at <= now()
      left join public.w5_answers a on a.week_id = k.week_id and a.idx = k.idx
      left join public.w5_answers mine on mine.week_id = k.week_id and mine.idx = k.idx
                                      and mine.user_id = auth.uid()
     where k.week_id = p_week
     group by k.idx, k.question_id, k.correct, k.voided, mine.choice, mine.is_correct
     order by k.idx
$$;

-- ── 6. Reports for Shawn (SQL editor only; sailors can't run these) ──────────
-- Counts and opted-in handles only, safe to share with Marketing:
--   select * from public.w5_report('2026-11-20');
create or replace function public.w5_report(p_week text)
returns table (line text, value text)
language sql stable security definer set search_path = public as $$
    with t as (select a.*, s.score, s.total_ms
                 from public.w5_attempts a
                 cross join lateral public._w5_score(a.user_id, a.week_id) s
                where a.week_id = p_week)
    select 'entries', count(*)::text from t
    union all select 'finished all questions', count(*) filter (where finished_at is not null)::text from t
    union all select 'perfect scores', count(*) filter (where score = (select count(*) from public.w5_key
                                         where week_id = p_week and not voided))::text from t
    union all select 'average score', coalesce(round(avg(score), 2)::text, '-') from t
    union all select 'handles shown (opted in)', coalesce(string_agg(handle, ', ' order by score desc, total_ms),
                                         '(none)') from t where show_handle
    union all
    select 'Q' || r.idx || ' ' || r.question_id,
           coalesce(r.pct_correct::text, '-') || '% right, avg ' || coalesce(r.avg_seconds::text, '-') || ' s'
           || case when r.voided then ' (VOIDED)'
                   when r.pct_correct < 40 then '  <- flag for Question Bank review' else '' end
      from public.w5_reveal(p_week) r
$$;

-- Top finishers WITH emails, for sending the prize. Shawn only, never Marketing:
--   select * from public.w5_winners_private('2026-11-20');
create or replace function public.w5_winners_private(p_week text)
returns table (place int, email text, handle text, score int, seconds numeric, finished_at timestamptz)
language sql stable security definer set search_path = public as $$
    select (row_number() over (order by s.score desc, s.total_ms asc,
                                        t.finished_at asc nulls last))::int,
           u.email::text, t.handle, s.score, round(s.total_ms / 1000.0, 1), t.finished_at
      from public.w5_attempts t
      join auth.users u on u.id = t.user_id
      cross join lateral public._w5_score(t.user_id, t.week_id) s
     where t.week_id = p_week
     order by 1
     limit 10
$$;

-- Who may call what.
revoke all on function public._w5_open_week() from public, anon, authenticated;
revoke all on function public._w5_score(uuid, text) from public, anon, authenticated;
revoke all on function public.w5_report(text) from public, anon, authenticated;
revoke all on function public.w5_winners_private(text) from public, anon, authenticated;
revoke all on function public.w5_weeks_now() from public, anon;
revoke all on function public.w5_state(text) from public, anon;
revoke all on function public.w5_start(text, text, boolean) from public, anon;
revoke all on function public.w5_show(text, int) from public, anon;
revoke all on function public.w5_answer(text, int, text) from public, anon;
revoke all on function public.w5_leaderboard(text, int) from public, anon;
revoke all on function public.w5_reveal(text) from public, anon;
grant execute on function public.w5_weeks_now() to authenticated, service_role;
grant execute on function public.w5_state(text) to authenticated, service_role;
grant execute on function public.w5_start(text, text, boolean) to authenticated, service_role;
grant execute on function public.w5_show(text, int) to authenticated, service_role;
grant execute on function public.w5_answer(text, int, text) to authenticated, service_role;
grant execute on function public.w5_leaderboard(text, int) to authenticated, service_role;
grant execute on function public.w5_reveal(text) to authenticated, service_role;
grant execute on function public.w5_report(text) to service_role;
grant execute on function public.w5_winners_private(text) to service_role;

-- ── 7. Prize access: comp_until (T47) ────────────────────────────────────────
alter table public.profiles add column if not exists comp_until date;

-- The guard: same as 20261103_plank_owner_beta.sql, plus comp_until is
-- dashboard-only too.
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
            'beta_until', null,
            'comp_until', null
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
    -- Prize access date: set only by Shawn in the dashboard (T47).
    if o ? 'comp_until' and (n->'comp_until') is distinct from (o->'comp_until') then
        patch := patch || jsonb_build_object('comp_until', o->'comp_until');
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

-- Usage limits treat live prize access like a live beta: Chief.
create or replace function public._effective_tier(uid uuid)
returns text
language sql
stable
security definer
set search_path = public
as $$
    select case
             when p.beta_until is not null and p.beta_until >= current_date then 'chief'
             when p.comp_until is not null and p.comp_until >= current_date then 'chief'
             when p.tier = 'trial' and p.trial_start is not null
                  and p.trial_start < now() - interval '3 days' then 'free'
             else coalesce(p.tier, 'free')
           end
    from public.profiles p where p.id = uid
$$;

revoke all on function public._effective_tier(uuid) from public, anon, authenticated;

-- ── 8. The ?src=w5 sign-up code ──────────────────────────────────────────────
-- Same lists as 20261102_signup_source.sql, plus 'weekly_five' / 'w5'.
alter table public.profiles drop constraint if exists profiles_heard_from_check;
alter table public.profiles add constraint profiles_heard_from_check
    check (heard_from is null or heard_from in
        ('facebook', 'instagram', 'tiktok', 'youtube', 'reddit', 'discord', 'google',
         'podcast', 'card', 'weekly_five', 'friend', 'other'));
alter table public.profiles drop constraint if exists profiles_signup_src_check;
alter table public.profiles add constraint profiles_signup_src_check
    check (signup_src is null or signup_src in
        ('fb', 'ig', 'tt', 'yt', 'rd', 'dc', 'pts', 'card', 'w5'));
