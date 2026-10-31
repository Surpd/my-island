/* oxlint-disable typescript/no-explicit-any, react/react-compiler, react-hooks/exhaustive-deps, jsx-a11y/prefer-tag-over-role, jsx-a11y/control-has-associated-label, jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/no-noninteractive-tabindex */
'use client';

import {
  Fragment,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import {
  AlertTriangle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clipboard,
  Copy,
  Expand,
  BookOpenCheck,
  Monitor,
  School,
  Plus,
  Palette,
  Pencil,
  Redo2,
  RefreshCw,
  Save,
  Scissors,
  Search,
  Trash2,
  Undo2,
  X,
} from 'lucide-react';
import Link from 'next/link';
import { ConstructorInspectorShell } from './constructor-inspector-shell';
import { useConstructorLayout } from './use-constructor-layout';
import { useSchoolTeacherColors } from './use-school-teacher-colors';
import { ConstructorPublicationPanel } from './constructor-publication-panel';
import { ConstructorAudienceRecalculation } from './constructor-audience-recalculation';
import { normalizeRoomKey, normalizeRoomDisplay, normalizeRoomInput } from './schedule-rooms';
import { ConstructorResolutionPanel } from './constructor-resolution-panel';
import { formatLessonTimeRange } from './lesson-time';
import { ConstructorSchoolStart, type SchoolWeekIndex } from './constructor-school-start';
import { TeacherColorPanel } from './teacher-color-panel';
import { schedulePlacementClassIds, linkedInstructionalGroupsForColumn, isWholeClassAudience, remapPastedLessonAudience, groupClassIds, groupCourseContext, mergeScheduleDrafts, parseQuick, rankTeachersForAudience, sameScheduleMeaning, shouldWarnMissingTeacher, shortSubject, shortTeacher, type ScheduleMergeConflict } from './schedule-constructor-logic';
import { ParticipantsPicker, type AudienceRule, type AudiencePreview } from './participants-picker';
import { audienceGrade, audienceLabel, audienceShortLabel, subjectLabel, studentMemberships } from './school-audience';
import { assignmentIsActive } from './teacher-directory-logic';

const readOnlySchoolWorkspace = String((import.meta as ImportMeta & { env?: Record<string, string | boolean> }).env?.VITE_READ_ONLY_SCHOOL_WORKSPACE || '') === 'true';
const scheduleDataSourceLabel = String((import.meta as ImportMeta & { env?: Record<string, string | boolean> }).env?.VITE_DATA_SOURCE_LABEL || '');

type Api = <T>(path: string, init?: RequestInit) => Promise<T>;
type Raw = Record<string, any>;
type Group = {
  id: string;
  name: string;
  group_type: string;
  subject?: string;
  base_class_name?: string;
  archived_at?: string | null;
  role?: string;
  semantic_dimension?: string;
  subject_subgroup?: string;
  course?: string;
  exam_track?: string;
};
type Teacher = {
  id: string;
  name: string;
  assignments: { group_id: string; subject: string; course?: string; grade_level?: string; exam_track?: string }[];
};
type Student = {
  id: string;
  name: string;
  groups: string[];
  classes: string[];
};
type Slot = { start: string; end: string };
type Lesson = {
  id: string;
  day: number;
  period: number;
  classId: string;
  subject: string;
  teacherId: string;
  groupId: string;
  room: string;
  raw?: string;
  teacherIds?: string[];
  groupIds?: string[];
  classIds?: string[];
  audience?: AudienceRule;
  audience_snapshot?: { student_ids: string[]; resolved_at: string } | null;
  excludedGroupIds?: string[];
  audienceMode?: 'remainder' | 'canonical';
  canonicalRole?: string;
  specialMark?: 'lunch' | 'no_lesson';
  needsClarification?: boolean;
  clarificationReason?: string;
  importError?: boolean;
  canonical?: Raw;
  grade_scope?: string;
  activity?: string;
  weekday?: number;
  source?: Raw;
  kind?: 'lesson' | 'event';
  informational?: boolean;
  start?: string;
  end?: string;
  place?: string;
  description?: string;
  format?: string;
  blockId?: string;
  blockName?: string;
  activityType?: 'lesson' | 'club' | 'elective' | 'event';
  activityCatalogId?: string;
  activityNameSnapshot?: string;
  eligibleGrades?: number[];
  confirmedParticipantIds?: string[];
  electiveDirection?: 'digital';
  displayClassIds?: string[];
  sdepIndependent?: boolean;
};
type DayMode = 'online' | 'sdep';
type ScheduleActivity = { id: string; name: string; type: 'club' | 'elective' | 'event'; aliases: string[]; teacher_ids: string[]; room: string; eligible_grades: number[]; status: 'active' | 'archived'; revision: number };
type Payload = {
  days: number[];
  slots: Slot[];
  classes: string[];
  lessons: Lesson[];
  reference_date?: string;
  week_start?: string;
  column_widths?: Record<string, number>;
  column_width_mode?: 'auto' | 'manual';
  import_review?: Raw;
  day_modes?: Record<string, Record<string, DayMode>>;
};
type Draft = {
  id: string;
  name: string;
  revision: number;
  updated_at?: string;
  base_version_id?: string | null;
  transfer_result?: { transferred: number; needs_clarification: number; import_errors: number };
  payload: Payload;
};
type LocalDraftRecovery = { id: string; revision: number; name: string; payload: Payload; base?: Draft | null };
const recoveryKey = (workspace: string, id: string) => `my-island-schedule-recovery:${workspace}:${id}`;
function audiencePreviewLesson(lesson: Lesson) {
  const { id, day, period, start, end, kind, canonicalRole, specialMark, informational,
    audience, audienceMode, groupId, groupIds, classId, classIds, excludedGroupIds,
    grade_scope, activity, weekday, importError, subject, audience_snapshot, activityType } = lesson;
  // Preview needs only the legacy audience rule from canonical metadata. Sending
  // the full imported assignment/source blob made every preview request needlessly large.
  const audienceRule = lesson.canonical?.assignment?.metadata?.audience_rule;
  const canonical = audienceRule ? { assignment: { metadata: { audience_rule: audienceRule } } } : undefined;
  return { id, day, period, start, end, kind, canonicalRole, specialMark, informational,
    audience, audienceMode, groupId, groupIds, classId, classIds, excludedGroupIds,
    canonical, grade_scope, activity, weekday, importError, subject, audience_snapshot, activityType };
}
function audiencePreviewPayload(payload: Payload) {
  return { reference_date: payload.reference_date, week_start: payload.week_start, slots: payload.slots,
    lessons: payload.lessons.map(audiencePreviewLesson) };
}
function sameDraftContent(first: Draft, second: Draft) {
  return sameScheduleMeaning({ name: first.name, payload: first.payload }, { name: second.name, payload: second.payload });
}
function unknownConflictBase(server: Draft): Draft {
  const payload = Object.fromEntries(Object.keys(server.payload).map((key) => [key, undefined])) as unknown as Payload;
  payload.lessons = [];
  return { ...server, name: '', payload };
}
function conflictLessonLabel(lesson: Lesson, groups: Group[], teachers: Teacher[]) {
  const when = `${DAYS[lesson.day] || `День ${lesson.day + 1}`}, ${lesson.period + 1}-й урок`;
  const groupIds = lesson.groupIds?.length ? lesson.groupIds : lesson.groupId ? [lesson.groupId] : [];
  const teacherIds = lesson.teacherIds?.length ? lesson.teacherIds : lesson.teacherId ? [lesson.teacherId] : [];
  const groupNames = groupIds.map((id) => groups.find((item) => item.id === id)?.name || id);
  const teacherNames = teacherIds.map((id) => teachers.find((item) => item.id === id)?.name || id);
  return `${when}: ${lesson.subject || 'Без предмета'} · ${groupNames.join(', ') || 'без группы'} · ${teacherNames.join(', ') || 'без преподавателя'} · ${lesson.room || 'без кабинета'}`;
}
type DraftSummary = Omit<Draft, 'payload'> & {
  created_at?: string;
  week_start?: string | null;
  is_primary_template?: boolean;
  source_template?: { id?: string; revision?: number } | null;
  draft_kind: 'demo' | 'test' | 'user' | 'unclassified';
  lesson_count: number;
  event_count: number;
  group_count: number;
  day_count: number;
};
type Cell = { row: number; col: number };
type GridColumn = {
  key: string;
  label: string;
  classIds: string[];
  day?: number;
};
type Clip = {
  width: number;
  height: number;
  cells: { row: number; col: number; lessons: Lesson[] }[];
  cutIds: string[];
};
type Issue = {
  lessonId: string;
  severity: 'error' | 'warning';
  type: 'teacher' | 'room' | 'audience' | 'data';
  message: string;
  related?: string;
  category: 'conflict' | 'review' | 'incomplete' | 'import' | 'critical';
};

const DAYS = [
  'Понедельник',
  'Вторник',
  'Среда',
  'Четверг',
  'Пятница',
  'Суббота',
];
const CLIP_MIME = 'application/x-my-island-schedule+json';
const EMPTY_AUDIENCE_PREVIEWS: Record<string, AudiencePreview> = {};
const CARD_COLORS = ['#e5f0fd', '#fce8e5', '#e9f2dd', '#f5e8fa', '#fff0d5', '#ddf2ee', '#ebebfb', '#f7e7ed', '#e8f1f2', '#f2eddd'];
function stableColor(value: string) {
  let hash = 0;
  for (const char of value) hash = (hash * 31 + char.charCodeAt(0)) | 0;
  return CARD_COLORS[Math.abs(hash) % CARD_COLORS.length];
}
function addMinutes(time: string, amount: number) {
  const [h, m] = time.split(':').map(Number);
  const value = h * 60 + m + amount;
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
}
function minutes(time: string) { const [hour, minute] = time.split(':').map(Number); return hour * 60 + minute; }
function lessonTimes(lesson: Lesson, slots: Slot[]) {
  const slot = slots[lesson.period];
  const range = lesson.kind === 'event' || lesson.canonicalRole === 'special_event' || Boolean(lesson.start && lesson.end && slot && (lesson.start !== slot.start || lesson.end !== slot.end));
  return { start: range ? lesson.start || slots[lesson.period]?.start || '' : slots[lesson.period]?.start || '', end: range ? lesson.end || slots[lesson.period]?.end || '' : slots[lesson.period]?.end || '' };
}
function firstOverlappingSlot(start: string, end: string, slots: Slot[]) {
  return slots.findIndex((slot) => minutes(start) < minutes(slot.end) && minutes(slot.start) < minutes(end));
}
function affectedSlots(start: string, end: string, slots: Slot[]) {
  return slots.flatMap((slot, index) => minutes(start) < minutes(slot.end) && minutes(slot.start) < minutes(end) ? [index + 1] : []);
}
function overlapsSlot(lesson: Lesson, day: number, period: number, slots: Slot[]) {
  if (lesson.day !== day || !slots[period]) return false;
  const span = lessonTimes(lesson, slots);
  return Boolean(span.start && span.end && minutes(span.start) < minutes(slots[period].end) && minutes(slots[period].start) < minutes(span.end));
}
function keyOf(cell: Cell) {
  return `${cell.row}:${cell.col}`;
}
function freshId() {
  return crypto.randomUUID();
}
function lessonClasses(lesson: Lesson) {
  if (lesson.activityType === 'club') return [];
  return lesson.classIds?.length ? lesson.classIds : lesson.classId ? [lesson.classId] : [];
}
function lessonDayMode(payload: Payload, lesson: Lesson, classId: string): DayMode | null {
  return payload.day_modes?.[classId]?.[String(lesson.day)] || null;
}
function allLessonClassesHaveMode(payload: Payload, lesson: Lesson, mode: DayMode): boolean {
  const classIds = lessonClasses(lesson);
  return classIds.length > 0 && classIds.every((classId) => lessonDayMode(payload, lesson, classId) === mode);
}
function isSdepIndependentLesson(payload: Payload, lesson: Lesson): boolean {
  if (!allLessonClassesHaveMode(payload, lesson, 'sdep')) return false;
  if (typeof lesson.sdepIndependent === 'boolean') return lesson.sdepIndependent;
  const assignedTeacher = Boolean(lesson.teacherId || lesson.teacherIds?.length);
  return !assignedTeacher && !String(lesson.room || '').trim() && lesson.format !== 'offline' && lesson.kind !== 'event';
}
function lessonNeedsPhysicalRoom(payload: Payload, lesson: Lesson): boolean {
  if (allLessonClassesHaveMode(payload, lesson, 'online')) return false;
  return !isSdepIndependentLesson(payload, lesson);
}
function visualClasses(lesson: Lesson, groups: Group[]) {
  if (lesson.activityType !== 'club') return lessonClasses(lesson);
  if (lesson.displayClassIds?.length) return schedulePlacementClassIds(lesson);
  if (lesson.eligibleGrades?.length) return groups.filter((group) => group.group_type === 'class' && lesson.eligibleGrades?.includes(Number(audienceGrade(group)))).map((group) => group.id);
  return [];
}
function linkedInstructionalGroups(lesson: Lesson, groups: Group[]) {
  const assignment = lesson.canonical?.assignment || {};
  const metadata = assignment.metadata || {};
  const ids = [
    ...(lesson.groupIds || []),
    lesson.groupId,
    ...(lesson.audience?.kind === 'groups' ? lesson.audience.group_ids || [] : []),
    ...(assignment.canonical_group_ids || []),
    ...(metadata.audience?.canonical_group_ids || []),
    ...(metadata.audience_rule?.group_ids || []),
  ].map((value) => String(value || '')).filter(Boolean);
  return [...new Set(ids)]
    .map((id) => groups.find((group) => group.id === id))
    .filter((group): group is Group => Boolean(group && group.group_type !== 'class'));
}
function englishLessonCaption(lesson: Lesson, groups: Group[], localGroups?: Group[]) {
  if (subjectLabel(lesson.subject) !== 'Английский язык') return null;
  const linked = localGroups || linkedInstructionalGroups(lesson, groups);
  if (!linked.length) return { short: 'Англ. · гр. ?', full: 'Английский язык · группа не определена' };
  const parts = linked.map((group) => {
    const number = group.subject_subgroup?.match(/\d+/u)?.[0]
      || group.name.match(/(?:группа|group)\s*(\d+)/iu)?.[1]
      || group.name.match(/^english:(\d+)$/iu)?.[1];
    return number ? { short: `гр. ${number}`, full: `группа ${number}` } : { short: audienceShortLabel(group), full: audienceLabel(group) };
  });
  return {
    short: `Англ. · ${parts.map((part) => part.short).join(', ')}`,
    full: `Английский язык · ${parts.map((part) => part.full).join(', ')}`,
  };
}
function audienceOf(lesson: Lesson, groups: Group[]): AudienceRule {
  if (lesson.activityType === 'elective') return { kind: 'students', student_ids: [] };
  if (lesson.audience) return lesson.audience.kind ? lesson.audience : { kind: 'groups', group_ids: [] };
  const classIds = lessonClasses(lesson);
  const instructional = [...new Set([...(lesson.groupIds || []), lesson.groupId].filter((id) => id && groups.find((group) => group.id === id)?.group_type !== 'class'))];
  const sourceRule = lesson.canonical?.assignment?.metadata?.audience_rule;
  if (sourceRule?.kind === 'students' && sourceRule.include_student_ids?.length)
    return { kind: 'students', student_ids: sourceRule.include_student_ids.map(String) };
  if (sourceRule?.kind === 'whole_grade')
    return { kind: 'classes', grade: String(sourceRule.grade || ''), class_ids: sourceRule.class_group_id ? [String(sourceRule.class_group_id)] : [] };
  if (lesson.audienceMode === 'remainder' || sourceRule?.kind === 'available_slot' || sourceRule?.kind === 'remaining')
    return { kind: 'available_slot', class_ids: sourceRule?.class_group_id ? [String(sourceRule.class_group_id)] : classIds, grade: String(sourceRule?.grade || '') || undefined };
  if (instructional.length) return { kind: 'groups', group_ids: instructional };
  return { kind: 'classes', class_ids: classIds };
}
function projectedClasses(audience: AudienceRule, groups: Group[], students: Student[], fallback: string[] = []) {
  const classes = groups.filter((group) => group.group_type === 'class');
  if (audience.kind === 'classes' || audience.kind === 'available_slot') {
    return audience.class_ids?.length ? audience.class_ids : audience.grade
      ? classes.filter((group) => audienceGrade(group) === audience.grade).map((group) => group.id)
      : [];
  }
  const participantIds = audience.kind === 'students'
    ? new Set(audience.student_ids || [])
    : new Set(students.filter((student) => student.groups.some((id) => (audience.group_ids || []).includes(id))).map((student) => student.id));
  const found = [...new Set(students.filter((student) => participantIds.has(student.id)).flatMap((student) => student.classes))];
  return found.length ? found : fallback;
}
function audiencePatch(audience: AudienceRule, groups: Group[], students: Student[], fallback: string[] = []): Partial<Lesson> {
  const classIds = projectedClasses(audience, groups, students, fallback);
  const groupIds = audience.kind === 'groups' ? [...new Set(audience.group_ids || [])] : audience.kind === 'students' ? [] : classIds;
  return {
    audience,
    audienceMode: 'canonical',
    excludedGroupIds: [],
    groupIds,
    groupId: audience.kind === 'groups' ? groupIds[0] || '' : audience.kind === 'students' ? '' : classIds[0] || '',
    classIds,
    classId: classIds[0] || '',
  };
}
function audienceNeedsReview(lesson: Lesson, preview?: AudiencePreview) {
  if (lesson.activityType === 'elective') return false;
  if (!preview || !lesson.audience_snapshot) return false;
  if (preview.status !== 'ok' || preview.proposed_student_ids == null)
    return Boolean(preview.needs_recalculation);
  const saved = [...new Set(lesson.audience_snapshot.student_ids || [])].sort();
  const proposed = [...new Set(preview.proposed_student_ids)].sort();
  return saved.length !== proposed.length || saved.some((id, index) => id !== proposed[index]);
}
function audienceFromGroup(groupId: string, classIds: string[], groups: Group[]): AudienceRule {
  return groupId && groups.find((group) => group.id === groupId)?.group_type !== 'class'
    ? { kind: 'groups', group_ids: [groupId] }
    : { kind: 'classes', class_ids: groupId ? [groupId] : classIds };
}
function ElectiveClassFields({ groups, classIds, eligibleGrades, onClassIdsChange, onEligibleGradesChange }: {
  groups: Group[];
  classIds: string[];
  eligibleGrades: number[];
  onClassIdsChange: (ids: string[]) => void;
  onEligibleGradesChange: (grades: number[]) => void;
}) {
  const classes = groups.filter((group) => group.group_type === 'class');
  const grades = [...new Set(classes.map((group) => Number(audienceGrade(group))).filter((grade) => Number.isFinite(grade) && grade > 0))].sort((a, b) => a - b);
  return <>
    <fieldset className="constructor-elective-choice">
      <legend>Курс доступен для:</legend>
      {grades.map((grade) => <label key={grade}><input type="checkbox" checked={eligibleGrades.includes(grade)} onChange={(event) => onEligibleGradesChange(event.target.checked ? [...new Set([...eligibleGrades, grade])].sort((a, b) => a - b) : eligibleGrades.filter((item) => item !== grade))} /> {grade} класс</label>)}
      <small>Выбор классов не записывает в курс всех учеников. Именной состав курсов по выбору не ведётся.</small>
    </fieldset>
    <fieldset className="constructor-elective-choice">
      <legend>Размещение в расписании:</legend>
      {classes.map((group) => <label key={group.id}><input type="checkbox" checked={classIds.includes(group.id)} onChange={(event) => onClassIdsChange(event.target.checked ? [...new Set([...classIds, group.id])] : classIds.filter((id) => id !== group.id))} /> {group.name}</label>)}
    </fieldset>
  </>;
}
function isNoLesson(lesson: Lesson) {
  return lesson.specialMark === 'no_lesson' || lesson.canonicalRole === 'explicit_no_lesson' || ['нет урока', 'нет занятия'].some((value) => sameText(lesson.subject, value));
}
function isLunch(lesson: Lesson) {
  const assignment = lesson.canonical?.assignment || {};
  const mark = lesson.specialMark || assignment.metadata?.special_mark;
  const role = lesson.canonicalRole || assignment.assignment_kind || assignment.role;
  return mark === 'lunch' || role === 'lunch' || role === 'special_lunch'
    || role === 'residual' && ['обед', 'lunch'].includes((lesson.subject || lesson.activity || '').trim().toLocaleLowerCase('ru'));
}
function isSpecialMark(lesson: Lesson) { return Boolean(lesson.specialMark) || isLunch(lesson) || isNoLesson(lesson) || lesson.canonicalRole === 'special_event'; }
function editable(target: EventTarget | null) {
  return (
    target instanceof Element &&
    Boolean(target.closest('input, textarea, select, [contenteditable="true"]'))
  );
}
function sameText(left: string, right: string) {
  return (
    left.toLocaleLowerCase('ru').replace(/[^\p{L}\p{N}]/gu, '') ===
    right.toLocaleLowerCase('ru').replace(/[^\p{L}\p{N}]/gu, '')
  );
}
function issuesFor(
  payload: Payload,
  groups: Group[],
  students: Student[],
  previews: Record<string, AudiencePreview>,
  independentPractice: Set<string> = new Set(),
): Issue[] {
  const result: Issue[] = [];
  const add = (
    lessonId: string,
    severity: Issue['severity'],
    message: string,
    related?: string,
    category?: Issue['category'],
  ) => result.push({
    lessonId,
    severity,
    message,
    related,
    category: category || (/уже ведёт|кабинет .+ занят|назначено одновременно/i.test(message) ? 'conflict' : 'incomplete'),
    type: /преподавател/i.test(message) ? 'teacher' : /кабинет/i.test(message) ? 'room' : /ученик|группа «|группа одновременно|межклассов/i.test(message) ? 'audience' : 'data',
  });
  for (const lesson of payload.lessons) {
    const audience = previews[lesson.id];
    if (lesson.kind !== 'event' && subjectLabel(lesson.subject) === 'Английский язык' && !linkedInstructionalGroups(lesson, groups).length)
      result.push({ lessonId: lesson.id, severity: 'warning', type: 'audience', category: 'import', message: 'Не определена группа английского урока в canonical-назначении.' });
    if (audienceNeedsReview(lesson, audience)) add(lesson.id, 'warning', 'Состав учеников отличается от сохранённого или текущие данные требуют проверки. Сохранённый состав не заменён.', undefined, 'review');
    if (!payload.days.includes(lesson.day) || !payload.slots[lesson.period])
      add(lesson.id, 'error', 'День или время занятия не входят в шаблон.', undefined, 'critical');
    if (lesson.kind === 'event' && (!lesson.start || !lesson.end || minutes(lesson.end) <= minutes(lesson.start)))
      add(lesson.id, 'error', 'У события некорректный временной диапазон.', undefined, 'critical');
    if (!isSpecialMark(lesson) && !lesson.subject.trim()) add(lesson.id, 'error', lesson.kind === 'event' ? 'Не указано название события.' : 'Не указан предмет.');
    if (!isSpecialMark(lesson) && lessonNeedsPhysicalRoom(payload, lesson) && !String(lesson.room || '').trim())
      add(lesson.id, 'warning', 'Для очного занятия не указан кабинет.');
    if (lesson.activityType !== 'elective' && audience && audience.status !== 'ok' && !lesson.informational && !lesson.needsClarification && !lesson.importError) {
      result.push({ lessonId: lesson.id, severity: audience.status === 'error' ? 'error' : 'warning', type: 'audience', category: lesson.importError ? 'import' : 'incomplete', message: audience.reason || 'Невозможно точно определить участников.' });
    }
    if (!isSpecialMark(lesson) && shouldWarnMissingTeacher(lesson, independentPractice.has(lesson.id) || isSdepIndependentLesson(payload, lesson)))
      add(lesson.id, 'warning', 'Преподаватель пока не назначен.');
  }
  for (let i = 0; i < payload.lessons.length; i++)
    for (let j = i + 1; j < payload.lessons.length; j++) {
      const a = payload.lessons[i],
        b = payload.lessons[j];
      if (isSpecialMark(a) || isSpecialMark(b)) continue;
      if (a.day !== b.day) continue;
      const sa = lessonTimes(a, payload.slots),
        sb = lessonTimes(b, payload.slots);
      if (!sa.start || !sb.start || sa.start >= sb.end || sb.start >= sa.end) continue;
      const describe = (lesson: Lesson) =>
        `${lessonClasses(lesson).map((id) => groups.find((item) => item.id === id)?.name || id).join(', ') || 'класс'} в ${payload.slots[lesson.period]?.start}`;
      const pair = (message: string) => add(a.id, 'error', `${message}: ${describe(b)}.`, b.id);
      const teacherIdsA = new Set([...(a.teacherIds || []), a.teacherId].filter(Boolean));
      const teacherIdsB = new Set([...(b.teacherIds || []), b.teacherId].filter(Boolean));
      const independentA = isSdepIndependentLesson(payload, a);
      const independentB = isSdepIndependentLesson(payload, b);
      const bothIndependentSdep = independentA && independentB;
      if (!bothIndependentSdep && [...teacherIdsA].some((id) => teacherIdsB.has(id)))
        pair('Преподаватель уже ведёт урок');
      if (!bothIndependentSdep && lessonNeedsPhysicalRoom(payload, a) && lessonNeedsPhysicalRoom(payload, b) && normalizeRoomKey(a.room) && normalizeRoomKey(a.room) === normalizeRoomKey(b.room)) pair(`Кабинет ${normalizeRoomDisplay(a.room)} занят`);
      const studentIdsA = new Set(previews[a.id]?.status === 'ok' ? previews[a.id].student_ids || [] : []);
      const studentIdsB = new Set(previews[b.id]?.status === 'ok' ? previews[b.id].student_ids || [] : []);
      const overlappingStudents = [...studentIdsA].filter((id) => studentIdsB.has(id));
      if (!bothIndependentSdep && overlappingStudents.length && a.activityType !== 'elective' && b.activityType !== 'elective') {
        const sample = overlappingStudents.slice(0, 3).map((studentId) => students.find((item) => item.id === studentId)?.name || studentId);
        const remainder = overlappingStudents.length > sample.length ? ` и ещё ${overlappingStudents.length - sample.length}` : '';
        pair(`Учеников назначено одновременно: ${overlappingStudents.length} (${sample.join(', ')}${remainder})`);
      }
    }
  return result.concat(payload.lessons.filter((lesson) => lesson.needsClarification || lesson.importError).map((lesson) => ({
    lessonId: lesson.id,
    type: 'data' as const,
    category: 'import' as const,
    severity: lesson.importError ? 'error' as const : 'warning' as const,
    message: lesson.clarificationReason || 'Canonical-аудиторию нужно сопоставить с данными школы.',
  })));
}

function readableTeacherText(hex: string) {
  const rgb = [1,3,5].map(i => parseInt(hex.slice(i,i+2),16)/255).map(v => v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4);
  const luminance = .2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2];
  return luminance > .25 ? '#202339' : luminance > .179 ? '#000000' : '#ffffff';
}

export function ScheduleConstructor({ api }: { api: Api }) {
  const [workspace, setWorkspace] = useState<'demo' | 'school'>('demo');
  const workspaceRef = useRef<'demo' | 'school'>('demo');
  const [directoryAccess, setDirectoryAccess] = useState<'checking' | 'demo' | 'school'>('checking');
  const [demoAvailable, setDemoAvailable] = useState(false);
  const [consolidatedSchedule, setConsolidatedSchedule] = useState(false);
  const [publicationOpen, setPublicationOpen] = useState(false);
  const [resolutionIssue, setResolutionIssue] = useState<Issue|null>(null);
  const [drafts, setDrafts] = useState<DraftSummary[]>([]);
  const [schoolDrafts, setSchoolDrafts] = useState<DraftSummary[]>([]);
  const [schoolWeekIndex, setSchoolWeekIndex] = useState<SchoolWeekIndex>({});
  const [schoolStartSection, setSchoolStartSection] = useState<'template' | 'weeks'>('template');
  const [hiddenSchoolDrafts, setHiddenSchoolDrafts] = useState<string[]>([]);
  const [demoDrafts, setDemoDrafts] = useState<DraftSummary[]>([]);
  const [draftFilter, setDraftFilter] = useState<'demo' | 'user' | 'test' | 'unclassified'>('demo');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [groups, setGroups] = useState<Group[]>([]);
  const [teachers, setTeachers] = useState<Teacher[]>([]);
  const [students, setStudents] = useState<Student[]>([]);
  const [knownRooms, setKnownRooms] = useState<string[]>([]);
  const [activities, setActivities] = useState<ScheduleActivity[]>([]);
  const [activityEditor, setActivityEditor] = useState<{ item?: ScheduleActivity; name: string; type: 'club' | 'elective'; aliases: string; teacher_ids: string[]; room: string; eligible_grades: number[] } | null>(null);
  const [catalogSaving, setCatalogSaving] = useState(false);
  const [activityType, setActivityType] = useState<'lesson' | 'club' | 'elective' | 'event'>('lesson');
  const [exporting, setExporting] = useState(false);
  const [lastExport, setLastExport] = useState<{ draftId: string; revision: number; name: string; week: string; status: string; url?: string; reconnect?: boolean } | null>(null);
  const [newWeekStart, setNewWeekStart] = useState('');
  useEffect(() => {
    try { const saved = JSON.parse(window.localStorage.getItem('my-island-hidden-school-drafts:v1') || '[]'); setHiddenSchoolDrafts(Array.isArray(saved) ? saved.filter(item => typeof item === 'string') : []); }
    catch { setHiddenSchoolDrafts([]); }
  }, []);
  const hideSchoolDraft = (id: string) => setHiddenSchoolDrafts((current) => {
    const next = [...new Set([...current, id])];
    try { window.localStorage.setItem('my-island-hidden-school-drafts:v1', JSON.stringify(next)); } catch { /* Hiding is a local convenience only. */ }
    return next;
  });
  const restoreSchoolDraft = (id: string) => setHiddenSchoolDrafts((current) => {
    const next = current.filter((item) => item !== id);
    try { window.localStorage.setItem('my-island-hidden-school-drafts:v1', JSON.stringify(next)); } catch { /* Hiding is a local convenience only. */ }
    return next;
  });
  const activityPath = () => `/api/admin/schedule/activities?${workspace === 'school' ? 'workspace=school&' : ''}include_archived=true`;
  const directoryByWorkspace = useRef<{ demo: { groups: Group[]; teachers: Teacher[]; students: Student[]; rooms: string[] }; school: { groups: Group[]; teachers: Teacher[]; students: Student[]; rooms: string[] } }>({ demo: { groups: [], teachers: [], students: [], rooms: [] }, school: { groups: [], teachers: [], students: [], rooms: [] } });
  const [audienceSnapshot, setAudienceSnapshot] = useState<{ key: string; lessons: Record<string, AudiencePreview> } | null>(null);
  const [previewRetryNonce, setPreviewRetryNonce] = useState(0);
  const [newAudienceSnapshot, setNewAudienceSnapshot] = useState<{ key: string; preview: AudiencePreview } | null>(null);
  const [day, setDay] = useState(0);
  const [viewMode, setViewMode] = useState<'day' | 'week'>('day');
  const [dayModeEditor, setDayModeEditor] = useState<{ classId: string; day: number } | null>(null);
  const [dayModesOpen, setDayModesOpen] = useState(false);
  const [weekTarget, setWeekTarget] = useState('');
  const [targetClassId, setTargetClassId] = useState('');
  const [panelMode, setPanelMode] = useState<'lesson' | 'settings'>('lesson');
  const [panelOpen, setPanelOpen] = useState(false);
  const drawerPointer = useRef<{ pointerId: number; startX: number; startY: number; moved: boolean } | null>(null);
  const suppressDrawerClick = useRef(false);
  const handleDrawerPointerDown = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!event.isPrimary) return;
    drawerPointer.current = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, moved: false };
    suppressDrawerClick.current = false;
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const handleDrawerPointerMove = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const gesture = drawerPointer.current;
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    if (Math.abs(event.clientX - gesture.startX) > 8) gesture.moved = true;
  };
  const handleDrawerPointerUp = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const gesture = drawerPointer.current;
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    drawerPointer.current = null;
    const deltaX = event.clientX - gesture.startX;
    const deltaY = event.clientY - gesture.startY;
    if (gesture.moved) suppressDrawerClick.current = true;
    if (Math.abs(deltaX) >= 40 && Math.abs(deltaX) > Math.abs(deltaY) * 1.25) setPanelOpen(deltaX < 0);
  };
  const handleDrawerPointerCancel = () => { drawerPointer.current = null; suppressDrawerClick.current = true; };
  const toggleDrawerFromClick = () => {
    if (suppressDrawerClick.current) { suppressDrawerClick.current = false; return; }
    setPanelOpen((open) => !open);
  };
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [colorMode, setColorMode] = useState<'teacher' | 'subject' | 'none'>('teacher');
  const [colorPanelOpen, setColorPanelOpen] = useState(false);
  const [zoom, setZoom] = useState(100);
  const [fullScreen, setFullScreen] = useState(false);
  const [fitWidth, setFitWidth] = useState(true);
  const [conflictsOpen, setConflictsOpen] = useState(false);
  const [issueSeverity, setIssueSeverity] = useState('all');
  const [issueType, setIssueType] = useState('all');
  const [anchor, setAnchor] = useState<Cell | null>(null);
  const [focus, setFocus] = useState<Cell | null>(null);
  const [selectedId, setSelectedId] = useState('');
  const [quick, setQuick] = useState('');
  const [creationMode, setCreationMode] = useState<'quick' | 'form' | 'event' | 'lunch' | 'no_lesson'>('quick');
  const [creationError, setCreationError] = useState('');
  const [renameValue, setRenameValue] = useState<string | null>(null);
  const [renameError, setRenameError] = useState('');
  const [recalculationOpen, setRecalculationOpen] = useState(false);
  const [quickChoice, setQuickChoice] = useState<{ subject?: string; groupId?: string; teacherId?: string; room?: string; classIds?: string[] }>({});
  const [newTeacherManuallySelected, setNewTeacherManuallySelected] = useState(false);
  const [newLesson, setNewLesson] = useState<{ subject: string; groupId: string; teacherId: string; teacherIds?: string[]; room: string; classIds: string[]; audience?: AudienceRule; informational?: boolean; day?: number; period?: number; start?: string; end?: string; place?: string; description?: string; format?: string; blockId?: string; blockName?: string; activityCatalogId?: string; activityNameSnapshot?: string; eligibleGrades?: number[]; confirmedParticipantIds?: string[]; electiveDirection?: 'digital' }>({ subject: '', groupId: '', teacherId: '', room: '', classIds: [] });
  const [query, setQuery] = useState('');
  const [classFilter, setClassFilter] = useState('');
  const [teacherFilter, setTeacherFilter] = useState('');
  const [subjectFilter, setSubjectFilter] = useState('');
  const [teacherQuery, setTeacherQuery] = useState('');
  const [density, setDensity] = useState<'comfortable' | 'compact'>(
    'comfortable',
  );
  const [status, setStatus] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  const [versionConflict, setVersionConflict] = useState<{ base: Draft | null; server: Draft; local: Draft; conflicts: ScheduleMergeConflict[] } | null>(null);
  const [conflictActionBusy, setConflictActionBusy] = useState(false);
  const [conflictChoices, setConflictChoices] = useState<Record<string, 'local' | 'server'>>({});
  const [dirty, setDirty] = useState(false);
  const [resolutionRevision,setResolutionRevision]=useState<number|null>(null);
  const [resolutionStates,setResolutionStates] = useState<Record<string,Array<{id:string;type:string;reason:string;lesson_ids?:string[]}>>>({});
  const [history, setHistory] = useState<Payload[]>([]);
  const [future, setFuture] = useState<Payload[]>([]);
  const [context, setContext] = useState<{ x: number; y: number } | null>(null);
  const root = useRef<HTMLDivElement>(null);
  useConstructorLayout(root, draft?.id);
  const quickInput = useRef<HTMLInputElement>(null);
  const clip = useRef<Clip | null>(null);
  const dragging = useRef<{
    id: string;
    destination?: Cell;
    handled?: boolean;
    duplicate?: boolean;
  } | null>(null);
  const columnHeaders = useRef<Record<string, HTMLDivElement | null>>({});
  const gridWrap = useRef<HTMLDivElement | null>(null);
  const columnResize = useRef<{ pointerId: number; key: string; startX: number; startWidth: number; scale: number; widths: Record<string, number> } | null>(null);
  const [resizePreview, setResizePreview] = useState<Record<string, number> | null>(null);
  const revision = useRef(0);
  const baseDraftRef = useRef<Draft | null>(null);
  const schedulePath = (suffix = '') => workspace === 'school'
    ? `/api/admin/schedule/experiments/school${suffix}`
    : `/api/admin/schedule/experiments${suffix}`;
  const editActivity = (item?: ScheduleActivity, requestedType: 'club' | 'elective' = 'club') => {
    setActivityEditor({ item, name: item?.name || '', type: item?.type === 'elective' ? 'elective' : requestedType,
      aliases: (item?.aliases || []).join(', '), teacher_ids: item?.teacher_ids || [], room: item?.room || '', eligible_grades: item?.eligible_grades || [] });
  };
  const saveActivityEditor = async () => {
    if (!activityEditor || !activityEditor.name.trim() || catalogSaving) return;
    const { item, ...fields } = activityEditor;
    const record = { ...fields, name: fields.name.trim(), aliases: fields.aliases.split(',').map((s) => s.trim()).filter(Boolean), status: item?.status || 'active' };
    setCatalogSaving(true);
    try {
      const saved = await api<ScheduleActivity>(item ? `${activityPath().split('?')[0]}/${encodeURIComponent(item.id)}${workspace === 'school' ? '?workspace=school' : ''}` : activityPath(), { method: item ? 'PUT' : 'POST', body: JSON.stringify({ ...record, ...(item ? { expected_revision: item.revision } : {}) }) });
      setActivities((items) => item ? items.map((entry) => entry.id === saved.id ? saved : entry) : [...items, saved]);
      setStatus(`Каталог: «${saved.name}» сохранён.`);
      setActivityEditor(null);
    } catch (error) { setStatus(`Не удалось сохранить запись каталога: ${String((error as Error).message || error)}`); }
    finally { setCatalogSaving(false); }
  };
  const archiveActivity = async (item: ScheduleActivity) => {
    try {
      const saved = await api<ScheduleActivity>(`${activityPath().split('?')[0]}/${encodeURIComponent(item.id)}${workspace === 'school' ? '?workspace=school' : ''}`, { method: 'PUT', body: JSON.stringify({ ...item, status: item.status === 'active' ? 'archived' : 'active', expected_revision: item.revision }) });
      setActivities((items) => items.map((entry) => entry.id === saved.id ? saved : entry));
      setStatus(`«${saved.name}» ${saved.status === 'archived' ? 'перенесён в архив' : 'восстановлен'}.`);
    } catch (error) { setStatus(`Не удалось изменить каталог: ${String((error as Error).message || error)}`); }
  };
  const exportDraft = async () => {
    if (!draft || dirty || saving || exporting) return;
    setExporting(true);
    const exportingRevision = revision.current;
    try {
      const path = workspace === 'school' ? `/api/admin/schedule/experiments/school/${encodeURIComponent(draft.id)}/export` : `/api/admin/schedule/experiments/${encodeURIComponent(draft.id)}/export`;
      const result = await api<{ status: 'exported' | 'existing' | 'authorization_required'; url?: string; message?: string; reconnect_available?: boolean }>(path, { method: 'POST', body: JSON.stringify({ expected_revision: revision.current }) });
      setLastExport({ draftId: draft.id, revision: exportingRevision, name: draft.name, week: draft.payload.week_start || 'неделя не задана', status: result.status, url: result.url, reconnect: result.reconnect_available });
      setStatus(result.status === 'authorization_required' ? result.message || 'Для экспорта требуется подключить Google аккаунт.' : `${result.status === 'existing' ? 'Экспорт уже существует' : 'Экспорт выполнен'} · ${draft.name} · ${draft.payload.week_start || 'неделя не задана'}`);
    } catch (error) { setStatus(`Не удалось экспортировать черновик: ${String((error as Error).message || error)}`); }
    finally { setExporting(false); }
  };
  const createWeek = async () => {
    if (!draft || dirty || saving || !newWeekStart) return;
    const parts = newWeekStart.split('-').map(Number);
    const date = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
    if (date.getUTCDay() !== 1) { setStatus('Выберите понедельник для начала недели.'); return; }
    try {
      const result = await api<Draft>(schedulePath(`/${encodeURIComponent(draft.id)}/week`), { method: 'POST', body: JSON.stringify({ expected_revision: revision.current, week_start: newWeekStart }) });
      await refreshDrafts();
      await open(result.id);
      setStatus(`Создана отдельная неделя с ${newWeekStart}.`);
    } catch (error) { setStatus(`Не удалось создать неделю: ${String((error as Error).message || error)}`); }
  };
  const draftRef = useRef<Draft | null>(null);
  const changeVersion = useRef(0);
  const savingRef = useRef(false);
  const previewQueue = useRef<Promise<void>>(Promise.resolve());
  const queuePreview = (task: () => Promise<void>) => {
    const pending = previewQueue.current.catch(() => undefined).then(task);
    previewQueue.current = pending.catch(() => undefined);
    return pending;
  };
  const load = async () => {
    const capabilities = await api<{ demo_available: boolean; school_available: boolean; consolidated_schedule?: boolean }>('/api/admin/schedule/experiments/capabilities');
    setDemoAvailable(capabilities.demo_available);
    setConsolidatedSchedule(Boolean(capabilities.consolidated_schedule));
    let schoolDirectoryError = '';
    const [list, schoolList, schoolDirectory, groupData, teacherData, studentData, scheduleData] = await Promise.all([
        capabilities.demo_available ? api<{ items: DraftSummary[] }>('/api/admin/schedule/experiments') : Promise.resolve({ items: [] }),
        capabilities.school_available ? api<{ items: DraftSummary[] }>('/api/admin/schedule/experiments/school').catch(() => ({ items: [] })) : Promise.resolve({ items: [] }),
        capabilities.school_available ? api<{ groups?: Raw[]; teachers?: Raw[]; students?: Raw[]; rooms?: Raw[] }>('/api/admin/schedule/experiments/school/directory').catch((error) => { schoolDirectoryError = String((error as Error).message || error); return {} as { groups?: Raw[]; teachers?: Raw[]; students?: Raw[]; rooms?: Raw[] }; }) : Promise.resolve({} as { groups?: Raw[]; teachers?: Raw[]; students?: Raw[]; rooms?: Raw[] }),
        capabilities.demo_available ? api<{ items: Raw[] }>('/api/admin/groups?include_archived=true') : Promise.resolve({ items: [] }),
        capabilities.demo_available ? api<{ items: Raw[] }>('/api/admin/people?kind=teacher') : Promise.resolve({ items: [] }),
        capabilities.demo_available ? api<{ items: Raw[] }>('/api/admin/people?kind=student') : Promise.resolve({ items: [] }),
        capabilities.school_available || capabilities.demo_available ? api<SchoolWeekIndex & { blocks: Raw[]; weeks?: { week_start: string }[] }>(
          '/api/admin/schedule/observability?editor=true',
        ).catch((error) => ({ blocks: [], weeks: [] as { week_start: string }[], load_error: String((error as Error).message || error) })) : Promise.resolve({ blocks: [], weeks: [] as { week_start: string }[] }),
      ]);
    setDrafts(list.items || []);
    setDemoDrafts(list.items || []);
    setSchoolDrafts(schoolList.items || []);
    setSchoolWeekIndex(scheduleData as SchoolWeekIndex);
    const schoolReady = capabilities.school_available && Boolean(schoolDirectory.groups?.length);
    setDirectoryAccess(schoolReady ? 'school' : capabilities.demo_available ? 'demo' : 'school');
    if (schoolDirectoryError) setStatus(`Справочник школы временно недоступен: ${schoolDirectoryError}`);
    if (!capabilities.demo_available && schoolReady) {
      workspaceRef.current = 'school';
      setWorkspace('school');
      setDrafts(schoolList.items || []);
    }
    setDraftFilter((current) => current === 'demo' && !(list.items || []).some((item) => item.draft_kind === 'demo') ? 'user' : current);
      const demoGroups =
      (groupData.items || []).map((item) => ({
        id: String(item.id),
        name: audienceLabel(item),
        group_type: String(item.group_type),
        role: String(item.role || ''),
        semantic_dimension: String(item.semantic_dimension || ''),
        subject: subjectLabel(item.subject),
        subject_subgroup: String(item.subject_subgroup || ''),
        archived_at: item.archived_at || null,
        base_class_name: String(item.base_class_name || ''),
        course: String(item.course || item.course_name || ''),
        exam_track: groupCourseContext(item),
      }));
    const demoTeachers =
      (teacherData.items || []).map((item) => ({
        id: String(item.id),
        name: String(item.display_name || item.name),
        assignments: (item.teacher_assignments || []).filter((a: Raw) => assignmentIsActive(a)).map((a: Raw) => ({
          group_id: a.group_id ? String(a.group_id) : '',
          subject: subjectLabel(a.subject),
          course: String(a.course || a.course_name || ''),
          grade_level: String(a.grade_level || ''),
          exam_track: String(a.assignment_exam_track || a.exam_track || ''),
        })),
      }));
    const demoStudents =
      (studentData.items || []).map((item) => ({
        id: String(item.id),
        name: String(item.display_name || item.name),
        groups: [...new Set([...(item.groups || []).map((g: Raw) => String(typeof g === 'object' ? g.id : g)), ...studentMemberships(item).map((g: Raw) => String(g.group_id || g.id))])],
        classes: (item.base_classes || []).map((g: Raw) => String(g.id)),
      }));
    const mapGroups = (items: Raw[] = []) => items.map((item) => ({ id: String(item.id), name: audienceLabel(item), group_type: String(item.group_type), role: String(item.role || ''), semantic_dimension: String(item.semantic_dimension || ''), subject: subjectLabel(item.subject), archived_at: item.archived_at || null, subject_subgroup: String(item.subject_subgroup || ''), base_class_name: String(item.base_class_name || ''), course: String(item.course || item.course_name || ''), exam_track: groupCourseContext(item) }));
    const schoolGroups = mapGroups(schoolDirectory.groups || []);
    const schoolTeachers = (schoolDirectory.teachers || []).map((item) => ({ id: String(item.id), name: String(item.display_name || item.name), assignments: (item.assignments || item.teacher_assignments || []).filter((a: Raw) => assignmentIsActive(a)).map((a: Raw) => ({ group_id: a.group_id ? String(a.group_id) : '', subject: subjectLabel(a.subject), course: String(a.course || a.course_name || ''), grade_level: a.grade_level == null ? '' : String(a.grade_level), exam_track: String(a.exam_track || '') })) }));
    const schoolStudents = (schoolDirectory.students || []).map((item) => ({ id: String(item.id), name: String(item.display_name || item.name), groups: [...new Set([...(item.groups || []).map((g: Raw) => String(typeof g === 'object' ? g.id : g)), ...studentMemberships(item).map((g: Raw) => String(g.group_id || g.id))])], classes: (item.base_classes || item.classes || []).map((g: Raw) => String(typeof g === 'object' ? g.id : g)) }));
    const demoRooms =
      [
        ...new Set(
          (scheduleData.blocks || [])
            .flatMap((block) =>
              [
                ...(block.baseline_assignments || []),
                ...(block.effective_assignments || []),
              ].map((item: Raw) =>
                String(item.room || item.metadata?.room || ''),
              ),
            )
            .filter(Boolean),
        ),
      ].sort();
    const schoolRooms = (schoolDirectory.rooms || []).map((r: Raw) => String(typeof r === 'object' ? r.name || r.room || r.id : r));
    directoryByWorkspace.current = { demo: { groups: demoGroups, teachers: demoTeachers, students: demoStudents, rooms: demoRooms }, school: { groups: schoolGroups, teachers: schoolTeachers, students: schoolStudents, rooms: schoolRooms } };
    const selectedWorkspace = capabilities.demo_available ? workspaceRef.current : 'school';
    try { const catalog = await api<{ items?: ScheduleActivity[] }>(`/api/admin/schedule/activities?${selectedWorkspace === 'school' ? 'workspace=school&' : ''}include_archived=true`); setActivities(catalog.items || []); } catch { setActivities([]); }
    const initialDirectory = directoryByWorkspace.current[selectedWorkspace];
    setGroups(initialDirectory.groups); setTeachers(initialDirectory.teachers); setStudents(initialDirectory.students); setKnownRooms(initialDirectory.rooms);
  };
  useEffect(() => {
    void load().catch((error) => { setDirectoryAccess('school'); setWorkspace('school'); setStatus(String(error.message || error)); });
  }, [api]);
  useEffect(() => {
    const refreshDirectory = () => { void load().then(() => setStatus('Справочник обновлён')).catch((error) => setStatus(String(error.message || error))); };
    window.addEventListener('school-directory-updated', refreshDirectory);
    return () => window.removeEventListener('school-directory-updated', refreshDirectory);
  }, [api]);
  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);
  const refreshDrafts = async () => {
    const items = (await api<{ items: DraftSummary[] }>(schedulePath())).items || [];
    setDrafts(items);
    if (workspace === 'school') setSchoolDrafts(items); else setDemoDrafts(items);
  };
  const open = async (id: string) => {
    try {
      const value = await api<Draft>(
        schedulePath(`/${encodeURIComponent(id)}`),
      );
      let recovered: LocalDraftRecovery | null = null;
      try {
        const stored = window.localStorage.getItem(recoveryKey(workspace, id));
        if (stored) {
          const parsed = JSON.parse(stored) as LocalDraftRecovery;
          if (parsed.id === id && parsed.payload && Array.isArray(parsed.payload.lessons)) recovered = parsed;
        }
      } catch { /* Ignore an invalid or unavailable recovery copy. */ }
      const opened = recovered ? { ...value, name: recovered.name, payload: recovered.payload, revision: recovered.revision } : value;
      setDraft(opened);
      baseDraftRef.current = recovered ? recovered.base || (recovered.revision === value.revision ? value : null) : value;
      setVersionConflict(null);
      clip.current = null;
      revision.current = opened.revision;
      changeVersion.current++;
      setDirty(Boolean(recovered));
      setSaving(false);
      setSaveFailed(false);
      setHistory([]);
      setFuture([]);
      setSelectedId('');
      setAnchor(null);
      setFocus(null);
      setDay(opened.payload.days[0] || 0);
      setWeekTarget('');
      setStatus(recovered ? 'Восстановлена локальная копия несохранённых изменений; она появится на сервере после сохранения.' : '');
    } catch (error) {
      setStatus(String((error as Error).message));
    }
  };
  const create = async (source: 'empty' | 'canonical') => {
    try {
      const name =
        source === 'empty' ? 'Новый шаблон' : 'Копия действующего расписания';
      const value = await api<Draft>(schedulePath(), {
        method: 'POST',
        body: JSON.stringify({ name, source, payload: { draft_kind: 'user' } }),
      });
      await refreshDrafts();
      setDraftFilter('user');
      await open(value.id);
      if (source === 'canonical') {
        const result = value.transfer_result || { transferred: value.payload.lessons.length, needs_clarification: 0, import_errors: 0 };
        setStatus(`Копия создана: перенесено ${result.transferred} занятий; требуют уточнения ${result.needs_clarification}; ошибок импорта ${result.import_errors}.`);
      } else {
        setStatus('Создана пустая неделя. В сетке нет занятий.');
      }
    } catch (error) {
      setStatus(String((error as Error).message));
    }
  };
  const rename = async (item: DraftSummary) => {
    const name = window.prompt('Название черновика', item.name)?.trim();
    if (!name || name === item.name) return;
    try {
      const current = draft?.id === item.id ? baseDraftRef.current : await api<Draft>(schedulePath(`/${encodeURIComponent(item.id)}`));
      if (!current) return;
      const updated = await api<Draft>(schedulePath(`/${encodeURIComponent(item.id)}`), {
        method: 'PUT',
        body: JSON.stringify({ expected_revision: current.revision, name, payload: current.payload }),
      });
      await refreshDrafts();
      if (draft?.id === item.id) {
        revision.current = updated.revision;
        baseDraftRef.current = updated;
        setDraft((shown) => shown?.id === item.id ? { ...shown, name: updated.name, revision: updated.revision } : shown);
      }
    } catch (error) { setStatus(String((error as Error).message || error)); }
  };
  const renameOpenDraft = async () => {
    const current = draftRef.current;
    const base = baseDraftRef.current;
    const name = renameValue?.trim();
    if (!name) { setRenameError('Введите непустое название.'); return; }
    if (!current || !base || savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    try {
      const updated = await api<Draft>(schedulePath(`/${encodeURIComponent(current.id)}`), {
        method: 'PUT', body: JSON.stringify({ expected_revision: revision.current, name, payload: base.payload }),
      });
      revision.current = updated.revision;
      baseDraftRef.current = updated;
      setDraft((shown) => shown?.id === current.id ? { ...shown, name, revision: updated.revision, updated_at: updated.updated_at } : shown);
      setRenameValue(null);
      setRenameError('');
      setStatus('Название сохранено. Остальные изменения остаются в черновике.');
      void refreshDrafts();
    } catch (error) { setRenameError(String((error as Error).message || error)); }
    finally { savingRef.current = false; setSaving(false); }
  };
  const duplicate = async (item: DraftSummary) => {
    try {
      const copy = await api<Draft>(schedulePath(`/${encodeURIComponent(item.id)}/duplicate`), { method: 'POST' });
      await refreshDrafts();
      setDraftFilter(item.draft_kind === 'demo' ? 'user' : item.draft_kind);
      setStatus(`Создана копия «${copy.name}».`);
    } catch (error) { setStatus(String((error as Error).message || error)); }
  };
  const remove = async (item: DraftSummary) => {
    if (!window.confirm(`Удалить экспериментальный черновик «${item.name}»?`)) return;
    try {
      await api(schedulePath(`/${encodeURIComponent(item.id)}`), { method: 'DELETE' });
      setDrafts((items) => items.filter((draftItem) => draftItem.id !== item.id));
      if (draft?.id === item.id) { setDraft(null); setDirty(false); }
      setStatus(`Черновик «${item.name}» удалён.`);
    } catch (error) { setStatus(String((error as Error).message || error)); }
  };
  const mutate = (change: (payload: Payload) => Payload) => {
    setDraft((current) => {
      if (!current) return current;
      const next = change(current.payload);
      if (next === current.payload) return current;
      changeVersion.current++;
      setSaveFailed(false);
      setHistory((items) => [...items.slice(-49), current.payload]);
      setFuture([]);
      setDirty(true);
      return { ...current, payload: next };
    });
  };
  const updateDayMode = (classId: string, selectedDay: number, mode: DayMode | 'normal') => {
    mutate((state) => {
      const dayModes = { ...(state.day_modes || {}) };
      const classModes = { ...(dayModes[classId] || {}) };
      if (mode === 'normal') delete classModes[String(selectedDay)];
      else classModes[String(selectedDay)] = mode;
      if (Object.keys(classModes).length) dayModes[classId] = classModes;
      else delete dayModes[classId];
      return { ...state, day_modes: Object.keys(dayModes).length ? dayModes : undefined };
    });
  };
  const undo = () => {
    if (!draft || !history.length) return;
    changeVersion.current++;
    setSaveFailed(false);
    setFuture((items) => [...items, draft.payload]);
    setDraft({ ...draft, payload: history[history.length - 1] });
    setHistory((items) => items.slice(0, -1));
    setDirty(true);
  };
  const redo = () => {
    if (!draft || !future.length) return;
    changeVersion.current++;
    setSaveFailed(false);
    setHistory((items) => [...items, draft.payload]);
    setDraft({ ...draft, payload: future[future.length - 1] });
    setFuture((items) => items.slice(0, -1));
    setDirty(true);
  };
  const save = async () => {
    const current = draftRef.current;
    if (!current || savingRef.current || !dirty) return !dirty;
    const savedVersion = changeVersion.current;
    const expectedRevision = revision.current;
    const acceptSaved = (value: Draft) => {
      revision.current = value.revision;
      baseDraftRef.current = value;
      setDraft((shown) =>
        shown && shown.id === value.id
          ? { ...shown, ...(changeVersion.current === savedVersion ? { payload: value.payload } : {}), revision: value.revision, updated_at: value.updated_at }
          : shown,
      );
      if (changeVersion.current === savedVersion) {
        setDirty(false);
        try { window.localStorage.removeItem(recoveryKey(workspace, current.id)); } catch { /* Keep the in-memory save result. */ }
      }
    };
    const tryMergeSave = async (base: Draft | null, local: Draft, server: Draft, retry: number): Promise<{ saved?: Draft; server: Draft; conflicts: ScheduleMergeConflict[]; racedAgain?: boolean }> => {
      const mergeBase = base || unknownConflictBase(server);
      const merged = mergeScheduleDrafts(mergeBase, local, server);
      if (merged.conflicts.length) return { server, conflicts: merged.conflicts };
      try {
        const saved = await api<Draft>(schedulePath(`/${encodeURIComponent(server.id)}`), {
          method: 'PUT',
          body: JSON.stringify({ expected_revision: server.revision, name: merged.draft.name, payload: merged.draft.payload }),
        });
        return { saved, server, conflicts: [] };
      } catch (mergeError) {
        const failure = mergeError as Error & { status?: number };
        if (failure.status !== 409) throw mergeError;
        const latest = await api<Draft>(schedulePath(`/${encodeURIComponent(server.id)}`));
        if (sameDraftContent(local, latest)) return { saved: latest, server: latest, conflicts: [] };
        if (retry < 1) return tryMergeSave(base, local, latest, retry + 1);
        const finalMerge = mergeScheduleDrafts(base || unknownConflictBase(latest), local, latest);
        return { server: latest, conflicts: finalMerge.conflicts, racedAgain: finalMerge.conflicts.length === 0 };
      }
    };
    savingRef.current = true;
    setSaving(true);
    setSaveFailed(false);
    try {
      const value = await api<Draft>(
        schedulePath(`/${encodeURIComponent(current.id)}`),
        {
          method: 'PUT',
          body: JSON.stringify({
            expected_revision: expectedRevision,
            name: current.name,
            payload: current.payload,
          }),
        },
      );
      acceptSaved(value);
      setStatus('Черновик сохранён.');
      void refreshDrafts().catch(() => setStatus('Черновик сохранён. Список черновиков обновится при следующем открытии.'));
      return true;
    } catch (error) {
      const failure = error as Error & { status?: number };
      if (failure.status === 409) {
        try {
          const server = await api<Draft>(schedulePath(`/${encodeURIComponent(current.id)}`));
          const localNow = draftRef.current?.id === current.id ? draftRef.current : current;
          if (sameDraftContent(localNow, server)) {
            revision.current = server.revision;
            baseDraftRef.current = server;
            setDraft(server);
            setDirty(false);
            setSaveFailed(false);
            try { window.localStorage.removeItem(recoveryKey(workspace, current.id)); } catch { /* Server content is authoritative and matches this draft. */ }
            setStatus('Черновик уже содержит эти изменения. Версия синхронизирована.');
            return true;
          }
          const local = localNow;
          const base = baseDraftRef.current;
          const merged = await tryMergeSave(base, local, server, 0);
          if (merged.saved) {
            acceptSaved(merged.saved);
            setStatus('Ваши изменения объединены с обновлениями сервера и сохранены.');
            void refreshDrafts().catch(() => undefined);
            return true;
          }
          if (merged.racedAgain) {
            setDirty(true);
            setSaveFailed(false);
            setStatus('Сервер снова изменился во время объединения. Изменения сохранены в этой вкладке; нажмите «Сохранить черновик», чтобы сверить новую версию.');
            return false;
          }
          setConflictChoices({});
          setVersionConflict({ base, server: merged.server, local, conflicts: merged.conflicts });
          setSaveFailed(true);
          setStatus('Есть изменения одного и того же урока. Выберите, какой вариант сохранить.');
          return false;
        } catch (readError) {
          const readFailure = readError as Error & { status?: number };
          setSaveFailed(true);
          setStatus(`HTTP 409: расписание изменилось в другой вкладке или сеансе. Не удалось получить актуальную версию${readFailure.status ? ` (HTTP ${readFailure.status})` : ''}: ${String(readFailure.message || readError)}. Ваши изменения сохранены в этой вкладке.`);
          return false;
        }
      }
      if (!failure.status) {
        try {
          const persisted = await api<Draft>(schedulePath(`/${encodeURIComponent(current.id)}`));
          if (persisted.revision > expectedRevision && sameDraftContent(current, persisted)) {
            acceptSaved(persisted);
            setStatus('Черновик сохранён: сервер подтвердил запись при повторном чтении.');
            return true;
          }
          setSaveFailed(true);
          setStatus(persisted.revision > expectedRevision
            ? 'Ответ PUT не получен. На сервере обнаружена другая версия черновика; локальные изменения сохранены для сверки.'
            : 'Ответ PUT не получен. Сервер доступен, но запись черновика не подтверждена; локальные изменения сохранены.');
          return false;
        } catch (checkError) {
          const check = checkError as Error & { status?: number };
          if (check.status) {
            setSaveFailed(true);
            setStatus(`Не удалось сохранить: ответ PUT не получен; повторное чтение вернуло HTTP ${check.status}: ${check.message}. Локальная копия сохранена.`);
            return false;
          }
        }
      }
      setSaveFailed(true);
      const reason = failure.status ? `HTTP ${failure.status}: ${failure.message}` : String(failure.message || error);
      setStatus(
        `Не удалось сохранить: ${reason}. Изменения остались в этой вкладке.`,
      );
      return false;
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };
  const loadServerConflictVersion = () => {
    if (!versionConflict) return;
    const { server } = versionConflict;
    revision.current = server.revision;
    baseDraftRef.current = server;
    changeVersion.current++;
    setDraft(server);
    setDirty(false);
    setSaveFailed(false);
    setHistory([]);
    setFuture([]);
    setVersionConflict(null);
    try { window.localStorage.removeItem(recoveryKey(workspace, server.id)); } catch { /* The user explicitly chose the server version. */ }
    setStatus(`Загружена серверная версия черновика, ревизия ${server.revision}.`);
  };
  const saveLocalConflictCopy = async () => {
    if (!versionConflict || conflictActionBusy) return;
    setConflictActionBusy(true);
    let copyId = '';
    try {
      const local = draftRef.current?.id === versionConflict.local.id ? draftRef.current : versionConflict.local;
      const copy = await api<Draft>(schedulePath(`/${encodeURIComponent(versionConflict.server.id)}/duplicate`), {
        method: 'POST',
      });
      copyId = copy.id;
      const resolvedCopy = await api<Draft>(schedulePath(`/${encodeURIComponent(copy.id)}`), {
        method: 'PUT',
        body: JSON.stringify({
          expected_revision: copy.revision,
          name: `${local.name} (копия)`,
          payload: { ...local.payload, draft_kind: 'user' },
        }),
      });
      revision.current = resolvedCopy.revision;
      baseDraftRef.current = resolvedCopy;
      changeVersion.current++;
      setDraft(resolvedCopy);
      setDirty(false);
      setSaveFailed(false);
      setHistory([]);
      setFuture([]);
      setVersionConflict(null);
      setStatus(`Открыта отдельная копия «${resolvedCopy.name}». Исходная и серверная версии сохранены.`);
      void refreshDrafts().catch(() => setStatus('Ваша версия сохранена отдельным черновиком. Список черновиков обновится при следующем открытии.'));
    } catch (error) {
      if (copyId) {
        try { await api(schedulePath(`/${encodeURIComponent(copyId)}`), { method: 'DELETE' }); } catch { /* Keep any failed cleanup visible for manual recovery. */ }
      }
      const failure = error as Error & { status?: number };
      setSaveFailed(true);
      setStatus(`Не удалось создать отдельную копию${failure.status ? ` (HTTP ${failure.status})` : ''}: ${String(failure.message || error)}. Исходные изменения остались в этой вкладке.`);
    } finally {
      setConflictActionBusy(false);
    }
  };
  const resolveVersionConflict = async () => {
    if (!versionConflict || conflictActionBusy) return;
    setConflictActionBusy(true);
    try {
      const local = draftRef.current?.id === versionConflict.local.id ? draftRef.current : versionConflict.local;
      const server = versionConflict.server;
      const merge = mergeScheduleDrafts(versionConflict.base || unknownConflictBase(server), local, server, conflictChoices);
      const resolved = await api<Draft>(schedulePath(`/${encodeURIComponent(server.id)}`), {
        method: 'PUT',
        body: JSON.stringify({
          expected_revision: server.revision,
          name: merge.draft.name,
          payload: merge.draft.payload,
        }),
      });
      revision.current = resolved.revision;
      baseDraftRef.current = resolved;
      changeVersion.current++;
      setDraft(resolved);
      setDirty(false);
      setSaveFailed(false);
      setHistory([]);
      setFuture([]);
      setVersionConflict(null);
      try { window.localStorage.removeItem(recoveryKey(workspace, resolved.id)); } catch { /* Server confirmed the resolved content. */ }
      setStatus('Выбранные изменения сохранены в черновике.');
      void refreshDrafts().catch(() => undefined);
    } catch (error) {
      const failure = error as Error & { status?: number };
      if (failure.status === 409 && versionConflict) {
        try {
          const latest = await api<Draft>(schedulePath(`/${encodeURIComponent(versionConflict.server.id)}`));
          const local = draftRef.current?.id === versionConflict.local.id ? draftRef.current : versionConflict.local;
          const base = versionConflict.base;
          const comparison = mergeScheduleDrafts(base || unknownConflictBase(latest), local, latest);
          setConflictChoices({});
          setVersionConflict({ base, local, server: latest, conflicts: comparison.conflicts });
          setSaveFailed(true);
          setStatus('Черновик снова изменился. Сверьте новые отличия и повторите разрешение.');
          return;
        } catch { /* Fall through with the precise original HTTP status. */ }
      }
      setSaveFailed(true);
      setStatus(`Не удалось сохранить выбранный вариант${failure.status ? ` (HTTP ${failure.status})` : ''}: ${String(failure.message || error)}. Ваши изменения остались в этой вкладке.`);
    } finally {
      setConflictActionBusy(false);
    }
  };
  useEffect(() => {
    if (!draft || !dirty) return;
    const recovery: LocalDraftRecovery = { id: draft.id, revision: revision.current, name: draft.name, payload: draft.payload, base: baseDraftRef.current };
    try { window.localStorage.setItem(recoveryKey(workspace, draft.id), JSON.stringify(recovery)); } catch { /* The in-memory draft and unload guard remain active. */ }
  }, [draft, dirty, workspace]);
  useEffect(() => {
    const before = (event: BeforeUnloadEvent) => {
      if (dirty) event.preventDefault();
    };
    window.addEventListener('beforeunload', before);
    return () => window.removeEventListener('beforeunload', before);
  }, [dirty]);
  const payload = draft?.payload;
  const colorPairs = useMemo(() => {
    const slots = new Map<string, Set<string>>();
    for (const lesson of payload?.lessons || []) {
      const key = `${lesson.day}:${lesson.period}`;
      if (!slots.has(key)) slots.set(key, new Set());
      for (const id of [lesson.teacherId, ...(lesson.teacherIds || [])].filter(Boolean)) slots.get(key)!.add(id);
    }
    const pairs = new Map<string, [string,string]>();
    for (const ids of slots.values()) { const list = [...ids].sort(); for (let i=0;i<list.length;i++) for (let j=i+1;j<list.length;j++) pairs.set(JSON.stringify([list[i],list[j]]), [list[i],list[j]]); }
    return [...pairs.values()];
  }, [payload]);
  const colorScope = `my-island-school:${String((import.meta as ImportMeta & {env?:Record<string,string>}).env?.VITE_API_BASE_URL || 'same-origin')}:${workspace}`;
  const teacherRegistry = useSchoolTeacherColors(api, workspace, colorScope, teachers, colorPairs, consolidatedSchedule);

  const previewPayload = useMemo(() => payload ? audiencePreviewPayload(payload) : null, [payload]);
  const previewKey = previewPayload ? JSON.stringify(previewPayload) : '';
  const audiencePreviews = audienceSnapshot && audienceSnapshot.key === previewKey ? audienceSnapshot.lessons : EMPTY_AUDIENCE_PREVIEWS;
  const displayedClasses = (lesson: Lesson) => {
    if (lesson.activityType === 'club') return visualClasses(lesson, groups);
    if (lesson.activityType === 'elective') return lessonClasses(lesson);
    const rule = audienceOf(lesson, groups);
    const declared = lessonClasses(lesson);
    // Empty or partly populated rosters must not erase explicit grid placement.
    if ((rule.kind === 'classes' || rule.kind === 'available_slot') && declared.length) return declared;
    return audiencePreviews[lesson.id]?.status === 'ok' && audiencePreviews[lesson.id].class_ids?.length
      ? audiencePreviews[lesson.id].class_ids! : declared;
  };
  useEffect(() => {
    if (!previewKey || (focus && creationMode !== 'quick') || audienceSnapshot?.key === previewKey) return;
    let cancelled = false;
    let controller: AbortController | null = null;
    const timer = window.setTimeout(() => {
      void queuePreview(async () => {
        if (cancelled || savingRef.current) return;
        controller = new AbortController();
        const timeout = window.setTimeout(() => controller?.abort(), 10_000);
        try {
          const result = await api<{ lessons: Record<string, AudiencePreview> }>(schedulePath('/audience-preview'), {
            method: 'POST', body: `{"payload":${previewKey}}`, signal: controller.signal,
          });
          if (!cancelled) setAudienceSnapshot({ key: previewKey, lessons: result.lessons || {} });
        } finally { window.clearTimeout(timeout); }
      }).catch((error: Error) => {
        if (!cancelled && !savingRef.current) {
          const reason = controller?.signal.aborted ? 'Сервер не ответил за 10 секунд. Повторите расчёт позже.' : `Не удалось рассчитать участников: ${error.message}`;
          const ids = (JSON.parse(previewKey) as { lessons?: { id: string }[] }).lessons || [];
          setAudienceSnapshot({ key: previewKey, lessons: Object.fromEntries(ids.map((lesson) => [lesson.id, { status: 'error', reason }])) });
          setStatus(reason);
        }
      });
    }, 600);
    return () => { window.clearTimeout(timer); cancelled = true; controller?.abort(); };
  }, [api, previewKey, workspace, focus, creationMode, audienceSnapshot, previewRetryNonce]);
  const classes = (payload?.classes || [])
    .map((id) => groups.find((item) => item.id === id))
    .filter((item): item is Group => Boolean(item))
    .sort((a, b) => Number(a.name.match(/^\d+/)?.[0] || 0) - Number(b.name.match(/^\d+/)?.[0] || 0) || a.name.localeCompare(b.name, 'ru'));
  const visibleClasses = classes.filter(
    (item) => !classFilter || item.id === classFilter,
  );
  const gradeOf = (name: string) => name.match(/^\s*(\d{1,2})/)?.[1] || '';
  const dayModeLabel = (mode: DayMode | 'normal') => mode === 'online' ? 'Онлайн' : mode === 'sdep' ? 'SDEP' : 'Обычный';
  const dayModeIcon = (mode: DayMode | 'normal') => mode === 'online'
    ? <Monitor size={14} aria-hidden="true" />
    : mode === 'sdep'
      ? <BookOpenCheck size={14} aria-hidden="true" />
      : <School size={14} aria-hidden="true" />;
  const renderDayModeChoices = (classItem: Group, selectedDay: number, mode: DayMode | 'normal') => {
    const grade = Number(gradeOf(classItem.name));
    return <span className="constructor-day-mode-popover" role="group" aria-label={`Выбор режима ${classItem.name} · ${DAYS[selectedDay]}`} onClick={(event) => event.stopPropagation()}>
      <strong>{classItem.name} · {DAYS[selectedDay]}</strong>
      {(['normal', 'online', 'sdep'] as const).map((option) => <button
        type="button"
        key={option}
        className={`constructor-day-mode-option ${mode === option ? 'active' : ''} ${option}`}
        disabled={option === 'sdep' && (grade < 9 || grade > 11)}
        aria-pressed={mode === option}
        onClick={() => { updateDayMode(classItem.id, selectedDay, option); setDayModeEditor(null); }}
      >{dayModeIcon(option)}<span>{dayModeLabel(option)}</span></button>)}
    </span>;
  };
  const classColumns: GridColumn[] = [...new Set(visibleClasses.map((item) => gradeOf(item.name) || item.name))].sort((a, b) => Number(a) - Number(b) || a.localeCompare(b, 'ru')).map((grade) => {
        const matching = visibleClasses.filter(
          (item) => (gradeOf(item.name) || item.name) === grade,
        );
        return {
          key: `grade:${grade}`,
          label: gradeOf(matching[0]?.name || '') ? `${grade} класс` : grade,
          classIds: matching.map((item) => item.id),
        };
      });
  const selectedWeekTarget = (() => {
    if (weekTarget.startsWith('parallel:')) {
      const grade = weekTarget.slice('parallel:'.length);
      return {
        label: `${grade} класс`,
        classIds: classes.filter((item) => gradeOf(item.name) === grade).map((item) => item.id),
      };
    }
    if (weekTarget.startsWith('group:'))
      return { label: groups.find((item) => item.id === weekTarget.slice(6))?.name || 'Группа', classIds: classes.map((item) => item.id) };
    if (weekTarget.startsWith('teacher:'))
      return { label: teachers.find((item) => item.id === weekTarget.slice(8))?.name || 'Преподаватель', classIds: classes.map((item) => item.id) };
    const item = classes.find((value) => value.id === weekTarget) || classes[0];
    return { label: item?.name || 'Класс', classIds: item ? [item.id] : [] };
  })();
  const gridColumns: GridColumn[] = classColumns;
  const gradeScopeAtColumn = (column: number) => {
    const grades = new Set((gridColumns[column]?.classIds || [])
      .map((id) => gradeOf(groups.find((item) => item.id === id)?.name || ''))
      .filter(Boolean));
    return grades.size === 1 ? [...grades][0] : '';
  };
  const columnWeight = (column: GridColumn) => {
    const peak = Math.max(1, ...(payload?.days || []).flatMap((candidateDay) => (payload?.slots || []).map((_, period) =>
      (payload?.lessons || []).filter((lesson) => overlapsSlot(lesson, candidateDay, period, payload!.slots) && displayedClasses(lesson).some((id) => column.classIds.includes(id))).length)));
    return Math.min(2.2, 1 + Math.max(0, peak - 1) * 0.22);
  };
  const gridColumnTemplate = `68px ${gridColumns.map((column) => {
    const width = resizePreview?.[column.key] ?? (payload?.column_width_mode === 'manual' ? payload.column_widths?.[column.key] : undefined);
    return width ? `${width}px` : `minmax(${fitWidth ? '0px' : `${density === 'compact' ? 115 : 145}px`}, ${columnWeight(column)}fr)`;
  }).join(' ')}`;
  const beginColumnResize = (event: ReactPointerEvent<HTMLSpanElement>, key: string) => {
    if (event.button !== 0 || !payload) return;
    event.preventDefault();
    event.stopPropagation();
    const scale = fitWidth ? 1 : zoom / 100;
    const widths = Object.fromEntries(gridColumns.map((column) => [column.key,
      Math.round((columnHeaders.current[column.key]?.getBoundingClientRect().width || 160 * scale) / scale)]));
    columnResize.current = { pointerId: event.pointerId, key, startX: event.clientX, startWidth: widths[key], scale, widths };
    setResizePreview(widths);
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const moveColumnResize = (event: ReactPointerEvent<HTMLSpanElement>) => {
    const active = columnResize.current;
    if (!active || active.pointerId !== event.pointerId) return;
    event.preventDefault();
    setResizePreview({ ...active.widths, [active.key]: Math.max(90, Math.min(600,
      Math.round(active.startWidth + (event.clientX - active.startX) / active.scale))) });
  };
  const finishColumnResize = (event: ReactPointerEvent<HTMLSpanElement>, commit: boolean) => {
    const active = columnResize.current;
    if (!active || active.pointerId !== event.pointerId) return;
    event.stopPropagation();
    columnResize.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    const width = Math.max(90, Math.min(600, Math.round(active.startWidth + (event.clientX - active.startX) / active.scale)));
    setResizePreview(null);
    if (commit && width !== active.startWidth) mutate((state) => ({ ...state, column_width_mode: 'manual',
      column_widths: { ...active.widths, [active.key]: width } }));
  };
  const resetColumnWidth = (key: string) => mutate((state) => {
    const widths = { ...state.column_widths };
    delete widths[key];
    return { ...state, column_width_mode: Object.keys(widths).length ? 'manual' : 'auto', column_widths: widths };
  });
  const nudgeColumnWidth = (key: string, delta: number) => {
    const scale = fitWidth ? 1 : zoom / 100;
    const measured = Math.round((columnHeaders.current[key]?.getBoundingClientRect().width || 160 * scale) / scale);
    mutate((state) => ({ ...state, column_width_mode: 'manual', column_widths: {
      ...state.column_widths, [key]: Math.max(90, Math.min(600, (state.column_widths?.[key] || measured) + delta)),
    } }));
  };
  const gridRows = viewMode === 'week'
    ? (payload?.days || []).flatMap((value) => (payload?.slots || []).map((_, period) => ({ day: value, period })))
    : (payload?.slots || []).map((_, period) => ({ day, period }));
  const dayAtRow = (row: number) => gridRows[row]?.day ?? day;
  const periodAtRow = (row: number) => gridRows[row]?.period ?? row;
  const rowForLesson = (lesson: Lesson) => gridRows.findIndex((row) => row.day === lesson.day && row.period === lesson.period);
  const classIdAtColumn = (column: number) => {
    const ids = gridColumns[column]?.classIds || [];
    if (targetClassId && ids.includes(targetClassId)) return targetClassId;
    if (ids.length > 1) return '';
    return ids[0] || '';
  };
  const subjects = useMemo(
    () =>
      [
        ...new Set([
          ...teachers.flatMap((item) => item.assignments.map((a) => a.subject)),
          ...groups.map((item) => item.subject || ''),
          ...(payload?.lessons || []).map((item) => item.subject),
        ]),
      ]
        .filter(Boolean)
        .sort((a, b) => a.localeCompare(b, 'ru')),
    [teachers, groups, payload],
  );
  const rooms = useMemo(
    () =>
      [
        ...new Set(
          (payload?.lessons || []).map((item) => item.room).filter(Boolean),
        ),
      ].sort(),
    [payload],
  );
  const unplaced =
    payload?.lessons.filter(
      (item) =>
      item.needsClarification || !payload.days.includes(item.day) || !payload.slots[item.period],
    ) || [];
  const issues = useMemo(() => {
    if (!payload) return [];
    const practiceIds = new Set<string>();
    if (!dirty && resolutionRevision === draft?.revision) {
      for (const [lessonId, receipts] of Object.entries(resolutionStates))
        if (receipts.some((receipt) => receipt.type === 'independent_practice' && receipt.lesson_ids?.includes(lessonId))) practiceIds.add(lessonId);
    }
    return issuesFor(payload, groups, students, audiencePreviews, practiceIds);
  }, [payload, groups, students, audiencePreviews, dirty, draft?.revision, resolutionRevision, resolutionStates]);
  const selected = payload?.lessons.find((item) => item.id === selectedId);
  const selectedCell = focus &&
    payload && {
      day: dayAtRow(focus.row),
      period: periodAtRow(focus.row),
      classId: classIdAtColumn(focus.col),
      gradeScope: gradeScopeAtColumn(focus.col),
    };
  const newAudienceContextClassIds = newLesson.classIds.length
    ? newLesson.classIds
    : selectedCell?.classId
      ? [selectedCell.classId]
      : focus ? gridColumns[focus.col]?.classIds || [] : [];
  const previewDay = newLesson.day ?? selectedCell?.day ?? day;
  const previewPeriod = creationMode === 'event' && newLesson.start && newLesson.end
    ? firstOverlappingSlot(newLesson.start, newLesson.end, payload?.slots || [])
    : newLesson.period ?? selectedCell?.period ?? 0;
  const rawPreviewAudience = activityType === 'club' ? { kind: 'students' as const, student_ids: [] as string[] } : newLesson.audience || audienceFromGroup(newLesson.groupId, newLesson.classIds, groups);
  const previewAudience = rawPreviewAudience.kind === 'available_slot' && !rawPreviewAudience.class_ids?.length && !rawPreviewAudience.grade && selectedCell?.gradeScope
    ? { ...rawPreviewAudience, grade: selectedCell.gradeScope, class_ids: [] }
    : rawPreviewAudience;
  const concurrentPreviewLessons = (previewAudience.kind === 'available_slot' || creationMode === 'lunch') && payload
    ? payload.lessons.filter((lesson) => {
      if (lesson.day !== previewDay) return false;
      const existing = lessonTimes(lesson, payload.slots);
      const candidate = lessonTimes({ day: previewDay, period: previewPeriod, start: newLesson.start, end: newLesson.end, kind: creationMode === 'event' ? 'event' : 'lesson' } as Lesson, payload.slots);
      return Boolean(existing.start && existing.end && candidate.start && candidate.end
        && minutes(existing.start) < minutes(candidate.end) && minutes(candidate.start) < minutes(existing.end));
    }).map(audiencePreviewLesson)
    : [];
  const newAudienceKey = focus && creationMode !== 'quick' && activityType !== 'elective' ? JSON.stringify({
    reference_date: previewPayload?.reference_date,
    week_start: previewPayload?.week_start,
    slots: previewPayload?.slots,
    concurrentLessons: concurrentPreviewLessons,
    audience: previewAudience, informational: newLesson.informational,
    contextClassIds: newAudienceContextClassIds,
    gradeScope: selectedCell?.gradeScope,
    day: previewDay, period: previewPeriod,
    start: newLesson.start, end: newLesson.end, kind: creationMode,
  }) : '';
  const newAudiencePreview = newAudienceSnapshot?.key === newAudienceKey ? newAudienceSnapshot.preview : undefined;
  useEffect(() => {
    if (!payload || !previewPayload || !newAudienceKey || !focus) return;
    let cancelled = false;
    let controller: AbortController | null = null;
    const timer = window.setTimeout(() => {
      const rawAudience = activityType === 'club' ? { kind: 'students' as const, student_ids: [] as string[] } : newLesson.audience || audienceFromGroup(newLesson.groupId, newLesson.classIds, groups);
      const audience = rawAudience.kind === 'available_slot' && !rawAudience.class_ids?.length && !rawAudience.grade && selectedCell?.gradeScope
        ? { ...rawAudience, grade: selectedCell.gradeScope, class_ids: [] }
        : rawAudience;
      const mirror = audiencePatch(audience, groups, students, newAudienceContextClassIds);
      const candidate: Lesson = {
        id: '__new_audience_preview__', day: newLesson.day ?? selectedCell?.day ?? day,
        period: creationMode === 'event' && newLesson.start && newLesson.end
          ? firstOverlappingSlot(newLesson.start, newLesson.end, payload.slots)
          : newLesson.period ?? selectedCell?.period ?? 0,
        subject: newLesson.subject, teacherId: newLesson.teacherId, room: newLesson.room,
        groupId: mirror.groupId || '', classId: mirror.classId || '',
        groupIds: mirror.groupIds, classIds: mirror.classIds,
        audience: newLesson.informational ? undefined : audience,
        grade_scope: selectedCell?.gradeScope || undefined,
        informational: newLesson.informational, specialMark: creationMode === 'lunch' ? 'lunch' : creationMode === 'no_lesson' ? 'no_lesson' : undefined, kind: creationMode === 'event' ? 'event' : 'lesson',
        start: newLesson.start, end: newLesson.end,
      };
      void queuePreview(async () => {
        if (cancelled || savingRef.current) return;
        controller = new AbortController();
        const timeout = window.setTimeout(() => controller?.abort(), 10_000);
        try {
          const concurrentLessons = audience.kind === 'available_slot' || creationMode === 'lunch'
            ? payload.lessons.filter((lesson) => {
              if (lesson.day !== candidate.day) return false;
              const existing = lessonTimes(lesson, payload.slots);
              const target = lessonTimes(candidate, payload.slots);
              return Boolean(existing.start && existing.end && target.start && target.end
                && minutes(existing.start) < minutes(target.end) && minutes(target.start) < minutes(existing.end));
            })
            : [];
          const result = await api<{ lessons: Record<string, AudiencePreview> }>(schedulePath('/audience-preview'), {
            method: 'POST', body: JSON.stringify({ payload: {
              ...previewPayload, lessons: [...concurrentLessons.map(audiencePreviewLesson), audiencePreviewLesson(candidate)],
            } }), signal: controller.signal,
          });
          if (cancelled) return;
          const preview = result.lessons?.[candidate.id];
          setNewAudienceSnapshot({ key: newAudienceKey, preview: preview || { status: 'error', reason: 'Сервер не вернул состав для нового занятия.' } });
        } finally { window.clearTimeout(timeout); }
      }).catch((error: Error) => {
        if (!cancelled && !savingRef.current) {
          const reason = controller?.signal.aborted ? 'Сервер не ответил за 10 секунд. Повторите расчёт позже.' : `Не удалось рассчитать участников: ${error.message}`;
          setNewAudienceSnapshot({ key: newAudienceKey, preview: { status: 'error', reason } });
        }
      });
    }, 120);
    return () => { window.clearTimeout(timer); cancelled = true; controller?.abort(); };
  }, [api, newAudienceKey, groups, students, workspace, previewRetryNonce]);
  const quickParsed = parseQuick(quick, selectedCell?.classId || '', groups, teachers, students, subjects);
  const quickSubject = quickChoice.subject ?? (quickParsed.subjects.length === 1 ? quickParsed.subjects[0] : '');
  const quickGroupId = quickChoice.groupId ?? (quickParsed.groupIds.length === 1 ? quickParsed.groupIds[0] : '');
  const quickExplicitTeacherId = quickChoice.teacherId ?? (quickParsed.teacherIds.length === 1 ? quickParsed.teacherIds[0] : '');
  const quickRoom = quickChoice.room ?? quickParsed.room;
  const quickClassIds = quickChoice.classIds ?? (quickParsed.classIds.length === 1 ? quickParsed.classIds : selectedCell?.classId ? [selectedCell.classId] : []);
  const contextualSubjects = useMemo(() => {
    const context = new Set(quickClassIds);
    const grade = quickClassIds.map((id) => groups.find((item) => item.id === id)?.name.match(/^\d+/)?.[0]).find(Boolean) || '';
    const scores = new Map<string, number>();
    for (const lesson of payload?.lessons || []) {
      const overlapsContext = lessonClasses(lesson).some((id) => context.has(id))
        || grade && lessonClasses(lesson).some((id) => groups.find((item) => item.id === id)?.name.startsWith(grade));
      if (overlapsContext) scores.set(lesson.subject, (scores.get(lesson.subject) || 0) + (lesson.day === selectedCell?.day && lesson.period === selectedCell?.period ? 5 : 1));
    }
    for (const teacher of teachers) for (const assignment of teacher.assignments) {
      if (context.has(assignment.group_id)) scores.set(assignment.subject, (scores.get(assignment.subject) || 0) + 2);
    }
    return [...subjects].sort((a, b) => (scores.get(b) || 0) - (scores.get(a) || 0) || a.localeCompare(b, 'ru'));
  }, [quickClassIds, payload, groups, teachers, subjects, selectedCell?.day, selectedCell?.period]);
  const quickGroupOptions = useMemo(() => groups.filter((item) => item.group_type !== 'class' && !item.archived_at).map((item) => {
    const memberClasses = groupClassIds(item.id, groups, students, []);
    const contextMatch = memberClasses.some((id) => quickClassIds.includes(id));
    const subjectMatch = quickSubject && item.subject && subjectLabel(item.subject) === subjectLabel(quickSubject);
    const parsedMatch = quickParsed.groupIds.includes(item.id);
    return { item, score: Number(parsedMatch) * 8 + Number(subjectMatch) * 4 + Number(contextMatch) * 2 };
  }).sort((a, b) => b.score - a.score || a.item.name.localeCompare(b.item.name, 'ru')).map(({ item }) => item), [groups, students, quickClassIds, quickSubject, quickParsed.groupIds]);
  const matchesWeekTarget = (lesson: Lesson) => {
    if (!weekTarget) return true;
    if (weekTarget.startsWith('group:')) {
      const id = weekTarget.slice(6);
      return lesson.groupId === id || lesson.groupIds?.includes(id);
    }
    if (weekTarget.startsWith('teacher:')) {
      const id = weekTarget.slice(8);
      return lesson.teacherId === id || lesson.teacherIds?.includes(id);
    }
    return (lesson.activityType === 'club' ? visualClasses(lesson, groups) : displayedClasses(lesson)).some((id) => selectedWeekTarget.classIds.includes(id));
  };
  const bounds =
    anchor && focus
      ? {
          top: Math.min(anchor.row, focus.row),
          bottom: Math.max(anchor.row, focus.row),
          left: Math.min(anchor.col, focus.col),
          right: Math.max(anchor.col, focus.col),
        }
      : null;
  const inRange = (row: number, col: number) =>
    Boolean(
      bounds &&
      row >= bounds.top &&
      row <= bounds.bottom &&
      col >= bounds.left &&
      col <= bounds.right,
    );
  const selectCell = (row: number, col: number, extend = false, focusEditor = false) => {
    const cell = { row, col };
    if (!extend || !anchor) setAnchor(cell);
    setFocus(cell);
    setSelectedId('');
    if (!extend) { setQuick(''); setQuickChoice({}); }
    setCreationMode('quick');
    setPanelMode('lesson');
    if (viewMode === 'day' && gridColumns[col]?.classIds.length === 1)
      setTargetClassId(gridColumns[col].classIds[0]);
    else if (gridColumns[col]?.classIds.length > 1)
      setTargetClassId('');
    setTimeout(() => {
      if (focusEditor) quickInput.current?.focus();
      else root.current?.querySelector<HTMLElement>(`[data-cell="${keyOf(cell)}"]`)?.focus();
    }, 0);
  };
  const editCell = (initial?: string) => {
    if (!focus || !payload) return;
    setPanelMode('lesson');
    setPanelOpen(true);
    const classId = classIdAtColumn(focus.col);
    if (!classId) return;
    const found = payload.lessons.find(
      (item) =>
        item.day === dayAtRow(focus.row) &&
        item.period === periodAtRow(focus.row) &&
        lessonClasses(item).includes(classId),
    );
    if (found && !initial) setSelectedId(found.id);
    else {
      setSelectedId('');
      setQuick(initial || '');
      setQuickChoice({});
    }
    setTimeout(() => quickInput.current?.focus(), 0);
  };
  const addStructured = (fields: { subject: string; groupId: string; teacherId: string; teacherIds?: string[]; room: string; classIds: string[]; audience?: AudienceRule; informational?: boolean; specialMark?: 'lunch' | 'no_lesson'; day?: number; period?: number; kind?: 'lesson' | 'event'; start?: string; end?: string; place?: string; description?: string; format?: string; blockId?: string; blockName?: string; raw?: string; activityType?: Lesson['activityType']; activityCatalogId?: string; activityNameSnapshot?: string; eligibleGrades?: number[]; confirmedParticipantIds?: string[]; electiveDirection?: 'digital' }) => {
    if (!payload || !selectedCell) return;
    setCreationError('');
    const fail = (message: string) => { setCreationError(message); setStatus(message); };
    if (fields.kind === 'event' && (!fields.start || !fields.end || minutes(fields.end) <= minutes(fields.start))) {
      fail('У события время окончания должно быть позже начала.');
      return;
    }
    const eventPeriod = fields.kind === 'event' ? firstOverlappingSlot(fields.start!, fields.end!, payload.slots) : -1;
    if (fields.kind === 'event' && eventPeriod < 0) {
      fail('Время события не пересекается с сеткой. Уточните диапазон или добавьте слот.');
      return;
    }
    const explicitActivityAudience = fields.activityType === 'club' || fields.activityType === 'elective'
      ? { kind: 'students' as const, student_ids: [] as string[] }
      : undefined;
    const rawAudience = fields.informational && fields.kind === 'event' ? undefined : explicitActivityAudience || fields.audience || audienceFromGroup(fields.groupId, fields.classIds, groups);
    const audience = rawAudience?.kind === 'available_slot' && !rawAudience.class_ids?.length && !rawAudience.grade && selectedCell.gradeScope
      ? { ...rawAudience, grade: selectedCell.gradeScope, class_ids: [] }
      : rawAudience;
    const audienceFields = audience ? audiencePatch(audience, groups, students, fields.classIds) : null;
    const classIds = fields.activityType === 'club' ? [] : fields.activityType === 'elective' ? fields.classIds : audienceFields?.classIds?.length ? audienceFields.classIds : fields.kind === 'event' && fields.informational ? fields.classIds.length ? fields.classIds : payload.classes : fields.classIds;
    const visualClassIds = (fields.classIds.length ? fields.classIds : selectedCell.classId ? [selectedCell.classId] : gridColumns[focus!.col]?.classIds || []).filter((id) => payload.classes.includes(id));
    const validClassIds = (fields.activityType === 'club' ? visualClassIds : classIds).filter((id) => payload.classes.includes(id));
    const hasAudience = Boolean(audience && (audience.kind === 'groups' ? audience.group_ids?.length : audience.kind === 'students' ? audience.student_ids?.length : audience.grade || audience.class_ids?.length));
    const activityCanHaveNoConfirmedParticipants = fields.activityType === 'club' || fields.activityType === 'elective';
    if (!fields.subject.trim() || !validClassIds.length || (!hasAudience && !fields.informational && !activityCanHaveNoConfirmedParticipants) || fields.activityType === 'elective' && !fields.eligibleGrades?.length) {
      fail(!fields.subject.trim() ? 'Введите название занятия.' : !validClassIds.length ? 'Выберите классы размещения в сетке.' : fields.activityType === 'elective' && !fields.eligibleGrades?.length ? 'Выберите классы, для которых доступен курс.' : 'Выберите участников занятия: класс, учебную группу или учеников.');
      return;
    }
    const lesson: Lesson = {
      id: freshId(),
      day: fields.day ?? selectedCell.day,
      period: fields.kind === 'event' ? eventPeriod : fields.period ?? selectedCell.period,
      classId: fields.activityType === 'club' ? '' : validClassIds[0],
      classIds: fields.activityType === 'club' ? [] : validClassIds,
      ...(fields.activityType === 'club' ? { displayClassIds: visualClassIds, audience: { kind: 'students' as const, student_ids: [] } } : {}),
      ...(fields.activityType === 'elective' ? { audience: { kind: 'students' as const, student_ids: [] }, confirmedParticipantIds: [] } : {}),
      subject: fields.subject.trim(),
      teacherId: fields.teacherId || fields.teacherIds?.[0] || '',
      teacherIds: fields.teacherIds?.length ? fields.teacherIds : fields.teacherId ? [fields.teacherId] : [],
      groupId: audienceFields?.groupId || '',
      groupIds: audienceFields?.groupIds || [],
      ...(audience ? { audience } : {}),
      ...(fields.informational ? { informational: true } : {}),
      ...(fields.specialMark ? { specialMark: fields.specialMark, canonicalRole: fields.specialMark === 'no_lesson' ? 'explicit_no_lesson' : 'lunch' } : {}),
      room: fields.room,
      kind: fields.kind || 'lesson',
      ...(fields.start ? { start: fields.start } : {}),
      ...(fields.end ? { end: fields.end } : {}),
      ...(fields.place ? { place: fields.place } : {}),
      ...(fields.description ? { description: fields.description } : {}),
      ...(fields.format ? { format: fields.format } : {}),
      ...(fields.blockId ? { blockId: fields.blockId, blockName: fields.blockName } : {}),
      raw: fields.raw,
      activityType: fields.activityType || (fields.kind === 'event' ? 'event' : 'lesson'),
      ...(fields.activityCatalogId ? { activityCatalogId: fields.activityCatalogId } : {}),
      ...((fields.activityNameSnapshot || (fields.activityType === 'club' || fields.activityType === 'elective' ? fields.subject.trim() : '')) ? { activityNameSnapshot: fields.activityNameSnapshot || fields.subject.trim() } : {}),
      ...(fields.eligibleGrades?.length ? { eligibleGrades: fields.eligibleGrades } : {}),
      ...(fields.confirmedParticipantIds?.length ? { confirmedParticipantIds: fields.confirmedParticipantIds } : {}),
      ...(fields.electiveDirection ? { electiveDirection: fields.electiveDirection } : {}),
    };
    mutate((state) => ({ ...state, lessons: [...state.lessons, lesson] }));
    setSelectedId(lesson.id);
    setCreationError('');
    setCreationMode('quick');
  };
  const addLesson = () => {
    if (quickParsed.unknown.length || quickParsed.groupIds.length > 1 && !quickChoice.groupId || quickParsed.teacherIds.length > 1 && !quickChoice.teacherId) {
      setStatus('Уточните нераспознанные слова или выберите совпадение в предпросмотре.');
      return;
    }
    const classIds = quickGroupId ? groupClassIds(quickGroupId, groups, students, quickClassIds) : quickClassIds;
    const fallbackClassId = classIds[0];
    const groupId = quickGroupId || groups.find((item) => item.id === fallbackClassId)?.id || '';
    addStructured({ subject: quickSubject, groupId, teacherId: resolvedQuickTeacherId, room: quickRoom, classIds, raw: quick });
  };
  const updateLesson = (id: string, patch: Partial<Lesson>) =>
    mutate((state) => ({
      ...state,
      lessons: state.lessons.map((item) => {
        if (item.id !== id) return item;
        const next: Lesson = { ...item, ...patch,
          ...(workspace === 'school' && item.source ? { source: { ...item.source, manual_override: true } } : {}) };
        if (patch.subject !== undefined && patch.activityNameSnapshot === undefined && (next.activityType === 'club' || next.activityType === 'elective'))
          next.activityNameSnapshot = patch.subject;
        if (!patch.audience && patch.groupId !== undefined) {
          const group = groups.find((entry) => entry.id === patch.groupId);
          if (group?.group_type === 'class') {
            next.classId = group.id;
            next.classIds = [group.id];
            next.groupIds = [group.id];
          } else if (group) {
            next.classIds = groupClassIds(group.id, groups, students, lessonClasses(item));
            next.classId = next.classIds[0] || '';
            next.groupIds = [group.id];
          } else next.groupIds = [];
        }
        if (patch.teacherId !== undefined && patch.teacherIds === undefined)
          next.teacherIds = patch.teacherId ? [patch.teacherId] : [];
        if (patch.period !== undefined && item.kind !== 'event' && item.start && item.end && state.slots[item.period] && state.slots[patch.period] && item.end !== state.slots[item.period].end) {
          const shift = minutes(state.slots[patch.period].start) - minutes(state.slots[item.period].start);
          next.start = addMinutes(item.start, shift);
          next.end = addMinutes(item.end, shift);
        }
        if (item.kind === 'event' && (patch.start !== undefined || patch.end !== undefined)) {
          const span = lessonTimes(next, state.slots);
          if (span.start && span.end && minutes(span.start) < minutes(span.end)) {
            const period = firstOverlappingSlot(span.start, span.end, state.slots);
            if (period >= 0) next.period = period;
          }
        }
        return next;
      }),
    }));
  const reviewDecision = (lessonIds: string[], status: 'confirmed' | 'rejected') =>
    mutate((state) => ({
      ...state,
      import_review: {
        ...state.import_review,
        source_items: (state.import_review?.source_items || []).map((item: Raw) =>
          lessonIds.includes(item.lesson_id) ? { ...item, status } : item),
      },
    }));
  const openReviewLesson = (id: string) => {
    const lesson = payload?.lessons.find((item) => item.id === id);
    if (!lesson) return;
    setDay(lesson.day);
    setViewMode('day');
    setClassFilter('');
    setSelectedId(id);
    setPanelMode('lesson');
    setPanelOpen(true);
    const col = gridColumns.findIndex((entry) => lessonClasses(lesson).some((classId) => entry.classIds.includes(classId)));
    if (col >= 0) { setAnchor({ row: lesson.period, col }); setFocus({ row: lesson.period, col }); }
  };
  const removeSelected = () => {
    if (!payload) return;
    const ids = selectedId
      ? [selectedId]
      : payload.lessons
          .filter(
            (item) =>
              gridColumns.some(
                (column, col) =>
                  rowForLesson(item) >= 0 &&
                  displayedClasses(item).some((id) => column.classIds.includes(id)) &&
                  (viewMode === 'day' || matchesWeekTarget(item)) &&
                  inRange(rowForLesson(item), col),
              ),
          )
          .map((item) => item.id);
    if (ids.length) {
      mutate((state) => ({
        ...state,
        lessons: state.lessons.filter((item) => !ids.includes(item.id)),
      }));
      setSelectedId('');
    }
  };
  const copy = async (cut: boolean) => {
    if (!payload || !bounds) return;
    const cells: Clip['cells'] = [];
    const cutIds: string[] = [];
    for (let row = bounds.top; row <= bounds.bottom; row++)
      for (let col = bounds.left; col <= bounds.right; col++) {
        const column = gridColumns[col];
        const lessonDay = dayAtRow(row);
        const lessons = payload.lessons.filter(
          (item) =>
            overlapsSlot(item, lessonDay, periodAtRow(row), payload.slots) &&
            displayedClasses(item).some((id) => column?.classIds.includes(id)) &&
            (viewMode === 'day' || matchesWeekTarget(item)) &&
            (!selectedId || item.id === selectedId),
        );
        cells.push({ row: row - bounds.top, col: col - bounds.left, lessons });
        cutIds.push(...lessons.map((item) => item.id));
      }
    const uniqueIds = [...new Set(cutIds)];
    const value = {
      width: bounds.right - bounds.left + 1,
      height: bounds.bottom - bounds.top + 1,
      cells,
      cutIds: cut ? uniqueIds : [],
    };
    clip.current = value;
    try {
      await navigator.clipboard.writeText(
        JSON.stringify({ type: CLIP_MIME, ...value }),
      );
    } catch {
      /* Internal structured buffer works when clipboard permission is denied. */
    }
    setStatus(
      `${cut ? 'Вырезано' : 'Скопировано'}: ${uniqueIds.length} занятий, ${value.width} × ${value.height} ячеек.`,
    );
  };
  const paste = async () => {
    if (!payload || !focus) return;
    let value = clip.current;
    if (!value)
      try {
        const text = await navigator.clipboard.readText();
        const external = JSON.parse(text);
        if (external.type === CLIP_MIME && Array.isArray(external.cells))
          value = { ...(external as Clip), cutIds: [] };
      } catch {
        /* Use the internal buffer. */
      }
    if (!value) {
      setStatus('Буфер расписания пуст.');
      return;
    }
    const created: Lesson[] = [];
    const copiedIds = new Set<string>();
    const audienceChanges: string[] = [];
    for (const cell of value.cells) {
      const destination = gridColumns[focus.col + cell.col];
      const destinationRow = focus.row + cell.row;
      const period = periodAtRow(destinationRow);
      if (!destination || destinationRow >= gridRows.length) {
        setStatus('Вставка выходит за границы сетки. Источник не изменён.');
        return;
      }
      for (const lesson of cell.lessons) {
        if (copiedIds.has(lesson.id)) continue;
        copiedIds.add(lesson.id);
        const sourceClasses = displayedClasses(lesson);
        const explicitlySelectedClass = targetClassId && destination.classIds.includes(targetClassId) ? targetClassId : '';
        const targetClassIds = explicitlySelectedClass ? [explicitlySelectedClass] : destination.classIds;
        const audienceAlreadyFits = sourceClasses.length > 0 && sourceClasses.every((id) => targetClassIds.includes(id));
        const wholeClass = isWholeClassAudience(lesson);
        const needsAudienceConfirmation = !audienceAlreadyFits && lesson.activityType !== 'club' && !wholeClass
          || destination.classIds.length > 1 && !explicitlySelectedClass;
        const targetLabel = targetClassIds.map((id) => groups.find((group) => group.id === id)?.name || id).join(', ') || 'выбранной аудитории';
        if (needsAudienceConfirmation) {
          const sourceLabel = sourceClasses.map((id) => groups.find((group) => group.id === id)?.name || id).join(', ') || 'исходная аудитория';
          audienceChanges.push(`${lesson.subject || 'Занятие'}: ${sourceLabel} → ${targetLabel}`);
        }
        const replaceAudience = needsAudienceConfirmation;
        const newId = value.cutIds.includes(lesson.id) ? lesson.id : freshId();
        const remapped = remapPastedLessonAudience(
          lesson,
          audienceAlreadyFits && !explicitlySelectedClass ? sourceClasses : targetClassIds,
          newId,
          replaceAudience,
        );
        const classId = remapped.classId;
        created.push({
          ...remapped,
          day: dayAtRow(destinationRow),
          period,
          ...(lesson.activityType === 'club' ? { classId: '', classIds: [], displayClassIds: targetClassIds } : { classId }),
          ...(lesson.start && lesson.end && payload.slots[lesson.period] && payload.slots[period]
            ? { start: addMinutes(lesson.start, minutes(payload.slots[period].start) - minutes(payload.slots[lesson.period].start)), end: addMinutes(lesson.end, minutes(payload.slots[period].start) - minutes(payload.slots[lesson.period].start)) }
            : {}),
        });
      }
    }
    if (audienceChanges.length && !window.confirm(`Перед вставкой подтвердите новый охват:\n\n${audienceChanges.join('\n')}\n\nНазначить перечисленным классам?`)) {
      setStatus('Вставка отменена. Исходные занятия не изменены.');
      return;
    }
    mutate((state) => ({
      ...state,
      lessons: [
        ...state.lessons.filter((item) => !value!.cutIds.includes(item.id)),
        ...created,
      ],
    }));
    if (created[0]) {
      setSelectedId(created[0].id);
      setPanelMode('lesson');
      setPanelOpen(true);
    }
    clip.current = { ...value, cutIds: [] };
    setStatus(audienceChanges.length
      ? `Вставлено ${created.length} занятий. Новая аудитория подтверждена: ${audienceChanges.slice(0, 2).join('; ')}${audienceChanges.length > 2 ? ` и ещё ${audienceChanges.length - 2}` : ''}.`
      : `Вставлено ${created.length} занятий.`);
  };
  const move = (
    lessonId: string,
    row: number,
    col: number,
    duplicate: boolean,
  ) => {
    if (!payload) return;
    const lesson = payload.lessons.find((item) => item.id === lessonId),
      destination = gridColumns[col];
    if (!lesson || !destination) return;
    const sourceClasses = displayedClasses(lesson);
    const explicitlySelectedClass = targetClassId && destination.classIds.includes(targetClassId) ? targetClassId : '';
    const targetClassIds = explicitlySelectedClass ? [explicitlySelectedClass] : destination.classIds;
    const audienceAlreadyFits = sourceClasses.length > 0 && sourceClasses.every((id) => targetClassIds.includes(id));
    const wholeClass = isWholeClassAudience(lesson);
    const needsAudienceConfirmation = !audienceAlreadyFits && lesson.activityType !== 'club' && !wholeClass
      || destination.classIds.length > 1 && !explicitlySelectedClass;
    const targetLabel = targetClassIds.map((id) => groups.find((group) => group.id === id)?.name || id).join(', ') || 'выбранной аудитории';
    if (needsAudienceConfirmation) {
      const sourceLabel = sourceClasses.map((id) => groups.find((group) => group.id === id)?.name || id).join(', ') || 'исходная аудитория';
      if (!window.confirm(`${lesson.subject || 'Занятие'}: ${sourceLabel} → ${targetLabel}.\n\nПодтвердить новый охват?`)) return;
    }
    const destinationClassId = targetClassIds.length === 1 ? targetClassIds[0] : '';
    const newId = duplicate ? freshId() : lesson.id;
    const remapped = remapPastedLessonAudience(
      lesson,
      audienceAlreadyFits && !explicitlySelectedClass ? sourceClasses : targetClassIds,
      newId,
      needsAudienceConfirmation,
    );
    mutate((state) => ({
      ...state,
      lessons: [
        ...state.lessons.filter((item) => duplicate || item.id !== lessonId),
        {
          ...remapped,
          day: dayAtRow(row),
          period: periodAtRow(row),
          ...(lesson.activityType === 'club' ? { classId: '', classIds: [], displayClassIds: targetClassIds } : { classId: remapped.classId || destinationClassId }),
          ...(lesson.start && lesson.end && payload.slots[lesson.period] && payload.slots[periodAtRow(row)]
            ? { start: addMinutes(lesson.start, minutes(payload.slots[periodAtRow(row)].start) - minutes(payload.slots[lesson.period].start)), end: addMinutes(lesson.end, minutes(payload.slots[periodAtRow(row)].start) - minutes(payload.slots[lesson.period].start)) }
            : {}),
        },
      ],
    }));
    if (selectedId && selectedId !== lessonId) return;
    selectCell(row, col);
    setSelectedId(newId);
    setPanelOpen(true);
    setStatus(needsAudienceConfirmation
      ? `${duplicate ? 'Скопировано' : 'Перенесено'} для ${targetLabel} после подтверждения охвата.`
      : `${duplicate ? 'Скопировано' : 'Перенесено'}: ${lesson.subject || 'занятие'}.`);
  };
  const onKeyDown = (event: KeyboardEvent | ReactKeyboardEvent<HTMLDivElement>) => {
    if (editable(event.target)) return;
    const command = event.ctrlKey || event.metaKey;
    // Physical shortcut keys remain stable when the active keyboard layout is Cyrillic.
    const key = command && /^Key[CXVZY]$/.test(event.code)
      ? event.code.slice(3).toLowerCase()
      : event.key.toLowerCase();
    if (command && ['c', 'x', 'v', 'z', 'y'].includes(key)) {
      event.preventDefault();
      event.stopPropagation();
      if (key === 'c' || key === 'x') void copy(key === 'x');
      else if (key === 'v') void paste();
      else if ((key === 'z' && event.shiftKey) || key === 'y') redo();
      else undo();
      return;
    }
    if (key === 'escape') {
      event.stopPropagation();
      const menus = root.current?.querySelectorAll<HTMLDetailsElement>('details[data-ui-menu][open]');
      if (menus?.length) { menus.forEach(menu => { menu.open = false; menu.querySelector<HTMLElement>('summary')?.focus(); }); return; }
      if (panelOpen && window.matchMedia('(max-width: 900px)').matches) { setPanelOpen(false); return; }
      if (fullScreen) setFullScreen(false);
      setSelectedId('');
      setContext(null);
      return;
    }
    if (event.key.startsWith('Arrow') && focus && payload) {
      event.preventDefault();
      event.stopPropagation();
      const next = {
        row: Math.max(
          0,
          Math.min(
            gridRows.length - 1,
            focus.row +
              (event.key === 'ArrowDown'
                ? 1
                : event.key === 'ArrowUp'
                  ? -1
                  : 0),
          ),
        ),
        col: Math.max(
          0,
          Math.min(
            gridColumns.length - 1,
            focus.col +
              (event.key === 'ArrowRight'
                ? 1
                : event.key === 'ArrowLeft'
                  ? -1
                  : 0),
          ),
        ),
      };
      selectCell(next.row, next.col, event.shiftKey, false);
    } else if (key === 'enter' && focus) {
      event.preventDefault();
      event.stopPropagation();
      editCell();
    } else if ((key === 'delete' || key === 'backspace') && focus) {
      event.preventDefault();
      event.stopPropagation();
      removeSelected();
    } else if (key.length === 1 && !command && !event.altKey && focus) {
      event.stopPropagation();
      editCell(event.key);
    }
  };
  useEffect(() => {
    const handleGridShortcut = (event: KeyboardEvent) => {
      if (!root.current?.contains(document.activeElement) || editable(event.target))
        return;
      onKeyDown(event);
    };
    window.addEventListener('keydown', handleGridShortcut, true);
    return () => window.removeEventListener('keydown', handleGridShortcut, true);
  });
  const resourceDay = selected?.day ?? (creationMode !== 'quick' ? newLesson.day : undefined) ?? (focus ? dayAtRow(focus.row) : day);
  const resourcePeriod = selected?.period ?? (creationMode !== 'quick' ? newLesson.period : undefined) ?? (focus ? periodAtRow(focus.row) : 0);
  const resourceSlot = payload?.slots[resourcePeriod];
  const resourceStart = selected?.kind === 'event' ? selected.start || resourceSlot?.start : !selected && creationMode === 'event' ? newLesson.start || resourceSlot?.start : resourceSlot?.start;
  const resourceEnd = selected?.kind === 'event' ? selected.end || resourceSlot?.end : !selected && creationMode === 'event' ? newLesson.end || resourceSlot?.end : resourceSlot?.end;
  const occupied = payload?.lessons.filter((item) => {
    if (item.id === selected?.id || item.day !== resourceDay || !resourceStart || !resourceEnd) return false;
    const span = lessonTimes(item, payload.slots);
    return Boolean(span.start && span.end && minutes(span.start) < minutes(resourceEnd) && minutes(resourceStart) < minutes(span.end));
  }) || [];
  const busyTeachers = new Set(occupied.flatMap((item) => [...(item.teacherIds || []), item.teacherId].filter(Boolean)));
  const busyRooms = new Set(occupied.map((item) => normalizeRoomKey(item.room)).filter(Boolean));
  const teacherBusyWith = (id: string) => occupied.find((item) => item.teacherId === id || item.teacherIds?.includes(id));
  const teacherContextAudience = selected
    ? audienceOf(selected, groups)
    : creationMode === 'quick'
      ? quickGroupId && groups.find((item) => item.id === quickGroupId)?.group_type !== 'class'
        ? { kind: 'groups' as const, group_ids: [quickGroupId] }
        : { kind: 'classes' as const, class_ids: quickClassIds }
      : newLesson.audience || audienceFromGroup(newLesson.groupId, newLesson.classIds, groups);
  const teacherContextClassIds = selected ? lessonClasses(selected) : creationMode === 'quick' ? quickClassIds : newLesson.classIds;
  const teacherContext = {
    groupIds: teacherContextAudience.kind === 'groups' ? teacherContextAudience.group_ids || [] : [],
    classIds: teacherContextAudience.kind === 'classes' || teacherContextAudience.kind === 'available_slot'
      ? teacherContextAudience.class_ids || teacherContextClassIds : teacherContextClassIds,
    grade: teacherContextAudience.kind === 'classes' || teacherContextAudience.kind === 'available_slot' ? teacherContextAudience.grade : undefined,
    subject: selected?.subject || (creationMode === 'quick' ? quickSubject : newLesson.subject),
    course: teacherContextAudience.kind === 'groups'
      ? groups.find((item) => (teacherContextAudience.group_ids || []).includes(item.id))?.course
      : undefined,
    examTrack: teacherContextAudience.kind === 'groups'
      ? groups.find((item) => (teacherContextAudience.group_ids || []).includes(item.id))?.exam_track
      : undefined,
  };
  const teacherResolution = rankTeachersForAudience(teachers, groups, teacherContext, [...busyTeachers], students);
  const rankedTeachers = teacherResolution.teachers as Teacher[];
  const recommendedTeacherId = teacherResolution.defaultTeacherId;
  const resolvedQuickTeacherId = quickChoice.teacherId ?? (quickParsed.teacherIds.length === 1
    ? quickParsed.teacherIds[0]
    : quickParsed.teacherIds.length ? '' : recommendedTeacherId);
  const resolvedNewTeacherId = newTeacherManuallySelected || activityType === 'elective' ? newLesson.teacherId : newLesson.teacherId || recommendedTeacherId;
  const allRooms = [...new Map([...knownRooms, ...rooms].filter(Boolean).map((room) => [normalizeRoomKey(room), normalizeRoomDisplay(room)])).values()];
  const slotIssues = (payload?.slots || []).flatMap((slot, index, all) => {
    const start = Number(slot.start.slice(0, 2)) * 60 + Number(slot.start.slice(3, 5));
    const end = Number(slot.end.slice(0, 2)) * 60 + Number(slot.end.slice(3, 5));
    const problems: string[] = [];
    if (!/^\d{2}:\d{2}$/.test(slot.start) || !/^\d{2}:\d{2}$/.test(slot.end) || end <= start)
      problems.push('Конец должен быть позже начала.');
    all.forEach((other, otherIndex) => {
      if (otherIndex === index) return;
      const otherStart = Number(other.start.slice(0, 2)) * 60 + Number(other.start.slice(3, 5));
      const otherEnd = Number(other.end.slice(0, 2)) * 60 + Number(other.end.slice(3, 5));
      if (start < otherEnd && otherStart < end) problems.push(`Пересекается с уроком ${otherIndex + 1}.`);
    });
    return problems.map((message) => ({ index, message }));
  });
  useEffect(()=>{let active=true;if(!conflictsOpen||dirty||!draft)return;void api<{exceptions:Record<string,Array<{id:string;type:string;reason:string;lesson_ids?:string[]}>>}>(`/api/admin/schedule/constructor/${encodeURIComponent(draft.id)}/resolution/preview`,{method:'POST',body:JSON.stringify({expected_revision:draft.revision,lesson_ids:draft.payload.lessons.map(l=>l.id)})}).then(result=>{if(active){setResolutionStates(result.exceptions||{});setResolutionRevision(draft.revision);}}).catch(()=>{if(active)setResolutionStates({});});return()=>{active=false;};},[api,conflictsOpen,dirty,draft?.id,draft?.revision]);
  const workflowFor=(item:Issue)=>{const receipts=dirty||resolutionRevision!==draft?.revision?[]:[...(resolutionStates[item.lessonId]||[]),...(item.related?resolutionStates[item.related]||[]:[])];const targets=[item.lessonId,...(item.related?[item.related]:[])];const coversIssue=(receipt:{lesson_ids?:string[]})=>targets.every(id=>receipt.lesson_ids?.includes(id));if(item.category==='critical')return 'critical';if(receipts.some(r=>r.type==='defer'&&coversIssue(r)))return 'deferred';if(receipts.some(r=>r.type==='joint'&&item.category==='conflict'&&coversIssue(r)))return 'acknowledged';if(receipts.some(r=>r.type==='independent_practice'&&item.type==='teacher'&&targets.some(id=>r.lesson_ids?.includes(id))))return 'acknowledged';if(receipts.some(r=>r.type==='confirm_roster'&&item.category==='review'&&item.type==='audience'&&r.lesson_ids?.includes(item.lessonId)))return 'acknowledged';return item.type==='room'||item.type!=='audience'&&item.category==='incomplete'?'quick':'decision';};
  const filteredIssues = issues.filter((item) => issueSeverity === 'all' || item.severity === issueSeverity).filter((item) => issueType === 'all' || (issueType === 'teacher' ? item.type === 'teacher' : issueType === 'room' ? item.type === 'room' : issueType === 'student' ? item.type === 'audience' : item.type === 'data'));
  const conflictIssues = issues.filter((item) => item.category === 'conflict');
  const conflictLessons = new Set(conflictIssues.flatMap((item) => [item.lessonId, ...(item.related ? [item.related] : [])]));
  const conflictPairs = new Set(conflictIssues.filter((item) => item.related).map((item) => [item.lessonId, item.related!].sort().join(':')));
  const versionConflicts = versionConflict?.conflicts || [];
  const conflictFieldLabels: Record<string, string> = {
    '$lesson': 'занятие целиком', subject: 'предмет', teacherId: 'преподаватель', teacherIds: 'преподаватели',
    room: 'кабинет', groupId: 'учебная группа', groupIds: 'учебные группы', classId: 'класс', classIds: 'классы',
    audience: 'аудитория', audience_snapshot: 'зафиксированный состав учеников', day: 'день', period: 'урок', start: 'начало', end: 'окончание',
    name: 'название черновика', days: 'учебные дни', slots: 'время уроков', classes: 'набор классов', reference_date: 'дата расписания', week_start: 'начало недели',
  };
  const readableConflictValue = (value: unknown) => typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
    ? String(value)
    : JSON.stringify(value) ?? 'не задано';
  const conflictValueLabel = (change: ScheduleMergeConflict, side: 'local' | 'server') => {
    const value = change[side];
    if (value == null) return change.field === '$lesson' ? 'занятие удалено' : 'не задано';
    if (change.field === '$lesson') return conflictLessonLabel(value as Lesson, groups, teachers);
    if (change.field === 'day') return DAYS[Number(value)] || readableConflictValue(value);
    if (change.field === 'period') return `${Number(value) + 1}-й урок`;
    if (change.field === 'teacherId') return teachers.find((item) => item.id === value)?.name || readableConflictValue(value);
    if (change.field === 'teacherIds') return (value as string[]).map((id) => teachers.find((item) => item.id === id)?.name || id).join(', ') || 'не назначены';
    if (['groupId', 'classId'].includes(change.field)) return groups.find((item) => item.id === value)?.name || readableConflictValue(value);
    if (['groupIds', 'classIds'].includes(change.field)) return (value as string[]).map((id) => groups.find((item) => item.id === id)?.name || id).join(', ') || 'нет';
    if (change.field === 'audience_snapshot') return `${(value as Raw).student_ids?.length || 0} учеников`;
    if (change.field === 'audience') {
      const rule = value as AudienceRule;
      if (rule.kind === 'groups') return (rule.group_ids || []).map((id) => groups.find((item) => item.id === id)?.name || id).join(', ') || 'группы не выбраны';
      if (rule.kind === 'students') return `${rule.student_ids?.length || 0} отдельных учеников`;
      return rule.grade ? `Весь ${rule.grade} класс` : (rule.class_ids || []).map((id) => groups.find((item) => item.id === id)?.name || id).join(', ') || (rule.kind === 'available_slot' ? 'Все остальные' : 'классы не выбраны');
    }
    if (Array.isArray(value)) return value.map((item) => typeof item === 'object' ? JSON.stringify(item) : String(item)).join(', ');
    if (typeof value === 'object') return JSON.stringify(value);
    return readableConflictValue(value);
  };
  const conflictTitle = (change: ScheduleMergeConflict) => {
    const lesson = versionConflict?.local.payload.lessons.find((item) => item.id === change.lessonId)
      || versionConflict?.server.payload.lessons.find((item) => item.id === change.lessonId);
    const subject = lesson?.subject ? `«${lesson.subject}» · ` : '';
    const where = lesson ? `${DAYS[lesson.day] || ''}, ${lesson.period + 1}-й урок` : '';
    return change.lessonId ? `${subject}${where} · ${conflictFieldLabels[change.field] || change.field}` : conflictFieldLabels[change.field] || change.field;
  };

  const workspaceTabs = demoAvailable ? (
      <nav className="constructor-workspaces" aria-label="Пространство конструктора">
        {demoAvailable ? <button className={workspace === 'demo' ? 'active' : ''} disabled={directoryAccess === 'checking'} onClick={() => { workspaceRef.current = 'demo'; setWorkspace('demo'); setGroups(directoryByWorkspace.current.demo.groups); setTeachers(directoryByWorkspace.current.demo.teachers); setStudents(directoryByWorkspace.current.demo.students); setKnownRooms(directoryByWorkspace.current.demo.rooms); setDrafts(demoDrafts); setDraft(null); }}>Демо <small>Синтетические данные</small></button> : null}
        <button className={workspace === 'school' ? 'active' : ''} disabled={directoryAccess !== 'school'} onClick={() => { workspaceRef.current = 'school'; setWorkspace('school'); setGroups(directoryByWorkspace.current.school.groups); setTeachers(directoryByWorkspace.current.school.teachers); setStudents(directoryByWorkspace.current.school.students); setKnownRooms(directoryByWorkspace.current.school.rooms); setDrafts(schoolDrafts); setDraft(null); }}>Школа <small>Школьный справочник</small></button>
      </nav>
  ) : null;
  return (
    <div
      className={`constructor constructor-ux ${draft ? 'has-draft' : ''} ${fullScreen ? 'is-fullscreen' : ''}`}
      role="application"
      aria-label="Конструктор расписания"
      ref={root}
      tabIndex={0}
      onClick={() => context && setContext(null)}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          if (context) setContext(null);
          root.current?.querySelectorAll<HTMLDetailsElement>('details[data-ui-menu][open]').forEach((menu) => { menu.open = false; menu.querySelector<HTMLElement>('summary')?.focus(); });
          if (conflictsOpen) setConflictsOpen(false);
        }
      }}
    >
      <header className="constructor-top">
        <div>
          <small>РАСПИСАНИЕ · {workspace === 'school' ? 'ШКОЛА' : 'ПЕСОЧНИЦА'}</small>
          <h1><span className="desktop-label">Конструктор расписания</span><span className="mobile-label">Расписание</span></h1>{draft ? <div className="constructor-current-draft" title={draft.name}><span>{draft.name}</span><button aria-label="Переименовать открытый черновик" aria-expanded={renameValue !== null} title="Переименовать" onClick={() => { setRenameValue(draft.name); setRenameError(''); }}><Pencil size={16} /></button>{renameValue !== null ? <div className="constructor-rename-popover" role="dialog" aria-label="Переименовать открытый черновик"><form onSubmit={(event) => { event.preventDefault(); void renameOpenDraft(); }}><label>Новое название<input ref={(element) => element?.focus()} aria-label="Новое название черновика" value={renameValue} onChange={(event) => setRenameValue(event.target.value)} onKeyDown={(event) => { event.stopPropagation(); if (event.key === 'Escape') { setRenameValue(null); setRenameError(''); } }} /></label>{renameError ? <p role="alert">{renameError}</p> : null}<div><button type="submit" disabled={saving}>Сохранить название</button><button type="button" onClick={() => { setRenameValue(null); setRenameError(''); }}>Отмена</button></div></form></div> : null}</div> : null}
          <p>{consolidatedSchedule && workspace === 'school' ? 'Изменения сохраняются в черновике. Публикация — после отдельной проверки и подтверждения.' : 'Черновик · опубликованное расписание не меняется'}</p>
          {scheduleDataSourceLabel ? <span className="admin-data-source">{scheduleDataSourceLabel}</span> : <span className="admin-data-source">{directoryAccess === 'school' ? 'Школьный справочник подключён' : directoryAccess === 'checking' ? 'Подключение…' : 'Локальная песочница'}</span>}{draft ? workspaceTabs : null}
        </div>
        <div className="constructor-actions">
          {!draft ? <>
          <Link href="/admin/people">Школьный справочник</Link>
          <button onClick={() => void load().then(() => setStatus('Справочник обновлён')).catch((error) => setStatus(String(error.message || error)))}><RefreshCw size={16} /> Обновить справочник</button>
          </> : null}
          {draft ? (
            <>
              {workspace === 'school' ? <button disabled={saving} onClick={() => void (async () => { if (await save()) { setSchoolStartSection(draft.payload.week_start ? 'weeks' : 'template'); setDraft(null); } })()}>{draft.payload.week_start ? 'К неделям' : 'К основному шаблону'}</button> : null}
              <span className={dirty ? 'constructor-unsaved' : ''}>
                {saving ? 'Сохраняется' : dirty ? saveFailed ? 'Ошибка сохранения' : 'Есть несохранённые изменения' : 'Сохранено'}
              </span>
              <button aria-label="Сохранить черновик" onClick={() => void save()} disabled={!dirty || saving}>
                <Save size={16} /><span className="desktop-label">Сохранить черновик</span><span className="mobile-label">Сохранить</span>
              </button>
              <button onClick={() => setRecalculationOpen(true)} disabled={saving}>Пересчитать аудитории</button>
              {consolidatedSchedule && workspace === 'school' ? <button className="constructor-publication-trigger" aria-label="Открыть публикацию расписания" disabled={dirty || saving} title={dirty ? 'Сначала сохраните черновик' : 'Проверить изменения и опубликовать'} onClick={() => setPublicationOpen(true)}>Публикация</button> : null}
            </>
          ) : null}
        </div>
      </header>
      {recalculationOpen && draft ? <ConstructorAudienceRecalculation api={api} apiPrefix={workspace === 'school' ? '/api/admin/schedule/constructor' : '/api/admin/schedule/experiments'} draftId={draft.id} revision={draft.revision} dirty={dirty} day={day} classId={classFilter || (selected ? audienceOf(selected, groups).kind === 'groups' ? audienceOf(selected, groups).group_ids?.[0] : lessonClasses(selected)[0] : undefined)} classLabel={groups.find((group) => group.id === classFilter)?.name} onClose={() => setRecalculationOpen(false)} onUpdated={(value) => { const updated = value as Draft; if (updated?.payload) { setHistory((items) => [...items.slice(-49), draft.payload]); setFuture([]); revision.current = updated.revision; baseDraftRef.current = updated; setDraft(updated); setDirty(false); changeVersion.current++; setAudienceSnapshot(null); void refreshDrafts(); } }} /> : null}
      {resolutionIssue && draft ? <ConstructorResolutionPanel api={api} draftId={draft.id} revision={draft.revision} lessons={draft.payload.lessons} slots={draft.payload.slots} issue={resolutionIssue} teacherNames={Object.fromEntries(teachers.map(t=>[t.id,t.name]))} groupNames={Object.fromEntries(groups.map(g=>[g.id,g.name]))} studentNames={Object.fromEntries(students.map(s=>[s.id,s.name]))} dirty={dirty} onClose={()=>setResolutionIssue(null)} onNavigate={id=>{openReviewLesson(id);setResolutionIssue(null);setConflictsOpen(false);}} onUpdated={async(value)=>{const updated=value as Draft|undefined;if(updated?.payload){setHistory(items=>[...items.slice(-49),draft.payload]);setFuture([]);revision.current=updated.revision;baseDraftRef.current=updated;setDraft(updated);setDirty(false);changeVersion.current++;}else{await open(draft.id);}await refreshDrafts();}} /> : null}
      {publicationOpen && draft ? <ConstructorPublicationPanel api={api} draftId={draft.id} revision={draft.revision} weekStart={draft.payload.week_start} teacherNames={Object.fromEntries(teachers.map(item => [item.id,item.name]))} groupNames={Object.fromEntries(groups.map(item => [item.id,item.name]))} onClose={() => setPublicationOpen(false)} onPublished={async () => { await refreshDrafts(); await open(draft.id); }} /> : null}
      {!draft ? workspaceTabs : null}
      {status ? (
        <output className="constructor-status">
          {status}
          <button
            aria-label="Закрыть уведомление"
            onClick={() => setStatus('')}
          >
            <X size={15} />
          </button>
        </output>
      ) : null}
      {colorPanelOpen ? <TeacherColorPanel teachers={teachers} registry={teacherRegistry} onFilter={(id) => { setTeacherFilter(id); setFiltersOpen(Boolean(id)); }} onClose={() => setColorPanelOpen(false)} /> : null}
      {activityEditor ? <div className="constructor-conflict-backdrop"><dialog ref={(node) => { if (node && !node.open) node.showModal(); }} onCancel={(event) => { event.preventDefault(); if (!catalogSaving) setActivityEditor(null); }} className="constructor-conflict-dialog constructor-catalog-dialog" aria-modal="true" aria-labelledby="constructor-catalog-title">
        <header><h2 id="constructor-catalog-title">Каталог кружков и курсов</h2><button aria-label="Закрыть каталог" disabled={catalogSaving} onClick={() => setActivityEditor(null)}><X size={18} /></button></header>
        <label>Запись каталога<select aria-label="Редактировать запись каталога" value={activityEditor.item?.id || ''} onChange={(event) => editActivity(activities.find((item) => item.id === event.target.value))}><option value="">Создать новую</option>{activities.map((item) => <option key={item.id} value={item.id}>{item.name}{item.status === 'archived' ? ' · в архиве' : ''}</option>)}</select></label>
        <label>Название<input autoFocus value={activityEditor.name} onChange={(event) => setActivityEditor((item) => item ? { ...item, name: event.target.value } : item)} /></label>
        <label>Тип<select value={activityEditor.type} onChange={(event) => setActivityEditor((item) => item ? { ...item, type: event.target.value === 'elective' ? 'elective' : 'club' } : item)}><option value="club">Кружок</option><option value="elective">Курс по выбору</option></select></label>
        <label>Другие названия<input placeholder="Например, Game Design" value={activityEditor.aliases} onChange={(event) => setActivityEditor((item) => item ? { ...item, aliases: event.target.value } : item)} /></label>
        <label>Преподаватели<select multiple value={activityEditor.teacher_ids} onChange={(event) => setActivityEditor((item) => item ? { ...item, teacher_ids: [...event.target.selectedOptions].map((option) => option.value) } : item)}>{teachers.map((teacher) => <option key={teacher.id} value={teacher.id}>{teacher.name}</option>)}</select></label>
        <label>Кабинет<input value={normalizeRoomInput(activityEditor.room)} onChange={(event) => setActivityEditor((item) => item ? { ...item, room: event.target.value } : item)} /></label>
        <p>Изменения каталога не переписывают занятия уже созданных недель. Кружки не назначаются ученикам.</p>
        <footer>{activityEditor.item ? <button disabled={catalogSaving} onClick={async () => { await archiveActivity(activityEditor.item!); setActivityEditor(null); }}>{activityEditor.item.status === 'active' ? 'В архив' : 'Восстановить'}</button> : null}<button disabled={catalogSaving} onClick={() => setActivityEditor(null)}>Отмена</button><button className="is-primary" disabled={catalogSaving || !activityEditor.name.trim()} onClick={() => void saveActivityEditor()}>{catalogSaving ? 'Сохраняем…' : 'Сохранить запись'}</button></footer>
      </dialog></div> : null}
      {versionConflict ? (
        <div className="constructor-conflict-backdrop">
          <section className="constructor-conflict-dialog" role="dialog" aria-modal="true" aria-labelledby="constructor-conflict-title">
            <header>
              <div><small>КОНФЛИКТ ВЕРСИЙ</small><h2 id="constructor-conflict-title">Расписание изменилось в другой вкладке или сеансе</h2></div>
              <button aria-label="Закрыть окно конфликта" onClick={() => setVersionConflict(null)}><X size={18} /></button>
            </header>
            <p>Обнаружено {versionConflicts.length} изменений, которые затрагивают одни и те же данные. Остальные изменения будут сохранены вместе.</p>
            <div className="constructor-conflict-diff">
              <strong>Выберите, какой вариант оставить</strong>
              {versionConflicts.map((change) => <article key={change.key}>
                <b>{conflictTitle(change)}</b>
                <span>Ваш вариант: {conflictValueLabel(change, 'local')}</span>
                <span>Другой вариант: {conflictValueLabel(change, 'server')}</span>
                <label><select aria-label={`Вариант для ${conflictTitle(change)}`} value={conflictChoices[change.key] || 'local'} onChange={(event) => setConflictChoices((current) => ({ ...current, [change.key]: event.target.value as 'local' | 'server' }))}><option value="local">Оставить мой вариант</option><option value="server">Оставить другой вариант</option></select></label>
              </article>)}
            </div>
            <details className="constructor-conflict-details"><summary>Подробнее</summary><p>Исходная версия: {versionConflict.base ? `ревизия ${versionConflict.base.revision}` : 'недоступна для восстановления; различия требуют выбора вручную'} · ваша ревизия {versionConflict.local.revision} · серверная ревизия {versionConflict.server.revision}.</p></details>
            <footer>
              <button onClick={() => setVersionConflict(null)} disabled={conflictActionBusy}>Отложить</button>
              <button onClick={() => void saveLocalConflictCopy()} disabled={conflictActionBusy}>{conflictActionBusy ? 'Создаём копию…' : 'Сохранить как отдельную копию'}</button>
              <button className="is-primary" onClick={() => void resolveVersionConflict()} disabled={conflictActionBusy}>{conflictActionBusy ? 'Сохраняем…' : 'Сохранить выбранные изменения'}</button>
              <button onClick={loadServerConflictVersion} disabled={conflictActionBusy}>Загрузить серверную версию</button>
            </footer>
          </section>
        </div>
      ) : null}
      {directoryAccess === 'checking' ? <section className="constructor-school-gate">Проверяем подключение к справочнику…</section> : workspace === 'school' && !draft ? (
        <ConstructorSchoolStart api={api} drafts={schoolDrafts} index={schoolWeekIndex} hiddenIds={hiddenSchoolDrafts} readOnly={readOnlySchoolWorkspace} initialSection={schoolStartSection}
          onOpen={open} onCopy={(item) => void duplicate(item as DraftSummary)} onHide={hideSchoolDraft} onRestore={restoreSchoolDraft}
          onCreateEmpty={() => void create('empty')} onCreated={async (id) => { setSchoolStartSection('weeks'); await refreshDrafts(); await open(id); }}
          onPublication={async (id) => { await open(id); setPublicationOpen(true); }} />
      ) : !draft ? (
        <section className="constructor-start">
          <h2>С чего начнём?</h2>
          <section className="constructor-source-card">
            <h3>Основной шаблон школы</h3>
            <p>Создайте отдельный черновик. Изменения в нём не меняют действующее школьное расписание.</p>
          </section>
          <div className="constructor-start-actions">
            <button onClick={() => void create('empty')}>
              <Plus /> Пустая школьная неделя
              <small>Настройте классы и время, затем добавляйте занятия.</small>
            </button>
            <button onClick={() => void create('canonical')}>
              <Copy /> На основе действующего расписания
              <small>Независимая копия действующего шаблона для редактирования.</small>
            </button>
          </div>
          <div className="constructor-draft-filters" role="group" aria-label="Разделы черновиков">
            <button className={draftFilter === 'demo' ? 'active' : ''} onClick={() => setDraftFilter('demo')}>Демо · {drafts.filter((item) => item.draft_kind === 'demo').length}</button>
            <button className={draftFilter === 'user' ? 'active' : ''} onClick={() => setDraftFilter('user')}>Мои черновики · {drafts.filter((item) => item.draft_kind === 'user').length}</button>
            <button className={draftFilter === 'test' ? 'active' : ''} onClick={() => setDraftFilter('test')}>Тестовые · {drafts.filter((item) => item.draft_kind === 'test').length}</button>
            <button className={draftFilter === 'unclassified' ? 'active' : ''} onClick={() => setDraftFilter('unclassified')}>Не разобраны · {drafts.filter((item) => item.draft_kind === 'unclassified').length}</button>
          </div>
          <h3>{draftFilter === 'demo' ? 'Демонстрационные недели' : draftFilter === 'test' ? 'Тестовые данные' : draftFilter === 'unclassified' ? 'Черновики без подтверждённого происхождения' : 'Пользовательские черновики'}</h3>
          {draftFilter === 'test' ? <p>Сохранённые записи разработки. Они не участвуют в школьном шаблоне.</p> : null}
          {draftFilter === 'unclassified' ? <p>Эти записи сохранены без признака происхождения. Они доступны для просмотра и не скрыты без проверки владельца.</p> : null}
          {drafts.some((item) => item.draft_kind === draftFilter) ? (
            drafts.filter((item) => item.draft_kind === draftFilter).map((item) => (
              <article className="constructor-draft" key={item.id}>
                <button className="constructor-draft-open" onClick={() => void open(item.id)}><strong>{item.name}</strong><span>{item.draft_kind === 'demo' ? 'Источник: синтетический fixture' : item.base_version_id ? 'Источник: canonical' : 'Источник: пустая неделя'} · {item.lesson_count} занятий · {item.event_count} событий · {item.day_count} дней · {item.group_count} групп</span><span>Создан: {item.created_at ? new Date(item.created_at).toLocaleString('ru-RU') : '—'} · Изменён: {item.updated_at ? new Date(item.updated_at).toLocaleString('ru-RU') : '—'}</span><code>ID {item.id}</code><ChevronDown size={16} /></button>
                <div className="constructor-draft-actions"><button onClick={() => void rename(item)}>Переименовать</button><button onClick={() => void duplicate(item)}>Дублировать</button><button onClick={() => void remove(item)}>Удалить</button></div>
              </article>
            ))
          ) : (
            <p>{draftFilter === 'demo' ? 'Демо-неделя ещё не подготовлена в этой среде.' : 'В этом разделе пока нет черновиков.'}</p>
          )}
        </section>
      ) : payload ? (
        <>
          {payload.import_review?.status === 'canonical_copy' ? <p className="constructor-status">{payload.import_review.message} · {payload.lessons.length} карточек · версия {draft.base_version_id}</p> : null}
          {workspace === 'school' && conflictsOpen && payload.import_review?.status === 'source_led_applied' ? <details className="constructor-unplaced constructor-source-review">
            <summary><strong>Сверка «Было → Стало» · {(payload.import_review.source_items || []).filter((item: Raw) => item.status === 'needs_confirmation').length} требуют решения</strong></summary>
            <p>Источник: Google Sheets «2026/27 шаблон». {payload.import_review.source_cell_count} занятых ячеек, {payload.import_review.source_block_count} блоков. Старая версия сохранена в локальной резервной копии. Подтверждения сохраняются вместе с черновиком.</p>
            {(payload.import_review.source_items || []).map((item: Raw) => {
              const lesson = payload.lessons.find((entry) => entry.id === item.lesson_id);
              if (!lesson) return null;
              const sameCases = (payload.import_review?.source_items || []).filter((other: Raw) =>
                other.status === 'needs_confirmation' && other.proposed?.subject === item.proposed?.subject &&
                other.proposed?.group_id === item.proposed?.group_id && other.proposed?.teacher_id === item.proposed?.teacher_id &&
                JSON.stringify(other.reasons) === JSON.stringify(item.reasons) &&
                JSON.stringify(Object.values(other.source_text || {})) === JSON.stringify(Object.values(item.source_text || {})));
              return <details key={item.lesson_id} className="constructor-review-item">
                <summary>{DAYS[item.day]} {item.start}–{item.end} · {lessonClasses(lesson).map((id) => groups.find((group) => group.id === id)?.name || id).join(', ')} · {lesson.subject} · {item.status === 'needs_confirmation' ? 'Требует проверки' : item.status === 'auto_applied' ? 'Перенесено' : item.status === 'confirmed' ? 'Подтверждено' : 'Отклонено'}</summary>
                <div className="constructor-review-comparison"><div><b>Исходник · {item.source_cells?.join(', ')}</b><p>{Object.values(item.source_text || {}).join(' / ')}</p></div><div><b>Было</b><p>{item.before?.length ? item.before.map((entry: Raw) => `${entry.subject || 'Без предмета'} · ${(entry.group_ids || []).map((id: string) => groups.find((group) => group.id === id)?.name || 'группа не найдена').join(', ') || 'без группы'} · ${(entry.teacher_ids || []).map((id: string) => teachers.find((teacher) => teacher.id === id)?.name || 'преподаватель не найден').join(', ') || 'без преподавателя'}${entry.room ? ` · ${normalizeRoomDisplay(entry.room)}` : ''}`).join('; ') : 'В прежнем черновике нет'}</p></div><div><b>Стало</b><p>{lesson.subject} · {groups.find((group) => group.id === lesson.groupId)?.name || 'группа требует уточнения'} · {teachers.find((teacher) => teacher.id === lesson.teacherId)?.name || 'преподаватель требует уточнения'}{lesson.room ? ` · ${normalizeRoomDisplay(lesson.room)}` : ''}</p></div></div>
                {item.reasons?.length ? <p>Причина: {item.reasons.join('; ')}</p> : <p>Исходник изменился или отсутствовал в прежнем черновике; запись перенесена автоматически.</p>}
                <div className="constructor-draft-actions"><button onClick={() => openReviewLesson(item.lesson_id)}>Показать и исправить</button>{item.status === 'needs_confirmation' ? <><button onClick={() => reviewDecision([item.lesson_id], 'confirmed')}>Подтвердить</button><button onClick={() => reviewDecision([item.lesson_id], 'rejected')}>Отклонить сопоставление</button>{sameCases.length > 1 ? <button onClick={() => reviewDecision(sameCases.map((other: Raw) => other.lesson_id), 'confirmed')}>Подтвердить одинаковые · {sameCases.length}</button> : null}</> : null}</div>
              </details>;
            })}
            <p>Исключены из сетки: {payload.import_review.excluded_legacy_no_lesson_count || 0} прежних служебных отметок NO_LESSON и {payload.import_review.excluded_old_source_cells?.length || 0} ячеек старой версии, отсутствующих в актуальном шаблоне.</p>
            {(payload.import_review.visual_exclusions || []).map((item: Raw) => <p key={item.source_cell}>Отдельная заметка {item.source_cell}: «{item.raw_text?.trim()}» · {item.reason}.</p>)}
          </details> : null}
          <div className="constructor-toolbar constructor-command-bar">
            <div className="constructor-primary-tools" role="group" aria-label="Вид расписания и инструменты">
            <div className="constructor-view-tabs" role="group" aria-label="Режим просмотра">
              <button className={viewMode === 'day' ? 'active' : ''} onClick={() => { setViewMode('day'); setDayModesOpen(false); setDayModeEditor(null); setAnchor(null); setFocus(null); setSelectedId(''); }}>День</button>
              <button className={viewMode === 'week' ? 'active' : ''} onClick={() => { setViewMode('week'); setDayModeEditor(null); setAnchor(null); setFocus(null); setSelectedId(''); }}>Неделя</button>
            </div>
            {viewMode === 'day' ? (
              <div className="constructor-day-tabs">
                {payload.days.map((value) => (
                  <button key={value} aria-label={DAYS[value]} title={DAYS[value]} className={day === value ? 'active' : ''} onClick={() => { setDay(value); setAnchor(null); setFocus(null); setSelectedId(''); }}>
                    {DAYS[value].slice(0, 2)}
                  </button>
                ))}
              </div>
            ) : null}
            {viewMode === 'week' ? <select aria-label="Объект недельного просмотра" value={weekTarget} onChange={(event) => { setWeekTarget(event.target.value); setAnchor(null); setFocus(null); setSelectedId(''); }}>
              <option value="">Вся школа</option>
              {classes.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              {[...new Set(classes.map((item) => gradeOf(item.name)).filter(Boolean))].map((grade) => <option key={`parallel:${grade}`} value={`parallel:${grade}`}>{grade} класс · параллель</option>)}
              {groups.filter((item) => item.group_type !== 'class' && !item.archived_at).map((item) => <option key={`group:${item.id}`} value={`group:${item.id}`}>Группа · {item.name}</option>)}
              {teachers.map((item) => <option key={`teacher:${item.id}`} value={`teacher:${item.id}`}>Преподаватель · {item.name}</option>)}
            </select> : null}
            {viewMode === 'week' ? <span className="constructor-week-modes">
              <button type="button" className={dayModesOpen ? 'active' : ''} aria-expanded={dayModesOpen} aria-controls="constructor-day-modes-matrix" onClick={() => { setDayModesOpen((open) => !open); setDayModeEditor(null); }}>
                <BookOpenCheck size={14} aria-hidden="true" /> Режимы дней
              </button>
              {dayModesOpen ? <section id="constructor-day-modes-matrix" className="constructor-day-modes-matrix" role="dialog" aria-label="Режимы дней по классам">
                <header><div><strong>Режимы дней</strong><small>Настройки отдельных административных классов</small></div><button type="button" aria-label="Закрыть матрицу режимов" onClick={() => { setDayModesOpen(false); setDayModeEditor(null); }}><X size={15} /></button></header>
                <div className="constructor-day-modes-table-wrap"><table>
                  <thead><tr><th scope="col">Класс</th>{[0, 1, 2, 3, 4].map((weekday) => <th key={weekday} scope="col">{['Пн', 'Вт', 'Ср', 'Чт', 'Пт'][weekday]}</th>)}</tr></thead>
                  <tbody>{classes.map((classItem) => <tr key={classItem.id}>
                    <th scope="row">{classItem.name}</th>
                    {[0, 1, 2, 3, 4].map((weekday) => {
                      const mode = payload.day_modes?.[classItem.id]?.[String(weekday)] || 'normal';
                      const editing = dayModeEditor?.classId === classItem.id && dayModeEditor.day === weekday;
                      const modeLabel = dayModeLabel(mode);
                      return <td key={`${classItem.id}:${weekday}`}>
                        <span className="constructor-day-mode-control">
                          <button type="button" className={`constructor-day-mode-button ${mode}`} aria-label={`${classItem.name}, ${DAYS[weekday]}: ${modeLabel}. Изменить режим дня`} title={`${classItem.name} · ${DAYS[weekday]} · ${modeLabel}`} aria-expanded={editing} onClick={(event) => { event.stopPropagation(); setDayModeEditor(editing ? null : { classId: classItem.id, day: weekday }); }}>
                            {dayModeIcon(mode)}
                          </button>
                          {editing ? renderDayModeChoices(classItem, weekday, mode) : null}
                        </span>
                      </td>;
                    })}
                  </tr>)}</tbody>
                </table></div>
                <footer className="constructor-day-modes-legend"><span><School size={12} /> Обычный</span><span><Monitor size={12} /> Онлайн</span><span><BookOpenCheck size={12} /> SDEP · 9–11</span></footer>
              </section> : null}
            </span> : null}
            <label className="constructor-toolbar-search"><Search size={15} /><input aria-label="Найти занятие" placeholder="Найти урок…" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
            <button className="constructor-issue-count" onClick={() => setConflictsOpen((open) => !open)} aria-expanded={conflictsOpen}>
              <AlertTriangle size={14} /><span className="desktop-label">Проверка · {conflictLessons.size} занятий · {conflictPairs.size} парных совпадений</span><span className="mobile-label">Проверка · {conflictLessons.size}</span>
            </button>
            <button onClick={() => setFiltersOpen((value) => !value)} aria-expanded={filtersOpen}>Фильтры</button>
              <button className="constructor-open-colors" onClick={() => setColorPanelOpen(true)} title="Поиск и цвета преподавателей"><Palette size={15}/><span>Преподаватели</span></button>
              <details className="constructor-menu constructor-view-menu" data-ui-menu>
                <summary><Expand size={15} /><span className="desktop-label">Вид и инструменты</span><span className="mobile-label">Ещё</span><ChevronDown size={13} /></summary>
                <div className="constructor-menu-body"><strong>Отображение</strong>
            <select
              aria-label="Плотность сетки"
              value={density}
              onChange={(event) =>
                setDensity(event.target.value as typeof density)
              }
            >
              <option value="comfortable">Крупная сетка</option>
              <option value="compact">Компактная сетка</option>
            </select>
            <button onClick={() => { setFitWidth(true); setZoom(100); }} aria-label="Уместить сетку по ширине">По ширине</button>
            <button onClick={() => mutate((state) => ({ ...state, column_width_mode: 'auto' }))} disabled={payload.column_width_mode !== 'manual'}>Автоширина</button>
            <button onClick={() => { setFitWidth(false); setZoom((value) => Math.max(70, value - 10)); }} aria-label="Уменьшить масштаб">−</button>
            <select className="constructor-zoom" aria-label="Масштаб" value={zoom} onChange={(event) => { setFitWidth(false); setZoom(Number(event.target.value)); }}>{[70, 80, 85, 90, 100, 110, 120, 130, 140, 150].map((value) => <option key={value} value={value}>{value}%</option>)}</select>
            <button onClick={() => { setFitWidth(false); setZoom((value) => Math.min(150, value + 10)); }} aria-label="Увеличить масштаб">+</button>
            <button onClick={() => setFullScreen((value) => !value)} aria-label={fullScreen ? 'Выйти из полного экрана' : 'Полный экран'}><Expand size={16} /></button>
                  <button onClick={() => editActivity()}>+ Каталог кружков / курсов</button>
                  <button className="constructor-mobile-tool" onClick={() => { setPanelMode((mode) => mode === 'settings' ? 'lesson' : 'settings'); setPanelOpen(true); setFocus(null); setAnchor(null); setSelectedId(''); }}>Шаблон</button>
                </div>
              </details>
            </div>
            <div className="constructor-edit-tools" role="group" aria-label="Редактирование расписания">
            <button
              onClick={undo}
              disabled={!history.length}
              aria-label="Отменить"
            >
              <Undo2 size={17} />
            </button>
            <button
              onClick={redo}
              disabled={!future.length}
              aria-label="Повторить"
            >
              <Redo2 size={17} />
            </button>
              <button onClick={() => void copy(false)} disabled={!bounds} title="Копировать · Ctrl/Cmd+C" aria-label="Копировать"><Clipboard size={15} /><span>Копировать</span></button>
              <button onClick={() => void copy(true)} disabled={!bounds} title="Вырезать · Ctrl/Cmd+X" aria-label="Вырезать"><Scissors size={15} /><span>Вырезать</span></button>
              <button onClick={() => void paste()} disabled={!focus} title="Вставить · Ctrl/Cmd+V" aria-label="Вставить"><Copy size={15} /><span>Вставить</span></button>
              <button onClick={removeSelected} disabled={!focus} title="Удалить · Delete" aria-label="Удалить"><Trash2 size={15} /><span>Удалить</span></button>
            <button
              className={`constructor-secondary-edit ${panelMode === 'settings' ? 'active' : ''}`}
              onClick={() => { setPanelMode((mode) => mode === 'settings' ? 'lesson' : 'settings'); setPanelOpen(true); setFocus(null); setAnchor(null); setSelectedId(''); }}
            >
              Шаблон
            </button>
              <details className="constructor-menu constructor-draft-menu" data-ui-menu>
                <summary>Черновик <ChevronDown size={13} /></summary>
                <div className="constructor-menu-body" role="group" aria-label="Черновик и публикация недели">
            {consolidatedSchedule && workspace === 'school' ? <button className="constructor-mobile-tool" aria-label="Открыть публикацию расписания" disabled={dirty || saving} onClick={() => setPublicationOpen(true)}>Проверить и опубликовать</button> : null}
            <button
              onClick={() => {
                void (async () => {
                  if (await save()) setDraft(null);
                })();
              }}
            >
              {workspace === 'school' ? 'К шаблону и неделям' : 'Все черновики'}
            </button>
            <button onClick={() => void exportDraft()} disabled={dirty || saving || exporting || !draft.payload.week_start}>{exporting ? 'Экспортируем…' : 'Экспортировать в Google Sheets'}</button>
            {lastExport?.draftId === draft.id && lastExport.revision === draft.revision && !dirty ? <span className="constructor-export-result">{lastExport.reconnect ? <button onClick={async () => { try { const result = await api<{ authorization_url: string }>('/api/admin/google/reconnect?schedule_export=true', { method: 'POST' }); window.location.assign(result.authorization_url); } catch (error) { setStatus(String((error as Error).message || error)); } }}>Подключить Google для экспорта</button> : null}{lastExport.name} · {lastExport.week} · {lastExport.status === 'existing' ? 'уже экспортировано' : lastExport.status === 'authorization_required' ? 'нужна авторизация' : 'экспортировано'}{lastExport.url ? <> · <a href={lastExport.url} target="_blank" rel="noreferrer">Открыть таблицу</a></> : null}</span> : null}
            {workspace !== 'school' ? <><input type="date" aria-label="Понедельник новой недели" value={newWeekStart} onChange={(event) => setNewWeekStart(event.target.value)} /><button onClick={() => void createWeek()} disabled={dirty || saving || !newWeekStart}>Создать неделю</button></> : null}

            <input
              aria-label="Название черновика"
              value={draft.name}
              onChange={(event) => {
                changeVersion.current++;
                setSaveFailed(false);
                setDraft({ ...draft, name: event.target.value });
                setDirty(true);
              }}
            />
                  <Link href="/admin/people">Школьный справочник</Link>
                  <button onClick={() => void load().then(() => setStatus('Справочник обновлён')).catch((error) => setStatus(String(error.message || error)))}><RefreshCw size={16} /> Обновить справочник</button>
                </div>
              </details>
              <span className="constructor-selection-caption">{selected ? selected.subject : selectedCell ? `${DAYS[selectedCell.day]} · ${payload.slots[selectedCell.period]?.start}` : 'Выберите ячейку или занятие'}</span>
            </div>
          </div>
          <div className={`constructor-filters ${filtersOpen || query ? '' : 'is-collapsed'}`}>
            <label>Цвета <select aria-label="Режим цвета" value={colorMode} onChange={(event) => setColorMode(event.target.value as typeof colorMode)}><option value="teacher">По преподавателям</option><option value="subject">По предметам</option><option value="none">Без заливки</option></select></label>
            <select
              aria-label="Фильтр по классу"
              value={classFilter}
              onChange={(event) => {
                setClassFilter(event.target.value);
                if (gridWrap.current) gridWrap.current.scrollLeft = 0;
                setAnchor(null);
                setFocus(null);
              }}
            >
              <option value="">Все классы</option>
              {classes.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            <select
              aria-label="Фильтр по учителю"
              value={teacherFilter}
              onChange={(event) => setTeacherFilter(event.target.value)}
            >
              <option value="">Все преподаватели</option>
              {teachers.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            <select
              aria-label="Фильтр по предмету"
              value={subjectFilter}
              onChange={(event) => setSubjectFilter(event.target.value)}
            >
              <option value="">Все предметы</option>
              {subjects.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
            {query
              ? payload.lessons
                  .filter((item) =>
                    `${item.subject} ${item.room} ${groups.find((g) => g.id === item.groupId)?.name || ''}`
                      .toLocaleLowerCase('ru')
                      .includes(query.toLocaleLowerCase('ru')),
                  )
                  .slice(0, 5)
                  .map((item) => (
                    <button
                      key={item.id}
                      onClick={() => {
                        setDay(item.day);
                        setViewMode('day');
                        setPanelMode('lesson');
                        setPanelOpen(true);
                        setClassFilter('');
                        setAnchor({
                          row: item.period,
                          col: classes.findIndex(
                            (group) => group.id === item.classId,
                          ),
                        });
                        setFocus({
                          row: item.period,
                          col: classes.findIndex(
                            (group) => group.id === item.classId,
                          ),
                        });
                        setSelectedId(item.id);
                        setQuery('');
                      }}
                    >
                      {DAYS[item.day]?.slice(0, 2)} · {item.subject} ·{' '}
                      {groups.find((g) => g.id === item.classId)?.name}
                    </button>
                  ))
              : null}
          </div>
          {conflictsOpen ? (
            <section className="constructor-conflict-panel">
              <header><strong>Центр решений расписания</strong><button onClick={() => setConflictsOpen(false)} aria-label="Закрыть список проблем"><X size={15} /></button></header>
              {!dirty&&resolutionRevision===draft?.revision&&Object.keys(resolutionStates).length?<details className="constructor-acknowledged-history"><summary>Подтверждённые решения и отложенные записи</summary>{[...new Map(Object.entries(resolutionStates).flatMap(([lid,items])=>items.filter(r=>r.type!=='room').map(r=>[r.id,{...r,lessonId:lid}] as const))).values()].map(r=><article key={r.id}><strong>{r.type==='joint'?'Совместное занятие':r.type==='independent_practice'?'Самостоятельная практика':r.type==='defer'?'Отложенное решение':'Подтверждение участников'}</strong><p>{r.reason}</p><button onClick={()=>setResolutionIssue({lessonId:r.lessonId,related:(r as {lesson_ids?:string[]}).lesson_ids?.find(id=>id!==r.lessonId),type:r.type==='joint'||r.type==='independent_practice'?'teacher':'audience',category:'review',severity:'warning',message:r.reason})}>Рассмотреть или отменить</button></article>)}</details>:null}
              <div className="constructor-conflict-filters">
                <select aria-label="Важность проблемы" value={issueSeverity} onChange={(event) => setIssueSeverity(event.target.value)}><option value="all">Все уровни</option><option value="error">Ошибки</option><option value="warning">Предупреждения</option></select>
                <select aria-label="Тип проблемы" value={issueType} onChange={(event) => setIssueType(event.target.value)}><option value="all">Все типы</option><option value="teacher">Преподаватель</option><option value="room">Кабинет</option><option value="student">Группа или ученик</option><option value="data">Данные занятия</option></select>
              </div>
              {(['quick', 'decision', 'acknowledged', 'deferred', 'critical'] as const).map((category) => {
                const categoryIssues = filteredIssues.filter((item) => workflowFor(item) === category);
                if (!categoryIssues.length) return null;
                const title = category === 'quick' ? 'Исправляемые быстро' : category === 'decision' ? 'Требуют решения администратора' : category === 'acknowledged' ? 'Подтверждённые исключения' : category === 'deferred' ? 'Отложенные решения' : 'Критические технические проблемы';
                const affected = new Set(categoryIssues.map((item) => item.lessonId));
                const pairs = new Set(categoryIssues.filter((item) => item.related).map((item) => [item.lessonId, item.related!].sort().join(':')));
                const count = category === 'decision' ? `${affected.size} занятий · ${pairs.size} парных совпадений` : `${affected.size} занятий`;
                return <section className="constructor-issue-category" key={category}><h3>{title} · {count}</h3><div className="constructor-conflict-list">{categoryIssues.map((item, index) => {
                  const lesson = payload.lessons.find((entry) => entry.id === item.lessonId);
                  const targetGroup = groups.find((entry) => entry.id === lesson?.groupId);
                  const dayName = lesson ? DAYS[lesson.day] || 'День не определён' : 'Занятие не найдено';
                  const startTime = lesson ? payload.slots[lesson.period]?.start || 'Время не определено' : '';
                  return <article key={`${item.lessonId}:${index}`} className={item.severity}><div><button className="constructor-issue-open" onClick={()=>setResolutionIssue(item)}>Разобрать проблему</button><strong>{item.type === 'teacher' ? 'Преподаватель' : item.type === 'room' ? 'Кабинет' : item.type === 'audience' ? 'Группа или ученик' : 'Данные занятия'} · {item.severity === 'error' ? 'Ошибка' : 'Предупреждение'}</strong><small>{item.message}</small><small>{dayName}, {startTime} · {lesson?.subject || 'Занятие'} · {lesson ? lessonClasses(lesson).map((id) => groups.find((entry) => entry.id === id)?.name || id).join(', ') : ''} · {targetGroup?.name || 'аудитория не выбрана'}</small>{item.related ? <small>Связано с: {payload.lessons.find((entry) => entry.id === item.related)?.subject || 'занятием'}</small> : null}</div><button onClick={() => { if (!lesson) return; setDay(lesson.day); setViewMode('day'); setClassFilter(''); setPanelMode('lesson'); setPanelOpen(true); const col = gridColumns.findIndex((entry) => lessonClasses(lesson).some((id) => entry.classIds.includes(id))); if (col >= 0) { setAnchor({row: lesson.period, col}); setFocus({row: lesson.period, col}); } setSelectedId(lesson.id); setConflictsOpen(false); }}>Показать в расписании</button></article>;
                })}</div></section>;
              })}
            </section>
          ) : null}
          {conflictsOpen && unplaced.length ? (
            <details className="constructor-unplaced">
              <summary><strong>Требуют уточнения · {unplaced.length}</strong></summary>
              <p>Исходные занятия сохранены. Уточните состав, преподавателя или охват отметки; сами по себе эти вопросы не являются конфликтами расписания.</p>
              {unplaced.map((item) => <details key={item.id}>
                <summary>{DAYS[item.day] || 'День не определён'} {payload.slots[item.period]?.start || '—'} · {item.subject || 'Без предмета'} · {item.clarificationReason || (!payload.days.includes(item.day) || !payload.slots[item.period] ? 'В исходном блоке отсутствует день или время.' : '')}</summary>
                <p>{(item as Raw).source?.raw_text || item.raw || 'Исходный текст не указан'}</p>
                <button onClick={() => { openReviewLesson(item.id); setConflictsOpen(false); }}>Показать и исправить</button>
              </details>)}
            </details>
          ) : null}
          <div className={`constructor-layout ${panelOpen ? 'panel-open' : 'panel-closed'}`}>
            <div ref={gridWrap} className={`constructor-grid-wrap ${density}`}>
              <div
                className="constructor-grid"
                style={{
                  gridTemplateColumns: gridColumnTemplate,
                  zoom: fitWidth ? 1 : zoom / 100,
                }}
              >
                <div className="constructor-corner">Время</div>
                {gridColumns.map((item) => (
                  <div className="constructor-head" key={item.key} ref={(element) => { columnHeaders.current[item.key] = element; }}>
                    <span className="constructor-head-title">{item.label}</span>
                    {viewMode === 'day' ? <div className="constructor-day-mode-list day" aria-label={`Режим выбранного дня для ${item.label}`}>
                      {item.classIds.map((classId) => {
                        const classItem = classes.find((entry) => entry.id === classId);
                        if (!classItem) return null;
                        const mode = payload.day_modes?.[classId]?.[String(day)] || 'normal';
                        const editing = dayModeEditor?.classId === classId && dayModeEditor.day === day;
                        return <span className="constructor-day-mode-control" key={classId}>
                          <button type="button" className={`constructor-day-mode-button ${mode}`} aria-label={`${classItem.name}, ${DAYS[day]}: ${dayModeLabel(mode)}. Изменить режим дня`} title={`${classItem.name} · ${DAYS[day]} · ${dayModeLabel(mode)}`} aria-expanded={editing} onClick={(event) => { event.stopPropagation(); setDayModeEditor(editing ? null : { classId, day }); }}>
                            {dayModeIcon(mode)}
                          </button>
                          {editing ? renderDayModeChoices(classItem, day, mode) : null}
                        </span>;
                      })}
                    </div> : item.classIds.length > 1 ? <span className="constructor-week-class-labels" title={item.classIds.map((classId) => classes.find((entry) => entry.id === classId)?.name).filter(Boolean).join(' · ')}>
                      {item.classIds.map((classId) => classes.find((entry) => entry.id === classId)?.name).filter(Boolean).join(' · ')}
                    </span> : null}
                    <span
                      className="constructor-column-resizer"
                      role="separator"
                      tabIndex={0}
                      aria-label={`Ширина колонки ${item.label}`}
                      aria-orientation="vertical"
                      title="Перетащите для изменения ширины · двойной щелчок — автоширина"
                      onPointerDown={(event) => beginColumnResize(event, item.key)}
                      onPointerMove={moveColumnResize}
                      onPointerUp={(event) => finishColumnResize(event, true)}
                      onPointerCancel={(event) => finishColumnResize(event, false)}
                      onLostPointerCapture={(event) => {
                        if (columnResize.current?.pointerId === event.pointerId) {
                          columnResize.current = null;
                          setResizePreview(null);
                        }
                      }}
                      onDoubleClick={(event) => { event.stopPropagation(); resetColumnWidth(item.key); }}
                      onKeyDown={(event) => {
                        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                          event.preventDefault();
                          event.stopPropagation();
                          nudgeColumnWidth(item.key, event.key === 'ArrowLeft' ? -10 : 10);
                        } else if (event.key === 'Home') {
                          event.preventDefault();
                          event.stopPropagation();
                          resetColumnWidth(item.key);
                        }
                      }}
                    />
                  </div>
                ))}
                {gridRows.map(({ day: rowDay, period }, row) => (
                  <Fragment key={`${rowDay}:${period}`}>
                  {viewMode === 'week' && period === 0 ? <div className="constructor-day-band" style={{ gridColumn: `1 / span ${gridColumns.length + 1}` }}>{DAYS[rowDay]}</div> : null}
                  <div className="constructor-row">
                    <div className="constructor-time">
                      <strong>{period + 1} урок</strong>
                      <span>
                        {payload.slots[period].start}–{payload.slots[period].end}
                      </span>
                    </div>
                    {gridColumns.map((column, col) => {
                      const cellDay = rowDay;
                      const lessons = payload.lessons.filter(
                        (item) =>
                          overlapsSlot(item, cellDay, period, payload.slots) &&
                          (item.activityType === 'club' ? visualClasses(item, groups) : displayedClasses(item)).some((id) => column.classIds.includes(id)) &&
                          (viewMode === 'day' || matchesWeekTarget(item)) &&
                          (!teacherFilter || item.teacherId === teacherFilter || item.teacherIds?.includes(teacherFilter)) &&
                          (!subjectFilter || item.subject === subjectFilter) &&
                          (!query || `${item.subject} ${item.room} ${groups.find((g) => g.id === item.groupId)?.name || ''} ${teachers.find((t) => t.id === item.teacherId)?.name || ''}`.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru'))),
                      ).sort((a, b) => a.classId.localeCompare(b.classId));
                      return (
                        <div
                          key={column.key}
                          role="gridcell"
                          tabIndex={0}
                          onKeyDown={(event) => {
                            if ((event.target as Element).closest('.constructor-card')) return;
                            if (event.key === 'Enter') {
                              event.preventDefault();
                              selectCell(row, col);
                              setTimeout(() => quickInput.current?.focus(), 0);
                            }
                          }}
                          aria-label={`${column.label}, ${DAYS[cellDay]}, ${payload.slots[period].start}`}
                          data-cell={keyOf({ row, col })}
                          className={`constructor-cell ${inRange(row, col) ? 'selected' : ''}`}
                          onClick={(event) => {
                            if (
                              !(event.target as Element).closest(
                                '.constructor-card',
                              )
                            )
                              selectCell(row, col, event.shiftKey);
                          }}
                          onDoubleClick={() => {
                            selectCell(row, col);
                            editCell();
                          }}
                          onDragOver={(event) => {
                            event.preventDefault();
                            if (dragging.current)
                              dragging.current.destination = { row, col };
                          }}
                          onDragEnter={(event) => {
                            event.preventDefault();
                            if (dragging.current)
                              dragging.current.destination = { row, col };
                          }}
                          onDrop={(event) => {
                            event.preventDefault();
                            const id =
                              event.dataTransfer.getData('text/plain') ||
                              dragging.current?.id;
                            if (id) {
                              if (dragging.current)
                                dragging.current.handled = true;
                              move(id, row, col, event.altKey);
                            }
                          }}
                          onContextMenu={(event) => {
                            event.preventDefault();
                            const card = (
                              event.target as Element
                            ).closest<HTMLElement>('[data-lesson-id]');
                            selectCell(row, col);
                            if (card?.dataset.lessonId)
                              setSelectedId(card.dataset.lessonId);
                            setContext({ x: event.clientX, y: event.clientY });
                          }}
                        >
                          <div className={`constructor-cell-lessons ${lessons.length > 3 ? 'many' : ''}`} style={{ gridTemplateColumns: `repeat(${lessons.length > 3 ? 2 : Math.max(1, lessons.length)}, minmax(0, 1fr))` }}>
                            {lessons.map((item) => {
                              const localGroups = linkedInstructionalGroupsForColumn(item, groups, students, column.classIds);
                              const englishCaption = item.kind === 'event' ? null : englishLessonCaption(item, groups, localGroups);
                              const isAvailableSlot = audienceOf(item, groups).kind === 'available_slot';
                              const classCaption = visualClasses(item, groups).filter((id) => column.classIds.includes(id)).map((id) => groups.find((g) => g.id === id)?.name || id).join(', ');
                              return (
                                <button
                                  key={item.id}
                                  data-lesson-id={item.id}
                                  title={`${englishCaption?.full || item.subject}\n${isAvailableSlot ? 'Все остальные' : linkedInstructionalGroups(item, groups).map((group) => audienceLabel(group)).join(', ') || classCaption || 'Группа не определена'}\n${isAvailableSlot ? 'Все остальные' : classCaption || 'Класс не определён'}\n${teachers.find((t) => t.id === item.teacherId)?.name || 'Без учителя'} · ${item.room ? normalizeRoomDisplay(item.room) : 'Без кабинета'}${item.kind === 'event' ? `\n${item.start || ''}–${item.end || ''} · ${item.place || ''}\n${item.description || ''}` : ''}`}
                                  style={{ backgroundColor: colorMode === 'none' ? undefined : colorMode === 'teacher' ? (item.teacherId ? teacherRegistry.color(item.teacherId) : '#f1f3f8') : stableColor(item.subject || 'none'), '--teacher-accent': colorMode === 'teacher' && item.teacherId ? teacherRegistry.accent(item.teacherId) : '#8290aa', '--teacher-text': colorMode === 'teacher' && item.teacherId ? readableTeacherText(teacherRegistry.color(item.teacherId)) : '#202339' } as React.CSSProperties}
                                  className={`constructor-card ${item.kind === 'event' ? 'event' : ''} ${item.kind === 'event' || item.end && item.end !== payload.slots[item.period]?.end ? `multislot ${item.period !== period ? 'event-continuation' : ''} ${overlapsSlot(item, cellDay, period + 1, payload.slots) ? 'event-continues' : ''}` : ''} ${selectedId === item.id ? 'active' : ''} ${conflictsOpen && issues.some((issue) => (issue.lessonId === item.id || issue.related === item.id) && issue.severity === 'error') ? 'error' : conflictsOpen && issues.some((issue) => issue.lessonId === item.id || issue.related === item.id) ? 'warning' : ''}`}
                                  draggable
                                  onDragStart={(event) => {
                                    event.dataTransfer.setData(
                                      'text/plain',
                                      item.id,
                                    );
                                    event.dataTransfer.effectAllowed =
                                      'copyMove';
                                    dragging.current = { id: item.id };
                                  }}
                                  onDragEnd={(event) => {
                                    const active = dragging.current;
                                    if (active?.destination && !active.handled)
                                      move(
                                        active.id,
                                        active.destination.row,
                                        active.destination.col,
                                        event.altKey,
                                      );
                                    dragging.current = null;
                                  }}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    selectCell(row, col);
                                    setSelectedId(item.id);
                                    setPanelMode('lesson');
                                  }}
                                  onDoubleClick={(event) => {
                                    event.stopPropagation();
                                    setSelectedId(item.id);
                                    setPanelMode('lesson');
                                    setPanelOpen(true);
                                  }}
                                >
                                  <span>
                                    <strong>
                                      {item.kind === 'event' ? <>◆ {item.subject}{item.period !== period ? ' ↳' : ''}</> : <><span className="constructor-subject-short">{item.period !== period ? '↳ ' : ''}{englishCaption?.short || shortSubject(item.subject || 'Новый урок')}</span><span className="constructor-subject-full">{item.period !== period ? '↳ ' : ''}{englishCaption?.full || subjectLabel(item.subject) || 'Новый урок'}</span></>}
                                    </strong>
                                    <small>
                                      {item.activityType === 'club' ? 'Участники не назначены' : <>{lessonClasses(item).length > 1 ? <span title="Совместное занятие">↔ </span> : null}{isAvailableSlot ? 'Все остальные' : localGroups.length ? localGroups.map((group) => audienceShortLabel(group)).join(', ') : classCaption || 'Участники требуют уточнения'}</>}
                                    </small>
                                    <small className="constructor-teacher-line">{shortTeacher(teachers.find((t) => t.id === item.teacherId)?.name || '—')}{item.room ? ` · ${normalizeRoomDisplay(item.room)}` : item.place ? ` · ${item.place}` : ''}{[...new Set(item.teacherIds || [])].filter(id => id !== item.teacherId).map(id => <i key={id} className="constructor-co-teacher" style={{background:teacherRegistry.accent(id)}} title={teachers.find(t=>t.id===id)?.name || 'Преподаватель не найден'}>{shortTeacher(teachers.find(t=>t.id===id)?.name || '—')}</i>)}</small>
                                  </span>
                                  {conflictsOpen && issues.some(
                                    (issue) => issue.lessonId === item.id || issue.related === item.id,
                                  ) ? (
                                    <AlertTriangle size={13} />
                                  ) : null}
                                </button>
                              );
                            })}
                          </div>
                          {focus?.row === row && focus.col === col ? (
                            <div className="constructor-cell-entry-wrap">
                              {!selected && creationMode === 'quick' ? <input
                                ref={quickInput}
                                className="constructor-cell-entry"
                                aria-label="Быстрый ввод урока в ячейке"
                                value={quick}
                                onClick={(event) => event.stopPropagation()}
                                onDoubleClick={(event) => event.stopPropagation()}
                                onChange={(event) => { setQuick(event.target.value); setQuickChoice({}); }}
                                onKeyDown={(event) => {
                                  event.stopPropagation();
                                  if (event.key === 'Enter') { event.preventDefault(); addLesson(); }
                                  if (event.key === 'Escape') { setQuick(''); setQuickChoice({}); event.currentTarget.closest<HTMLElement>('[data-cell]')?.focus(); }
                                }}
                                placeholder="Введите урок…"
                              /> : null}
                              <button
                                className="constructor-cell-add"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  setSelectedId('');
                                  setQuick('');
                                  setQuickChoice({});
                                  setCreationMode('quick');
                                  setPanelMode('lesson');
                                  setPanelOpen(true);
                                  setTimeout(() => quickInput.current?.focus(), 0);
                                }}
                              ><Plus size={13} /> Урок</button>
                            </div>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                  </Fragment>
                ))}
              </div>
            </div>
            <ConstructorInspectorShell open={panelOpen} onClose={() => setPanelOpen(false)}>
              <div className="constructor-inspector-head">
                <div>
                  <small>{selected ? selected.kind === 'event' ? 'СОБЫТИЕ' : 'ЗАНЯТИЕ' : 'ВЫБРАННАЯ ЯЧЕЙКА'}</small>
                  <h2>
                    {selected
                      ? selected.subject || 'Новое занятие'
                    : selectedCell
                      ? `${gridColumns[focus!.col]?.label} · ${DAYS[selectedCell.day]}, ${payload.slots[selectedCell.period]?.start}`
                        : 'Выберите ячейку'}
                  </h2>
                </div>
                <div className="constructor-inspector-head-actions"><button onClick={() => setPanelOpen(false)} aria-label="Закрыть панель"><X size={17} /></button></div>
              </div>
              {selected ? (
                <button
                  className="constructor-slot-choice"
                  onClick={() => setSelectedId('')}
                >
                  Выделить весь слот
                </button>
              ) : null}
              {focus ? (
                <>
                  {!selected && creationMode === 'quick' && (gridColumns[focus.col]?.classIds.length || 0) > 1 ? (
                    <label>Класс занятия
                      <select aria-label="Класс занятия в параллели" value={targetClassId} onChange={(event) => { setTargetClassId(event.target.value); setQuick(''); setQuickChoice({}); }}>
                        <option value="">Выберите класс</option>
                        {gridColumns[focus.col].classIds.map((id) => <option key={id} value={id}>{groups.find((item) => item.id === id)?.name}</option>)}
                      </select>
                    </label>
                  ) : null}
                  {!selected ? (
                    <div className="constructor-creation">
                      <strong>Что добавляем?</strong>
                      <div className="constructor-creation-tabs" role="group" aria-label="Что добавляем?">
                        {(['lesson', 'lunch', 'club', 'elective', 'event'] as const).map((type) => <button key={type} aria-pressed={type === 'lunch' ? creationMode === 'lunch' : creationMode !== 'lunch' && activityType === type} className={(type === 'lunch' ? creationMode === 'lunch' : creationMode !== 'lunch' && activityType === type) ? 'active' : ''} onClick={() => {
                          setCreationError(''); setActivityType(type === 'lunch' ? 'lesson' : type);
                          setCreationMode(type === 'lunch' ? 'lunch' : type === 'event' ? 'event' : 'form');
                          const classIds = quickClassIds.length ? quickClassIds : selectedCell?.classId ? [selectedCell.classId] : focus ? gridColumns[focus.col]?.classIds || [] : [];
                          const slot = payload.slots[selectedCell?.period ?? 0];
                          const eligibleGrades = [...new Set(classIds.map((id) => Number(audienceGrade(groups.find((group) => group.id === id) || { id, name: '', group_type: 'class' }))).filter((grade) => Number.isFinite(grade) && grade > 0))];
                          setNewLesson({ subject: '', groupId: '', teacherId: '', room: '', classIds, audience: type === 'elective' ? { kind: 'students', student_ids: [] } : { kind: 'classes', class_ids: classIds }, ...(type === 'elective' ? { eligibleGrades, confirmedParticipantIds: [] } : {}), ...(type === 'event' ? { day: selectedCell?.day, start: slot?.start, end: slot?.end } : {}) });
                          setNewTeacherManuallySelected(false); setTeacherQuery('');
                        }}>{type === 'lesson' ? 'Урок' : type === 'lunch' ? 'Обед' : type === 'club' ? 'Кружок' : type === 'elective' ? 'Курс по выбору' : 'Событие'}</button>)}
                      </div>
                      {activityType === 'lesson' && creationMode !== 'lunch' ? <div className="constructor-creation-tabs" role="group" aria-label="Способ редактирования урока">
                        <button className={creationMode === 'quick' ? 'active' : ''} onClick={() => setCreationMode('quick')}>Быстрый ввод</button>
                        <button className={creationMode === 'form' ? 'active' : ''} onClick={() => { setNewLesson({ subject: quickSubject, groupId: quickGroupId, teacherId: quickExplicitTeacherId, room: quickRoom, classIds: quickClassIds, audience: audienceFromGroup(quickGroupId, quickClassIds, groups) }); setNewTeacherManuallySelected(Boolean(quickExplicitTeacherId)); setTeacherQuery(''); setCreationMode('form'); }}>Форма</button>
                      </div> : null}
                      {creationError ? <p role="alert" className="constructor-unrecognized">{creationError}</p> : null}
                      {creationMode === 'quick' ? <>
                        <label className="constructor-mobile-quick-field">Быстрый ввод<input className="constructor-mobile-quick" aria-label="Быстрый ввод занятия" value={quick} placeholder="Предмет, группа, преподаватель, кабинет" onChange={(event) => { setQuick(event.target.value); setQuickChoice({}); }} onKeyDown={(event) => { event.stopPropagation(); if (event.key === 'Enter') { event.preventDefault(); addLesson(); } }} /></label>
                        {quick.trim() ? <div className="constructor-suggestions">
                          <strong>Распознано из ввода в ячейке</strong>
                          <label>Предмет <select aria-label="Распознанный предмет" value={quickSubject} onChange={(event) => setQuickChoice((choice) => ({ ...choice, subject: event.target.value }))}>
                            <option value="">{quickParsed.subjects.length > 1 ? 'Несколько совпадений — выберите' : 'Выберите предмет'}</option>
                            {contextualSubjects.map((item) => <option key={item} value={item}>{item}</option>)}
                          </select></label>
                          <label>Учебная группа <select aria-label="Распознанная группа" value={quickGroupId} onChange={(event) => setQuickChoice((choice) => ({ ...choice, groupId: event.target.value }))}>
                            <option value="">{quickParsed.groupIds.length > 1 ? 'Несколько совпадений — выберите' : 'Весь выбранный класс'}</option>
                            {quickGroupOptions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                          </select></label>
                          <label>Поиск преподавателя <input value={teacherQuery} onChange={(event) => setTeacherQuery(event.target.value)} placeholder="Имя или фамилия" /></label>
                          <label>Преподаватель <select aria-label="Распознанный преподаватель" value={resolvedQuickTeacherId} onChange={(event) => setQuickChoice((choice) => ({ ...choice, teacherId: event.target.value }))}>
                            <option value="">{quickParsed.teacherIds.length > 1 ? 'Несколько совпадений — выберите' : 'Не назначен'}</option>
                            {rankedTeachers.filter((item) => item.id === resolvedQuickTeacherId || !teacherQuery || item.name.toLocaleLowerCase('ru').includes(teacherQuery.toLocaleLowerCase('ru'))).map((item) => <option key={item.id} value={item.id}>{item.name}{busyTeachers.has(item.id) ? ` · занят: ${teacherBusyWith(item.id)?.subject || 'занятие'}` : ' · свободен'}</option>)}
                          </select></label>
                          <label>Кабинет <input aria-label="Распознанный кабинет" value={quickRoom} onChange={(event) => setQuickChoice((choice) => ({ ...choice, room: event.target.value }))} /></label>
                          <span>Аудитория: {(quickGroupId ? groupClassIds(quickGroupId, groups, students, quickClassIds) : quickClassIds).map((id) => groups.find((item) => item.id === id)?.name || id).join(', ') || 'уточните в форме'}</span>
                          {quickParsed.unknown.length ? <p className="constructor-unrecognized">Не распознано: {quickParsed.unknown.join(', ')}. Уточните текст или откройте форму.</p> : null}
                          <button onClick={addLesson} disabled={!quickSubject || Boolean(quickParsed.unknown.length) || quickParsed.groupIds.length > 1 && !quickChoice.groupId || quickParsed.teacherIds.length > 1 && !quickChoice.teacherId}>Добавить занятие</button>
                          <button onClick={() => { setNewLesson({ subject: quickSubject, groupId: quickGroupId, teacherId: quickExplicitTeacherId, room: quickRoom, classIds: quickClassIds, audience: audienceFromGroup(quickGroupId, quickClassIds, groups) }); setNewTeacherManuallySelected(Boolean(quickExplicitTeacherId)); setTeacherQuery(''); setCreationMode('form'); }}>Открыть подробную форму</button>
                        </div> : null}
                      </> : <div className="constructor-create-form">
                        {creationMode !== 'lunch' && creationMode !== 'no_lesson' ? <><label>{activityType === 'event' ? 'Название события' : activityType === 'lesson' ? 'Предмет' : activityType === 'club' ? 'Кружок' : 'Курс по выбору'} <input aria-label="Название активности" placeholder="Выберите или введите название" list="constructor-new-subjects" value={newLesson.subject} onFocus={(event) => { try { event.currentTarget.showPicker(); } catch { /* Native datalist may open on typing instead. */ } }} onChange={(event) => setNewLesson((item) => ({ ...item, subject: event.target.value }))} /></label><datalist id="constructor-new-subjects">{[...subjects, ...activities.filter((item) => item.status === 'active').map((item) => item.name)].filter((item, index, all) => all.indexOf(item) === index).map((item) => <option key={item} value={item} />)}</datalist></> : <p className="constructor-special-mark">{creationMode === 'lunch' ? 'Обед' : 'Нет урока'}</p>}
                        {(activityType === 'club' || activityType === 'elective') ? <>
                          <label>Запись каталога <select value={newLesson.activityCatalogId || ''} onChange={(event) => { const item = activities.find((entry) => entry.id === event.target.value); setNewLesson((current) => ({ ...current, activityCatalogId: item?.id || '', activityNameSnapshot: item?.name || '', subject: item?.name || current.subject })); }}><option value="">Без привязки к каталогу</option>{activities.filter((item) => item.status === 'active' && item.type === activityType).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
                          <details><summary>Управление каталогом · {activities.length}</summary><div className="constructor-related-actions"><button onClick={() => { const item = activities.find((entry) => entry.id === newLesson.activityCatalogId); editActivity(item, activityType as 'club' | 'elective'); }}>Создать / изменить выбранную запись</button>{activities.filter((item) => item.status === 'active').map((item) => <button key={item.id} onClick={() => void archiveActivity(item)}>В архив: {item.name}</button>)}{activities.filter((item) => item.status === 'archived').map((item) => <button key={item.id} onClick={() => void archiveActivity(item)}>Восстановить: {item.name}</button>)}</div></details>
                          {activityType === 'elective' ? <ElectiveClassFields groups={groups.filter((group) => group.group_type === 'class' && payload.classes.includes(group.id))} classIds={newLesson.classIds} eligibleGrades={newLesson.eligibleGrades || []} onClassIdsChange={(classIds) => setNewLesson((current) => ({ ...current, classIds }))} onEligibleGradesChange={(eligibleGrades) => setNewLesson((current) => ({ ...current, eligibleGrades }))} /> : null}
                          {activityType === 'elective' ? <><label>Направление <select value={newLesson.electiveDirection || ''} onChange={(event) => setNewLesson((current) => ({ ...current, electiveDirection: event.target.value === 'digital' ? 'digital' : undefined }))}><option value="">Не указано</option><option value="digital">Цифровое</option></select></label></> : null}
                        </> : null}
                        {creationMode === 'event' ? <p>{payload.week_start ? 'Событие относится только к этой неделе.' : 'Событие повторяется в неделях, созданных из этого шаблона.'}</p> : null}
                        {creationMode === 'event' ? <label className="constructor-informational"><input type="checkbox" checked={Boolean(newLesson.informational)} onChange={(event) => setNewLesson((item) => ({ ...item, informational: event.target.checked }))} /> Информационное событие без назначенных учеников</label> : null}
                        {creationMode !== 'lunch' && creationMode !== 'no_lesson' ? <><label>Преподаватель <input aria-label="Преподаватель нового занятия" placeholder="Начните вводить имя или фамилию" list="constructor-new-teachers" value={teacherQuery || teachers.find((item) => item.id === resolvedNewTeacherId)?.name || ''} onFocus={(event) => { try { event.currentTarget.showPicker(); } catch { /* Native datalist may open on typing instead. */ } }} onChange={(event) => { const value = event.target.value; const match = teachers.find((item) => item.name.toLocaleLowerCase('ru') === value.trim().toLocaleLowerCase('ru')); setNewTeacherManuallySelected(true); setTeacherQuery(match ? '' : value); setNewLesson((item) => ({ ...item, teacherId: match?.id || '' })); }} /><datalist id="constructor-new-teachers">{rankedTeachers.map((item) => <option key={item.id} value={item.name} label={busyTeachers.has(item.id) ? `${item.name} · занят: ${teacherBusyWith(item.id)?.subject || 'занятие'}` : `${item.name} · свободен`} />)}</datalist></label><label>Кабинет <input aria-label="Кабинет нового занятия" list="constructor-rooms" value={normalizeRoomInput(newLesson.room)} onChange={(event) => setNewLesson((item) => ({ ...item, room: event.target.value }))} /></label></> : null}
                        {creationMode === 'lunch' ? <label>Место (необязательно)<input aria-label="Место обеда" value={newLesson.room} onChange={(event) => setNewLesson((item) => ({ ...item, room: event.target.value }))} /></label> : null}
                        {activityType === 'club' ? <><label>Размещение в сетке<select multiple aria-label="Классы размещения кружка" value={newAudienceContextClassIds} onChange={(event) => setNewLesson((item) => ({ ...item, classIds: [...event.target.selectedOptions].map((option) => option.value) }))}>{groups.filter((group) => group.group_type === 'class' && payload.classes.includes(group.id)).map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}</select></label><p>Участники не назначены. Кружок виден в конструкторе; ученикам он появится после назначения участников.</p></> : null}
                        {activityType === 'elective' ? <p>Ученики и учебные группы отдельно не назначаются. Параллельные курсы доступны выбранным классам независимо друг от друга.</p> : !newLesson.informational && activityType !== 'club' ? <ParticipantsPicker groups={groups} students={students} audience={newLesson.audience || audienceFromGroup(newLesson.groupId, newLesson.classIds, groups)} subject={newLesson.subject} contextClassIds={newAudienceContextClassIds} gradeScope={selectedCell?.gradeScope} preview={newAudiencePreview} onRetryPreview={() => { setAudienceSnapshot(null); setNewAudienceSnapshot(null); setPreviewRetryNonce((value) => value + 1); }} onOpenBlockingLesson={(id) => { const lesson = payload.lessons.find((item) => item.id === id); if (lesson) openReviewLesson(lesson.id); }} onChange={(audience) => { const mirror = audiencePatch(audience, groups, students, newAudienceContextClassIds); setNewLesson((item) => ({ ...item, audience, groupId: mirror.groupId || '', classIds: mirror.classIds || [] })); }} /> : null}
                        {creationMode === 'event' ? <div className="constructor-field-row"><label>День <select aria-label="День нового занятия" value={newLesson.day ?? selectedCell?.day ?? day} onChange={(event) => setNewLesson((item) => ({ ...item, day: Number(event.target.value) }))}>{payload.days.map((value) => <option key={value} value={value}>{DAYS[value]}</option>)}</select></label><label>Начало <input type="time" value={newLesson.start || ''} onChange={(event) => setNewLesson((item) => ({ ...item, start: event.target.value }))} /></label></div> : null}
                        {creationMode === 'form' && newLesson.blockId ? <label>Название параллельного блока <input value={newLesson.blockName || ''} onChange={(event) => setNewLesson((item) => ({ ...item, blockName: event.target.value }))} placeholder="Например, курсы по выбору 5–6" /></label> : null}
                        {creationMode === 'event' ? <>
                          <label>Конец <input type="time" value={newLesson.end || ''} onChange={(event) => setNewLesson((item) => ({ ...item, end: event.target.value }))} /></label>
                          {newLesson.start && newLesson.end && minutes(newLesson.end) > minutes(newLesson.start) ? <p className="constructor-affected-slots">Затронуты уроки: {affectedSlots(newLesson.start, newLesson.end, payload.slots).join(', ') || 'нет пересечений с сеткой'}</p> : null}
                          <label>Сопровождающие <select multiple aria-label="Сопровождающие нового события" value={newLesson.teacherIds || []} onChange={(event) => setNewLesson((item) => ({ ...item, teacherIds: [...event.target.selectedOptions].map((option) => option.value) }))}>{rankedTeachers.map((item) => <option key={item.id} value={item.id}>{item.name}{busyTeachers.has(item.id) ? ' · занят' : ''}</option>)}</select></label>
                          <label>Место <input value={newLesson.place || ''} onChange={(event) => setNewLesson((item) => ({ ...item, place: event.target.value }))} /></label>
                          <label>Формат <select value={newLesson.format || ''} onChange={(event) => setNewLesson((item) => ({ ...item, format: event.target.value }))}><option value="">Не указан</option><option value="offline">Очно</option><option value="online">Онлайн</option><option value="offsite">Вне школы</option></select></label>
                          <label>Описание <textarea value={newLesson.description || ''} onChange={(event) => setNewLesson((item) => ({ ...item, description: event.target.value }))} /></label>
                        </> : null}
                        <button onClick={() => addStructured({ ...newLesson, subject: creationMode === 'lunch' ? 'Обед' : creationMode === 'no_lesson' ? 'Нет урока' : newLesson.subject, activityType, kind: activityType === 'event' || creationMode === 'event' ? 'event' : 'lesson', specialMark: creationMode === 'lunch' ? 'lunch' : creationMode === 'no_lesson' ? 'no_lesson' : undefined, teacherId: creationMode === 'lunch' || creationMode === 'no_lesson' ? '' : resolvedNewTeacherId, room: newLesson.room, start: activityType === 'event' || creationMode === 'event' ? newLesson.start : undefined, end: activityType === 'event' || creationMode === 'event' ? newLesson.end : undefined })}>Добавить {activityType === 'event' || creationMode === 'event' ? 'событие' : activityType === 'club' ? 'кружок' : activityType === 'elective' ? 'курс по выбору' : creationMode === 'lunch' ? 'обед' : creationMode === 'no_lesson' ? 'пометку «Нет урока»' : 'занятие'}</button>
                      </div>}
                    </div>
                  ) : null}
                  {selected ? (
                    <>
                      <div className="constructor-issues">
                        {issues
                          .filter((item) => item.lessonId === selected.id || item.related === selected.id)
                          .map((item, index) => (
                            <button
                              key={index}
                              className={item.severity}
                              onClick={() => {
                                const related = payload.lessons.find(
                                  (lesson) => lesson.id === item.related,
                                );
                                if (related) {
                                  setDay(related.day);
                                  setClassFilter('');
                                  setViewMode('day');
                                  setPanelMode('lesson');
                                  setPanelOpen(true);
                                  const col = gridColumns.findIndex(
                                    (column) => lessonClasses(related).some((id) => column.classIds.includes(id)),
                                  );
                                  if (col >= 0) {
                                    setAnchor({ row: related.period, col });
                                    setFocus({ row: related.period, col });
                                  }
                                  setSelectedId(related.id);
                                }
                              }}
                            >
                              <AlertTriangle size={14} /> {item.message}
                              {item.related ? ' →' : ''}
                            </button>
                          ))}
                      </div>
                      <div className="constructor-related-actions"><button onClick={() => { const blockId = selected.blockId || freshId(); if (!selected.blockId) updateLesson(selected.id, { blockId, blockName: selected.blockName || 'Параллельные занятия' }); setSelectedId(''); setNewLesson({ subject: '', groupId: '', teacherId: '', room: '', classIds: lessonClasses(selected), day: selected.day, period: selected.period, blockId, blockName: selected.blockName || 'Параллельные занятия' }); setNewTeacherManuallySelected(false); setCreationMode('form'); }}>+ Параллельное занятие</button>{selected.blockId ? <span>{selected.blockName || 'Параллельные занятия'} · {payload.lessons.filter((item) => item.blockId === selected.blockId).length}</span> : null}</div>
                      {selected.blockId ? <label>Название блока <input value={selected.blockName || ''} onChange={(event) => mutate((state) => ({ ...state, lessons: state.lessons.map((item) => item.blockId === selected.blockId ? { ...item, blockName: event.target.value } : item) }))} /></label> : null}
                      {isLunch(selected) ? <p className="constructor-special-mark">Обед</p> : <>
                      <label>
                        Предмет
                        <input
                          list="constructor-subjects"
                          value={selected.subject}
                          onChange={(event) =>
                            updateLesson(selected.id, {
                              subject: event.target.value,
                            })
                          }
                        />
                      </label>
                      <datalist id="constructor-subjects">
                        {subjects.map((item) => (
                          <option key={item} value={item} />
                        ))}
                      </datalist>
                      <label>Тип активности <select value={selected.activityType || (selected.kind === 'event' ? 'event' : 'lesson')} onChange={(event) => { const type = event.target.value as Lesson['activityType']; const explicitOnly = type === 'club'; const participants = selected.confirmedParticipantIds || []; updateLesson(selected.id, { activityType: type, kind: type === 'event' ? 'event' : 'lesson', ...(explicitOnly ? { audience: { kind: 'students', student_ids: type === 'club' ? [] : participants }, groupId: '', groupIds: [], ...(type === 'club' ? { confirmedParticipantIds: undefined, displayClassIds: visualClasses(selected, groups), classIds: [], classId: '' } : {}) } : {}) }); }}><option value="lesson">Урок</option><option value="club">Кружок</option><option value="elective">Курс по выбору</option><option value="event">Событие</option></select></label>
                      </>}
                      {(selected.activityType === 'club' || selected.activityType === 'elective') ? <>
                        <label>Каталог <select value={selected.activityCatalogId || ''} onChange={(event) => { const item = activities.find((entry) => entry.id === event.target.value); updateLesson(selected.id, { activityCatalogId: item?.id || undefined, activityNameSnapshot: item?.name || undefined, subject: item?.name || selected.subject }); }}><option value="">Без привязки</option>{activities.filter((item) => (item.status === 'active' || item.id === selected.activityCatalogId) && item.type === selected.activityType).map((item) => <option key={item.id} value={item.id} disabled={item.status === 'archived'}>{item.name}{item.status === 'archived' ? ' · архив' : ''}</option>)}</select></label>
                        {selected.activityType === 'elective' ? <ElectiveClassFields groups={groups.filter((group) => group.group_type === 'class' && payload.classes.includes(group.id))} classIds={lessonClasses(selected)} eligibleGrades={selected.eligibleGrades || []} onClassIdsChange={(classIds) => updateLesson(selected.id, { classIds, classId: classIds[0] || '' })} onEligibleGradesChange={(eligibleGrades) => updateLesson(selected.id, { eligibleGrades })} /> : null}
                        {selected.activityType === 'elective' ? <><label>Направление <select value={selected.electiveDirection || ''} onChange={(event) => updateLesson(selected.id, { electiveDirection: event.target.value === 'digital' ? 'digital' : undefined })}><option value="">Не указано</option><option value="digital">Цифровое</option></select></label></> : null}
                      </> : null}
                      {selected.kind === 'event' ? <label className="constructor-informational"><input type="checkbox" checked={Boolean(selected.informational)} onChange={(event) => updateLesson(selected.id, event.target.checked ? { informational: true, audience: undefined, groupId: '', groupIds: [] } : { informational: false, ...audiencePatch({ kind: 'classes', class_ids: lessonClasses(selected) }, groups, students) })} /> Информационное событие без назначенных учеников</label> : null}

                      {!isLunch(selected) ? <>
                      <label>
                        Найти преподавателя
                        <input
                          value={teacherQuery}
                          onChange={(event) =>
                            setTeacherQuery(event.target.value)
                          }
                          placeholder="Фамилия или имя"
                        />
                      </label>
                      <label>
                        Преподаватель
                        <select
                          value={selected.teacherId}
                          onChange={(event) =>
                            updateLesson(selected.id, {
                              teacherId: event.target.value,
                            })
                          }
                        >
                          <option value="">Не назначен</option>
                          {rankedTeachers
                            .filter(
                              (item) =>
                                item.id === selected.teacherId ||
                                !teacherQuery ||
                                item.name
                                  .toLocaleLowerCase('ru')
                                  .includes(
                                    teacherQuery.toLocaleLowerCase('ru'),
                                  ),
                            )
                            .map((item) => (
                              <option key={item.id} value={item.id}>
                                {item.name}
                                {busyTeachers.has(item.id) ? ` · занят: ${teacherBusyWith(item.id)?.subject || 'занятие'}` : ' · свободен'}
                              </option>
                            ))}
                        </select>
                      </label>
                      {selected.teacherId ? <label className="constructor-teacher-color">Цвет преподавателя
                        <input type="color" aria-label="Цвет преподавателя" disabled={!teacherRegistry.ready || teacherRegistry.pending.has(selected.teacherId)} value={teacherRegistry.color(selected.teacherId)} onChange={(event) => teacherRegistry.setColor(selected.teacherId, event.target.value)} />
                        <button type="button" disabled={!teacherRegistry.overrides[selected.teacherId]} onClick={() => teacherRegistry.resetColor(selected.teacherId)}>Сбросить</button>
                        {teacherRegistry.error ? <span role="alert">{teacherRegistry.error}</span> : null}
                        {teacherRegistry.notice ? <output>{teacherRegistry.notice}</output> : null}
                        <button type="button" onClick={() => setColorPanelOpen(true)}>Все преподаватели</button>
                      </label> : null}
                      </> : null}
                      <label>
                        Кабинет
                        <input
                          list="constructor-rooms"
                          value={normalizeRoomInput(selected.room)}
                          onChange={(event) =>
                            updateLesson(selected.id, {
                              room: event.target.value,
                            })
                          }
                        />
                      </label>
                      {allLessonClassesHaveMode(payload, selected, 'sdep') ? <label className="constructor-sdep-independent-toggle"><input type="checkbox" checked={isSdepIndependentLesson(payload, selected)} onChange={(event) => updateLesson(selected.id, { sdepIndependent: event.target.checked })} /> Самостоятельная работа: без обычных конфликтов кабинета, преподавателя и учеников</label> : null}
                      <datalist id="constructor-rooms">
                        {[...new Map(rooms.map((room) => [normalizeRoomKey(room), normalizeRoomDisplay(room)])).values()].map((item) => (
                          <option key={item} value={item} />
                        ))}
                      </datalist>
                      {selected.activityType === 'elective' ? <p>Участники поимённо не назначаются. Доступность курса и его размещение в классах указаны отдельно.</p> : !selected.informational && selected.activityType !== 'club' ? <><ParticipantsPicker groups={groups} students={students} audience={audienceOf(selected, groups)} subject={selected.subject} contextClassIds={lessonClasses(selected)} preview={audiencePreviews[selected.id]} onRetryPreview={() => { setAudienceSnapshot(null); setNewAudienceSnapshot(null); setPreviewRetryNonce((value) => value + 1); }} onConfirmRecalculation={() => setRecalculationOpen(true)} onOpenBlockingLesson={(id) => { const lesson = payload.lessons.find((item) => item.id === id); if (lesson) openReviewLesson(lesson.id); }} legacyNotice={selected.audienceMode === 'remainder' && !selected.audience ? 'Сохранено исходное правило остатка из canonical. Его можно заменить динамическим расчётом по времени.' : undefined} onChange={(audience) => updateLesson(selected.id, audiencePatch(audience, groups, students, lessonClasses(selected)))} />{selected.audienceMode === 'remainder' && !selected.audience ? <button onClick={() => updateLesson(selected.id, audiencePatch(audienceOf(selected, groups), groups, students, lessonClasses(selected)))}>Включить динамический расчёт остатка</button> : null}</> : null}
                      <div className="constructor-field-row">
                        <label>
                          День
                          <select
                            value={selected.day}
                            onChange={(event) =>
                              updateLesson(selected.id, {
                                day: Number(event.target.value),
                              })
                            }
                          >
                            {payload.days.map((item) => (
                              <option key={item} value={item}>
                                {DAYS[item]}
                              </option>
                            ))}
                          </select>
                        </label>
                        {selected.kind === 'event' ? <label>Начало <input type="time" value={selected.start || payload.slots[selected.period]?.start || ''} onChange={(event) => updateLesson(selected.id, { start: event.target.value })} /></label> : <label>Время <select value={selected.period} onChange={(event) => updateLesson(selected.id, { period: Number(event.target.value) })}>{payload.slots.map((slot, index) => <option key={index} value={index}>{index + 1} · {formatLessonTimeRange(slot.start, slot.end)}</option>)}</select></label>}
                      </div>
                      {selected.kind !== 'event' && selected.end && selected.end !== payload.slots[selected.period]?.end ? <label>Конец многослотового занятия <input type="time" value={selected.end} onChange={(event) => updateLesson(selected.id, { end: event.target.value })} /></label> : null}
                      {selected.kind === 'event' ? <fieldset className="constructor-event-fields"><legend>Событие · время и сопровождение</legend>
                        <label>Конец <input type="time" value={selected.end || payload.slots[selected.period]?.end || ''} onChange={(event) => updateLesson(selected.id, { end: event.target.value })} /></label>
                        {selected.start && selected.end && minutes(selected.end) > minutes(selected.start) ? <p className="constructor-affected-slots">Затронуты уроки: {affectedSlots(selected.start, selected.end, payload.slots).join(', ') || 'нет пересечений с сеткой'}</p> : null}
                        <label>Место <input value={selected.place || ''} onChange={(event) => updateLesson(selected.id, { place: event.target.value })} /></label>
                        <label>Формат <select value={selected.format || ''} onChange={(event) => updateLesson(selected.id, { format: event.target.value })}><option value="">Не указан</option><option value="offline">Очно</option><option value="online">Онлайн</option><option value="offsite">Вне школы</option></select></label>
                        <label>Описание <textarea value={selected.description || ''} onChange={(event) => updateLesson(selected.id, { description: event.target.value })} /></label>
                        <label>Сопровождающие <select multiple aria-label="Сопровождающие преподаватели" value={selected.teacherIds || []} onChange={(event) => { const ids = [...event.target.selectedOptions].map((option) => option.value); updateLesson(selected.id, { teacherIds: ids, teacherId: ids[0] || '' }); }}>{teachers.map((item) => <option key={item.id} value={item.id}>{item.name}{busyTeachers.has(item.id) ? ' · занят' : ''}</option>)}</select></label>
                      </fieldset> : null}
                      <button
                        className="constructor-delete"
                        onClick={removeSelected}
                      >
                        <Trash2 size={15} /> Удалить занятие
                      </button>

                    </>
                  ) : (
                    <div className="constructor-hint">
                  {creationMode === 'lunch' && !selected ? 'Выберите состав участников обеда; преподаватель и название занятия не требуются.' : activityType === 'club' && !selected ? 'Укажите название и размещение кружка. Преподаватель, место и каталог необязательны.' : selectedCell?.gradeScope ? 'Укажите название урока и аудиторию для всей параллели или выбранной учебной группы.' : 'Введите название урока и нажмите Enter. Двойной клик по карточке откроет поля занятия.'}
                    </div>
                  )}
                  <details className="constructor-resources">
                    <summary>Занятость в это время · {busyTeachers.size} преподавателей · {busyRooms.size} кабинетов</summary>
                    <strong>
                      Свободные преподаватели:{' '}
                      {
                        teachers.filter(
                          (item) =>
                            !busyTeachers.has(item.id) &&
                            (!selected?.subject ||
                              item.assignments.some((a) =>
                                sameText(a.subject, selected.subject),
                              )),
                        ).length
                      }
                    </strong>
                    <div>
                      {teachers
                        .filter(
                          (item) =>
                            !busyTeachers.has(item.id) &&
                            (!selected?.subject ||
                              item.assignments.some((a) =>
                                sameText(a.subject, selected.subject),
                              )),
                        )
                        .slice(0, 8)
                        .map((item) => (
                          <span key={item.id}>{item.name}</span>
                        ))}
                    </div>
                    <strong>Занятые преподаватели: {busyTeachers.size}</strong>
                    <div>
                      {teachers
                        .filter((item) => busyTeachers.has(item.id))
                        .map((item) => (
                          <span key={item.id}>{item.name} · {teacherBusyWith(item.id)?.subject || 'занятие'}</span>
                        ))}
                    </div>
                    <strong>
                      {allRooms.length
                        ? `Свободные кабинеты: ${allRooms.filter((room) => !busyRooms.has(normalizeRoomKey(room))).length}`
                        : 'Справочник кабинетов пока пуст'}
                    </strong>
                    <div>
                      {allRooms
                        .filter((room) => !busyRooms.has(normalizeRoomKey(room)))
                        .slice(0, 12)
                        .map((room) => (
                          <span key={room}>{room}</span>
                        ))}
                    </div>
                    <strong>Занятые кабинеты: {busyRooms.size}</strong>
                    <div>{allRooms.filter((room) => busyRooms.has(normalizeRoomKey(room))).map((room) => <span key={room}>{room} · {occupied.find((item) => normalizeRoomKey(item.room) === normalizeRoomKey(room))?.subject}</span>)}</div>
                  </details>
                </>
              ) : panelMode === 'settings' ? (
                <div className="constructor-settings">
                  <label>Дата состава учеников <input type="date" value={payload.reference_date || ''} onChange={(event) => mutate((state) => ({ ...state, reference_date: event.target.value || undefined }))} /></label>
                  <h3>Классы</h3>
                  {groups
                    .filter((item) => item.group_type === 'class')
                    .map((item) => (
                      <label key={item.id}>
                        <input
                          type="checkbox"
                          checked={payload.classes.includes(item.id)}
                          onChange={() =>
                            mutate((state) => ({
                              ...state,
                              classes: state.classes.includes(item.id)
                                ? state.classes.filter((id) => id !== item.id)
                                : [...state.classes, item.id],
                            }))
                          }
                        />{' '}
                        {item.name}
                      </label>
                    ))}
                  <h3>Дни</h3>
                  {DAYS.map((name, index) => (
                    <label key={name}>
                      <input
                        type="checkbox"
                        checked={payload.days.includes(index)}
                        onChange={() =>
                          mutate((state) => ({
                            ...state,
                            days: state.days.includes(index)
                              ? state.days.filter((day) => day !== index)
                              : [...state.days, index].sort((a, b) => a - b),
                          }))
                        }
                      />{' '}
                      {name}
                    </label>
                  ))}
                  <h3>Время уроков</h3>
                  <table className="constructor-slot-table">
                    <thead><tr><th>№</th><th>Начало</th><th>Конец</th><th>Удалить</th><th>Проверка</th></tr></thead>
                    <tbody>{payload.slots.map((slot, index) => {
                      const problems = slotIssues.filter((item) => item.index === index);
                      return <tr key={index} className={problems.length ? 'invalid' : ''}>
                        <th scope="row">{index + 1}</th>
                        <td><input aria-label={`Начало ${index + 1} урока`} type="time" value={slot.start} onChange={(event) => mutate((state) => ({ ...state, slots: state.slots.map((item, at) => at === index ? { ...item, start: event.target.value } : item) }))} /></td>
                        <td><input aria-label={`Конец ${index + 1} урока`} type="time" value={slot.end} onChange={(event) => mutate((state) => ({ ...state, slots: state.slots.map((item, at) => at === index ? { ...item, end: event.target.value } : item) }))} /></td>
                        <td><button aria-label={`Удалить ${index + 1} урок`} title={payload.lessons.some((item) => item.period === index) ? 'Сначала перенесите занятия из этого слота' : 'Удалить слот'} disabled={payload.lessons.some((item) => item.period === index)} onClick={() => mutate((state) => ({ ...state, slots: state.slots.filter((_, at) => at !== index), lessons: state.lessons.map((item) => item.period > index ? { ...item, period: item.period - 1 } : item) }))}><Trash2 size={14} /></button></td>
                        <td className="constructor-slot-problem">{problems.map((item, problemIndex) => <span key={problemIndex}>{item.message}</span>)}</td>
                      </tr>;
                    })}</tbody>
                  </table>
                  <button onClick={() => mutate((state) => ({ ...state, slots: [...state.slots, { start: state.slots.length ? addMinutes(state.slots[state.slots.length - 1].end, 10) : '09:00', end: state.slots.length ? addMinutes(state.slots[state.slots.length - 1].end, 55) : '09:45' }] }))}>+ Временной слот</button>
                </div>
              ) : (
                <div className="constructor-hint">Выберите ячейку расписания или откройте настройку шаблона.</div>
              )}
              <div className="constructor-editor-actions"><button onClick={() => setPanelOpen(false)}>К сетке</button><span>{saving ? 'Сохраняется' : dirty ? 'Есть изменения' : 'Сохранено'}</span><button onClick={() => void save()} disabled={!dirty || saving}><Save size={15}/> Сохранить черновик</button></div>
            </ConstructorInspectorShell>
            <button
              type="button"
              className={`constructor-drawer-tab${panelOpen ? ' is-open' : ''}`}
              aria-label={panelOpen ? 'Закрыть редактор расписания' : 'Открыть редактор расписания'}
              aria-expanded={panelOpen}
              aria-controls="constructor-inspector-drawer"
              title={panelOpen ? 'Закрыть редактор' : 'Открыть редактор'}
              onPointerDown={handleDrawerPointerDown}
              onPointerMove={handleDrawerPointerMove}
              onPointerUp={handleDrawerPointerUp}
              onPointerCancel={handleDrawerPointerCancel}
              onClick={toggleDrawerFromClick}
            >{panelOpen ? <ChevronRight size={18} aria-hidden="true" /> : <ChevronLeft size={18} aria-hidden="true" />}</button>
          </div>
          <footer className="constructor-footer">
            <span>
              Клик — ячейка · Enter — урок · Esc — весь слот · Shift+стрелки —
              диапазон · Ctrl/Cmd+C/X/V — копировать, вырезать, вставить ·
              Alt+перетаскивание — копия
            </span>

          </footer>
          {context ? (
            <div
              className="constructor-context"
              role="menu"
              tabIndex={-1}
              onKeyDown={(event) => {
                if (event.key === 'Escape') setContext(null);
              }}
              style={{ left: context.x, top: context.y }}
              onClick={(event) => event.stopPropagation()}
            >
              <button
                onClick={() => {
                  editCell();
                  setContext(null);
                }}
              >
                Редактировать
              </button>
              <button
                onClick={() => {
                  void copy(false);
                  setContext(null);
                }}
              >
                Копировать
              </button>
              <button
                onClick={() => {
                  void copy(true);
                  setContext(null);
                }}
              >
                Вырезать
              </button>
              <button
                onClick={() => {
                  void paste();
                  setContext(null);
                }}
              >
                Вставить
              </button>
              <button
                onClick={() => {
                  removeSelected();
                  setContext(null);
                }}
              >
                Удалить
              </button>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
