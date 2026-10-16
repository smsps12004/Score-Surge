-- Generated-question pool — 13 Oct 2026.
-- Additions only. Run this once in the Supabase SQL editor. Until it's run, the app's
-- save_generated_questions() quietly does nothing (it never blocks an exam), so the
-- app is safe to deploy before or after this.
--
-- Shawn's call, 13 Oct 2026: every question the AI writes gets saved, no matter what,
-- so nothing is paid for twice and the bank can grow on its own.
--
-- What this table is NOT: it is not the verified bank, and the app never reads from it.
-- Every row lands as review_status = 'unverified'. A row only becomes a verified bank
-- question by going through the Score Surge DB gates (quote match against the real
-- manual, the adversarial AI judge, drift detection) and being copied into
-- questions.db / data/ps_question_bank.csv. The hard rule holds: a sailor is never
-- shown an unverified question as if it were verified.
--
-- `gate` records what the app's own quote check said when the question was written:
--   quote_matched   — its quoted line was found in the retrieved manual text (the
--                     sailor was shown it as "Quoted from ...", not as verified)
--   quote_not_found — the quote wasn't in the text; the sailor never saw this one
--   no_source_text  — no manual text was available for this topic; written from memory
-- Rows that failed the gate are kept on purpose ("no matter what") — they're useful for
-- measuring the generator, and they can never reach a sailor from here.

create table if not exists public.generated_questions (
    id               uuid primary key default gen_random_uuid(),
    created_at       timestamptz not null default now(),
    -- Content, not personal data: keep the question if the account is deleted.
    sailor_id        uuid references auth.users(id) on delete set null,
    origin           text not null check (origin in ('mock_exam', 'study_guide_practice')),
    rating           text not null check (char_length(rating) <= 10),
    paygrade         text not null check (char_length(paygrade) <= 10),
    topic            text not null check (char_length(topic) <= 200),
    question         text not null check (char_length(question) <= 2000),
    answer_a         text not null check (char_length(answer_a) <= 1000),
    answer_b         text not null check (char_length(answer_b) <= 1000),
    answer_c         text not null check (char_length(answer_c) <= 1000),
    answer_d         text not null check (char_length(answer_d) <= 1000),
    correct_answer   text not null check (correct_answer in ('A', 'B', 'C', 'D')),
    explanation      text check (char_length(explanation) <= 4000),
    source_manual    text check (char_length(source_manual) <= 300),
    chapter_section  text check (char_length(chapter_section) <= 300),
    source_quote     text check (char_length(source_quote) <= 4000),
    gate             text not null check (gate in ('quote_matched', 'quote_not_found', 'no_source_text')),
    matched_article  text check (char_length(matched_article) <= 40),
    review_status    text not null default 'unverified'
                     check (review_status in ('unverified', 'verified', 'rejected')),
    -- Same hash as _question_hash() in app.py. Unique, so the same question written
    -- twice is stored once.
    question_hash    text not null unique
);

create index if not exists generated_questions_topic_idx
    on public.generated_questions (rating, paygrade, topic, review_status);

alter table public.generated_questions enable row level security;

-- Sailors can add questions the app wrote for them, and read back only their own: no
-- update, no delete. Reviewing and promoting rows is done from the dashboard / the
-- Score Surge DB tooling with the service role, which bypasses RLS.
--
-- The read policy is not optional. The app saves with INSERT ... ON CONFLICT DO NOTHING
-- (so a repeat question is stored once), and Postgres requires a row inserted that way
-- to pass a SELECT policy too. Without one, every save failed with "new row violates
-- row-level security policy" -- caught 13 Oct 2026 by running this file against a real
-- Postgres before shipping it.
do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'generated_questions'
          and policyname = 'sailors read own generated questions'
    ) then
        create policy "sailors read own generated questions"
            on public.generated_questions for select
            using (auth.uid() = sailor_id);
    end if;
end $$;

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'generated_questions'
          and policyname = 'sailors insert generated questions'
    ) then
        create policy "sailors insert generated questions"
            on public.generated_questions for insert
            with check (auth.uid() = sailor_id and review_status = 'unverified');
    end if;
end $$;
