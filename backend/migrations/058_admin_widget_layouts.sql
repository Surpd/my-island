begin;
create table if not exists public.admin_widget_layouts (
    user_id uuid primary key references public.users(id) on delete cascade,
    revision integer not null check(revision > 0),
    widgets_json text not null
);
alter table public.admin_widget_layouts add column if not exists schema_version integer not null default 1 check(schema_version > 0);
alter table public.admin_widget_layouts enable row level security;
revoke all on public.admin_widget_layouts from anon, authenticated;
grant all on public.admin_widget_layouts to service_role;
commit;
