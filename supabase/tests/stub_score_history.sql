-- The live score_history table was made in the Supabase dashboard (columns read
-- 5 Nov 2026: id, user_id, date, topic, score, total, pct, created_at). Same shape
-- here so migrations that touch it can be tested.
create table if not exists public.score_history (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references auth.users on delete cascade,
    date text, topic text, score integer, total integer, pct integer,
    created_at timestamptz default now()
);
alter table public.score_history enable row level security;
