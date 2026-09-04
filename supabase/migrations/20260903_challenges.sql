-- Challenge button — Change 3, 3 Sep 2026.
-- Additions only. Run this once in the Supabase SQL editor (or via the CLI) before
-- the "Challenge this question" feature in app.py will work — the app writes to and
-- reads from this table but cannot create it itself.
--
-- Fixed 4 Sep 2026: the two CREATE POLICY statements below originally used
-- "create policy if not exists", which is not valid Postgres syntax — only
-- CREATE TABLE and CREATE INDEX support IF NOT EXISTS; CREATE POLICY doesn't.
-- That made the whole script fail (and roll back — Supabase's SQL editor runs a
-- multi-statement script as one implicit transaction, so the table and indexes
-- below never got created either). Replaced with a pg_policies existence check
-- inside a DO block, which does the same job — safe to run more than once,
-- won't error if the policy already exists — and is actually valid SQL.

create table if not exists public.challenges (
    challenge_id uuid primary key default gen_random_uuid(),
    -- Mock Exam / Practice Questions are generated fresh each session and never get a
    -- database row of their own, so this is a hash of the question's own content
    -- (see _question_hash() in app.py), not a foreign key.
    question_id  text not null,
    sailor_id    uuid not null references auth.users(id) on delete cascade,
    reason_text  text not null,
    ai_verdict   text not null check (ai_verdict in ('upholds', 'confirms-error', 'inconclusive')),
    ai_reasoning text not null,
    created_at   timestamptz not null default now(),
    -- 'resolved'  — upheld, nothing to follow up on
    -- 'priority'  — sailor was right, first-in-line for the next Score Surge DB audit
    -- 'flagged'   — inconclusive, queued for the next audit, no urgency
    status       text not null check (status in ('resolved', 'priority', 'flagged'))
);

create index if not exists challenges_sailor_created_idx
    on public.challenges (sailor_id, created_at);

create index if not exists challenges_sailor_question_idx
    on public.challenges (sailor_id, question_id);

alter table public.challenges enable row level security;

-- A sailor can log their own challenge...
do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'challenges'
          and policyname = 'sailors insert own challenges'
    ) then
        create policy "sailors insert own challenges"
            on public.challenges for insert
            with check (auth.uid() = sailor_id);
    end if;
end $$;

-- ...and read only their own challenge history (the app's rate limit and
-- already-challenged checks both run as the sailor, not a service role).
do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'challenges'
          and policyname = 'sailors read own challenges'
    ) then
        create policy "sailors read own challenges"
            on public.challenges for select
            using (auth.uid() = sailor_id);
    end if;
end $$;
