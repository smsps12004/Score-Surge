-- Weekly Five, week of 2026-11-20 (made by `python weekly_five.py seed 2026-11-20`).
-- Opens Fri 20 Nov 2026 06:30 PM Central, closes Sun 22 Nov 2026 11:59 PM Central.
-- Questions (verified bank): ADV054, ADV077, ADV094, ADV107, ADV146.
-- Paste into Supabase > SQL Editor > Run. Safe to run twice. It never changes a week
-- that's already loaded: if this week exists with other questions it stops with an
-- error and changes nothing.
-- Needs 20261105_weekly_five.sql to have been run first.

begin;
insert into public.w5_weeks (week_id, opens_at, closes_at)
values ('2026-11-20', '2026-11-20T18:30:00-06:00', '2026-11-22T23:59:00-06:00')
on conflict (week_id) do nothing;

insert into public.w5_key (week_id, idx, question_id, correct) values
  ('2026-11-20', 1, 'ADV054', 'A'),
  ('2026-11-20', 2, 'ADV077', 'A'),
  ('2026-11-20', 3, 'ADV094', 'D'),
  ('2026-11-20', 4, 'ADV107', 'C'),
  ('2026-11-20', 5, 'ADV146', 'C')
on conflict (week_id, idx) do nothing;

do $$
begin
  if (select count(*) from public.w5_key
       where week_id = '2026-11-20' and question_id in ('ADV054', 'ADV077', 'ADV094', 'ADV107', 'ADV146')) <> 5 then
    raise exception 'Week 2026-11-20 is already loaded with different questions. Nothing changed.';
  end if;
end $$;
commit;

-- Check: should list 5 rows.
select idx, question_id from public.w5_key where week_id = '2026-11-20' order by idx;
