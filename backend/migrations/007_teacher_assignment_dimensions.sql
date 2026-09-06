-- Teacher access can target an independent subject subgroup/dimension without
-- encoding base class, Classroom, and exam track into one group name.

alter table public.teacher_assignments
  add column if not exists base_class_name text,
  add column if not exists subject_subgroup text,
  add column if not exists classroom_course_id uuid references public.classroom_courses(id),
  add column if not exists exam_track text;

create index if not exists teacher_assignments_journal_dimensions_idx
  on public.teacher_assignments(subject, subject_subgroup, base_class_name, exam_track, active);
