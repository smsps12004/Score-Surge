\set ON_ERROR_STOP 1
-- Two sailors. A is a trial, B is paid chief with a stripe id.
insert into auth.users values ('00000000-0000-0000-0000-00000000000a','a@x.com'),('00000000-0000-0000-0000-00000000000b','b@x.com'),('00000000-0000-0000-0000-00000000000c','c@x.com');
-- rows created by the "dashboard"/auth trigger (privileged)
insert into public.profiles (id,email,tier,trial_start) values ('00000000-0000-0000-0000-00000000000a','a@x.com','trial',now());
insert into public.profiles (id,email,tier,stripe_customer_id) values ('00000000-0000-0000-0000-00000000000b','b@x.com','chief','cus_B');

create temp table results (name text, ok boolean);
grant all on results to authenticated, anon, service_role;

-- ── as sailor A ─────────────────────────────────────────
set role authenticated;
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-00000000000a',false);
select set_config('request.jwt.claims','{"sub":"00000000-0000-0000-0000-00000000000a","email":"a@x.com"}',false);

update public.profiles set tier='chief' where id=auth.uid();
insert into results select 'A cannot self-upgrade trial->chief', tier='trial' from public.profiles where id=auth.uid();
update public.profiles set stripe_customer_id='cus_B' where id=auth.uid();
insert into results select 'A cannot set a stripe customer id', stripe_customer_id is null from public.profiles where id=auth.uid();
update public.profiles set trial_start=now()+interval '30 days' where id=auth.uid();
insert into results select 'A cannot extend own trial', trial_start <= now() from public.profiles where id=auth.uid();
update public.profiles set email='b@x.com' where id=auth.uid();
insert into results select 'A cannot change email to another sailor''s', email='a@x.com' from public.profiles where id=auth.uid();
update public.profiles set rating='XX' where id=auth.uid();
insert into results select 'invalid rating rejected', rating is null from public.profiles where id=auth.uid();
update public.profiles set rating='PS' where id=auth.uid();
insert into results select 'first rating pick sticks', rating='PS' and rating_set_at is not null from public.profiles where id=auth.uid();
update public.profiles set rating='YN' where id=auth.uid();
insert into results select 'rating change within 180 days is blocked', rating='PS' from public.profiles where id=auth.uid();
update public.profiles set rating_set_at=now()-interval '1 year' where id=auth.uid();
insert into results select 'A cannot backdate rating_set_at', rating_set_at > now()-interval '1 day' from public.profiles where id=auth.uid();
update public.profiles set rating=null where id=auth.uid();
insert into results select 'A cannot clear the rating', rating='PS' from public.profiles where id=auth.uid();
update public.profiles set active_session='tok1' where id=auth.uid();
insert into results select 'A can claim a login session', active_session='tok1' from public.profiles where id=auth.uid();
update public.profiles set tier='free' where id=auth.uid();
insert into results select 'trial -> free still allowed (app expires trials)', tier='free' from public.profiles where id=auth.uid();
update public.profiles set tier='trial' where id=auth.uid();
insert into results select 'free -> trial blocked', tier='free' from public.profiles where id=auth.uid();
delete from public.profiles where id=auth.uid();
insert into results select 'A cannot delete own profile (re-trial trick)', count(*)=1 from public.profiles where id=auth.uid();
update public.profiles set tier='free' where id='00000000-0000-0000-0000-00000000000b';
reset role;
insert into results select 'A cannot touch B''s row', tier='chief' from public.profiles where id='00000000-0000-0000-0000-00000000000b';
set role authenticated;

-- usage: A is free now -> ai limit 3
select public.consume_usage('ai'); select public.consume_usage('ai'); select public.consume_usage('ai');
insert into results select 'free: 4th AI use refused', (public.consume_usage('ai')->>'allowed')::boolean = false;
insert into results select 'refused use does not bump counter', (public.get_usage()->'ai'->>'used')::int = 3;
insert into results select 'get_usage shows 0 left', (public.get_usage()->'ai'->>'left')::int = 0;
do $$ begin
  begin
    insert into public.usage_counters values (auth.uid(), to_char(now() at time zone 'utc','YYYY-MM'), 'ai', 0);
    insert into results values ('A cannot write own counters', false);
  exception when others then insert into results values ('A cannot write own counters', true);
  end;
  begin
    update public.usage_counters set used=0 where user_id=auth.uid();
    insert into results select 'A cannot reset own counters', (public.get_usage()->'ai'->>'used')::int = 3;
  exception when others then insert into results values ('A cannot reset own counters', true);
  end;
  begin
    update public.usage_limits set monthly_limit=999;
    insert into results select 'A cannot raise limits', (select monthly_limit from public.usage_limits where tier='free' and kind='ai')=3;
  exception when others then insert into results values ('A cannot raise limits', true);
  end;
end $$;

-- ── new sailor C inserts own row (app path) trying to cheat ───────
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-00000000000c',false);
select set_config('request.jwt.claims','{"sub":"00000000-0000-0000-0000-00000000000c","email":"c@x.com"}',false);
insert into public.profiles (id,email,tier,trial_start,stripe_customer_id) values (auth.uid(),'b@x.com','chief',now()+interval '1 year','cus_B');
insert into results select 'C insert: tier forced to trial', tier='trial' from public.profiles where id=auth.uid();
insert into results select 'C insert: trial starts now', trial_start <= now() from public.profiles where id=auth.uid();
insert into results select 'C insert: no stripe id', stripe_customer_id is null from public.profiles where id=auth.uid();
insert into results select 'C insert: email is own login email', email='c@x.com' from public.profiles where id=auth.uid();
-- trial ai limit 10
insert into results select 'trial: first AI use allowed', (public.consume_usage('ai')->>'allowed')::boolean;
insert into results select 'download counted separately', (public.consume_usage('download')->>'used')::int = 1;
-- expired trial counts as free
reset role;
update public.profiles set trial_start=now()-interval '5 days' where id='00000000-0000-0000-0000-00000000000c';
set role authenticated;
insert into results select 'expired trial gets free limits', public.get_usage()->>'tier' = 'free';

-- ── service role (webhook) ────────────────────────────────
reset role; set role service_role;
update public.profiles set tier='petty_officer', stripe_customer_id='cus_C' where id='00000000-0000-0000-0000-00000000000c';
reset role;
insert into results select 'webhook can still grant a tier + customer id', tier='petty_officer' and stripe_customer_id='cus_C' from public.profiles where id='00000000-0000-0000-0000-00000000000c';
update public.profiles set rating='YN' where id='00000000-0000-0000-0000-00000000000a';
insert into results select 'dashboard can change a rating any time', rating='YN' from public.profiles where id='00000000-0000-0000-0000-00000000000a';

-- anon cannot call usage functions
set role anon;
do $$ begin
  begin perform public.consume_usage('ai'); insert into results values ('anon cannot spend usage', false);
  exception when others then insert into results values ('anon cannot spend usage', true); end;
end $$;
reset role;

select case when ok then 'PASS' else 'FAIL' end, name from results;
select count(*) filter (where ok) || '/' || count(*) as total from results;
