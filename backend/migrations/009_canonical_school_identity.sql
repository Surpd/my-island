-- Canonical school people stay independent from Telegram accounts. The legacy
-- users.identity_id column remains the runtime compatibility link.

alter table public.identities
  add column if not exists origin text not null default 'legacy',
  add column if not exists source_ref text,
  add column if not exists manually_confirmed boolean not null default false,
  add column if not exists created_at timestamptz not null default now();

create table if not exists public.account_identity_links (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  identity_id uuid not null references public.identities(id),
  status text not null default 'confirmed' check (status in ('pending', 'confirmed', 'conflict', 'revoked')),
  source text not null,
  source_ref text,
  confirmed_by uuid references public.users(id),
  confirmed_at timestamptz,
  created_at timestamptz not null default now(),
  unique (user_id),
  unique (identity_id)
);

insert into public.account_identity_links (user_id, identity_id, status, source, source_ref, confirmed_at)
select u.id, u.identity_id, 'confirmed', 'legacy_backfill', 'users.identity_id', u.created_at
  from public.users u
 where u.identity_id is not null
on conflict (user_id) do nothing;

do $$
declare
  target_user_id uuid;
  target_identity_id uuid;
  candidate_count integer;
  identity_count integer;
  journal_source_ref text;
  marker text;
  subgroup text;
  target_group_id uuid;
begin
  select count(*), min(u.id::text)::uuid
    into candidate_count, target_user_id
    from public.users u
   where u.identity_id is null
     and exists (select 1 from public.user_roles r where r.user_id = u.id and r.role = 'teacher' and r.source = 'telegram_bootstrap')
     and exists (select 1 from public.user_roles r where r.user_id = u.id and r.role = 'admin' and r.source = 'telegram_bootstrap');

  if candidate_count = 0 then
    raise notice 'No unlinked verified Teacher+Admin Telegram account; canonical owner seed skipped';
    return;
  elsif candidate_count > 1 then
    raise exception 'Expected at most one verified Teacher+Admin Telegram account without school identity, found %', candidate_count;
  end if;

  select count(*), min(i.id::text)::uuid
    into identity_count, target_identity_id
    from public.identities i
   where lower(regexp_replace(i.display_name, '\s+', ' ', 'g')) = lower('Дмитрий Филиппов');

  if identity_count > 1 then
    raise exception 'Multiple canonical Дмитрий Филиппов identities require manual resolution';
  elsif identity_count = 0 then
    insert into public.identities (kind, display_name, status, origin, source_ref, manually_confirmed)
    values ('teacher', 'Дмитрий Филиппов', 'active', 'manual_confirmation', 'owner-confirmed:2026-09-07', true)
    returning id into target_identity_id;
  end if;

  if exists (select 1 from public.users where identity_id = target_identity_id and id <> target_user_id) then
    raise exception 'Canonical Дмитрий Филиппов identity is already linked to another account';
  end if;

  update public.users set identity_id = target_identity_id, role = 'teacher' where id = target_user_id;
  insert into public.user_roles (user_id, role, source) values
    (target_user_id, 'teacher', 'manual_confirmation'),
    (target_user_id, 'admin', 'manual_confirmation')
  on conflict (user_id, role) do update set source = excluded.source;

  insert into public.account_identity_links
    (user_id, identity_id, status, source, source_ref, confirmed_by, confirmed_at)
  values
    (target_user_id, target_identity_id, 'confirmed', 'manual_confirmation', 'verified Telegram session + owner confirmation', target_user_id, now())
  on conflict (user_id) do update set
    identity_id = excluded.identity_id,
    status = excluded.status,
    source = excluded.source,
    source_ref = excluded.source_ref,
    confirmed_by = excluded.confirmed_by,
    confirmed_at = excluded.confirmed_at;

  for marker, subgroup in values ('9-1', 'A'), ('9-2', 'B'), ('9-3', 'C') loop
    select concat('journal:', jgm.source_id::text, ':', marker)
      into journal_source_ref
      from public.journal_group_mappings jgm
      join public.journal_sources js on js.id = jgm.source_id
     where jgm.group_marker = marker
       and js.subject = 'Математика'
       and jgm.subject_subgroup = subgroup
     order by js.last_synced_at desc nulls last
     limit 1;

    if journal_source_ref is null then
      raise exception 'Missing confirmed journal mapping for % → %', marker, subgroup;
    end if;

    select id into target_group_id from public.groups where name = marker || ' Дмитрий' and group_type = 'subject_subgroup' limit 1;
    if target_group_id is null then
      insert into public.groups (name, group_type) values (marker || ' Дмитрий', 'subject_subgroup') returning id into target_group_id;
    end if;

    update public.journal_group_mappings
       set group_id = target_group_id
     where group_marker = marker and subject_subgroup = subgroup
       and source_id in (select id from public.journal_sources where subject = 'Математика');

    insert into public.teacher_assignments
      (teacher_identity_id, group_id, subject, subject_subgroup, capability, source, source_ref, active, created_by)
    values
      (target_identity_id, target_group_id, 'Математика', subgroup, 'teach', 'manual_confirmation', journal_source_ref, true, target_user_id)
    on conflict (teacher_identity_id, group_id, subject, capability, source)
    do update set subject_subgroup = excluded.subject_subgroup, source_ref = excluded.source_ref, active = true;

    journal_source_ref := null;
    target_group_id := null;
  end loop;

  insert into public.audit_log (actor_user_id, action, entity_type, entity_id, details)
  values (target_user_id, 'identity.confirmed', 'identity', target_identity_id,
    jsonb_build_object('roles', jsonb_build_array('teacher','admin'), 'memberships', jsonb_build_array('9-1 Дмитрий','9-2 Дмитрий','9-3 Дмитрий'), 'source', 'owner_confirmation'));
end $$;

create index if not exists account_identity_links_status_idx on public.account_identity_links(status, created_at);
alter table public.account_identity_links enable row level security;
revoke all on table public.account_identity_links from anon, authenticated;
