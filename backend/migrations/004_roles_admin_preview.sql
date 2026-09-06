-- Additive role and admin-preview foundation for the custom Telegram auth model.
-- Authorization is enforced by the backend over a Postgres connection. The Supabase
-- Data API remains deny-by-default and these tables are not exposed to anon/authenticated.

create table if not exists public.user_roles (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  role app_role not null,
  source text not null default 'legacy_user_role',
  created_at timestamptz not null default now(),
  unique (user_id, role)
);

create index if not exists user_roles_user_idx on public.user_roles(user_id);
create index if not exists user_roles_role_idx on public.user_roles(role);

insert into public.user_roles (user_id, role, source)
select u.id, u.role, 'legacy_user_role'
  from public.users u
on conflict (user_id, role) do nothing;

create table if not exists public.student_preview_sessions (
  id uuid primary key default extensions.gen_random_uuid(),
  actor_user_id uuid not null references public.users(id) on delete cascade,
  target_user_id uuid not null references public.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  ended_at timestamptz
);

create index if not exists student_preview_actor_active_idx
  on public.student_preview_sessions(actor_user_id, expires_at)
  where ended_at is null;
create index if not exists student_preview_target_idx
  on public.student_preview_sessions(target_user_id, created_at desc);

alter table public.user_roles enable row level security;
alter table public.student_preview_sessions enable row level security;
revoke all on table public.user_roles, public.student_preview_sessions from anon, authenticated;
