-- Canonical school foundation: user-facing labels, explicit dimensions and
-- provenance for groups; source references for temporal membership history.
-- This migration does not create school structure from external names.

alter table public.groups
  add column if not exists display_name text,
  add column if not exists subject text,
  add column if not exists base_class_name text,
  add column if not exists subject_subgroup text,
  add column if not exists exam_track text,
  add column if not exists provenance_source text,
  add column if not exists provenance_ref text,
  add column if not exists canonical boolean not null default true;

alter table public.memberships
  add column if not exists source_ref text not null default '',
  add column if not exists created_by uuid references public.users(id);

alter table public.memberships
  drop constraint if exists memberships_group_id_identity_id_source_key;

alter table public.memberships
  drop constraint if exists memberships_group_identity_role_source_ref_key;

alter table public.memberships
  add constraint memberships_group_identity_role_source_ref_key
  unique (group_id, identity_id, member_role, source, source_ref);

create index if not exists groups_canonical_dimensions_idx
  on public.groups(canonical, group_type, base_class_name, subject, subject_subgroup, exam_track);
create index if not exists memberships_provenance_idx
  on public.memberships(source, source_ref, active, valid_from, valid_until);
create index if not exists memberships_created_by_idx
  on public.memberships(created_by);

alter table public.groups enable row level security;
alter table public.memberships enable row level security;
revoke all on table public.groups, public.memberships from anon, authenticated;
