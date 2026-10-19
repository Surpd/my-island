/* oxlint-disable typescript/no-explicit-any, typescript/no-base-to-string */
import { audienceGrades, subjectLabel } from './school-audience.ts';

type Row = Record<string, any>;
export type AssignmentMerge = { grade: string; subject: string; rows: Row[]; groups: Row[]; eligible: boolean; reason: string; covered: number; expected: number };

const normalize = (value: unknown) => String(value ?? '').trim().replace(/\s+/gu, ' ').toLocaleLowerCase('ru');
const dateKey = (value: Date) => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
const validityKey = (value: unknown): string | null => {
  if (value == null || String(value).trim() === '') return null;
  const source = String(value).trim();
  const date = source.match(/^(\d{4}-\d{2}-\d{2})(?:$|[T\s])/u)?.[1];
  if (date) {
    const parsedDate = new Date(`${date}T00:00:00.000Z`);
    return Number.isNaN(parsedDate.getTime()) || parsedDate.toISOString().slice(0, 10) !== date ? '' : date;
  }
  const parsed = new Date(source);
  return Number.isNaN(parsed.getTime()) ? '' : dateKey(parsed);
};

/** Whether an assignment is effective on the local calendar date. Validity bounds are inclusive. */
export function assignmentIsActive(item: Row, referenceDate: Date = new Date()): boolean {
  if (item.active === false || item.active === 0 || String(item.status || '').trim().toLocaleLowerCase('ru') === 'inactive') return false;
  const today = dateKey(referenceDate);
  const from = validityKey(item.valid_from);
  const until = validityKey(item.valid_until);
  if (from === '' || until === '' || (from && from > today) || (until && until < today)) return false;
  return true;
}
const assignmentContext = (row: Row) => [normalize(subjectLabel(row.subject)), normalize(row.subject_subgroup), normalize(row.exam_track), normalize(row.capability || 'teach'), String(row.classroom_course_id || ''), String(row.valid_from || ''), String(row.valid_until || '')].join('|');

export function analyzeAssignmentMerges(assignments: Row[], allTeachers: Row[], groups: Row[], students: Row[], referenceDate?: Date): AssignmentMerge[] {
  const groupById = new Map(groups.map((group) => [String(group.id), group]));
  const memberships = (groupId: string) => new Set(students.filter((student) => [...(student.memberships || []), ...(student.instructional_memberships || [])].some((membership: Row) => String(membership.group_id || membership.id) === groupId)).map((student) => String(student.id)));
  const active = assignments.filter((row) => assignmentIsActive(row, referenceDate));
  const buckets = new Map<string, { grade: string; rows: Row[]; groups: Row[] }>();
  for (const row of active) {
    const group = groupById.get(String(row.group_id));
    if (!group || ['class', 'exam_track'].includes(String(group.group_type || '').toLowerCase()) || row.exam_track) continue;
    const grades = audienceGrades(group, students);
    if (grades.length !== 1) continue;
    const grade = grades[0];
    const key = `${grade}|${assignmentContext(row)}`;
    const bucket = buckets.get(key) || { grade, rows: [], groups: [] };
    bucket.rows.push(row); bucket.groups.push(group); buckets.set(key, bucket);
  }
  const analyses: AssignmentMerge[] = [];
  for (const bucket of buckets.values()) {
    const uniqueGroups = [...new Map(bucket.groups.map((group) => [String(group.id), group])).values()];
    if (uniqueGroups.length < 2) continue;
    const expected = new Set(students.filter((student) => {
      const baseClasses = student.base_classes || [];
      return String(student.class_name || '').match(new RegExp(`^${bucket.grade}(?:[-\\s]|$)`)) || baseClasses.some((klass: Row) => String(typeof klass === 'object' ? klass.base_class_name || klass.name : klass).match(new RegExp(`^${bucket.grade}(?:[-\\s]|$)`)));
    }).map((student) => String(student.id)));
    const seen = new Set<string>();
    let overlap = false;
    const union = new Set<string>();
    for (const group of uniqueGroups) {
      const ids = memberships(String(group.id));
      for (const id of ids) { if (seen.has(id)) overlap = true; seen.add(id); union.add(id); }
    }
    const sourceOverlap = uniqueGroups.some((group) => (group.source_overlaps || []).some((item: Row) => Number(item.count || 0) > 0));
    const complete = expected.size > 0 && expected.size === union.size && [...expected].every((id) => union.has(id));
    const teacherIds = new Set(bucket.rows.map((row) => String(row.teacher_identity_id || '')));
    const competing = allTeachers.flatMap((teacher) => (teacher.teacher_assignments || teacher.assignments || []).filter((row: Row) => assignmentIsActive(row, referenceDate) && assignmentContext(row) === assignmentContext(bucket.rows[0]) && (String(row.grade_level || '') === bucket.grade || audienceGrades(groupById.get(String(row.group_id)) || {}, students).includes(bucket.grade)) && !bucket.rows.some((candidate) => String(candidate.id) === String(row.id))));
    const ownTeacherCount = teacherIds.size;
    const reason = ownTeacherCount !== 1 ? 'Назначения относятся к разным преподавателям.' : sourceOverlap ? 'В исходных активных membership найдены пересечения.' : overlap ? 'В действующих составах групп есть пересечение.' : !complete ? 'Составы не покрывают всех учеников параллели ровно один раз.' : competing.length ? 'Найдены другие назначения этого предмета в параллели.' : 'Подтверждено полное непересекающееся покрытие параллели.';
    analyses.push({ grade: bucket.grade, subject: String(bucket.rows[0].subject || ''), rows: bucket.rows, groups: uniqueGroups, eligible: ownTeacherCount === 1 && !sourceOverlap && !overlap && complete && competing.length === 0, reason, covered: union.size, expected: expected.size });
  }
  return analyses.sort((a, b) => Number(a.grade) - Number(b.grade) || a.subject.localeCompare(b.subject, 'ru'));
}
