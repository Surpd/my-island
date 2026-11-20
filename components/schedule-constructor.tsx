/* oxlint-disable typescript/no-explicit-any, react/react-compiler, react-hooks/exhaustive-deps, jsx-a11y/prefer-tag-over-role, jsx-a11y/control-has-associated-label, jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/no-noninteractive-tabindex */
'use client';

import {
  Fragment,
  useEffect,
  useLayoutEffect,
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
import { ConstructorScheduleExport } from './constructor-schedule-export';
import { ConstructorTermComparison, type TermComparisonChoice, type TermComparisonChoices, type TermComparisonSide, type TermComparisonSource } from './constructor-term-comparison';
import { normalizeRoomKey, normalizeRoomDisplay, normalizeRoomInput, isVirtualRoom } from './schedule-rooms';
import { ConstructorResolutionPanel } from './constructor-resolution-panel';
import { formatLessonTimeRange } from './lesson-time';
import { ConstructorSchoolStart, type SchoolWeekIndex } from './constructor-school-start';
import { TeacherColorPanel } from './teacher-color-panel';
import { schedulePlacementClassIds, linkedInstructionalGroupsForColumn, isWholeClassAudience, remapPastedLessonAudience, groupClassIds, groupCourseContext, mergeScheduleDrafts, parseQuick, rankTeachersForAudience, sameScheduleMeaning, shouldWarnMissingTeacher, shortSubject, shortTeacher, confirmedClassLunchMarker, type ScheduleMergeConflict } from './schedule-constructor-logic';
import { LaboratoryPanel, type LaboratoryBlueprint, type LaboratoryPreview } from './schedule-laboratory-panel';
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
  attendance_reason?: string;
  attendance_history?: { at: string; actor: string; reason: string }[];
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
  allDay?: boolean;
  informational?: boolean;
  start?: string;
  end?: string;
  place?: string;
  description?: string;
  connectionInfo?: string;
  format?: string;
  blockId?: string;
  blockName?: string;
  activityType?: 'laboratory' | 'lesson' | 'practice' | 'consultation' | 'club' | 'elective' | 'event';
  responsible_teacher_ids?: string[];
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
  laboratory_blueprints?: LaboratoryBlueprint[];
  laboratory_meetings?: Record<string, unknown>;
  column_widths?: Record<string, number>;
  column_width_mode?: 'auto' | 'manual';
  import_review?: Raw;
  school_source?: TermComparisonSource;
  day_modes?: Record<string, Record<string, DayMode>>;
  grade_modes?: Record<string, Record<string, DayMode>>;
};
function prepareOptionalActivityPayload(payload: Payload, activities: ScheduleActivity[]) {
  const problems: string[] = [];
  const lessons = payload.lessons.map((lesson) => {
    const activityType = lesson.activityType;
    if (activityType !== 'club' && activityType !== 'elective') return lesson;
    const typeLabel = activityType === 'club' ? 'Кружок' : 'Курс по выбору';
    const catalog = lesson.activityCatalogId
      ? activities.find((item) => item.id === lesson.activityCatalogId)
      : undefined;
    if (lesson.activityCatalogId && !catalog) {
      problems.push(`${typeLabel} «${lesson.subject || lesson.id}» (${lesson.id}): запись каталога не найдена. Снимите привязку или выберите существующую.`);
      return lesson;
    }
    if (catalog && catalog.type !== activityType) {
      problems.push(`${typeLabel} «${lesson.subject || lesson.id}» (${lesson.id}): тип не совпадает с записью каталога «${catalog.name}».`);
      return lesson;
    }
    const snapshot = String(lesson.activityNameSnapshot || lesson.subject || catalog?.name || '').trim();
    if (!snapshot) {
      problems.push(`${typeLabel} «${lesson.subject || lesson.id}» (${lesson.id}): нет названия. Введите название или выберите запись каталога.`);
      return lesson;
    }
    return lesson.activityNameSnapshot === snapshot ? lesson : { ...lesson, activityNameSnapshot: snapshot };
  });
  return { payload: lessons.some((lesson, index) => lesson !== payload.lessons[index]) ? { ...payload, lessons } : payload, problems };
}
function copiedLesson(original: Lesson, id: string): Lesson {
  const lesson = { ...original, id };
  if (id !== original.id && (original.audience?.exclude_student_ids?.length || original.attendance_history?.length)) {
    if (original.audience) {
      lesson.audience = { ...original.audience };
      delete lesson.audience.exclude_student_ids;
    }
    delete lesson.attendance_history;
    delete lesson.attendance_reason;
    lesson.audience_snapshot = null;
  }
  return lesson;
}
function editorActivityType(lesson: Lesson): Lesson['activityType'] {
  if (lesson.activityType) return lesson.activityType;
  if (lesson.blockId && ['курс по выбору', 'курсы по выбору', 'цифровой трек'].includes(String(lesson.blockName || '').trim().toLocaleLowerCase('ru'))) return 'elective';
  return lesson.kind === 'event' ? 'event' : 'lesson';
}
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
function scheduleDraftLink() {
  const hash = window.location.hash;
  const hashQuery = hash.startsWith('#/admin/') && hash.includes('?') ? hash.slice(hash.indexOf('?') + 1) : '';
  const query = new URLSearchParams(hashQuery || window.location.search);
  return { draftId: query.get('draft'), lessonId: query.get('lesson') };
}
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
type IssueRoute = {
  draftId: string;
  origin: 'grid' | 'review' | 'comparison' | 'laboratory';
  title: string;
  ids: string[];
  active: string;
  slot?: TermComparisonSide;
  mayCreate?: boolean;
};

type Issue = {
  lessonId: string;
  code?: string;
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
function russianCount(count: number, one: string, few: string, many: string) {
  const lastTwo = Math.abs(count) % 100;
  const last = Math.abs(count) % 10;
  const noun = lastTwo >= 11 && lastTwo <= 14 ? many : last === 1 ? one : last >= 2 && last <= 4 ? few : many;
  return `${count} ${noun}`;
}
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
type DayModeState = DayMode | 'normal' | 'conflict';
function canonicalGradeOfGroup(group: Group | undefined): string {
  if (!group || (group.group_type !== 'class' && group.role !== 'base_class')) return '';
  const canonicalName = String(group.base_class_name || '').trim();
  const grade = canonicalName.match(/^(\d{1,2})(?:\D|$)/)?.[1];
  if (grade) return grade;
  return String(group.name || '').match(/^grade(\d{1,2}):base:/)?.[1] || '';
}
function dayModeForGrade(payload: Payload, grade: string, classes: Group[], weekday: number): DayModeState {
  const day = String(weekday);
  const explicit = payload.grade_modes?.[grade]?.[day];
  if (explicit) return explicit;
  const modes = new Set(classes.filter((group) => canonicalGradeOfGroup(group) === grade)
    .map((group) => payload.day_modes?.[group.id]?.[day] || 'normal'));
  return modes.size > 1 ? 'conflict' : [...modes][0] || 'normal';
}
function allLessonClassesHaveMode(payload: Payload, lesson: Lesson, mode: DayMode, classes: Group[]): boolean {
  const grades = [...new Set(lessonClasses(lesson).map((classId) => canonicalGradeOfGroup(classes.find((group) => group.id === classId))).filter(Boolean))];
  return grades.length > 0 && grades.every((grade) => dayModeForGrade(payload, grade, classes, lesson.day) === mode);
}
function isSdepIndependentLesson(payload: Payload, lesson: Lesson, classes: Group[]): boolean {
  // Existing grade/day mode is the complete rule; imported staffing and the
  // legacy per-lesson switch must not turn a self-study plan into a lesson.
  return lesson.activityType !== 'consultation' && allLessonClassesHaveMode(payload, lesson, 'sdep', classes);
}
function lessonOnline(payload: Payload, lesson: Lesson, classes: Group[]): boolean {
  return lesson.format === 'online' || isVirtualRoom(lesson.room) || allLessonClassesHaveMode(payload, lesson, 'online', classes);
}
function lessonNeedsPhysicalRoom(payload: Payload, lesson: Lesson, classes: Group[]): boolean {
  if (lessonOnline(payload, lesson, classes)) return false;
  return !isSdepIndependentLesson(payload, lesson, classes);
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
function readableIssueReason(value: string | null | undefined, fallback: string) {
  if (!value || /[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}|\b[a-z]+_[a-z_]+\b|\bcanonical\b|\bUUID\b/i.test(value)) return fallback;
  return value;
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
      result.push({ lessonId: lesson.id, severity: 'warning', type: 'audience', category: 'import', message: 'Не определена учебная группа для урока английского. Выберите группу и проверьте её состав.' });
    const resolvedLunchScope = lesson.specialMark === 'lunch' && (audience?.status === 'ok' || confirmedClassLunchMarker(lesson, groups));
    if (audienceNeedsReview(lesson, audience) && !resolvedLunchScope) add(lesson.id, 'warning', 'Состав учеников отличается от сохранённого или текущие данные требуют проверки. Сохранённый состав не заменён.', undefined, 'review');
    if (!payload.days.includes(lesson.day) || !payload.slots[lesson.period])
      add(lesson.id, 'error', 'День или время занятия не входят в шаблон.', undefined, 'critical');
    if (lesson.kind === 'event' && (!lesson.start || !lesson.end || minutes(lesson.end) <= minutes(lesson.start)))
      add(lesson.id, 'error', 'У события некорректный временной диапазон.', undefined, 'critical');
    if (!isSpecialMark(lesson) && !lesson.subject.trim()) add(lesson.id, 'error', lesson.kind === 'event' ? 'Не указано название события.' : 'Не указан предмет.');
    if (!isSpecialMark(lesson) && lessonNeedsPhysicalRoom(payload, lesson, groups) && !String(lesson.room || '').trim())
      add(lesson.id, 'warning', 'Для очного занятия не указан кабинет.');
    if (!['elective', 'club'].includes(lesson.activityType || '') && audience && audience.status !== 'ok' && !lesson.informational && !lesson.needsClarification && !lesson.importError) {
      result.push({ lessonId: lesson.id, severity: audience.status === 'error' ? 'error' : 'warning', type: 'audience', category: lesson.importError ? 'import' : 'incomplete', message: readableIssueReason(audience.reason, 'Не удалось определить участников. Проверьте выбранную группу и состав на дату занятия.') });
    }
    if (!isSpecialMark(lesson) && shouldWarnMissingTeacher(lesson, independentPractice.has(lesson.id) || isSdepIndependentLesson(payload, lesson, groups)))
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
        `${lessonClasses(lesson).map((id) => groups.find((item) => item.id === id)?.name || 'класс не уточнён').join(', ') || 'класс'} в ${lessonTimes(lesson,payload.slots).start || 'неуказанное время'}`;
      const pair = (message: string) => add(a.id, 'error', `${message}: ${describe(b)}.`, b.id);
      const teacherIdsA = new Set([...(a.teacherIds || []), a.teacherId].filter(Boolean));
      const teacherIdsB = new Set([...(b.teacherIds || []), b.teacherId].filter(Boolean));
      const independentA = isSdepIndependentLesson(payload, a, groups);
      const independentB = isSdepIndependentLesson(payload, b, groups);
      if (!independentA && !independentB && !independentPractice.has(a.id) && !independentPractice.has(b.id) && [...teacherIdsA].some((id) => teacherIdsB.has(id)))
        pair('Преподаватель уже ведёт урок');
      if (!independentA && !independentB && lessonNeedsPhysicalRoom(payload, a, groups) && lessonNeedsPhysicalRoom(payload, b, groups) && normalizeRoomKey(a.room) && normalizeRoomKey(a.room) === normalizeRoomKey(b.room)) pair(`Кабинет ${normalizeRoomDisplay(a.room)} занят`);
      const studentIdsA = new Set(previews[a.id]?.status === 'ok' ? previews[a.id].student_ids || [] : []);
      const studentIdsB = new Set(previews[b.id]?.status === 'ok' ? previews[b.id].student_ids || [] : []);
      // Clubs, electives, and events do not imply student participation.
      // Events take display priority; their source lessons remain intact.
      const optionalAudience = ['club', 'elective', 'event', 'consultation'].includes(a.activityType || '')
        || ['club', 'elective', 'event', 'consultation'].includes(b.activityType || '');
      const overlappingStudents = [...studentIdsA].filter((id) => studentIdsB.has(id));
      if (!independentA && !independentB && !optionalAudience && overlappingStudents.length) {
        const sample = overlappingStudents.slice(0, 3).map((studentId) => students.find((item) => item.id === studentId)?.name || 'ученик');
        const remainder = overlappingStudents.length > sample.length ? ` и ещё ${overlappingStudents.length - sample.length}` : '';
        pair(`Учеников назначено одновременно: ${overlappingStudents.length} (${sample.join(', ')}${remainder})`);
      }
    }
  return result.concat(payload.lessons.filter((lesson) => lesson.needsClarification || lesson.importError).filter((lesson) => !(lesson.specialMark === 'lunch' && !lesson.importError && (previews[lesson.id]?.status === 'ok' || confirmedClassLunchMarker(lesson, groups)))).map((lesson) => {
    const mismatchedMathGroups = lesson.clarificationReason?.includes('прежняя математическая группа удалена')
      ? linkedInstructionalGroups(lesson, groups).filter((group) => group.subject && subjectLabel(group.subject) !== subjectLabel(lesson.subject)) : [];
    const classNames = lessonClasses(lesson).map((id) => groups.find((group) => group.id === id)?.name).filter(Boolean);
    return {
      lessonId: lesson.id,
      ...(mismatchedMathGroups.length ? { code: 'stale_group_subject_mismatch' } : {}),
      type: 'data' as const,
      category: 'import' as const,
      severity: lesson.importError ? 'error' as const : 'warning' as const,
      message: mismatchedMathGroups.length
        ? `Ссылка ведёт в ${mismatchedMathGroups.map((group) => group.name).join(', ')} для математики, а предмет — ${subjectLabel(lesson.subject)}. Класс размещения: ${classNames.join(', ') || 'не подтверждён'}. Подтвердите аудиторию вручную; состав учеников не изменён.`
        : readableIssueReason(lesson.clarificationReason, 'Уточните, какие ученики посещают это занятие.'),
    };
  }));
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
  const [reviewFilter,setReviewFilter] = useState<'all'|'conflict'>();
  const [reviewMode, setReviewMode] = useState<'review' | 'history' | null>(null);
  const [resolutionFromReview, setResolutionFromReview] = useState(false);
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
  const [activityType, setActivityType] = useState<'lesson' | 'practice' | 'consultation' | 'club' | 'elective' | 'event'>('lesson');
  const [exportOpen, setExportOpen] = useState(false);
  const comparisonFromReview = useRef(false);
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const [comparisonFocus, setComparisonFocus] = useState<{ draftId: string; key: string } | null>(null);
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
  const [dayModeEditor, setDayModeEditor] = useState<{ grade: string; day: number } | null>(null);
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
  const [adminTableOpen, setAdminTableOpen] = useState(false);
  const [bulkElectiveSelection, setBulkElectiveSelection] = useState<string[]>([]);
  const [bulkElectivePreviewOpen, setBulkElectivePreviewOpen] = useState(false);
  const [bulkElectiveBlockName, setBulkElectiveBlockName] = useState('Цифровой трек');
  const [bulkElectiveTargetId, setBulkElectiveTargetId] = useState('__new__');
  const [issueSeverity, setIssueSeverity] = useState('all');
  const [issueRoute, setIssueRoute] = useState<IssueRoute | null>(null);
  const route = issueRoute?.draftId === draft?.id ? issueRoute : null;
  const routedLesson = draft?.payload.lessons.find(lesson => lesson.id === route?.active);
  const [issueDay, setIssueDay] = useState('');
  const [issueClass, setIssueClass] = useState('');
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
  const [newLesson, setNewLesson] = useState<{ subject: string; groupId: string; teacherId: string; teacherIds?: string[]; room: string; classIds: string[]; audience?: AudienceRule; informational?: boolean; allDay?: boolean; day?: number; period?: number; start?: string; end?: string; place?: string; description?: string; format?: string; connectionInfo?: string; blockId?: string; blockName?: string; activityCatalogId?: string; activityNameSnapshot?: string; eligibleGrades?: number[]; confirmedParticipantIds?: string[]; electiveDirection?: 'digital' }>({ subject: '', groupId: '', teacherId: '', room: '', classIds: [] });
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
  const quickFocusTimeout = useRef<number | null>(null);
  const scheduleQuickFocus = (target?: HTMLElement | null) => {
    if (quickFocusTimeout.current !== null) window.clearTimeout(quickFocusTimeout.current);
    quickFocusTimeout.current = window.setTimeout(() => {
      quickFocusTimeout.current = null;
      if (target) target.focus();
      else quickInput.current?.focus();
    }, 0);
  };
  const clip = useRef<Clip | null>(null);
  const dragging = useRef<{
    id: string;
    sourceCol: number;
    destination?: Cell;
    handled?: boolean;
    duplicate?: boolean;
  } | null>(null);
  const columnHeaders = useRef<Record<string, HTMLDivElement | null>>({});
  const gridWrap = useRef<HTMLDivElement | null>(null);
  const gridScroll = useRef({ left: 0, top: 0 });
  const restoringGridScroll = useRef(false);
  useLayoutEffect(() => {
    const grid = gridWrap.current;
    if (!grid) return;
    const { left, top } = gridScroll.current;
    restoringGridScroll.current = true;
    if (Math.abs(grid.scrollLeft - left) > 1) grid.scrollLeft = left;
    if (Math.abs(grid.scrollTop - top) > 1) grid.scrollTop = top;
    requestAnimationFrame(() => { restoringGridScroll.current = false; });
  }, [panelOpen, draft?.payload]);
  const columnResize = useRef<{ pointerId: number; key: string; startX: number; startWidth: number; scale: number; widths: Record<string, number> } | null>(null);
  const [resizePreview, setResizePreview] = useState<Record<string, number> | null>(null);
  const [labReturn, setLabReturn] = useState<LaboratoryBlueprint>();
  const [laboratoriesOpen, setLaboratoriesOpen] = useState(false);
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
  const linkedLesson = useRef<string | null>(null);
  const draftOpenRequest = useRef(0);
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
    if (schoolReady && (!capabilities.demo_available || scheduleDraftLink().draftId)) {
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
  const open = async (id: string, lessonId?: string, comparisonOnly = false) => {
    const request = ++draftOpenRequest.current;
    try {
      const value = await api<Draft>(
        schedulePath(`/${encodeURIComponent(id)}`),
      );
      if (request !== draftOpenRequest.current) return false;
      if (comparisonOnly && !value.payload?.school_source?.term2_preparation?.comparison?.items.length) return false;
      let recovered: LocalDraftRecovery | null = null;
      try {
        const stored = window.localStorage.getItem(recoveryKey(workspace, id));
        if (stored) {
          const parsed = JSON.parse(stored) as LocalDraftRecovery;
          if (parsed.id === id && parsed.payload && Array.isArray(parsed.payload.lessons)) recovered = parsed;
        }
      } catch { /* Ignore an invalid or unavailable recovery copy. */ }
      const opened = recovered ? { ...value, name: recovered.name, payload: recovered.payload, revision: recovered.revision } : value;
      setIssueRoute(null);
      setComparisonOpen(false);
      comparisonFromReview.current = false;
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
      const lesson = lessonId ? opened.payload.lessons.find(item => item.id === lessonId) : undefined;
      setSelectedId(lesson?.id || '');
      setAnchor(null);
      setFocus(null);
      setDay(lesson?.day ?? opened.payload.days[0] ?? 0);
      setWeekTarget('');
      linkedLesson.current = lesson?.id || null;
      if (lesson) {
        setViewMode('day');
        setPanelMode('lesson');
        setPanelOpen(true);
      }
      setStatus([
        recovered ? 'Восстановлены несохранённые изменения. Сохраните черновик, чтобы записать их.' : '',
        lessonId && !lesson ? 'В этой редакции указанное занятие отсутствует. Проверьте дату недели и выберите занятие в проверке расписания.' : '',
      ].filter(Boolean).join(' '));
      return true;
    } catch (error) {
      if (request === draftOpenRequest.current) setStatus(String((error as Error).message));
      return false;
    }
  };
  const openedLinkedDraft = useRef(false);
  useEffect(() => {
    if (directoryAccess !== 'school' || workspace !== 'school' || openedLinkedDraft.current) return;
    const { draftId, lessonId } = scheduleDraftLink();
    openedLinkedDraft.current = true;
    if (draftId) void open(draftId, lessonId || undefined);
  }, [directoryAccess, workspace]);
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
      const prepared = prepareOptionalActivityPayload(base.payload, activities);
      if (prepared.problems.length) {
        setRenameError(`Сначала исправьте: ${prepared.problems.join(' ')}`);
        return;
      }
      const updated = await api<Draft>(schedulePath(`/${encodeURIComponent(current.id)}`), {
        method: 'PUT', body: JSON.stringify({ expected_revision: revision.current, name, payload: prepared.payload }),
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
  const updateDayMode = (grade: string, selectedDay: number, mode: DayMode | 'normal') => {
    mutate((state) => {
      const affectedClassIds = classes.filter((item) => canonicalGradeOfGroup(item) === grade).map((item) => item.id);
      const dayModes = { ...state.day_modes };
      for (const classId of affectedClassIds) {
        const classModes = { ...dayModes[classId] };
        delete classModes[String(selectedDay)];
        if (Object.keys(classModes).length) dayModes[classId] = classModes;
        else delete dayModes[classId];
      }
      const gradeModes = { ...state.grade_modes };
      const gradeMode = { ...gradeModes[grade] };
      if (mode === 'normal') delete gradeMode[String(selectedDay)];
      else gradeMode[String(selectedDay)] = mode;
      if (Object.keys(gradeMode).length) gradeModes[grade] = gradeMode;
      else delete gradeModes[grade];
      return { ...state, day_modes: Object.keys(dayModes).length ? dayModes : undefined,
        grade_modes: Object.keys(gradeModes).length ? gradeModes : undefined };
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
    const termComparisonKey = route?.origin === 'comparison' && comparisonFocus?.draftId === current.id
      ? comparisonFocus.key : undefined;
    const prepared = prepareOptionalActivityPayload(current.payload, activities);
    if (prepared.problems.length) {
      setSaveFailed(true);
      setStatus(`Сохранение остановлено. ${prepared.problems.join(' ')}`);
      return false;
    }
    const localCurrent = prepared.payload === current.payload ? current : { ...current, payload: prepared.payload };
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
      const preparedMerge = prepareOptionalActivityPayload(merged.draft.payload, activities);
      if (preparedMerge.problems.length) throw new Error(`Сохранение остановлено. ${preparedMerge.problems.join(' ')}`);
      try {
        const saved = await api<Draft>(schedulePath(`/${encodeURIComponent(server.id)}`), {
          method: 'PUT',
          body: JSON.stringify({ expected_revision: server.revision, name: merged.draft.name, payload: preparedMerge.payload, term_comparison_key: termComparisonKey }),
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
            payload: prepared.payload,
            term_comparison_key: termComparisonKey,
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
          const localNowRaw = draftRef.current?.id === current.id ? draftRef.current : current;
          const preparedLocal = prepareOptionalActivityPayload(localNowRaw.payload, activities);
          if (preparedLocal.problems.length) throw new Error(`Сохранение остановлено. ${preparedLocal.problems.join(' ')}`);
          const localNow = preparedLocal.payload === localNowRaw.payload ? localNowRaw : { ...localNowRaw, payload: preparedLocal.payload };
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
          if (persisted.revision > expectedRevision && sameDraftContent(localCurrent, persisted)) {
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
          term_comparison_key: route?.origin === 'comparison' && comparisonFocus?.draftId === server.id
            ? comparisonFocus.key : undefined,
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
  const dayModeLabel = (mode: DayModeState) => mode === 'conflict' ? 'Нужно выбрать общий режим' : mode === 'online' ? 'Онлайн' : mode === 'sdep' ? 'SDEP' : 'Обычный';
  const dayModeIcon = (mode: DayModeState) => mode === 'conflict'
    ? <AlertTriangle size={14} aria-hidden="true" />
    : mode === 'online'
    ? <Monitor size={14} aria-hidden="true" />
    : mode === 'sdep'
      ? <BookOpenCheck size={14} aria-hidden="true" />
      : <School size={14} aria-hidden="true" />;
  const renderDayModeChoices = (grade: string, selectedDay: number, mode: DayModeState) => {
    const gradeNumber = Number(grade);
    return <span className="constructor-day-mode-popover" role="group" aria-label={`Выбор режима ${grade} класс · ${DAYS[selectedDay]}`} onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
      <strong>{grade} класс · {DAYS[selectedDay]}</strong>
      {mode === 'conflict' ? <small>Старые значения классов параллели различаются. Выберите общий режим.</small> : null}
      {(['normal', 'online', 'sdep'] as const).map((option) => <button
        type="button"
        key={option}
        className={`constructor-day-mode-option ${mode === option ? 'active' : ''} ${option}`}
        disabled={option === 'sdep' && (gradeNumber < 9 || gradeNumber > 11)}
        aria-pressed={mode === option}
        onClick={() => { updateDayMode(grade, selectedDay, option); setDayModeEditor(null); }}
      >{dayModeIcon(option)}<span>{dayModeLabel(option)}</span></button>)}
    </span>;
  };
  const classColumns: GridColumn[] = [...new Set(visibleClasses.map((item) => canonicalGradeOfGroup(item) || item.name))].sort((a, b) => Number(a) - Number(b) || a.localeCompare(b, 'ru')).map((grade) => {
        const matching = visibleClasses.filter(
          (item) => (canonicalGradeOfGroup(item) || item.name) === grade,
        );
        return {
          key: `grade:${grade}`,
          label: canonicalGradeOfGroup(matching[0]) ? `${grade} класс` : grade,
          classIds: matching.map((item) => item.id),
        };
      });
  const selectedWeekTarget = (() => {
    if (weekTarget.startsWith('parallel:')) {
      const grade = weekTarget.slice('parallel:'.length);
      return {
        label: `${grade} класс`,
      classIds: classes.filter((item) => canonicalGradeOfGroup(item) === grade).map((item) => item.id),
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
      .map((id) => canonicalGradeOfGroup(groups.find((item) => item.id === id)))
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
        ].map(subjectLabel)),
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
  const unlinkedElectives = payload?.lessons.filter((lesson) => editorActivityType(lesson) === 'elective' && !lesson.blockId) || [];
  const selectedBulkElectives = unlinkedElectives.filter((lesson) => bulkElectiveSelection.includes(lesson.id));
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
  const selectedActivityType = selected ? editorActivityType(selected) : undefined;
  useLayoutEffect(() => {
    if (!selected || !payload || linkedLesson.current !== selected.id || day !== selected.day || viewMode !== 'day') return;
    const classIds = selected.activityType === 'club' ? visualClasses(selected, groups) : displayedClasses(selected);
    const col = gridColumns.findIndex(column => classIds.some(id => column.classIds.includes(id)));
    if (col >= 0 && payload.days.includes(selected.day) && payload.slots[selected.period]) {
      linkedLesson.current = null;
      setAnchor({ row: selected.period, col });
      setFocus({ row: selected.period, col });
      requestAnimationFrame(() => {
        const card = gridWrap.current?.querySelector<HTMLElement>(`[data-cell="${keyOf({row:selected.period,col})}"] [data-lesson-id="${CSS.escape(selected.id)}"]`)
          || gridWrap.current?.querySelector<HTMLElement>(`[data-lesson-id="${CSS.escape(selected.id)}"]`);
        card?.scrollIntoView({block: 'center', inline: 'center'});
        card?.focus({ preventScroll: true });
        if (gridWrap.current) gridScroll.current = { left: gridWrap.current.scrollLeft, top: gridWrap.current.scrollTop };
      });
    } else if (groups.length) {
      linkedLesson.current = null;
    }
  }, [selected, payload, day, viewMode, gridColumns, groups.length]);

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
    ? Math.max(0, firstOverlappingSlot(newLesson.start, newLesson.end, payload?.slots || []))
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
          ? Math.max(0, firstOverlappingSlot(newLesson.start, newLesson.end, payload.slots))
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
    if (focusEditor) scheduleQuickFocus();
    else window.setTimeout(() => root.current?.querySelector<HTMLElement>(`[data-cell="${keyOf(cell)}"]`)?.focus(), 0);
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
    scheduleQuickFocus();
  };
  const addStructured = (fields: { subject: string; groupId: string; teacherId: string; teacherIds?: string[]; room: string; classIds: string[]; audience?: AudienceRule; informational?: boolean; specialMark?: 'lunch' | 'no_lesson'; day?: number; period?: number; kind?: 'lesson' | 'event'; allDay?: boolean; start?: string; end?: string; place?: string; description?: string; format?: string; connectionInfo?: string; blockId?: string; blockName?: string; raw?: string; activityType?: Lesson['activityType']; activityCatalogId?: string; activityNameSnapshot?: string; eligibleGrades?: number[]; confirmedParticipantIds?: string[]; electiveDirection?: 'digital' }) => {
    if (!payload || !selectedCell) return;
    setCreationError('');
    const fail = (message: string) => { setCreationError(message); setStatus(message); };
    const eventStart = fields.allDay ? '00:00' : fields.start;
    const eventEnd = fields.allDay ? '23:59' : fields.end;
    if (fields.kind === 'event' && (!eventStart || !eventEnd || minutes(eventEnd) <= minutes(eventStart))) {
      fail('У события время окончания должно быть позже начала.');
      return;
    }
    const eventPeriod = fields.kind === 'event' ? Math.max(0, firstOverlappingSlot(eventStart!, eventEnd!, payload.slots)) : -1;
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
    if (fields.activityType === 'consultation' && !fields.teacherId && !fields.teacherIds?.length) {
      fail('Выберите преподавателя консультации.');
      return;
    }
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
      subject: subjectLabel(fields.subject),
      teacherId: fields.teacherId || fields.teacherIds?.[0] || '',
      teacherIds: fields.teacherIds?.length ? fields.teacherIds : fields.teacherId ? [fields.teacherId] : [],
      groupId: audienceFields?.groupId || '',
      groupIds: audienceFields?.groupIds || [],
      ...(audience ? { audience } : {}),
      ...(fields.informational ? { informational: true } : {}),
      ...(fields.specialMark ? { specialMark: fields.specialMark, canonicalRole: fields.specialMark === 'no_lesson' ? 'explicit_no_lesson' : 'lunch' } : {}),
      room: fields.format === 'online' || isVirtualRoom(fields.room) ? '' : normalizeRoomDisplay(fields.room),
      kind: fields.kind || 'lesson',
      ...(fields.kind === 'event' ? { start: eventStart, end: eventEnd, ...(fields.allDay ? { allDay: true } : {}) } : fields.start ? { start: fields.start } : {}),
      ...(fields.place ? { place: fields.place } : {}),
      ...(fields.description ? { description: fields.description } : {}),
      ...(isVirtualRoom(fields.room) ? { format: 'online' } : fields.format ? { format: fields.format } : {}),
      ...(fields.connectionInfo ? { connectionInfo: fields.connectionInfo } : {}),
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
        const optionalActivity = next.activityType === 'club' || next.activityType === 'elective';
        if (patch.activityType !== undefined && patch.activityType !== item.activityType) {
          if (optionalActivity) next.activityCatalogId = '';
          else delete next.activityCatalogId;
          if (optionalActivity && patch.subject === undefined && patch.activityNameSnapshot === undefined)
            next.activityNameSnapshot = String(item.activityNameSnapshot || item.subject || '').trim() || undefined;
        }
        if (optionalActivity) {
          const name = patch.subject !== undefined
            ? patch.subject.trim()
            : patch.activityNameSnapshot !== undefined
              ? String(patch.activityNameSnapshot || '').trim()
              : String(next.activityNameSnapshot || next.subject || '').trim();
          if (name) next.activityNameSnapshot = name;
          else delete next.activityNameSnapshot;
        }
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
  const openReviewLesson = (id: string, revealGrid = false) => {
    const lesson = payload?.lessons.find((item) => item.id === id);
    if (!lesson) { setStatus('В этой редакции такого занятия нет. Откройте исходную неделю или выберите слот в сверке.'); return false; }
    setDay(lesson.day);
    setViewMode('day');
    setFiltersOpen(false);
    setClassFilter('');
    setTeacherFilter('');
    setSubjectFilter('');
    setQuery('');
    setSelectedId(id);
    setPanelMode('lesson');
    const placed = payload?.days.includes(lesson.day) && payload.slots[lesson.period] && visualClasses(lesson, groups).concat(displayedClasses(lesson)).some(id => classes.some(c => c.id === id));
    setPanelOpen(!revealGrid || !placed || !window.matchMedia('(max-width: 900px)').matches);
    if (!placed) setStatus('У занятия пока нет места в сетке. В редакторе можно указать день, время и класс.');
    linkedLesson.current = id;
    setAnchor(null);
    setFocus(null);
    return true;
  };
  const showIssueLesson = (id: string, origin: IssueRoute['origin'], title: string, related: string[] = []) => {
    if (!draft || !openReviewLesson(id, true)) return;
    setIssueRoute({draftId:draft.id, origin, title, ids:[...new Set([id,...related])], active:id});
    setConflictsOpen(false);
    setReviewMode(null);
  };
  const openReviewCenter = (filter: 'all' | 'conflict') => {
    setIssueRoute(null);
    setComparisonOpen(false);
    setConflictsOpen(false);
    setPanelOpen(false);
    setReviewFilter(filter);
    setReviewMode('review');
  };
  const openTermComparison = () => {
    setIssueRoute(null);
    setConflictsOpen(false);
    setPanelOpen(false);
    setComparisonOpen(true);
  };
  const backToIssues = () => {
    if (!route) return;
    setPanelOpen(false);
    setIssueRoute(null);
    if (route.origin === 'review') {setReviewFilter(undefined);setReviewMode('review');}
    else if (route.origin === 'laboratory') setLaboratoriesOpen(true);
    else if (route.origin === 'comparison') setComparisonOpen(true);
    else {
      setConflictsOpen(true);
      requestAnimationFrame(() => {
        const panel = document.querySelector<HTMLElement>('.constructor-conflict-panel');
        const button = panel?.querySelector<HTMLElement>(`[data-issue-open="${CSS.escape(route.active)}"]`);
        (button || panel)?.focus();
        (button || panel)?.scrollIntoView({block:'center'});
      });
    }
  };
  const openComparisonSlot = (side: TermComparisonSide, mayCreate = true) => {
    if (!draft) return;
    setIssueRoute({draftId:draft.id,origin:'comparison',title:`Слот · ${side.subject || 'Новое занятие'}`,ids:[],active:'',slot:side,mayCreate});
    setNewLesson({subject:side.subject || '',groupId:'',teacherId:'',room:side.room || '',classIds:[]});
    setQuick(''); setQuickChoice({}); setCreationError('');
    setTeacherQuery(''); setNewTeacherManuallySelected(false);
    setActivityType(side.kind === 'club' || side.kind === 'elective' || side.kind === 'event' ? side.kind : 'lesson');
    setViewMode('day'); setDay(side.day);
    setFiltersOpen(false);
    setClassFilter(''); setTeacherFilter(''); setSubjectFilter(''); setQuery('');
    setSelectedId(''); setPanelMode('lesson'); setPanelOpen(false);
    setAnchor(null); setFocus(null);
  };
  const slot = route?.slot;
  const slotClasses = useMemo(() => {
    const names = slot?.classes.map(name => name.split(' · ')[0].trim()) || [];
    return groups.filter(group => payload?.classes.includes(group.id) && names.includes(group.name)).map(group => group.id);
  }, [slot, groups, payload?.classes]);
  const slotRow = slot && payload?.days.includes(slot.day) ? firstOverlappingSlot(slot.start, slot.end, payload.slots) : -1;
  const slotColumn = slot && viewMode === 'day' && day === slot.day
    ? gridColumns.findIndex(column => column.classIds.some(id => slotClasses.includes(id))) : -1;
  const routeSlotCell = slotRow >= 0 && slotColumn >= 0 ? {row:slotRow, col:slotColumn} : null;
  useLayoutEffect(() => {
    if (!slot || slotRow < 0 || slotColumn < 0) return;
    const cell = { row: slotRow, col: slotColumn };
    setTargetClassId(slotClasses.length === 1 ? slotClasses[0] : '');
    setNewLesson(item => ({
      ...item, classIds: slotClasses, day: slot.day, period: slotRow,
      ...(slot.kind === 'event' ? {start:slot.start, end:slot.end} : {}),
    }));
    setAnchor(cell);
    setFocus(cell);
    const grid = gridWrap.current;
    const element = grid?.querySelector<HTMLElement>(`[data-cell="${keyOf(cell)}"]`);
    if (!grid || !element) return;
    // The available grid height changes after the comparison dialog closes.
    // Keep the destination visible during layout, until the user takes control.
    const keepVisible = new ResizeObserver(() => {
      const bounds = grid.getBoundingClientRect();
      const target = element.getBoundingClientRect();
      if (target.bottom > bounds.bottom || target.top < bounds.top) {
        grid.scrollTop += (target.top + target.bottom - bounds.top - bounds.bottom) / 2;
        gridScroll.current = {left:grid.scrollLeft, top:grid.scrollTop};
      }
    });
    const stop = () => keepVisible.disconnect();
    const frame = requestAnimationFrame(() => {
      element.scrollIntoView({block:'center', inline:'center'});
      element.focus({preventScroll:true});
      gridScroll.current = {left:grid.scrollLeft, top:grid.scrollTop};
      keepVisible.observe(grid);
    });
    for (const event of ['pointerdown', 'wheel', 'keydown']) window.addEventListener(event, stop, {capture:true, passive:true});
    return () => {
      cancelAnimationFrame(frame);
      stop();
      for (const event of ['pointerdown', 'wheel', 'keydown']) window.removeEventListener(event, stop, true);
    };
  }, [slot, slotRow, slotColumn, slotClasses]);
  const slotSelected = routeSlotCell && focus?.row === routeSlotCell.row && focus.col === routeSlotCell.col;
  const selectedBell = slotSelected ? payload?.slots[routeSlotCell.row] : null;
  const customSlotTime = slot && selectedBell && slot.kind !== 'event' && (selectedBell.start !== slot.start || selectedBell.end !== slot.end);
  const canCreateAtSlot = route?.mayCreate && slotSelected && !customSlotTime;
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
          copiedLesson(lesson, newId),
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
    const samePlacement = (dragging.current?.id === lessonId && dragging.current.sourceCol === col)
      || targetClassIds.length > 0 && targetClassIds.every(id => sourceClasses.includes(id));
    const needsAudienceConfirmation = !samePlacement && (
      lesson.activityType !== 'club' && !isWholeClassAudience(lesson)
      || destination.classIds.length > 1 && !explicitlySelectedClass
    );
    const targetLabel = targetClassIds.map((id) => groups.find((group) => group.id === id)?.name || id).join(', ') || 'выбранной аудитории';
    if (needsAudienceConfirmation) {
      const sourceLabel = sourceClasses.map((id) => groups.find((group) => group.id === id)?.name || id).join(', ') || 'исходная аудитория';
      if (!window.confirm(`${lesson.subject || 'Занятие'}: ${sourceLabel} → ${targetLabel}.\n\nПодтвердить новый охват?`)) return;
    }
    const newId = duplicate ? freshId() : lesson.id;
    const remapped = samePlacement ? copiedLesson(lesson, newId) : remapPastedLessonAudience(
      copiedLesson(lesson, newId),
      targetClassIds,
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
      if (quickFocusTimeout.current !== null && focus) {
        window.clearTimeout(quickFocusTimeout.current);
        quickFocusTimeout.current = null;
        root.current?.querySelector<HTMLElement>(`[data-cell="${keyOf(focus)}"]`)?.focus();
      }
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
      if (event.key === 'Enter' && event.target instanceof Element && event.target.closest('button, a, [role="button"]'))
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
  const busyRooms = new Set(occupied.filter(item => payload && lessonNeedsPhysicalRoom(payload, item, groups)).map((item) => normalizeRoomKey(item.room)).filter(Boolean));
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
  const resolvedQuickTeacherId = quickChoice.teacherId ?? (recommendedTeacherId || (quickParsed.teacherIds.length === 1 ? quickParsed.teacherIds[0] : ''));
  const resolvedNewTeacherId = newTeacherManuallySelected || activityType === 'elective' || activityType === 'practice' ? newLesson.teacherId : newLesson.teacherId || recommendedTeacherId;
  const newDayOnline = Boolean(payload && allLessonClassesHaveMode(payload, { classIds: newAudienceContextClassIds, day: previewDay } as Lesson, 'online', groups));
  const selectedDayOnline = Boolean(payload && selected && allLessonClassesHaveMode(payload, selected, 'online', groups));
  const newOnline = newDayOnline || newLesson.format === 'online' || isVirtualRoom(newLesson.room);
  const selectedOnline = selectedDayOnline || selected?.format === 'online' || isVirtualRoom(selected?.room);
  const allRooms = [...new Map([...knownRooms, ...rooms].filter(room => room && !isVirtualRoom(room)).map((room) => [normalizeRoomKey(room), normalizeRoomDisplay(room)])).values()];
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
  const copyIssueReport=async()=>{if(!draft)return;const groups=new Map<string,Issue[]>();for(const issue of filteredIssues){const code=issue.code||(issue.type==='teacher'&&/уже ведёт/i.test(issue.message)?'TEACHER_OVERLAP':issue.type==='room'&&/занят/i.test(issue.message)?'ROOM_OVERLAP':issue.type==='audience'&&/ученик/i.test(issue.message)?'STUDENT_OVERLAP':issue.type==='teacher'?'TEACHER_REVIEW':issue.type==='room'?'ROOM_REVIEW':issue.type==='audience'?'AUDIENCE_REVIEW':'SCHEDULE_REVIEW');groups.set(code,[...(groups.get(code)||[]),issue]);}const lines=['Отчёт проверки расписания',...[...groups].map(([code,items])=>{const ids=[...new Set(items.flatMap(item=>[item.lessonId,...(item.related?[item.related]:[])]).filter((id):id is string=>typeof id==='string'))];return `${code}\t${items.length}\t${ids.map(id=>`${window.location.origin}/admin/schedule/constructor?${new URLSearchParams({draft:draft.id,lesson:id})}`).join(', ')}`;})];await navigator.clipboard.writeText(lines.join('\n'));setStatus('Отчёт проверки скопирован без персональных данных.');};
  const matchesIssueLocation = (item: Issue) => (!issueDay && !issueClass) || [item.lessonId,item.related].some(id => {
    const lesson = payload?.lessons.find(entry => entry.id === id);
    return lesson && (!issueDay || String(lesson.day) === issueDay) && (!issueClass || displayedClasses(lesson).includes(issueClass));
  });
  const filteredIssues = issues.filter(matchesIssueLocation).filter((item) => issueSeverity === 'all' || item.severity === issueSeverity).filter((item) => issueType === 'all' || (issueType === 'teacher' ? item.type === 'teacher' : issueType === 'room' ? item.type === 'room' : issueType === 'student' ? item.type === 'audience' : item.type === 'data'));
  const conflictIssues = issues.filter((item) => item.category === 'conflict');
  const conflictLessons = new Set(conflictIssues.flatMap((item) => [item.lessonId, ...(item.related ? [item.related] : [])]));
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

  const schoolReview = workspace === 'school';
  const termComparison = schoolReview && !payload?.week_start ? payload?.school_source?.term2_preparation?.comparison : undefined;
  const termDecided = new Set(termComparison?.decisions?.map(decision => decision.key));
  const pendingTermItems = termComparison?.items.filter(item => item.initial_status !== 'accepted' && !termDecided.has(item.key)).length || 0;
  const termChoicePath = (id: string, step: string) =>
    schedulePath(`/${encodeURIComponent(id)}/term-comparison/${step}`);
  const previewTermChoices = async (key: string) => {
    if (!draft) throw new Error('Откройте рабочую копию.');
    return api<TermComparisonChoices>(termChoicePath(draft.id, 'choices'), {
      method: 'POST', body: JSON.stringify({ expected_revision: draft.revision, key }),
    });
  };
  const saveTermDecision = async (key: string, choice?: TermComparisonChoice, token?: string) => {
    if (!draft || dirty || saving || !termComparison) throw new Error('Сначала сохраните рабочую копию.');
    let updated: Draft;
    try {
      updated = await api<Draft>(termChoicePath(draft.id, choice ? 'apply' : 'decision'), {
        method: 'POST',
        body: JSON.stringify({ expected_revision: draft.revision, key,
          ...(choice ? { choice, preview_token: token } : { action: 'defer', reason: 'Решение отложено.' }) }),
      });
    } catch (failure) {
      if ((failure as Error & { status?: number }).status === 409) {
        await open(draft.id);
        throw new Error('Черновик изменился. Сверьте текущую версию перед сохранением решения.');
      }
      throw failure;
    }
    const schoolSource = updated.payload.school_source;
    setHistory(items => items.map(item => ({ ...item, school_source: schoolSource })));
    setFuture(items => items.map(item => ({ ...item, school_source: schoolSource })));
    revision.current = updated.revision;
    baseDraftRef.current = updated;
    setDraft(updated);
    setAudienceSnapshot(null);
    void refreshDrafts().catch(() => {});
  };
  const rosterClassId = classFilter || (selected
    ? audienceOf(selected, groups).kind === 'groups' ? audienceOf(selected, groups).group_ids?.[0] : lessonClasses(selected)[0]
    : undefined);
  const rosterClassLabel = groups.find(group => group.id === rosterClassId)?.name;
  const applyRosterDraft = (value: unknown) => {
    const updated = value as Draft | undefined;
    if (!draft || !updated?.payload || updated.id !== draft.id) return;
    setHistory(items => [...items.slice(-49), draft.payload]);
    setFuture([]);
    revision.current = updated.revision;
    baseDraftRef.current = updated;
    setDraft(updated);
    setDirty(false);
    changeVersion.current++;
    setAudienceSnapshot(null);
    void refreshDrafts().catch(() => setStatus('Составы сохранены. Список черновиков обновится при следующем открытии.'));
  };
  const workspaceTabs = demoAvailable ? (
      <nav className="constructor-workspaces" aria-label="Пространство конструктора">
        {demoAvailable ? <button className={workspace === 'demo' ? 'active' : ''} disabled={directoryAccess === 'checking'} onClick={() => { workspaceRef.current = 'demo'; setWorkspace('demo'); setGroups(directoryByWorkspace.current.demo.groups); setTeachers(directoryByWorkspace.current.demo.teachers); setStudents(directoryByWorkspace.current.demo.students); setKnownRooms(directoryByWorkspace.current.demo.rooms); setDrafts(demoDrafts); setDraft(null); }}>Демо <small>Синтетические данные</small></button> : null}
        <button className={workspace === 'school' ? 'active' : ''} disabled={directoryAccess !== 'school'} onClick={() => { workspaceRef.current = 'school'; setWorkspace('school'); setGroups(directoryByWorkspace.current.school.groups); setTeachers(directoryByWorkspace.current.school.teachers); setStudents(directoryByWorkspace.current.school.students); setKnownRooms(directoryByWorkspace.current.school.rooms); setDrafts(schoolDrafts); setDraft(null); }}>Школа <small>Школьный справочник</small></button>
      </nav>
  ) : null;
  return (
    <div
      className={`constructor constructor-ux ${draft ? 'has-draft' : ''} ${fullScreen ? 'is-fullscreen' : ''} ${termComparison ? 'has-term-comparison' : ''}`}
      role="application"
      aria-label="Конструктор расписания"
      ref={root}
      tabIndex={0}
      onClick={() => context && setContext(null)}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          if (quickFocusTimeout.current !== null && focus) {
            window.clearTimeout(quickFocusTimeout.current);
            quickFocusTimeout.current = null;
            root.current?.querySelector<HTMLElement>(`[data-cell="${keyOf(focus)}"]`)?.focus();
          }
          if (context) setContext(null);
          root.current?.querySelectorAll<HTMLDetailsElement>('details[data-ui-menu][open]').forEach((menu) => { menu.open = false; menu.querySelector<HTMLElement>('summary')?.focus(); });
          if (conflictsOpen) setConflictsOpen(false);
        }
      }}
    >
      <header className="constructor-top">
        <div>
          {draft || !schoolReview ? <small>РАСПИСАНИЕ · {workspace === 'school' ? 'ШКОЛА' : 'ПЕСОЧНИЦА'}</small> : null}
          <h1><span className="desktop-label">{!draft && schoolReview ? 'Расписание' : 'Конструктор расписания'}</span><span className="mobile-label">Расписание</span></h1>{draft ? <div className="constructor-current-draft" title={draft.name}><span>{draft.name}</span><button aria-label="Переименовать открытый черновик" aria-expanded={renameValue !== null} title="Переименовать" onClick={() => { setRenameValue(draft.name); setRenameError(''); }}><Pencil size={16} /></button>{renameValue !== null ? <div className="constructor-rename-popover" role="dialog" aria-label="Переименовать открытый черновик"><form onSubmit={(event) => { event.preventDefault(); void renameOpenDraft(); }}><label>Новое название<input ref={(element) => element?.focus()} aria-label="Новое название черновика" value={renameValue} onChange={(event) => setRenameValue(event.target.value)} onKeyDown={(event) => { event.stopPropagation(); if (event.key === 'Escape') { setRenameValue(null); setRenameError(''); } }} /></label>{renameError ? <p role="alert">{renameError}</p> : null}<div><button type="submit" disabled={saving}>Сохранить название</button><button type="button" onClick={() => { setRenameValue(null); setRenameError(''); }}>Отмена</button></div></form></div> : null}</div> : null}
          {!draft && !schoolReview ? <p>Создавайте шаблон и отдельные учебные недели.</p> : null}
          {scheduleDataSourceLabel ? <span className="admin-data-source">{scheduleDataSourceLabel}</span> : <span className="admin-data-source">{directoryAccess === 'school' ? 'Школьный справочник подключён' : directoryAccess === 'checking' ? 'Подключение…' : 'Локальная песочница'}</span>}{draft ? workspaceTabs : null}
        </div>
        <div className="constructor-actions">
          {!draft ? <details className="constructor-directory-tools"><summary>Справочник</summary>
          <Link href="/admin/people">Школьный справочник</Link>
          <button onClick={() => void load().then(() => setStatus('Справочник обновлён')).catch((error) => setStatus(String(error.message || error)))}><RefreshCw size={16} /> Обновить справочник</button>
          </details> : null}
          {draft ? (
            <>
              <output className={dirty ? 'constructor-unsaved' : ''}>
                {saving ? 'Сохраняется' : dirty ? saveFailed ? 'Ошибка сохранения' : 'Есть несохранённые изменения' : schoolReview ? 'Черновик сохранён' : 'Сохранено'}
              </output>
              <button aria-label="Сохранить черновик" onClick={() => void save()} disabled={!dirty || saving}>
                <Save size={16} />Сохранить
              </button>
              {schoolReview
                ? <button className="constructor-review-trigger" disabled={saving} onClick={() => openReviewCenter('all')}><BookOpenCheck size={16} />Проверить расписание</button>
                : <button onClick={() => setRecalculationOpen(true)} disabled={saving}>Пересчитать аудитории</button>}
            </>
          ) : null}
        </div>
      </header>
      {draft && termComparison?.items.length ? <div className="constructor-term-strip">
        <span>Второй триместр · ждут первичной проверки {pendingTermItems}</span>
        <button type="button" className="admin-button" onClick={openTermComparison}>Сверка с таблицей</button>
      </div> : null}
      {draft && termComparison && comparisonOpen ? <ConstructorTermComparison
        key={draft.id} comparison={termComparison} name={draft.name} revision={draft.revision} suspended={route?.origin === 'comparison'} dirty={dirty || saving} readOnly={readOnlySchoolWorkspace}
        initialKey={comparisonFocus?.draftId === draft.id ? comparisonFocus.key : null}
        lessons={draft.payload.lessons.map(lesson => {
          const classes = visualClasses(lesson, groups).map(id => groups.find(group => group.id === id)?.name).filter((label): label is string => Boolean(label));
          const teacherIds = [...new Set([lesson.teacherId, ...(lesson.teacherIds || [])].filter(Boolean))];
          const room = normalizeRoomDisplay(lesson.room || lesson.place || '');
          return {
            ...lessonTimes(lesson, draft.payload.slots),
            id: lesson.id,
            day: lesson.day,
            subject: lesson.subject,
            room,
            classes,
            teachers: teacherIds.map(id => teachers.find(teacher => teacher.id === id)?.name || 'Требует уточнения'),
            groups: linkedInstructionalGroups(lesson, groups).map(group => group.name),
            kind: isLunch(lesson) ? 'lunch' : editorActivityType(lesson) || 'lesson',
            warning: lesson.needsClarification ? lesson.clarificationReason || 'Требует уточнения' : '',
            label: [lesson.subject, ...classes, room].filter(Boolean).join(' · '),
          };
        })}
        onSelect={key => setComparisonFocus(key ? { draftId: draft.id, key } : null)}
        onClose={() => {
          setComparisonOpen(false);
          if (comparisonFromReview.current) {setReviewFilter(undefined);setReviewMode('review');}
          comparisonFromReview.current = false;
        }}
        onOpenLesson={(id, related = [], title) => {
          const partners = issues.filter(issue => issue.category === 'conflict' && issue.related && (issue.lessonId === id || issue.related === id))
            .flatMap(issue => [issue.lessonId,issue.related!]);
          showIssueLesson(id,'comparison',title || 'Сверка с таблицей',[...related,...partners]);
        }}
        onOpenSlot={openComparisonSlot}
        onPreview={previewTermChoices} onApply={saveTermDecision} onDefer={key => saveTermDecision(key)} /> : null}
      {draft && exportOpen ? <ConstructorScheduleExport
        api={api} id={draft.id} revision={draft.revision} name={draft.name}
        path={schedulePath(`/${encodeURIComponent(draft.id)}/export`)} readOnly={schoolReview && readOnlySchoolWorkspace}
        onClose={() => setExportOpen(false)} /> : null}
      {recalculationOpen && draft && !schoolReview ? <ConstructorAudienceRecalculation
        api={api} apiPrefix="/api/admin/schedule/experiments"
        draftId={draft.id} revision={draft.revision} dirty={dirty} day={day} classId={rosterClassId} classLabel={rosterClassLabel}
        onClose={() => setRecalculationOpen(false)} onUpdated={applyRosterDraft} /> : null}
      <datalist id="constructor-rooms">{allRooms.map(room => <option key={room} value={normalizeRoomInput(room)} />)}</datalist>
      {laboratoriesOpen && draft && <LaboratoryPanel key={draft.id} blueprints={(draft.payload.laboratory_blueprints || []).map(blueprint => {
        const meeting = draft.payload.lessons.find(l => l.id === 'lab-' + blueprint.id && l.activityType === 'laboratory');
        return meeting ? { ...blueprint, ...meeting, id: blueprint.id, classIds: meeting.classIds || blueprint.classIds, audience: meeting.audience || blueprint.audience } : blueprint;
      })} active={draft.payload.laboratory_meetings || {}} week={draft.payload.week_start} days={draft.payload.days} slots={draft.payload.slots} groups={groups} classes={draft.payload.classes} teachers={teachers} students={students} dirty={dirty || saving}
        initialMeeting={labReturn} onLesson={(id, title, meeting) => { setLabReturn(meeting); setLaboratoriesOpen(false); showIssueLesson(id, 'laboratory', title); }}
        onClose={() => { setLabReturn(undefined); setLaboratoriesOpen(false); }} onSave={blueprint => mutate(state => ({ ...state, laboratory_blueprints: [...(state.laboratory_blueprints || []).filter(b => b.id !== blueprint.id), blueprint] }))}
        onAction={async (meeting, deactivate, token) => {
          const response = await api<Draft & LaboratoryPreview>(schedulePath('/' + encodeURIComponent(draft.id) + '/laboratory'), { method: 'POST', body: JSON.stringify({ expected_revision: draft.revision, blueprint_id: meeting.id, meeting, deactivate, token }) });
          if (response.payload) { await open(draft.id); await refreshDrafts(); }
          return response;
        }} />}
      {resolutionIssue && draft ? <ConstructorResolutionPanel api={api} draftId={draft.id} revision={draft.revision} lessons={draft.payload.lessons} slots={draft.payload.slots} issue={resolutionIssue} teacherNames={Object.fromEntries(teachers.map(t=>[t.id,t.name]))} groupNames={Object.fromEntries(groups.map(g=>[g.id,g.name]))} studentNames={Object.fromEntries(students.map(s=>[s.id,s.name]))} dirty={dirty} onClose={()=>{setResolutionIssue(null);if(resolutionFromReview){setReviewFilter(undefined);setReviewMode('review');}setResolutionFromReview(false);}} onNavigate={id=>{showIssueLesson(id,resolutionFromReview?'review':'grid',resolutionIssue.message,[resolutionIssue.lessonId,...(resolutionIssue.related?[resolutionIssue.related]:[])]);setResolutionIssue(null);setResolutionFromReview(false);}} onUpdated={async(value)=>{const updated=value as Draft|undefined;if(updated?.payload){setHistory(items=>[...items.slice(-49),draft.payload]);setFuture([]);revision.current=updated.revision;baseDraftRef.current=updated;setDraft(updated);setDirty(false);changeVersion.current++;}else{await open(draft.id);}await refreshDrafts();}} /> : null}
      {reviewMode && draft && schoolReview ? <ConstructorPublicationPanel
        key={`${draft.id}:${reviewMode}`} api={api} mode={reviewMode} draftId={draft.id} draftName={draft.name}
        revision={draft.revision} weekStart={draft.payload.week_start} dirty={dirty} readOnly={readOnlySchoolWorkspace} publicationEnabled={consolidatedSchedule}
        day={day} classId={rosterClassId} classLabel={rosterClassLabel}
        teacherNames={Object.fromEntries(teachers.map(item => [item.id, item.name]))}
        groupNames={Object.fromEntries(groups.map(item => [item.id, item.name]))}
        lessonLabels={Object.fromEntries(draft.payload.lessons.map(lesson => [lesson.id, `${lesson.subject || 'Занятие'} · ${DAYS[lesson.day]}, ${lesson.start || draft.payload.slots[lesson.period]?.start || 'время не указано'}`]))}
        onClose={() => setReviewMode(null)}
        onSave={save}
        onRostersUpdated={applyRosterDraft}
        onOpenGridDecisions={() => {setReviewMode(null);setConflictsOpen(true);requestAnimationFrame(()=>document.querySelector<HTMLElement>('.constructor-conflict-panel')?.focus());}}
        initialFilter={reviewFilter}
        localIssues={issues}
        onResolveIssue={(ids,code) => {
          const topic = /room|кабинет/i.test(code) ? 'room' : /teacher|преподавател/i.test(code) ? 'teacher' : /audience|roster|student|group|состав/i.test(code) ? 'audience' : null;
          const matches = issues.filter(item => ids.includes(item.lessonId) && (!item.related || ids.includes(item.related)) && (!topic || item.type === topic));
          if (matches.length !== 1) return false;
          setReviewMode(null);setResolutionFromReview(true);setResolutionIssue(matches[0]);return true;
        }}
        lessonDetails={Object.fromEntries(draft.payload.lessons.map(lesson => [lesson.id, {
          ...lessonTimes(lesson,draft.payload.slots),day:lesson.day,subject:lesson.subject,
          classes:displayedClasses(lesson).flatMap(id => groups.find(g=>g.id===id)?.name || []),
          teachers:[...new Set([lesson.teacherId,...(lesson.teacherIds || [])])].flatMap(id=>teachers.find(t=>t.id===id)?.name || []),
          groups:linkedInstructionalGroups(lesson,groups).map(g=>g.name),room:normalizeRoomDisplay(lesson.room || lesson.place || '')
        }]))}
        onOpenComparison={termComparison?.items.length ? () => {comparisonFromReview.current = true;setReviewMode(null);openTermComparison();} : undefined}
        onOpenLesson={(id, context) => showIssueLesson(id,'review',context?.title || 'Проверка расписания',context?.relatedIds)}
        onNormalizedCopy={async copy => {
          if (!await open(copy.id)) setReviewMode(null);
          void refreshDrafts().catch(() => {});
          setStatus(`Создана копия «${copy.name}». Исправлений: ${copy.changed_count}. Сохранено занятий: ${copy.lesson_count}.`);
        }}
        onPublished={async () => { await open(draft.id); void refreshDrafts().catch(() => {}); }} /> : null}
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
          onOpen={async id => { await open(id); }} onCopy={(item) => void duplicate(item as DraftSummary)} onHide={hideSchoolDraft} onRestore={restoreSchoolDraft}
          onCompare={async id => {
            const opened = await open(id, undefined, true);
            if (opened) setComparisonOpen(true);
            return opened;
          }}
          onCreateEmpty={() => void create('empty')} onCreated={async (id) => { setSchoolStartSection('weeks'); await refreshDrafts(); await open(id); }}
          onReview={async id => { if (await open(id)) {setReviewFilter('all');setReviewMode('review');} }} />
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
          <div className="constructor-toolbar constructor-command-bar">
            <div className="constructor-primary-tools" role="group" aria-label="Вид расписания и инструменты">
            <div className="constructor-view-tabs" role="group" aria-label="Режим просмотра">
              <button className={viewMode === 'day' ? 'active' : ''} onClick={() => { setViewMode('day'); setDayModesOpen(false); setDayModeEditor(null); setAnchor(null); setFocus(null); setSelectedId(''); }}>День</button>
              <button className={viewMode === 'week' ? 'active' : ''} onClick={() => { setViewMode('week'); setDayModeEditor(null); setAnchor(null); setFocus(null); setSelectedId(''); }}>Неделя</button>
              <button type="button" aria-expanded={adminTableOpen} onClick={() => setAdminTableOpen(true)}>Таблица недели</button>
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
              {[...new Set(classes.map((item) => canonicalGradeOfGroup(item)).filter(Boolean))].map((grade) => <option key={`parallel:${grade}`} value={`parallel:${grade}`}>{grade} класс · параллель</option>)}
              {groups.filter((item) => item.group_type !== 'class' && !item.archived_at).map((item) => <option key={`group:${item.id}`} value={`group:${item.id}`}>Группа · {item.name}</option>)}
              {teachers.map((item) => <option key={`teacher:${item.id}`} value={`teacher:${item.id}`}>Преподаватель · {item.name}</option>)}
            </select> : null}
            {viewMode === 'week' ? <span className="constructor-week-modes">
              <button type="button" className={dayModesOpen ? 'active' : ''} aria-expanded={dayModesOpen} aria-controls="constructor-day-modes-matrix" onClick={() => { setDayModesOpen((open) => !open); setDayModeEditor(null); }}>
                <BookOpenCheck size={14} aria-hidden="true" /> Режимы дней
              </button>
              {dayModesOpen ? <section id="constructor-day-modes-matrix" className="constructor-day-modes-matrix" role="dialog" aria-label="Режимы дней по параллелям">
                <header><div><strong>Режимы дней</strong><small>Одна настройка для всей параллели</small></div><button type="button" aria-label="Закрыть матрицу режимов" onClick={() => { setDayModesOpen(false); setDayModeEditor(null); }}><X size={15} /></button></header>
                <div className="constructor-day-modes-table-wrap"><table>
                  <thead><tr><th scope="col">Класс</th>{[0, 1, 2, 3, 4].map((weekday) => <th key={weekday} scope="col">{['Пн', 'Вт', 'Ср', 'Чт', 'Пт'][weekday]}</th>)}</tr></thead>
                  <tbody>{['5', '6', '7', '8', '9', '10', '11'].map((grade) => <tr key={grade}>
                    <th scope="row">{grade} класс</th>
                    {[0, 1, 2, 3, 4].map((weekday) => {
                      const mode = dayModeForGrade(payload, grade, classes, weekday);
                      const editing = dayModeEditor?.grade === grade && dayModeEditor.day === weekday;
                      const modeLabel = dayModeLabel(mode);
                      return <td key={`${grade}:${weekday}`}>
                        <span className="constructor-day-mode-control">
                          <button type="button" className={`constructor-day-mode-button ${mode}`} aria-label={`${grade} класс, ${DAYS[weekday]}: ${modeLabel}. Изменить режим дня`} title={`${grade} класс · ${DAYS[weekday]} · ${modeLabel}`} aria-expanded={editing} onClick={(event) => { event.stopPropagation(); setDayModeEditor(editing ? null : { grade, day: weekday }); }}>
                            {dayModeIcon(mode)}
                          </button>
                          {editing ? renderDayModeChoices(grade, weekday, mode) : null}
                        </span>
                      </td>;
                    })}
                  </tr>)}</tbody>
                </table></div>
                <footer className="constructor-day-modes-legend"><span><School size={12} /> Обычный</span><span><Monitor size={12} /> Онлайн</span><span><BookOpenCheck size={12} /> SDEP · 9–11</span></footer>
              </section> : null}
            </span> : null}
            <label className="constructor-toolbar-search"><Search size={15} /><input aria-label="Найти занятие" placeholder="Найти урок…" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
            <button className="constructor-issue-count" onClick={() => {if(schoolReview) openReviewCenter('conflict'); else setConflictsOpen(open => !open);}} aria-expanded={schoolReview ? reviewMode === 'review' : conflictsOpen}>
              <AlertTriangle size={14} /><span className="desktop-label">{schoolReview ? 'Конфликты в сетке' : 'Проверка'} · {conflictLessons.size}</span><span className="mobile-label">{schoolReview ? 'В сетке' : 'Проверка'} · {conflictLessons.size}</span>
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
                <div className="constructor-menu-body" role="group" aria-label="Действия с черновиком">
            {schoolReview ? <>
              {consolidatedSchedule ? <button disabled={saving} onClick={() => setReviewMode('history')}>История публикаций</button> : null}
            </> : null}
            <button
              onClick={() => {
                void (async () => {
                  if (await save()) {
                    if (workspace === 'school') setSchoolStartSection(draft.payload.week_start ? 'weeks' : 'template');
                    setDraft(null);
                  }
                })();
              }}
            >
              {workspace === 'school' ? 'К шаблону и неделям' : 'Все черновики'}
            </button>
            <button type="button" onClick={() => { setLabReturn(undefined); setLaboratoriesOpen(true); }}>Лаборатории</button>
            <button type="button" disabled={dirty || saving} onClick={() => setExportOpen(true)}>Подготовить экспорт в Google Sheets</button>
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
          {adminTableOpen ? <div className="constructor-source-week-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setAdminTableOpen(false); }}>
            <section className="constructor-source-week-dialog" role="dialog" aria-modal="true" aria-labelledby="constructor-source-week-title">
              <header><div><strong id="constructor-source-week-title">Полная таблица исходной недели</strong><small>{payload.lessons.length} записей · полный административный источник</small></div><button type="button" aria-label="Закрыть таблицу недели" onClick={() => setAdminTableOpen(false)}><X size={16} /></button></header>
              {unlinkedElectives.length ? <details className="constructor-source-week-scroll constructor-elective-bulk-map">
                <summary>Сопоставить курсы без общего блока · {unlinkedElectives.length}</summary>
                <p>Выберите направления, которые должны входить в один общий блок. Совпадение названий и времени не выбирает занятия автоматически.</p>
                <div className="constructor-draft-actions">
                  <button type="button" onClick={() => {
                    const digital = unlinkedElectives.filter((lesson) => lesson.electiveDirection === 'digital');
                    setBulkElectiveSelection(digital.map((lesson) => lesson.id));
                    setBulkElectiveBlockName('Цифровой трек');
                    setBulkElectivePreviewOpen(false);
                  }}>Отметить направления с цифровым признаком · {unlinkedElectives.filter((lesson) => lesson.electiveDirection === 'digital').length}</button>
                  <button type="button" onClick={() => { setBulkElectiveSelection([]); setBulkElectivePreviewOpen(false); }}>Снять выбор</button>
                </div>
                <div className="constructor-conflict-list">{unlinkedElectives.map((lesson) => {
                  const selected = bulkElectiveSelection.includes(lesson.id);
                  const classesText = displayedClasses(lesson).map((id) => groups.find((group) => group.id === id)?.name || id).join(', ') || 'Классы не указаны';
                  const teachersText = [...new Set([...(lesson.teacherIds || []), lesson.teacherId].filter(Boolean))].map((id) => teachers.find((teacher) => teacher.id === id)?.name || 'Преподаватель требует проверки').join(', ') || 'Преподаватель не указан';
                  return <label key={lesson.id} data-elective-lesson-id={lesson.id} className="constructor-review-item"><input type="checkbox" aria-label={`Добавить «${lesson.subject || 'курс по выбору'}» в общий блок`} checked={selected} onChange={(event) => {
                    setBulkElectiveSelection((current) => event.target.checked ? [...new Set([...current, lesson.id])] : current.filter((id) => id !== lesson.id));
                    setBulkElectivePreviewOpen(false);
                  }} /><span><strong>{lesson.subject || 'Направление по выбору'}{lesson.electiveDirection === 'digital' ? ' · цифровой трек' : ''}</strong><small>{DAYS[lesson.day] || `День ${lesson.day + 1}`} · {lesson.start || payload.slots[lesson.period]?.start || '—'}–{lesson.end || payload.slots[lesson.period]?.end || '—'} · {classesText} · {teachersText} · {lesson.room || lesson.place || 'кабинет не указан'}</small></span></label>;
                })}</div>
                <label>Название общего блока <input aria-label="Название общего блока для сопоставления" value={bulkElectiveBlockName} onChange={(event) => { setBulkElectiveBlockName(event.target.value); setBulkElectivePreviewOpen(false); }} placeholder="Курс по выбору" /></label>
                <label>Общий блок <select aria-label="Целевой общий блок" value={bulkElectiveTargetId} onChange={(event) => {
                  setBulkElectiveTargetId(event.target.value);
                  const existing = payload.lessons.find((lesson) => lesson.blockId === event.target.value);
                  if (existing) setBulkElectiveBlockName(existing.blockName || 'Курс по выбору');
                  setBulkElectivePreviewOpen(false);
                }}><option value="__new__">Создать новый блок</option>{Array.from(new Map(payload.lessons.filter((lesson) => editorActivityType(lesson) === 'elective' && lesson.blockId).map((lesson) => [lesson.blockId!, lesson])).values()).map((lesson) => <option key={lesson.blockId} value={lesson.blockId}>{lesson.blockName || 'Курс по выбору'} · {DAYS[lesson.day] || ''} {lesson.start || payload.slots[lesson.period]?.start || ''}</option>)}</select></label>
                <button type="button" disabled={!bulkElectiveSelection.length} onClick={() => { setBulkElectiveBlockName((current) => current.trim() || 'Курс по выбору'); setBulkElectivePreviewOpen(true); }}>Предпросмотр · {bulkElectiveSelection.length} направлений</button>
                {bulkElectivePreviewOpen ? <section aria-label="Предпросмотр сопоставления курсов" className="constructor-review-comparison">
                  <strong>Будут сопоставлены {selectedBulkElectives.length} направления</strong>
                  <p>Блок: {bulkElectiveTargetId === '__new__' ? (bulkElectiveBlockName.trim() || 'Курс по выбору') : (payload.lessons.find((lesson) => lesson.blockId === bulkElectiveTargetId)?.blockName || bulkElectiveBlockName || 'Курс по выбору')}</p>
                  <ul>{selectedBulkElectives.map((lesson) => <li key={lesson.id}>{lesson.subject} · {DAYS[lesson.day] || `День ${lesson.day + 1}`} · {lesson.start || payload.slots[lesson.period]?.start || '—'}–{lesson.end || payload.slots[lesson.period]?.end || '—'} · {displayedClasses(lesson).map((id) => groups.find((group) => group.id === id)?.name || id).join(', ') || 'классы не указаны'}</li>)}</ul>
                  <p>Ученики не распределяются по направлениям. Публикация заново проверит аудиторию и конфликты.</p>
                  <button type="button" onClick={() => {
                    const selectedIds = new Set(bulkElectiveSelection);
                    const targetId = bulkElectiveTargetId === '__new__' ? freshId() : bulkElectiveTargetId;
                    const targetName = bulkElectiveTargetId === '__new__'
                      ? (bulkElectiveBlockName.trim() || 'Курс по выбору')
                      : (payload.lessons.find((lesson) => lesson.blockId === targetId)?.blockName || bulkElectiveBlockName.trim() || 'Курс по выбору');
                    mutate((state) => ({ ...state, lessons: state.lessons.map((lesson) => selectedIds.has(lesson.id)
                      ? { ...lesson, activityType: 'elective', blockId: targetId, blockName: targetName }
                      : lesson) }));
                    setBulkElectiveSelection([]);
                    setBulkElectivePreviewOpen(false);
                  }}>Подтвердить сопоставление выбранных направлений</button>
                  <button type="button" onClick={() => setBulkElectivePreviewOpen(false)}>Отмена</button>
                </section> : null}
              </details> : null}
              <div className="constructor-source-week-scroll"><table><thead><tr><th>День и время</th><th>Тип и занятие</th><th>Классы</th><th>Учебные группы</th><th>Преподаватель</th><th>Кабинет / место</th><th>Статус и проекция Campus</th><th></th></tr></thead><tbody>{payload.lessons.map(lesson=>{
                const rowIssues=issues.filter(issue=>issue.lessonId===lesson.id||issue.related===lesson.id);
                const audience=audiencePreviews[lesson.id];
                const blocker = lesson.specialMark === 'lunch' ? '' : lesson.importError ? lesson.clarificationReason || 'Импорт требует проверки' : lesson.needsClarification ? lesson.clarificationReason || 'Требуется подтверждение' : audience && audience.status !== 'ok' ? audience.reason || 'Аудитория не подтверждена' : rowIssues.some(issue=>issue.category==='conflict') ? 'Есть содержательный конфликт; Campus-проекция отложена' : '';
                const status=rowIssues.some(issue=>issue.category==='critical'||issue.category==='conflict')?'Конфликт':rowIssues.some(issue=>issue.category==='review'||issue.category==='import')?'Требует подтверждения':rowIssues.length?'Замечание':lesson.specialMark==='lunch'?'Информационная отметка':'Подтверждено';
                const teacherIds=[...new Set([...(lesson.teacherIds||[]),lesson.teacherId].filter(Boolean))];
                const lessonGroups=linkedInstructionalGroups(lesson,groups);
                const type=lesson.specialMark==='lunch'?'Обед':lesson.activityType==='practice'?'Практикум':lesson.activityType==='consultation'?'Консультация':editorActivityType(lesson)==='club'?'Кружок':editorActivityType(lesson)==='elective'?'Курс по выбору':editorActivityType(lesson)==='event'?'Мероприятие':'Урок';
                return <tr key={lesson.id}><td>{DAYS[lesson.day]||'—'} · {lesson.start||payload.slots[lesson.period]?.start||'—'}–{lesson.end||payload.slots[lesson.period]?.end||'—'}</td><td>{type}: {lesson.subject||'Обед'}</td><td>{displayedClasses(lesson).map(id=>groups.find(group=>group.id===id)?.name||id).join(', ')||'Не указаны'}</td><td>{lessonGroups.map(group=>group.name).join(', ')||'Не указаны'}</td><td>{teacherIds.map(id=>teachers.find(teacher=>teacher.id===id)?.name||'Требует проверки').join(', ')||'Не указан'}</td><td>{lesson.room||lesson.place||'Не указано'}</td><td><strong>{status}</strong>{blocker?<small>Campus: {blocker}</small>:null}</td><td><button type="button" onClick={()=>{openReviewLesson(lesson.id);setAdminTableOpen(false);}}>Открыть</button></td></tr>;
              })}</tbody></table></div>
            </section>
          </div> : null}
          {conflictsOpen || route?.origin === 'grid' ? (
            <section className="constructor-conflict-panel" tabIndex={-1} style={conflictsOpen ? undefined : {display:'none'}}>
              <header><strong>Центр решений расписания</strong><button onClick={() => setConflictsOpen(false)} aria-label="Закрыть список проблем"><X size={15} /></button></header>
              {!dirty&&resolutionRevision===draft?.revision&&Object.keys(resolutionStates).length?<details className="constructor-acknowledged-history"><summary>Подтверждённые решения и отложенные записи</summary>{[...new Map(Object.entries(resolutionStates).flatMap(([lid,items])=>items.filter(r=>r.type!=='room').map(r=>[r.id,{...r,lessonId:lid}] as const))).values()].map(r=><article key={r.id}><strong>{r.type==='joint'?'Совместное занятие':r.type==='independent_practice'?'Самостоятельная практика':r.type==='defer'?'Отложенное решение':'Подтверждение участников'}</strong><p>{r.reason}</p><button onClick={()=>setResolutionIssue({lessonId:r.lessonId,related:(r as {lesson_ids?:string[]}).lesson_ids?.find(id=>id!==r.lessonId),type:r.type==='joint'||r.type==='independent_practice'?'teacher':'audience',category:'review',severity:'warning',message:r.reason})}>Рассмотреть или отменить</button></article>)}</details>:null}
              <div className="constructor-conflict-filters">
                <select aria-label="День проблемы" value={issueDay} onChange={event=>setIssueDay(event.target.value)}><option value="">Все дни</option>{payload.days.map(d=><option key={d} value={d}>{DAYS[d]}</option>)}</select>
                <select aria-label="Класс проблемы" value={issueClass} onChange={event=>setIssueClass(event.target.value)}><option value="">Все классы</option>{classes.map(g=><option key={g.id} value={g.id}>{g.name}</option>)}</select>
                <select aria-label="Важность проблемы" value={issueSeverity} onChange={(event) => setIssueSeverity(event.target.value)}><option value="all">Все уровни</option><option value="error">Ошибки</option><option value="warning">Предупреждения</option></select>
                <select aria-label="Тип проблемы" value={issueType} onChange={(event) => setIssueType(event.target.value)}><option value="all">Все типы</option><option value="teacher">Преподаватель</option><option value="room">Кабинет</option><option value="student">Группа или ученик</option><option value="data">Данные занятия</option></select>
                <button type="button" onClick={()=>void copyIssueReport()}>Скопировать отчёт проверки</button>
              </div>
              {!filteredIssues.length ? <p role="status">По выбранным фильтрам проблем нет.</p> : null}
              {(['critical', 'decision', 'quick', 'deferred', 'acknowledged'] as const).map((category) => {
                const categoryIssues = filteredIssues.filter((item) => workflowFor(item) === category);
                if (!categoryIssues.length) return null;
                const title = category === 'quick' ? 'Исправляемые быстро' : category === 'decision' ? 'Требуют решения администратора' : category === 'acknowledged' ? 'Подтверждённые исключения' : category === 'deferred' ? 'Отложенные решения' : 'Критические технические проблемы';
                const affected = new Set(categoryIssues.map((item) => item.lessonId));
                const pairs = new Set(categoryIssues.filter((item) => item.related).map((item) => [item.lessonId, item.related!].sort().join(':')));
                const count = category === 'decision' ? `${russianCount(affected.size, 'занятие', 'занятия', 'занятий')} · ${russianCount(pairs.size, 'парное совпадение', 'парных совпадения', 'парных совпадений')}` : russianCount(affected.size, 'занятие', 'занятия', 'занятий');
                const reasonGroups = new Map<string, Issue[]>();
                for (const item of categoryIssues) reasonGroups.set(`${item.type}:${item.message}`, [...(reasonGroups.get(`${item.type}:${item.message}`) || []), item]);
                return <section className="constructor-issue-category" key={category}><h3>{title} · {count}</h3>{[...reasonGroups].map(([reason, examples])=><details key={reason} className="constructor-issue-reason"><summary>{reason.slice(reason.indexOf(':')+1)} · {examples.length}</summary><div className="constructor-conflict-list">{examples.map((item, index) => {
                  const lesson = payload.lessons.find((entry) => entry.id === item.lessonId);
                  const targetGroup = groups.find((entry) => entry.id === lesson?.groupId);
                  const dayName = lesson ? DAYS[lesson.day] || 'День не определён' : 'Занятие не найдено';
                  const startTime = lesson ? lessonTimes(lesson,payload.slots).start || 'Время не определено' : '';
                  return <article key={`${item.lessonId}:${index}`} className={item.severity}><div><button className="constructor-issue-open" onClick={()=>setResolutionIssue(item)}>Разобрать проблему</button><strong>{item.type === 'teacher' ? 'Преподаватель' : item.type === 'room' ? 'Кабинет' : item.type === 'audience' ? 'Группа или ученик' : 'Данные занятия'} · {item.severity === 'error' ? 'Ошибка' : 'Предупреждение'}</strong><small>{dayName}, {startTime} · {lesson?.subject || 'Занятие'} · {lesson ? lessonClasses(lesson).map((id) => groups.find((entry) => entry.id === id)?.name || 'класс не уточнён').join(', ') : ''} · {targetGroup?.name || 'аудитория не выбрана'}</small>{item.related ? <small>Связано с: {payload.lessons.find((entry) => entry.id === item.related)?.subject || 'занятием'}</small> : null}</div><button data-issue-open={item.lessonId} onClick={() => showIssueLesson(item.lessonId,'grid',item.message,item.related?[item.related]:[])}>Показать в расписании</button></article>;
                })}</div></details>)}</section>;
              })}
              <details className="constructor-source-week"><summary>Полная таблица исходной недели · {payload.lessons.length} занятий</summary><div className="constructor-source-week-scroll"><table><thead><tr><th>День и время</th><th>Занятие</th><th>Классы</th><th>Учебные группы</th><th>Преподаватель</th><th>Кабинет / место</th><th>Статус</th><th></th></tr></thead><tbody>{payload.lessons.map(lesson=>{const rowIssues=issues.filter(issue=>issue.lessonId===lesson.id||issue.related===lesson.id);const status=rowIssues.some(issue=>issue.category==='critical'||issue.category==='conflict')?'Конфликт':rowIssues.some(issue=>issue.category==='review'||issue.category==='import')?'Требует подтверждения':rowIssues.length?'Замечание':lesson.specialMark==='lunch'?'Информационная отметка':'Подтверждено';const teacherIds=[...new Set([...(lesson.teacherIds||[]),lesson.teacherId].filter(Boolean))];const lessonGroups=linkedInstructionalGroups(lesson,groups);return <tr key={lesson.id}><td>{DAYS[lesson.day]||'—'} · {payload.slots[lesson.period]?.start||lesson.start||'—'}</td><td>{lesson.subject||'Обед'}{editorActivityType(lesson)==='elective'?' · курс по выбору':''}</td><td>{displayedClasses(lesson).map(id=>groups.find(group=>group.id===id)?.name||id).join(', ')||'Не указаны'}</td><td>{lessonGroups.map(group=>group.name).join(', ')||'Не указаны'}</td><td>{teacherIds.map(id=>teachers.find(teacher=>teacher.id===id)?.name||'Преподаватель требует проверки').join(', ')||'Не указан'}</td><td>{lesson.room||lesson.place||'Не указано'}</td><td>{status}</td><td><button type="button" onClick={()=>{openReviewLesson(lesson.id);setConflictsOpen(false);}}>Открыть</button></td></tr>})}</tbody></table></div></details>
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
          {route ? <section className="constructor-issue-route" data-issue-route aria-label="Выбранная проблема">
            <div><strong>{route.title}</strong><small>{route.slot ? [DAYS[route.slot.day],route.slot.start,route.slot.classes.join(', ')].join(' · ') : routedLesson ? [DAYS[routedLesson.day], lessonTimes(routedLesson,payload.slots).start,routedLesson.subject,normalizeRoomDisplay(routedLesson.room)].filter(Boolean).join(' · ') : 'Занятие отсутствует в этой редакции'}</small></div>
            {(route.ids.length > 1 || (route.active && route.active !== selectedId)) ? <div className="constructor-route-lessons" role="group" aria-label="Связанные занятия">{route.ids.map(id => {
              const lesson = payload.lessons.find(item => item.id === id);
              const duplicateSubject = lesson && payload.lessons.some(other => other.id !== id && route.ids.includes(other.id) && other.subject === lesson.subject);
              const label = lesson && [lesson.subject || 'Занятие', ...(duplicateSubject ? [displayedClasses(lesson).flatMap(classId => groups.find(g => g.id === classId)?.name || []).join(', '), normalizeRoomDisplay(lesson.room)] : [])].filter(Boolean).join(' · ');
              return lesson ? <button key={id} aria-pressed={route.active===id} onClick={()=>{openReviewLesson(id,true);setIssueRoute({...route,active:id});}}>{label}</button> : null;
            })}</div> : null}
            {!panelOpen && (selected || canCreateAtSlot) ? <button onClick={()=>{if (route.slot && !selected) {
                const kind = route.slot.kind;
                setCreationMode(kind === 'lunch' || kind === 'event' || kind === 'no_lesson' ? kind : 'form');
              }
              setPanelOpen(true);}}>{selected?'Редактировать занятие':'Создать занятие в слоте'}</button> : null}
            {route.mayCreate && customSlotTime ? <small>Показан пересекающийся урок. Для создания занятия уточните время по звонкам.</small> : null}
            {route.slot && !selectedCell ? <small>Для этого времени или класса в сетке нет подходящей ячейки. Уточните размещение в сверке.</small> : null}
            <button onClick={backToIssues}>К проверке</button>
          </section> : null}
          <div className={`constructor-layout ${panelOpen ? 'panel-open' : 'panel-closed'}`}>
            <div ref={gridWrap} className={`constructor-grid-wrap ${density}`} onScroll={(event) => {
              if (!restoringGridScroll.current) gridScroll.current = { left: event.currentTarget.scrollLeft, top: event.currentTarget.scrollTop };
            }}>
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
                      {[...new Set(item.classIds.map((classId) => canonicalGradeOfGroup(classes.find((entry) => entry.id === classId))).filter(Boolean))].map((grade) => {
                        const mode = dayModeForGrade(payload, grade, classes, day);
                        const editing = dayModeEditor?.grade === grade && dayModeEditor.day === day;
                        return <span className="constructor-day-mode-control" key={grade}>
                          <button type="button" className={`constructor-day-mode-button ${mode}`} aria-label={`${grade} класс, ${DAYS[day]}: ${dayModeLabel(mode)}. Изменить режим дня`} title={`${grade} класс · ${DAYS[day]} · ${dayModeLabel(mode)}`} aria-expanded={editing} onClick={(event) => { event.stopPropagation(); setDayModeEditor(editing ? null : { grade, day }); }}>
                            {dayModeIcon(mode)}
                          </button>
                          {editing ? renderDayModeChoices(grade, day, mode) : null}
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
                          (!subjectFilter || subjectLabel(item.subject) === subjectFilter) &&
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
                              scheduleQuickFocus();
                            }
                          }}
                          aria-label={`${column.label}, ${DAYS[cellDay]}, ${payload.slots[period].start}`}
                          data-cell={keyOf({ row, col })}
                          data-issue-slot={routeSlotCell?.row === row && routeSlotCell.col === col ? true : undefined}
                          className={`constructor-cell ${inRange(row, col) ? 'selected' : ''}`}
                          onClick={(event) => {
                            if (
                              !(event.target as Element).closest(
                                '.constructor-card',
                              )
                              )
                              selectCell(row, col, event.shiftKey, !event.shiftKey);
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
                              const roomCaption = lessonOnline(payload, item, groups) ? 'Онлайн' : normalizeRoomDisplay(item.room);
                              const localGroups = linkedInstructionalGroupsForColumn(item, groups, students, column.classIds);
                              const englishCaption = item.kind === 'event' ? null : englishLessonCaption(item, groups, localGroups);
                              const isAvailableSlot = audienceOf(item, groups).kind === 'available_slot';
                              const classCaption = visualClasses(item, groups).filter((id) => column.classIds.includes(id)).map((id) => groups.find((g) => g.id === id)?.name || id).join(', ');
                              return (
                                <button
                                  id={`lesson-${item.id}-${row}-${col}`}
                                  aria-current={route?.active === item.id ? 'location' : undefined}
                                  data-issue-target={route?.active === item.id ? 'current' : route?.ids.includes(item.id) ? 'related' : undefined}
                                  key={item.id}
                                  data-lesson-id={item.id}
                                  title={`${englishCaption?.full || item.subject}\n${isAvailableSlot ? 'Все остальные' : linkedInstructionalGroups(item, groups).map((group) => audienceLabel(group)).join(', ') || classCaption || 'Группа не определена'}\n${isAvailableSlot ? 'Все остальные' : classCaption || 'Класс не определён'}\n${teachers.find((t) => t.id === item.teacherId)?.name || 'Без учителя'} · ${roomCaption || 'Без кабинета'}${item.kind === 'event' ? `\n${item.start || ''}–${item.end || ''} · ${item.place || ''}\n${item.description || ''}` : ''}`}
                                  style={{ backgroundColor: colorMode === 'none' ? undefined : colorMode === 'teacher' ? (item.teacherId ? teacherRegistry.color(item.teacherId) : '#f1f3f8') : stableColor(subjectLabel(item.subject) || 'none'), '--teacher-accent': colorMode === 'teacher' && item.teacherId ? teacherRegistry.accent(item.teacherId) : '#8290aa', '--teacher-text': colorMode === 'teacher' && item.teacherId ? readableTeacherText(teacherRegistry.color(item.teacherId)) : '#202339' } as React.CSSProperties}
                                  className={`constructor-card ${item.kind === 'event' ? 'event' : ''} ${item.kind === 'event' || item.end && item.end !== payload.slots[item.period]?.end ? `multislot ${item.period !== period ? 'event-continuation' : ''} ${overlapsSlot(item, cellDay, period + 1, payload.slots) ? 'event-continues' : ''}` : ''} ${selectedId === item.id ? 'active' : ''} ${conflictsOpen && issues.some((issue) => (issue.lessonId === item.id || issue.related === item.id) && issue.severity === 'error') ? 'error' : conflictsOpen && issues.some((issue) => issue.lessonId === item.id || issue.related === item.id) ? 'warning' : ''}`}
                                  draggable
                                  onDragStart={(event) => {
                                    event.dataTransfer.setData(
                                      'text/plain',
                                      item.id,
                                    );
                                    event.dataTransfer.effectAllowed =
                                      'copyMove';
                                    dragging.current = { id: item.id, sourceCol: col };
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
                                      {item.kind === 'event' ? <>◆ {item.subject}{item.period !== period ? ' ↳' : ''}</> : <><span className="constructor-subject-short">{item.period !== period ? '↳ ' : ''}{englishCaption?.short || shortSubject(subjectLabel(item.subject) || 'Новый урок')}</span><span className="constructor-subject-full">{item.period !== period ? '↳ ' : ''}{englishCaption?.full || subjectLabel(item.subject) || 'Новый урок'}</span></>}
                                    </strong>
                                    {item.activityType === 'practice' || item.activityType === 'consultation' || item.activityType === 'laboratory' ? <small>{item.activityType === 'practice' ? 'Практикум' : item.activityType === 'consultation' ? 'Консультация' : 'Лаборатория'}</small> : null}
                                    {item.activityType !== 'club' ? <small>{lessonClasses(item).length > 1 ? <span title="Совместное занятие">↔ </span> : null}{isAvailableSlot ? 'Все остальные' : localGroups.length ? localGroups.map((group) => audienceShortLabel(group)).join(', ') : classCaption || 'Участники требуют уточнения'}</small> : null}
                                    {item.activityType !== 'club' || item.teacherId || item.room || item.place || item.teacherIds?.length ? <small className="constructor-teacher-line">{shortTeacher(teachers.find((t) => t.id === item.teacherId)?.name || (item.activityType === 'club' ? '' : '—'))}{roomCaption ? ` · ${roomCaption}` : item.place ? ` · ${item.place}` : ''}{[...new Set(item.teacherIds || [])].filter(id => id !== item.teacherId).map(id => <i key={id} className="constructor-co-teacher" style={{background:teacherRegistry.accent(id)}} title={teachers.find(t=>t.id===id)?.name || 'Преподаватель не найден'}>{shortTeacher(teachers.find(t=>t.id===id)?.name || '—')}</i>)}</small> : null}
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
                                  if (event.key === 'Escape') {
                                    setQuick('');
                                    setQuickChoice({});
                                    const cell = event.currentTarget.closest<HTMLElement>('[data-cell]');
                                    scheduleQuickFocus(cell);
                                  }
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
                                  scheduleQuickFocus();
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
                <div className="constructor-inspector-head-actions">{route ? <button onClick={backToIssues}>К проверке</button> : null}<button onClick={() => setPanelOpen(false)} aria-label="Закрыть панель"><X size={17} /></button></div>
              </div>
              {selected && focus ? (
                <button
                  className="constructor-slot-choice"
                  onClick={() => setSelectedId('')}
                >
                  Выделить весь слот
                </button>
              ) : null}
              {focus || selected ? (
                <>
                  {focus && !selected && creationMode === 'quick' && (gridColumns[focus.col]?.classIds.length || 0) > 1 ? (
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
                      {creationMode === 'form' && ['lesson', 'practice', 'consultation'].includes(activityType) && (
                        <label>Вид занятия<select aria-label="Вид занятия" value={activityType} onChange={event => {
                          const kind = event.target.value as 'lesson' | 'practice' | 'consultation';
                          setActivityType(kind);
                        }}>
                          <option value="lesson">Урок</option>
                          <option value="practice">Практикум</option>
                          <option value="consultation">Консультация</option>
                        </select></label>
                      )}
                      {activityType === 'lesson' && creationMode !== 'lunch' ? <div className="constructor-creation-tabs" role="group" aria-label="Способ редактирования урока">
                        <button className={creationMode === 'quick' ? 'active' : ''} onClick={() => setCreationMode('quick')}>Быстрый ввод</button>
                        <button className={creationMode === 'form' ? 'active' : ''} onClick={() => { setNewLesson({ subject: quickSubject, groupId: quickGroupId, teacherId: resolvedQuickTeacherId, room: quickRoom, classIds: quickClassIds, audience: audienceFromGroup(quickGroupId, quickClassIds, groups) }); setNewTeacherManuallySelected(Boolean(quickChoice.teacherId)); setTeacherQuery(''); setCreationMode('form'); }}>Форма</button>
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
                          <button onClick={() => { setNewLesson({ subject: quickSubject, groupId: quickGroupId, teacherId: resolvedQuickTeacherId, room: quickRoom, classIds: quickClassIds, audience: audienceFromGroup(quickGroupId, quickClassIds, groups) }); setNewTeacherManuallySelected(Boolean(quickChoice.teacherId)); setTeacherQuery(''); setCreationMode('form'); }}>Открыть подробную форму</button>
                        </div> : null}
                      </> : <div className="constructor-create-form">
                        {creationMode !== 'lunch' && creationMode !== 'no_lesson' ? <><label>{activityType === 'event' ? 'Название события' : ['lesson', 'practice', 'consultation'].includes(activityType) ? 'Предмет' : activityType === 'club' ? 'Кружок' : 'Курс по выбору'} <input aria-label="Название активности" placeholder="Выберите или введите название" list="constructor-new-subjects" value={newLesson.subject} onFocus={(event) => { try { event.currentTarget.showPicker(); } catch { /* Native datalist may open on typing instead. */ } }} onChange={(event) => setNewLesson((item) => ({ ...item, subject: event.target.value }))} /></label><datalist id="constructor-new-subjects">{[...subjects, ...activities.filter((item) => item.status === 'active').map((item) => item.name)].filter((item, index, all) => all.indexOf(item) === index).map((item) => <option key={item} value={item} />)}</datalist></> : <p className="constructor-special-mark">{creationMode === 'lunch' ? 'Обед' : 'Нет урока'}</p>}
                        {(activityType === 'club' || activityType === 'elective') ? <>
                          <label>Запись каталога <select value={newLesson.activityCatalogId || ''} onChange={(event) => { const item = activities.find((entry) => entry.id === event.target.value); setNewLesson((current) => ({ ...current, activityCatalogId: item?.id || '', activityNameSnapshot: item?.name || '', subject: item?.name || current.subject })); }}><option value="">Без привязки к каталогу</option>{activities.filter((item) => item.status === 'active' && item.type === activityType).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
                          <details><summary>Управление каталогом · {activities.length}</summary><div className="constructor-related-actions"><button onClick={() => { const item = activities.find((entry) => entry.id === newLesson.activityCatalogId); editActivity(item, activityType as 'club' | 'elective'); }}>Создать / изменить выбранную запись</button>{activities.filter((item) => item.status === 'active').map((item) => <button key={item.id} onClick={() => void archiveActivity(item)}>В архив: {item.name}</button>)}{activities.filter((item) => item.status === 'archived').map((item) => <button key={item.id} onClick={() => void archiveActivity(item)}>Восстановить: {item.name}</button>)}</div></details>
                          {activityType === 'elective' ? <ElectiveClassFields groups={groups.filter((group) => group.group_type === 'class' && payload.classes.includes(group.id))} classIds={newLesson.classIds} eligibleGrades={newLesson.eligibleGrades || []} onClassIdsChange={(classIds) => setNewLesson((current) => ({ ...current, classIds }))} onEligibleGradesChange={(eligibleGrades) => setNewLesson((current) => ({ ...current, eligibleGrades }))} /> : null}
                          {activityType === 'elective' ? <><label>Направление <select value={newLesson.electiveDirection || ''} onChange={(event) => setNewLesson((current) => ({ ...current, electiveDirection: event.target.value === 'digital' ? 'digital' : undefined }))}><option value="">Не указано</option><option value="digital">Цифровое</option></select></label></> : null}
                        </> : null}
                        {creationMode === 'event' ? <p>{payload.week_start ? 'Событие относится только к этой неделе.' : 'Событие повторяется в неделях, созданных из этого шаблона.'}</p> : null}
                        {creationMode === 'event' ? <label className="constructor-informational"><input type="checkbox" checked={Boolean(newLesson.informational)} onChange={(event) => setNewLesson((item) => ({ ...item, informational: event.target.checked }))} /> Информационное событие без назначенных учеников</label> : null}
                        {creationMode !== 'lunch' && creationMode !== 'no_lesson' ? <><label>{activityType === 'practice' ? 'Присутствующий преподаватель (необязательно)' : 'Преподаватель'} <input aria-label="Преподаватель нового занятия" placeholder="Начните вводить имя или фамилию" list="constructor-new-teachers" value={teacherQuery || teachers.find((item) => item.id === resolvedNewTeacherId)?.name || ''} onFocus={(event) => { try { event.currentTarget.showPicker(); } catch { /* Native datalist may open on typing instead. */ } }} onChange={(event) => { const value = event.target.value; const match = teachers.find((item) => item.name.toLocaleLowerCase('ru') === value.trim().toLocaleLowerCase('ru')); setNewTeacherManuallySelected(true); setTeacherQuery(match ? '' : value); setNewLesson((item) => ({ ...item, teacherId: match?.id || '' })); }} /><datalist id="constructor-new-teachers">{rankedTeachers.map((item) => <option key={item.id} value={item.name} label={busyTeachers.has(item.id) ? `${item.name} · занят: ${teacherBusyWith(item.id)?.subject || 'занятие'}` : `${item.name} · свободен`} />)}</datalist></label><label>Кабинет <input aria-label="Кабинет нового занятия" list="constructor-rooms" disabled={newOnline} value={newOnline ? '' : normalizeRoomInput(newLesson.room)} onChange={(event) => setNewLesson((item) => ({ ...item, room: event.target.value }))} /></label></> : null}
                        {creationMode === 'lunch' ? <label>Место (необязательно)<input aria-label="Место обеда" value={newLesson.room} onChange={(event) => setNewLesson((item) => ({ ...item, room: event.target.value }))} /></label> : null}
                        {activityType === 'club' ? <><label>Размещение в сетке<select multiple aria-label="Классы размещения кружка" value={newAudienceContextClassIds} onChange={(event) => setNewLesson((item) => ({ ...item, classIds: [...event.target.selectedOptions].map((option) => option.value) }))}>{groups.filter((group) => group.group_type === 'class' && payload.classes.includes(group.id)).map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}</select></label><p>Поимённый список не нужен. Ученики этих классов увидят кружок в отдельном разделе расписания.</p></> : null}
                        {activityType === 'elective' ? <p>Ученики и учебные группы отдельно не назначаются. Параллельные курсы доступны выбранным классам независимо друг от друга.</p> : !newLesson.informational && activityType !== 'club' ? <ParticipantsPicker groups={groups} students={students} audience={newLesson.audience || audienceFromGroup(newLesson.groupId, newLesson.classIds, groups)} subject={newLesson.subject} contextClassIds={newAudienceContextClassIds} gradeScope={selectedCell?.gradeScope} preview={newAudiencePreview} onRetryPreview={() => { setAudienceSnapshot(null); setNewAudienceSnapshot(null); setPreviewRetryNonce((value) => value + 1); }} onOpenBlockingLesson={(id) => { const lesson = payload.lessons.find((item) => item.id === id); if (lesson) openReviewLesson(lesson.id); }} onChange={(audience) => { const mirror = audiencePatch(audience, groups, students, newAudienceContextClassIds); setNewLesson((item) => ({ ...item, audience, groupId: mirror.groupId || '', classIds: mirror.classIds || [] })); }} /> : null}
                        {creationMode === 'event' ? <><div className="constructor-field-row"><label>День <select aria-label="День нового занятия" value={newLesson.day ?? selectedCell?.day ?? day} onChange={(event) => setNewLesson((item) => ({ ...item, day: Number(event.target.value) }))}>{payload.days.map((value) => <option key={value} value={value}>{DAYS[value]}</option>)}</select></label><label>Начало <input type="time" disabled={Boolean(newLesson.allDay)} value={newLesson.allDay ? '00:00' : newLesson.start || ''} onChange={(event) => setNewLesson((item) => ({ ...item, start: event.target.value }))} /></label></div><label className="constructor-informational"><input type="checkbox" checked={Boolean(newLesson.allDay)} onChange={(event) => setNewLesson((item) => ({ ...item, allDay: event.target.checked, start: event.target.checked ? '00:00' : payload.slots[selectedCell?.period ?? 0]?.start || '09:00', end: event.target.checked ? '23:59' : payload.slots[selectedCell?.period ?? 0]?.end || '09:45' }))} /> На весь день</label></> : null}
                        {creationMode === 'form' ? <>{newDayOnline ? <p>Онлайн по режиму дня класса</p> : <label><input type="checkbox" checked={newOnline} onChange={event => setNewLesson(item => ({ ...item, format: event.target.checked ? 'online' : '', room: '' }))} /> Онлайн</label>}{newOnline && <details><summary>Подключение</summary><label>Ссылка или сведения для подключения<input value={newLesson.connectionInfo || ''} onChange={event => setNewLesson(item => ({ ...item, connectionInfo: event.target.value }))} /></label></details>}</> : null}
                        {creationMode === 'form' && newLesson.blockId ? <label>Название параллельного блока <input value={newLesson.blockName || ''} onChange={(event) => setNewLesson((item) => ({ ...item, blockName: event.target.value }))} placeholder="Например, курсы по выбору 5–6" /></label> : null}
                        {creationMode === 'event' ? <>
                          <label>Конец <input type="time" disabled={Boolean(newLesson.allDay)} value={newLesson.allDay ? '23:59' : newLesson.end || ''} onChange={(event) => setNewLesson((item) => ({ ...item, end: event.target.value }))} /></label>
                          {newLesson.start && newLesson.end && minutes(newLesson.end) > minutes(newLesson.start) ? <p className="constructor-affected-slots">Затронуты уроки: {affectedSlots(newLesson.start, newLesson.end, payload.slots).join(', ') || 'нет пересечений с сеткой'}</p> : null}
                          <label>Сопровождающие <select multiple aria-label="Сопровождающие нового события" value={newLesson.teacherIds || []} onChange={(event) => setNewLesson((item) => ({ ...item, teacherIds: [...event.target.selectedOptions].map((option) => option.value) }))}>{rankedTeachers.map((item) => <option key={item.id} value={item.id}>{item.name}{busyTeachers.has(item.id) ? ' · занят' : ''}</option>)}</select></label>
                          <label>Место <input value={newLesson.place || ''} onChange={(event) => setNewLesson((item) => ({ ...item, place: event.target.value }))} /></label>
                          <label>Формат <select value={newLesson.format || ''} onChange={(event) => setNewLesson((item) => ({ ...item, format: event.target.value }))}><option value="">Не указан</option><option value="offline">Очно</option><option value="online">Онлайн</option><option value="offsite">Вне школы</option></select></label>
                          <label>Описание <textarea value={newLesson.description || ''} onChange={(event) => setNewLesson((item) => ({ ...item, description: event.target.value }))} /></label>
                        </> : null}
                        <button onClick={() => addStructured({ ...newLesson, subject: creationMode === 'lunch' ? 'Обед' : creationMode === 'no_lesson' ? 'Нет урока' : newLesson.subject, activityType, kind: activityType === 'event' || creationMode === 'event' ? 'event' : 'lesson', specialMark: creationMode === 'lunch' ? 'lunch' : creationMode === 'no_lesson' ? 'no_lesson' : undefined, teacherId: creationMode === 'lunch' || creationMode === 'no_lesson' ? '' : resolvedNewTeacherId, room: newDayOnline && creationMode === 'form' && !isVirtualRoom(newLesson.room) ? '' : newLesson.room, start: activityType === 'event' || creationMode === 'event' ? newLesson.start : undefined, end: activityType === 'event' || creationMode === 'event' ? newLesson.end : undefined })}>Добавить {activityType === 'event' || creationMode === 'event' ? 'событие' : activityType === 'club' ? 'кружок' : activityType === 'elective' ? 'курс по выбору' : creationMode === 'lunch' ? 'обед' : creationMode === 'no_lesson' ? 'пометку «Нет урока»' : 'занятие'}</button>
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
                      <div className="constructor-related-actions"><button disabled={!focus} onClick={() => { const isElective = selectedActivityType === 'elective'; const blockId = selected.blockId || freshId(); const blockName = selected.blockName || (isElective ? 'Курс по выбору' : 'Параллельные занятия'); if (!selected.blockId) updateLesson(selected.id, { blockId, blockName }); setSelectedId(''); setActivityType(isElective ? 'elective' : 'lesson'); setNewLesson({ subject: '', groupId: '', teacherId: '', room: '', classIds: lessonClasses(selected), day: selected.day, period: selected.period, blockId, blockName, ...(isElective ? { eligibleGrades: selected.eligibleGrades || [], audience: { kind: 'students', student_ids: [] }, confirmedParticipantIds: [] } : {}) }); setNewTeacherManuallySelected(false); setCreationMode('form'); }}>+ Параллельное занятие</button>{selected.blockId ? <span>{selected.blockName || 'Параллельные занятия'} · {payload.lessons.filter((item) => item.blockId === selected.blockId).length}</span> : null}</div>
                      {selected.blockId && selectedActivityType !== 'elective' ? <label>Название блока <input value={selected.blockName || ''} onChange={(event) => mutate((state) => ({ ...state, lessons: state.lessons.map((item) => item.blockId === selected.blockId ? { ...item, blockName: event.target.value } : item) }))} /></label> : null}
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
                      <label>Тип активности <select value={!selected.activityType && selectedActivityType === 'elective' ? 'legacy_elective' : selectedActivityType || 'lesson'} onChange={(event) => { const type = event.target.value as Lesson['activityType']; const explicitOnly = type === 'club'; const participants = selected.confirmedParticipantIds || []; updateLesson(selected.id, { activityType: type, kind: type === 'event' ? 'event' : 'lesson', ...(explicitOnly ? { audience: { kind: 'students', student_ids: type === 'club' ? [] : participants }, groupId: '', groupIds: [], ...(type === 'club' ? { confirmedParticipantIds: undefined, displayClassIds: visualClasses(selected, groups), classIds: [], classId: '' } : {}) } : {}) }); }}>{!selected.activityType && selectedActivityType === 'elective' ? <option value="legacy_elective" disabled>Курс по выбору (старая запись)</option> : null}<option value="lesson">Урок</option>{selectedActivityType === 'laboratory' && <option value="laboratory">Лаборатория</option>}<option value="practice">Практикум</option><option value="consultation">Консультация</option><option value="club">Кружок</option><option value="elective">Курс по выбору</option><option value="event">Событие</option></select></label>
                      </>}
                      {(selectedActivityType === 'club' || selectedActivityType === 'elective') ? <>
                      <label>Каталог <select value={selected.activityCatalogId || ''} onChange={(event) => { const item = activities.find((entry) => entry.id === event.target.value); updateLesson(selected.id, { activityCatalogId: item?.id || '', activityNameSnapshot: item?.name || undefined, subject: item?.name || selected.subject }); }}><option value="">Без привязки</option>{activities.filter((item) => (item.status === 'active' || item.id === selected.activityCatalogId) && item.type === selectedActivityType).map((item) => <option key={item.id} value={item.id} disabled={item.status === 'archived'}>{item.name}{item.status === 'archived' ? ' · архив' : ''}</option>)}</select></label>
                        {selectedActivityType === 'elective' ? <ElectiveClassFields groups={groups.filter((group) => group.group_type === 'class' && payload.classes.includes(group.id))} classIds={lessonClasses(selected)} eligibleGrades={selected.eligibleGrades || []} onClassIdsChange={(classIds) => updateLesson(selected.id, { classIds, classId: classIds[0] || '' })} onEligibleGradesChange={(eligibleGrades) => updateLesson(selected.id, { eligibleGrades })} /> : null}
                      {selectedActivityType === 'elective' ? <>
                        <label>Общий блок курсов по выбору <select aria-label="Общий блок курсов по выбору" value={selected.blockId || ''} onChange={(event) => {
                          const target = event.target.value;
                          if (target === '__create__') {
                            updateLesson(selected.id, { blockId: freshId(), blockName: selected.blockName || 'Курс по выбору' });
                          } else if (!target) {
                            updateLesson(selected.id, { blockId: undefined, blockName: undefined });
                          } else {
                            const source = payload.lessons.find((item) => item.blockId === target && item.id !== selected.id);
                            updateLesson(selected.id, { blockId: target, blockName: source?.blockName || 'Курс по выбору' });
                          }
                        }}><option value="">Не сопоставлен</option><option value="__create__">Создать новый общий блок</option>{Array.from(new Map(payload.lessons.filter((item) => editorActivityType(item) === 'elective' && item.blockId).map((item) => [item.blockId!, item])).values()).map((item) => <option key={item.blockId} value={item.blockId}>{item.blockName || 'Курс по выбору'} · {item.day + 1}, {item.start || payload.slots[item.period]?.start || ''}</option>)}</select></label>
                        {selected.blockId ? <label>Название общего блока <input aria-label="Название общего блока" value={selected.blockName || ''} onChange={(event) => mutate((state) => ({ ...state, lessons: state.lessons.map((item) => item.blockId === selected.blockId ? { ...item, ...(editorActivityType(item) === 'elective' ? { activityType: 'elective' as const } : {}), blockName: event.target.value } : item) }))} placeholder="Курс по выбору" /></label> : <small>Укажите общий блок, чтобы связанные параллельные направления показывались ученикам одной карточкой.</small>}
                        <label>Направление <select value={selected.electiveDirection || ''} onChange={(event) => updateLesson(selected.id, { electiveDirection: event.target.value === 'digital' ? 'digital' : undefined })}><option value="">Не указано</option><option value="digital">Цифровое</option></select></label>
                      </> : null}
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
                          disabled={selectedOnline}
                          value={selectedOnline ? '' : normalizeRoomInput(selected.room)}
                          onChange={(event) =>
                            updateLesson(selected.id, {
                              room: normalizeRoomKey(event.target.value).startsWith('room:') ? normalizeRoomDisplay(event.target.value) : event.target.value,
                            })
                          }
                        />
                      </label>

                      {selectedActivityType === 'elective' ? <p>Участники поимённо не назначаются. Доступность курса и его размещение в классах указаны отдельно.</p> : !selected.informational && selectedActivityType !== 'club' ? <><ParticipantsPicker groups={groups} students={students} audience={audienceOf(selected, groups)} subject={selected.subject} contextClassIds={lessonClasses(selected)} preview={audiencePreviews[selected.id]} onRetryPreview={() => { setAudienceSnapshot(null); setNewAudienceSnapshot(null); setPreviewRetryNonce((value) => value + 1); }} onConfirmRecalculation={() => schoolReview ? setReviewMode('review') : setRecalculationOpen(true)} onOpenBlockingLesson={(id) => { const lesson = payload.lessons.find((item) => item.id === id); if (lesson) openReviewLesson(lesson.id); }} legacyNotice={selected.audienceMode === 'remainder' && !selected.audience ? 'Сохранено исходное правило остатка из canonical. Его можно заменить динамическим расчётом по времени.' : undefined} onChange={(audience) => updateLesson(selected.id, audiencePatch(audience, groups, students, lessonClasses(selected)))} />{selected.audienceMode === 'remainder' && !selected.audience ? <button onClick={() => updateLesson(selected.id, audiencePatch(audienceOf(selected, groups), groups, students, lessonClasses(selected)))}>Включить динамический расчёт остатка</button> : null}</> : null}
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
                        {selected.kind === 'event' ? <label>Начало <input type="time" disabled={Boolean(selected.allDay)} value={selected.allDay ? '00:00' : selected.start || payload.slots[selected.period]?.start || ''} onChange={(event) => updateLesson(selected.id, { start: event.target.value })} /></label> : <label>Время <select value={selected.period} onChange={(event) => updateLesson(selected.id, { period: Number(event.target.value) })}>{payload.slots.map((slot, index) => <option key={index} value={index}>{index + 1} · {formatLessonTimeRange(slot.start, slot.end)}</option>)}</select></label>}
                      </div>
                      {selected.audience && !['club', 'elective'].includes(selectedActivityType || '') ? <details key={selected.id}><summary>Персональные исключения · {selected.audience.exclude_student_ids?.length || 0}</summary>
                        <label>Основание<input value={selected.attendance_reason || ''} onChange={event => updateLesson(selected.id, { attendance_reason: event.target.value })} /></label>
                        <label>Не посещают это занятие<select multiple disabled={!selected.attendance_reason?.trim()} value={selected.audience.exclude_student_ids || []} onChange={event => updateLesson(selected.id, { audience: { ...selected.audience!, exclude_student_ids: [...event.target.selectedOptions].map(option => option.value) }, audience_snapshot: null })}>{students.filter(student => audiencePreviews[selected.id]?.student_ids?.includes(student.id) || selected.audience?.exclude_student_ids?.includes(student.id)).map(student => <option key={student.id} value={student.id}>{student.name}</option>)}</select></label>
                        {selected.attendance_history?.map((entry,index) => <p key={index}>{entry.at.slice(0,10)} · {entry.actor} · {entry.reason}</p>)}</details> : null}
                      {selectedActivityType === 'practice' ? <label>Ответственные за задание (без присутствия)<select multiple value={selected.responsible_teacher_ids || []} onChange={(event) => updateLesson(selected.id, { responsible_teacher_ids: [...event.target.selectedOptions].map(option => option.value) })}>{teachers.map(teacher => <option key={teacher.id} value={teacher.id}>{teacher.name}</option>)}</select></label> : null}
                      {selected.kind !== 'event' ? selectedDayOnline ? <p>Онлайн по режиму дня класса</p> : <label><input type="checkbox" checked={selectedOnline} onChange={event => updateLesson(selected.id, { format: event.target.checked ? 'online' : '', room: '' })} /> Онлайн</label> : null}
                      {selectedOnline ? <details><summary>Подключение</summary><label>Ссылка или сведения для подключения<input value={selected.connectionInfo || ''} onChange={event => updateLesson(selected.id, { connectionInfo: event.target.value })} /></label></details> : null}
                      {selected.kind !== 'event' && selected.end && selected.end !== payload.slots[selected.period]?.end ? <label>Конец многослотового занятия <input type="time" value={selected.end} onChange={(event) => updateLesson(selected.id, { end: event.target.value })} /></label> : null}
                      {selected.kind === 'event' ? <fieldset className="constructor-event-fields"><legend>Событие · время и сопровождение</legend>
                        <label className="constructor-informational"><input type="checkbox" checked={Boolean(selected.allDay)} onChange={(event) => updateLesson(selected.id, { allDay: event.target.checked, start: event.target.checked ? '00:00' : payload.slots[selected.period]?.start || '09:00', end: event.target.checked ? '23:59' : payload.slots[selected.period]?.end || '09:45' })} /> На весь день</label>
                        <label>Конец <input type="time" disabled={Boolean(selected.allDay)} value={selected.allDay ? '23:59' : selected.end || payload.slots[selected.period]?.end || ''} onChange={(event) => updateLesson(selected.id, { end: event.target.value })} /></label>
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
                                sameText(subjectLabel(a.subject), subjectLabel(selected.subject)),
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
                                sameText(subjectLabel(a.subject), subjectLabel(selected.subject)),
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
              <div className="constructor-editor-actions"><button onClick={() => setPanelOpen(false)}>К сетке</button><span>{saving ? 'Сохраняется' : dirty ? 'Есть изменения' : 'Сохранено'}</span><button onClick={() => void save()} disabled={!dirty || saving}><Save size={15}/> {termComparison ? 'Сохранить правку' : 'Сохранить черновик'}</button></div>
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
