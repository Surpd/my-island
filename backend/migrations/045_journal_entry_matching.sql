-- Additive release: retain every original journal row and result ID/value.
begin;
select pg_advisory_xact_lock(hashtext('journal-book:1RaT1O-qNnbc3WnQs2Fhikcu03mPLl6JpatqhqBU5a8I'));
alter table public.journal_students
  add column if not exists entry_kind text not null default 'individual',
  add column if not exists classification_method text;
alter table public.journal_students add constraint journal_students_entry_kind_check
  check (entry_kind in ('individual','team'));
alter table public.journal_students add constraint journal_students_team_identity_check
  check (entry_kind <> 'team' or identity_id is null);

create table public.journal_team_members (
  journal_student_id uuid not null references public.journal_students(id) on delete cascade,
  identity_id uuid not null references public.identities(id),
  created_by uuid references public.users(id),
  created_at timestamptz not null default now(),
  primary key (journal_student_id, identity_id)
);
create index journal_team_members_identity_idx on public.journal_team_members(identity_id);
alter table public.journal_team_members enable row level security;
revoke all on public.journal_team_members from anon, authenticated;
grant all on public.journal_team_members to service_role;

-- The user explicitly identified these 13 existing art rows as teams.
-- No name heuristic or personal-name list is used for future imports.
do $$
declare team_ids uuid[];
begin
  if exists (select 1 from public.journal_sync_runs where
    spreadsheet_id='1RaT1O-qNnbc3WnQs2Fhikcu03mPLl6JpatqhqBU5a8I' and status='running') then
    raise exception 'Wait for the active journal import before classification';
  end if;
  select array_agg(student.id) into team_ids
  from public.journal_students student join public.journal_sources source on source.id=student.source_id
  where source.spreadsheet_id='1RaT1O-qNnbc3WnQs2Fhikcu03mPLl6JpatqhqBU5a8I'
    and source.sheet_id='1118307500' and source.grade=9 and student.active is true
    and student.identity_id is null;
  if coalesce(cardinality(team_ids),0)<>13
    or (select count(*) from public.journal_results where journal_student_id=any(team_ids) and is_current is true)<>37
    or exists(select 1 from public.journal_results where journal_student_id=any(team_ids) and identity_id is not null) then
    raise exception 'Production art-team baseline changed; inspect before applying';
  end if;
  update public.journal_students set entry_kind='team', classification_method='user_confirmed_art_team_roster'
  where id=any(team_ids);
end $$;
commit;
