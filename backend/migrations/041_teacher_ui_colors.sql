-- School-wide schedule presentation preferences. These colors are not lesson
-- content and do not participate in canonical schedule or source identity mapping.
create table if not exists public.teacher_ui_colors (
    school_id text not null,
    teacher_identity_id uuid not null references public.identities(id) on delete cascade,
    base_color text not null check (base_color ~ '^#[0-9A-Fa-f]{6}$'),
    manual_color text check (manual_color is null or manual_color ~ '^#[0-9A-Fa-f]{6}$'),
    revision bigint not null default 1 check (revision > 0),
    base_source text not null default 'fallback' check (base_source in ('fallback', 'approved_source')),
    source_evidence text,
    updated_by text,
    updated_at timestamptz not null default now(),
    primary key (school_id, teacher_identity_id),
    check ((base_source = 'approved_source') = (source_evidence is not null))
);

create index if not exists teacher_ui_colors_teacher_idx
    on public.teacher_ui_colors (teacher_identity_id);

alter table public.teacher_ui_colors enable row level security;
revoke all on public.teacher_ui_colors from anon, authenticated;
grant all on public.teacher_ui_colors to service_role;
