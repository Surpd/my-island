-- Additive metadata needed to persist the accepted Grade 7 directory model.
-- This migration does not change memberships or canonical schedule semantics.
begin;

alter table public.groups
  add column if not exists role text,
  add column if not exists semantic_dimension text;

alter table public.groups
  drop constraint if exists groups_role_check;
alter table public.groups
  add constraint groups_role_check
  check (role is null or role in ('base_class','instructional_partition','elective_or_special','unknown'));

commit;
