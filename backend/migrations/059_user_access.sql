begin;

alter table public.users
  add column telegram_username text,
  add column telegram_first_name text,
  add column telegram_last_name text,
  add column last_active_at timestamptz,
  add column access_revoked_at timestamptz,
  add column telegram_auth_after bigint;

alter table public.identity_claims
  alter column identity_id drop not null,
  add column submitted_name text,
  add column submitted_class_name text,
  add column review_reason text;

-- Preserve older duplicate requests as reviewed conflicts. A new request must
-- never compete with another pending request for the same account.
with pending as (
  select id, row_number() over (partition by user_id order by created_at desc, id desc) as position
    from public.identity_claims where status = 'pending'
)
update public.identity_claims c
   set status = 'identity_conflict', reviewed_at = now(),
       review_reason = 'Позднее была подана другая заявка.'
  from pending p where p.id = c.id and p.position > 1;
create unique index identity_claims_one_pending_per_user
  on public.identity_claims(user_id) where status = 'pending';

-- Canonical links own school access. Normalize the compatibility pointer
-- without restoring links that were absent, pending, conflicting or revoked.
update public.users u set identity_id = null
 where identity_id is not null and not exists (
   select 1 from public.account_identity_links l
    where l.user_id = u.id and l.identity_id = u.identity_id and l.status = 'confirmed'
 );
update public.users u set identity_id = l.identity_id
  from public.account_identity_links l
 where l.user_id = u.id and l.status = 'confirmed' and u.identity_id is distinct from l.identity_id;

create table public.access_control (
  id integer primary key check (id = 1),
  manager_user_id uuid references public.users(id) on delete restrict
);
insert into public.access_control(id) values (1);

create table public.telegram_registration_drafts (
  user_id uuid primary key references public.users(id) on delete cascade,
  step text not null,
  requested_role text check (requested_role in ('student', 'teacher')),
  class_name text,
  display_name text,
  updated_at timestamptz not null default now()
);
create table public.telegram_processed_updates (
  update_id bigint primary key,
  telegram_user_id bigint not null,
  created_at timestamptz not null default now()
);
create index telegram_processed_updates_user_idx on public.telegram_processed_updates(telegram_user_id, created_at);
create index telegram_processed_updates_age_idx on public.telegram_processed_updates(created_at);
create table public.access_login_attempts (
  client_key text not null,
  minute_bucket bigint not null,
  attempts integer not null,
  primary key (client_key, minute_bucket)
);
create index access_login_attempts_age_idx on public.access_login_attempts(minute_bucket);

alter table public.access_control enable row level security;
alter table public.telegram_registration_drafts enable row level security;
alter table public.telegram_processed_updates enable row level security;
alter table public.access_login_attempts enable row level security;
revoke all on table public.access_control, public.telegram_registration_drafts,
  public.telegram_processed_updates, public.access_login_attempts from anon, authenticated;

commit;
