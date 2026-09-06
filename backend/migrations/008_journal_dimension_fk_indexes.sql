-- Cover the optional Classroom foreign keys introduced for independent mapping dimensions.

create index if not exists journal_group_mappings_classroom_course_idx
  on public.journal_group_mappings(classroom_course_id);
create index if not exists teacher_assignments_classroom_course_idx
  on public.teacher_assignments(classroom_course_id);
