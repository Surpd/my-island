begin;

create table if not exists public.schedule_activity_catalog (
  id text primary key,
  name text not null,
  type text not null check (type in ('club','elective','event')),
  aliases text not null default '[]',
  description text not null default '',
  teacher_ids text not null default '[]',
  room text not null default '',
  eligible_grades text not null default '[]',
  revision bigint not null default 1 check (revision > 0),
  archived boolean not null default false,
  created_by uuid references public.users(id) on delete set null,
  updated_by uuid references public.users(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists schedule_activity_catalog_active_idx on public.schedule_activity_catalog(archived,name);

insert into public.schedule_activity_catalog(id,name,type,aliases) values
 ('animation','Анимация','club','["анимация","мультипликация"]'),
 ('aikido','Айкидо','club','["айкидо"]'),
 ('spanish','Испанский язык','club','["испанский","испанский язык"]'),
 ('french','Французский язык','club','["французский","французский язык"]'),
 ('game_design','Геймдизайн','club','["game design","геймдизайн","game-design"]'),
 ('chess','Шахматы','club','["шахматы","шахматный клуб","японские шахматы","сёги","shogi"]'),
 ('academic_drawing','Академический рисунок','club','["академический рисунок","рисунок"]'),
 ('atelier','Ателье','club','["ателье"]'),
 ('vocal','Вокал','club','["вокал","вокальный клуб"]'),
 ('programming','Программирование','club','["программирование","программирование на python"]')
on conflict(id) do nothing;

alter table public.schedule_activity_catalog enable row level security;
revoke all on table public.schedule_activity_catalog from anon, authenticated;

commit;
