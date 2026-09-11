-- Collapse derived student projection states to lesson / no_lesson.
begin;

alter table public.schedule_student_allocations
  drop constraint if exists schedule_student_allocations_allocation_kind_check;

update public.schedule_student_allocations
   set allocation_kind = 'no_lesson',
       reason = case when reason in ('window','end_of_day','lunch','break','skip') then 'no_lesson' else reason end,
       provenance = case
         when jsonb_typeof(provenance) = 'object' then provenance || jsonb_build_object('legacy_allocation_kind', allocation_kind)
         else jsonb_build_object('legacy_allocation_kind', allocation_kind)
       end
 where allocation_kind in ('lunch','window','end_of_day','skip');

alter table public.schedule_student_allocations
  add constraint schedule_student_allocations_allocation_kind_check
  check (allocation_kind in ('lesson','no_lesson','unassigned','conflict'));

commit;
