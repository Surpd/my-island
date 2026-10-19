begin;

create table if not exists public.teacher_subjects (
  id uuid primary key default extensions.gen_random_uuid(),
  teacher_identity_id uuid not null references public.identities(id) on delete cascade,
  subject text not null,
  source text not null default 'admin_override',
  active boolean not null default true,
  created_by uuid references public.users(id),
  created_at timestamptz not null default now(),
  unique (teacher_identity_id, subject, source)
);

create index if not exists teacher_subjects_teacher_active_idx
  on public.teacher_subjects(teacher_identity_id, active);

alter table public.teacher_subjects enable row level security;
revoke all on table public.teacher_subjects from anon, authenticated;

commit;
