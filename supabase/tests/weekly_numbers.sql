\set ON_ERROR_STOP 1
-- weekly_numbers() / weekly_signups_private() (20261105_weekly_numbers.sql) on a real
-- Postgres. Needs stub.sql + every migration + a score_history table (the live one
-- was made in the dashboard; tests/stub_score_history.sql makes the same shape).
\o /dev/null
insert into auth.users (id, email, raw_user_meta_data) values
  ('00000000-0000-0000-0000-0000000000b1','new1@x.com','{"full_name":"Pat Sailor"}'),
  ('00000000-0000-0000-0000-0000000000b2','shawn+srctest@x.com','{}'),
  ('00000000-0000-0000-0000-0000000000b3','old@x.com','{}');
insert into public.profiles (id, email, tier, trial_start, created_at, heard_from, signup_src, stripe_customer_id) values
  ('00000000-0000-0000-0000-0000000000b1','new1@x.com','trial', now() - interval '1 day', now() - interval '2 days','instagram','ig', null),
  ('00000000-0000-0000-0000-0000000000b2','shawn+srctest@x.com','free', null, now() - interval '1 day', 'tiktok','tt', null),
  ('00000000-0000-0000-0000-0000000000b3','old@x.com','chief', null, now() - interval '30 days', null, null, 'cus_1');
-- one placement test (3 topic rows in one insert), one labelled mock, one old unlabelled mock
insert into public.score_history (user_id, date, topic, score, total, pct, source) values
  ('00000000-0000-0000-0000-0000000000b1','Nov 05','A',3,5,60,'placement'),
  ('00000000-0000-0000-0000-0000000000b1','Nov 05','B',4,5,80,'placement'),
  ('00000000-0000-0000-0000-0000000000b1','Nov 05','C',2,5,40,'placement');
insert into public.score_history (user_id, date, topic, score, total, pct, source) values
  ('00000000-0000-0000-0000-0000000000b1','Nov 05','A',8,10,80,'mock');
insert into public.score_history (user_id, date, topic, score, total, pct) values
  ('00000000-0000-0000-0000-0000000000b3','Nov 04','A',7,10,70);
create temp table results (name text, ok boolean);
create function pg_temp.v(s text, l text) returns bigint language sql as
  $$ select value from public.weekly_numbers(7) where section = s and line = l $$;
insert into results select 'new accounts leave out + test addresses', pg_temp.v('new accounts','total') = 1;
insert into results select 'by heard_from', pg_temp.v('new accounts by "How did you hear"','instagram') = 1;
insert into results select 'by link code', pg_temp.v('new accounts by link code','ig') = 1;
insert into results select 'trials started', pg_temp.v('activity','trials started') = 1;
insert into results select 'placement counted once (3 rows = 1 test)', pg_temp.v('activity','placement tests finished') = 1;
insert into results select 'mock exams: labelled + old unlabelled', pg_temp.v('activity','mock exams finished') = 2;
insert into results select 'plans right now', pg_temp.v('plans right now','chief') = 1 and pg_temp.v('plans right now','linked to Stripe') = 1;
insert into results select 'no email anywhere in the counts',
  not exists (select 1 from public.weekly_numbers(7) where line like '%@%');
insert into results select 'private list has the Google name and flags test accounts',
  (select name from public.weekly_signups_private(7) where email = 'new1@x.com') = 'Pat Sailor'
  and (select test_account from public.weekly_signups_private(7) where email like 'shawn+%');
insert into results select 'private list is only the last 7 days',
  not exists (select 1 from public.weekly_signups_private(7) where email = 'old@x.com');
insert into results select 'a bad label is refused', true;
do $$ begin
  begin insert into public.score_history (user_id, date, topic, score, total, pct, source)
        values ('00000000-0000-0000-0000-0000000000b1','x','x',1,1,100,'hacked');
        update results set ok = false where name = 'a bad label is refused';
  exception when check_violation then null; end;
end $$;
grant all on results to authenticated;
set role authenticated;
do $$ begin
  begin perform * from public.weekly_numbers(7); insert into results values ('sailors can''t run the counts', false);
  exception when insufficient_privilege then insert into results values ('sailors can''t run the counts', true); end;
  begin perform * from public.weekly_signups_private(7); insert into results values ('sailors can''t see the private list', false);
  exception when insufficient_privilege then insert into results values ('sailors can''t see the private list', true); end;
end $$;
reset role;
\o
select name, ok from results;
select case when bool_and(ok) then 'ALL PASS (' || count(*) || ')'
            else 'FAILED: ' || string_agg(name, '; ') filter (where not ok) end from results;
