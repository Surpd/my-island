begin;

alter table public.identities
  add column if not exists admin_note text;
alter table public.groups
  add column if not exists admin_note text;

commit;
