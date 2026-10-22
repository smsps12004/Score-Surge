-- "How did you hear about us?" — 22 Oct 2026 (marketing review item 8).
--
-- What this does, in plain English:
--   Adds one optional column to each sailor's profile holding their answer to the
--   signup question. Only the eight listed answers (or blank) can be stored, so the
--   counts stay clean. The values must match HEARD_FROM_CHOICES in public_site.py.
--
-- Additions only. Nothing is dropped or deleted. Safe to run more than once.
-- The app works before this is run too: it simply skips saving the answer on the
-- profile until the column exists (the answer is also kept on the login itself).

alter table public.profiles add column if not exists heard_from text;

alter table public.profiles drop constraint if exists profiles_heard_from_check;
alter table public.profiles add constraint profiles_heard_from_check
    check (heard_from is null or heard_from in
           ('facebook', 'instagram', 'tiktok', 'reddit', 'discord', 'google',
            'friend', 'other'));

-- Copy answers given by sailors who signed up before this ran (stored on the login).
update public.profiles p
   set heard_from = u.raw_user_meta_data->>'heard_from'
  from auth.users u
 where u.id = p.id
   and p.heard_from is null
   and u.raw_user_meta_data->>'heard_from' in
       ('facebook', 'instagram', 'tiktok', 'reddit', 'discord', 'google', 'friend', 'other');

-- To see the counts later:
--   select coalesce(heard_from, '(skipped)') as source, count(*)
--     from public.profiles group by 1 order by 2 desc;
