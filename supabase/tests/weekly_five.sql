\set ON_ERROR_STOP 1
-- The Weekly Five (20261105_weekly_five.sql) against a real Postgres.
-- Run after stub.sql and every migration (see the header of weekly_five.py).
-- Prints one row per check; the last line says ALL PASS or lists what failed.

insert into auth.users (id, email) values
  ('00000000-0000-0000-0000-0000000000a1','p1@x.com'),
  ('00000000-0000-0000-0000-0000000000a2','p2@x.com'),
  ('00000000-0000-0000-0000-0000000000a3','p3@x.com');
insert into public.profiles (id, email, tier) values
  ('00000000-0000-0000-0000-0000000000a1','p1@x.com','free'),
  ('00000000-0000-0000-0000-0000000000a2','p2@x.com','free'),
  ('00000000-0000-0000-0000-0000000000a3','p3@x.com','chief');

-- An open week and a closed one.
insert into public.w5_weeks values ('2026-11-20', now() - interval '1 hour', now() + interval '2 days');
insert into public.w5_key (week_id, idx, question_id, correct) values
  ('2026-11-20',1,'Q1','A'),('2026-11-20',2,'Q2','B'),('2026-11-20',3,'Q3','C'),
  ('2026-11-20',4,'Q4','D'),('2026-11-20',5,'Q5','A');
insert into public.w5_weeks values ('2026-11-13', now() - interval '8 days', now() - interval '1 day');
insert into public.w5_key (week_id, idx, question_id, correct) values
  ('2026-11-13',1,'Q6','A'),('2026-11-13',2,'Q7','B'),('2026-11-13',3,'Q8','C'),
  ('2026-11-13',4,'Q9','D'),('2026-11-13',5,'Q10','A');

\o /dev/null
create temp table results (name text, ok boolean);
grant all on results to authenticated, anon, service_role;

create function pg_temp.as_user(u text) returns void language sql as $$
  select set_config('request.jwt.claim.sub', u, false),
         set_config('request.jwt.claims', json_build_object('sub', u)::text, false);
$$;

-- ── Sailor 1 plays ──────────────────────────────────────
set role authenticated;
select pg_temp.as_user('00000000-0000-0000-0000-0000000000a1');

insert into results select 'nothing started yet', (public.w5_state('2026-11-20')->>'started')::boolean = false;
insert into results select 'start works and names the 5 questions',
  (public.w5_start('2026-11-20', '@squared.away', true)->>'questions')::int = 5;
insert into results select 'starting twice keeps the one attempt',
  (public.w5_start('2026-11-20', 'other_name', false)->>'started')::boolean
  and (select handle from public.w5_attempts where user_id = auth.uid()) = '@squared.away';
insert into results select 'question 2 can''t be opened before question 1',
  public.w5_show('2026-11-20', 2)->>'error' = 'out of order';
insert into results select 'question 1 opens with about 20 s left',
  (public.w5_show('2026-11-20', 1)->>'ms_left')::int between 19000 and 20000;
insert into results select 'its number is handed out only now',
  public.w5_state('2026-11-20')->'current'->>'question_id' = 'Q1';
insert into results select 'answering says nothing about right or wrong',
  not (public.w5_answer('2026-11-20', 1, 'a') ? 'is_correct')
  and (public.w5_state('2026-11-20')->>'score') is null;
insert into results select 'an answer can''t be changed',
  (public.w5_answer('2026-11-20', 1, 'B')->>'answered')::int = 1;

-- Q2: wait too long (the test backdates the start; a sailor can't).
select public.w5_show('2026-11-20', 2);
reset role;
update public.w5_answers set shown_at = now() - interval '25 seconds'
 where user_id = '00000000-0000-0000-0000-0000000000a1' and idx = 2;
set role authenticated;
select pg_temp.as_user('00000000-0000-0000-0000-0000000000a1');
insert into results select 'a refresh after the time ran out shows 0 s left',
  (public.w5_show('2026-11-20', 2)->>'ms_left')::int = 0;
select public.w5_answer('2026-11-20', 2, 'B');   -- the right letter, but late
-- Q3 right, Q4 wrong, Q5 timed out with no answer.
select public.w5_show('2026-11-20', 3); select public.w5_answer('2026-11-20', 3, 'C');
select public.w5_show('2026-11-20', 4); select public.w5_answer('2026-11-20', 4, 'A');
select public.w5_show('2026-11-20', 5); select public.w5_answer('2026-11-20', 5, null);
insert into results select 'finished: score 2 (Q1, Q3; Q2 was late)',
  (public.w5_state('2026-11-20')->>'finished')::boolean
  and (public.w5_state('2026-11-20')->>'score')::int = 2;
insert into results select 'no sixth question', public.w5_show('2026-11-20', 6)->>'error' = 'finished';
insert into results select 'the right letters stay hidden while the week is open',
  (select count(*) from public.w5_reveal('2026-11-20')) = 0;

-- Cheating attempts.
do $$ begin
  begin perform * from public.w5_key; insert into results values ('sailors can''t read the answer key', false);
  exception when insufficient_privilege then insert into results values ('sailors can''t read the answer key', true); end;
  begin perform * from public.w5_answers; insert into results values ('sailors can''t read graded answers', false);
  exception when insufficient_privilege then insert into results values ('sailors can''t read graded answers', true); end;
  begin update public.w5_attempts set finished_at = null; insert into results values ('sailors can''t edit an attempt', false);
  exception when insufficient_privilege then insert into results values ('sailors can''t edit an attempt', true); end;
  begin insert into public.w5_attempts (user_id, week_id) values (auth.uid(), '2026-11-13');
        insert into results values ('sailors can''t write attempts directly', false);
  exception when insufficient_privilege then insert into results values ('sailors can''t write attempts directly', true); end;
  begin perform * from public.w5_winners_private('2026-11-20'); insert into results values ('sailors can''t see winners'' emails', false);
  exception when insufficient_privilege then insert into results values ('sailors can''t see winners'' emails', true); end;
  begin perform * from public.w5_report('2026-11-20'); insert into results values ('sailors can''t run Shawn''s report', false);
  exception when insufficient_privilege then insert into results values ('sailors can''t run Shawn''s report', true); end;
end $$;
insert into results select 'a closed week can''t be started',
  (public.w5_start('2026-11-13', null, false)->>'started')::boolean = false
  and public.w5_state('2026-11-13')->>'status' = 'closed';

-- ── Sailor 2: perfect, no name shown ─────────────────────
select pg_temp.as_user('00000000-0000-0000-0000-0000000000a2');
select public.w5_start('2026-11-20', '<script>', true);
insert into results select 'a bad handle is never stored',
  (select handle is null and not show_handle from public.w5_attempts where user_id = auth.uid());
select public.w5_show('2026-11-20', 1); select public.w5_answer('2026-11-20', 1, 'A');
select public.w5_show('2026-11-20', 2); select public.w5_answer('2026-11-20', 2, 'B');
select public.w5_show('2026-11-20', 3); select public.w5_answer('2026-11-20', 3, 'C');
select public.w5_show('2026-11-20', 4); select public.w5_answer('2026-11-20', 4, 'D');
select public.w5_show('2026-11-20', 5); select public.w5_answer('2026-11-20', 5, 'A');
insert into results select 'sailor 2 scores 5', (public.w5_state('2026-11-20')->>'score')::int = 5;

-- ── Sailor 3 started but walked away after Q1 ────────────
select pg_temp.as_user('00000000-0000-0000-0000-0000000000a3');
select public.w5_start('2026-11-20', 'chief_k', true);
select public.w5_show('2026-11-20', 1); select public.w5_answer('2026-11-20', 1, 'A');
insert into results select 'leaderboard while open: finished players only, sailor 2 first, no name',
  (select count(*) from public.w5_leaderboard('2026-11-20')) = 2
  and (select name from public.w5_leaderboard('2026-11-20') where place = 1) = 'Anonymous sailor'
  and (select score from public.w5_leaderboard('2026-11-20') where place = 1) = 5
  and (select name from public.w5_leaderboard('2026-11-20') where place = 2) = '@squared.away';
insert into results select 'leaderboard marks my own line',
  not exists (select 1 from public.w5_leaderboard('2026-11-20') where is_me);

-- ── The week closes; a question is voided ────────────────
reset role;
update public.w5_weeks set closes_at = now() - interval '1 minute' where week_id = '2026-11-20';
update public.w5_key set voided = true where week_id = '2026-11-20' and idx = 3;
set role authenticated;
select pg_temp.as_user('00000000-0000-0000-0000-0000000000a1');
insert into results select 'after close: the right letters are shown',
  (select string_agg(correct, '' order by idx) from public.w5_reveal('2026-11-20')) = 'ABCDA';
insert into results select 'after close: my own picks are shown (late one blank)',
  (select coalesce(my_choice, '-') from public.w5_reveal('2026-11-20') where idx = 2) = '-'
  and (select my_choice from public.w5_reveal('2026-11-20') where idx = 1) = 'A';
insert into results select 'after close: % right per question (Q1 3 of 3)',
  (select pct_correct from public.w5_reveal('2026-11-20') where idx = 1) = 100;
insert into results select 'voiding Q3 re-scores everyone (sailor 1: 2 -> 1, sailor 2: 5 -> 4)',
  (public.w5_state('2026-11-20')->>'score')::int = 1
  and (select score from public.w5_leaderboard('2026-11-20') where place = 1) = 4;
insert into results select 'after close the walk-away counts too (3 on the board)',
  (select count(*) from public.w5_leaderboard('2026-11-20')) = 3;
insert into results select 'no new question after close',
  public.w5_show('2026-11-20', 1)->>'error' = 'finished';
select pg_temp.as_user('00000000-0000-0000-0000-0000000000a3');
insert into results select 'the walk-away can''t start Q2 after close',
  public.w5_show('2026-11-20', 2)->>'error' = 'closed';

-- ── Shawn (dashboard / SQL editor) ───────────────────────
reset role;
insert into results select 'Shawn''s report counts 3 entries',
  (select value from public.w5_report('2026-11-20') where line = 'entries') = '3';
insert into results select 'the report lists opted-in handles only',
  (select value from public.w5_report('2026-11-20') where line = 'handles shown (opted in)')
  = '@squared.away, chief_k';
insert into results select 'the report never shows an email',
  not exists (select 1 from public.w5_report('2026-11-20') where value like '%@x.com%');
insert into results select 'winners list has the email for the prize',
  (select email from public.w5_winners_private('2026-11-20') where place = 1) = 'p2@x.com';

-- ── Prize access (comp_until) ────────────────────────────
update public.profiles set comp_until = current_date + 30 where email = 'p2@x.com';
insert into results select 'prize access counts as Chief for usage limits',
  public._effective_tier('00000000-0000-0000-0000-0000000000a2') = 'chief';
update public.profiles set comp_until = current_date - 1 where email = 'p2@x.com';
insert into results select 'expired prize access drops back to the real plan',
  public._effective_tier('00000000-0000-0000-0000-0000000000a2') = 'free';
set role authenticated;
select pg_temp.as_user('00000000-0000-0000-0000-0000000000a1');
update public.profiles set comp_until = '2030-01-01' where id = auth.uid();
insert into results select 'a sailor can''t give themselves prize access',
  (select comp_until is null from public.profiles where id = auth.uid());
update public.profiles set signup_src = 'w5' where id = auth.uid();
insert into results select 'the w5 sign-up code is accepted',
  (select signup_src = 'w5' from public.profiles where id = auth.uid());
reset role;

\o
select name, ok from results;
select case when bool_and(ok) then 'ALL PASS (' || count(*) || ')'
            else 'FAILED: ' || string_agg(name, '; ') filter (where not ok) end
  from results;
