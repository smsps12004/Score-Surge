-- Signup source: new answers + link codes — 2 Nov 2026 (T2, Marketing's list of 28 Oct).
--
-- What this does, in plain English:
--   1. Lets the "How did you hear about us?" answer also be YouTube, Podcast, or
--      Business card / QR code (the old eight answers still work).
--   2. Adds one optional column, signup_src, holding the short code from the link
--      the sailor arrived through (app.strategicsailor.com/?src=ig and so on). It is
--      kept even if the sailor picks a different answer, so Marketing can compare.
--   3. Copies both from the login itself for anyone who signed up before this ran.
--
-- Additions only. Nothing is dropped or deleted except the old list of allowed
-- answers, which is replaced by a longer one. Safe to run more than once.
-- The app works before this is run too: it just leaves out what the database won't
-- take yet (the answers are also kept on the login, and step 3 copies them later).
--
-- The lists below must match HEARD_FROM_CHOICES and SRC_CODES in public_site.py
-- (run_checks.py checks this).

alter table public.profiles add column if not exists heard_from text;
alter table public.profiles drop constraint if exists profiles_heard_from_check;
alter table public.profiles add constraint profiles_heard_from_check
    check (heard_from is null or heard_from in
           ('facebook', 'instagram', 'tiktok', 'youtube', 'reddit', 'discord', 'google',
            'podcast', 'card', 'friend', 'other'));

alter table public.profiles add column if not exists signup_src text;
alter table public.profiles drop constraint if exists profiles_signup_src_check;
alter table public.profiles add constraint profiles_signup_src_check
    check (signup_src is null or signup_src in
           ('fb', 'ig', 'tt', 'yt', 'rd', 'dc', 'pts', 'card'));

-- Copy answers given by sailors who signed up before this ran (stored on the login).
update public.profiles p
   set heard_from = u.raw_user_meta_data->>'heard_from'
  from auth.users u
 where u.id = p.id
   and p.heard_from is null
   and u.raw_user_meta_data->>'heard_from' in
       ('facebook', 'instagram', 'tiktok', 'youtube', 'reddit', 'discord', 'google',
        'podcast', 'card', 'friend', 'other');

update public.profiles p
   set signup_src = u.raw_user_meta_data->>'signup_src'
  from auth.users u
 where u.id = p.id
   and p.signup_src is null
   and u.raw_user_meta_data->>'signup_src' in
       ('fb', 'ig', 'tt', 'yt', 'rd', 'dc', 'pts', 'card');

-- To see the counts later (totals only, no names or emails):
--   select coalesce(heard_from, '(skipped)') as answer,
--          coalesce(signup_src, '(no link code)') as link_code,
--          count(*)
--     from public.profiles
--    group by 1, 2
--    order by 3 desc;
