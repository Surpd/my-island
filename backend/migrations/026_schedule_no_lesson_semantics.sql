-- Collapse derived student projection states to lesson / no_lesson.
-- Source-context rows (lunch/break/🥨) are not student projection rows at all.
begin;

alter table public.schedule_student_allocations
  drop constraint if exists schedule_student_allocations_allocation_kind_check;

-- Remove historical allocations/audiences that were accidentally materialized
-- from raw lunch/break markers. The source lesson rows themselves remain
-- immutable and retain their raw/provenance payload.
delete from public.schedule_student_allocations sa
 where sa.allocation_kind in ('lunch', 'break')
    or exists (
         select 1
           from public.schedule_lessons sl
          where sl.id = sa.lesson_id
            and (
              sl.activity_type in ('nonlesson', 'cancelled')
              or lower(trim(sl.subject)) in ('обед', 'перерыв', 'обед / перерыв', '🥨', '🍽️')
              or coalesce(sl.raw_payload ->> 'raw_text', '') in ('🥨', '🍽️')
            )
       );

delete from public.schedule_lesson_audiences sla
 where exists (
         select 1
           from public.schedule_lessons sl
          where sl.id = sla.lesson_id
            and (
              sl.activity_type in ('nonlesson', 'cancelled')
              or lower(trim(sl.subject)) in ('обед', 'перерыв', 'обед / перерыв', '🥨', '🍽️')
              or coalesce(sl.raw_payload ->> 'raw_text', '') in ('🥨', '🍽️')
            )
       );

update public.schedule_student_allocations
   set allocation_kind = 'no_lesson',
       reason = case when reason in ('window','end_of_day','lunch','break','skip') then 'no_lesson' else reason end,
       provenance = case
         when jsonb_typeof(provenance) = 'object' then provenance || jsonb_build_object('legacy_allocation_kind', allocation_kind)
         else jsonb_build_object('legacy_allocation_kind', allocation_kind)
       end
 where allocation_kind in ('window','end_of_day','skip');

alter table public.schedule_student_allocations
  add constraint schedule_student_allocations_allocation_kind_check
  check (allocation_kind in ('lesson','no_lesson','unassigned','conflict'));

commit;
