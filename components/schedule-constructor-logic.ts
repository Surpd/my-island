import { audienceGrade, subjectLabel } from './school-audience.ts';
import { assignmentIsActive } from './teacher-directory-logic.ts';

export type ScheduleGroup = { id: string; name: string; group_type: string; subject?: string; base_class_name?: string; subject_subgroup?: string; role?: string; semantic_dimension?: string; course?: string; exam_track?: string; archived_at?: string | null };
export type ScheduleTeacher = { id: string; name: string; assignments: { group_id?: string | null; subject: string; course?: string; grade_level?: string | number | null; exam_track?: string; active?: boolean | number; status?: string; valid_from?: string | null; valid_until?: string | null }[] };
export type ScheduleStudent = { id: string; groups: string[]; classes: string[] };

export function shouldWarnMissingTeacher(
  lesson: { activityType?: string; kind?: string; informational?: boolean; teacherId?: string; teacherIds?: string[] },
  independentPractice = false,
): boolean {
  if (lesson.activityType === 'club' || lesson.activityType === 'event' || lesson.kind === 'event' || lesson.informational === true || independentPractice) return false;
  return !lesson.teacherId && !(lesson.teacherIds || []).length;
}

type PlacementLesson = {
  activityType?: string;
  classId?: string;
  classIds?: string[];
  displayClassIds?: string[];
};

/** Classes whose grid cells should contain a lesson, including visual club placement. */
export function schedulePlacementClassIds(lesson: PlacementLesson, selectedColumnClassIds: string[] = []): string[] {
  const ids = lesson.activityType === 'club'
    ? lesson.displayClassIds
    : lesson.classIds?.length ? lesson.classIds : lesson.classId ? [lesson.classId] : [];
  return [...new Set((ids?.length ? ids : selectedColumnClassIds).filter(Boolean))];
}

type LinkedLesson = {
  groupId?: string;
  groupIds?: string[];
  audience?: { kind?: string; group_ids?: string[] };
  canonical?: { assignment?: {
    canonical_group_ids?: string[];
    metadata?: { audience?: { canonical_group_ids?: string[] }; audience_rule?: { group_ids?: string[] } };
  } };
};
type LinkedGroup = { id: string; group_type: string };
type MembershipStudent = { groups?: string[]; classes?: string[] };

/**
 * Return linked instructional groups appropriate to the current class column.
 * Narrow only when canonical student memberships prove a class match; when the
 * directory has no usable membership evidence, preserve the shared label.
 */
export function linkedInstructionalGroupsForColumn<T extends LinkedGroup>(
  lesson: LinkedLesson,
  groups: T[],
  students: MembershipStudent[],
  columnClassIds: string[],
): T[] {
  const assignment = lesson.canonical?.assignment;
  const metadata = assignment?.metadata;
  const ids = [...new Set([
    ...(lesson.groupIds || []), lesson.groupId || '',
    ...(lesson.audience?.kind === 'groups' ? lesson.audience.group_ids || [] : []),
    ...(assignment?.canonical_group_ids || []),
    ...(metadata?.audience?.canonical_group_ids || []),
    ...(metadata?.audience_rule?.group_ids || []),
  ].map(String).filter(Boolean))];
  const linked = ids.map((id) => groups.find((group) => group.id === id))
    .filter((group): group is T => Boolean(group && group.group_type !== 'class'));
  if (linked.length < 2 || !columnClassIds.length) return linked;
  const classSet = new Set(columnClassIds);
  const classesByGroup = new Map<string, Set<string>>();
  for (const student of students) {
    const studentClasses = student.classes || [];
    if (!studentClasses.length) continue;
    for (const id of student.groups || []) {
      const found = classesByGroup.get(id) || new Set<string>();
      studentClasses.forEach((classId) => found.add(classId));
      classesByGroup.set(id, found);
    }
  }
  const mapped = linked.filter((group) => classesByGroup.has(group.id));
  if (!mapped.length) return linked;
  const matching = mapped.filter((group) => [...(classesByGroup.get(group.id) || [])].some((id) => classSet.has(id)));
  return matching.length ? matching : linked;
}

/** Copy a lesson into a destination while keeping explicit canonical audiences intact. */
export function remapPastedLessonAudience<T extends object>(
  lesson: T,
  destinationClassIds: string[],
  newId: string,
): T {
  const source = lesson as Record<string, unknown>;
  const activityType = source.activityType;
  if (activityType === 'club') {
    return { ...source, id: newId, classId: '', classIds: [], displayClassIds: [...destinationClassIds] } as T;
  }
  const audience = source.audience && typeof source.audience === 'object'
    ? source.audience as Record<string, unknown> : undefined;
  const sourceClassIds = new Set([
    ...(Array.isArray(source.classIds) ? source.classIds.filter((id): id is string => typeof id === 'string') : []),
    ...(typeof source.classId === 'string' && source.classId ? [source.classId] : []),
  ]);
  const sourceGroupIds = Array.isArray(source.groupIds)
    ? source.groupIds.filter((id): id is string => typeof id === 'string') : [];
  const sourceGroupId = typeof source.groupId === 'string' ? source.groupId : '';
  const hasNonClassGroup = sourceGroupIds.some((id) => !sourceClassIds.has(id))
    || Boolean(sourceGroupId && !sourceClassIds.has(sourceGroupId));
  const explicitGroupOrStudentAudience = audience?.kind === 'groups' || audience?.kind === 'students' || hasNonClassGroup;
  if (explicitGroupOrStudentAudience) return { ...source, id: newId } as T;
  const classes = [...new Set(destinationClassIds.filter(Boolean))];
  const next: Record<string, unknown> = {
    ...source,
    id: newId,
    classId: classes.length === 1 ? classes[0] : '',
    classIds: classes,
  };
  const sourceClasses = [...sourceClassIds].sort();
  if (JSON.stringify(sourceClasses) !== JSON.stringify([...classes].sort())) delete next.audience_snapshot;
  if (sourceGroupId && sourceClassIds.has(sourceGroupId)) next.groupId = classes.length === 1 ? classes[0] : '';
  if (sourceGroupIds.length && sourceGroupIds.every((id) => sourceClassIds.has(id))) next.groupIds = classes;
  if (audience && (audience.kind === 'classes' || audience.kind === 'available_slot')) {
    const { grade: _staleGrade, ...rest } = audience;
    next.audience = { ...rest, class_ids: classes };
  }
  return next as T;
}

export type TeacherAudienceContext = {
  groupIds?: string[];
  classIds?: string[];
  grade?: string;
  subject?: string;
  course?: string;
  examTrack?: string;
};

function normalizedText(value: string | undefined) {
  return (value || '').toLocaleLowerCase('ru').replace(/[^\p{L}\p{N}]/gu, '');
}

export function groupCourseContext(group: Pick<ScheduleGroup, 'subject_subgroup' | 'exam_track'>): string {
  const exam = normalizedText(group.exam_track);
  const subgroup = normalizedText(group.subject_subgroup);
  const level = /^(base|база|базовая)$/u.test(subgroup) ? 'база'
    : /^(advanced|угл|углубленная|углублённая|профиль|профильная)$/u.test(subgroup) ? 'профиль' : '';
  if (exam === 'егэ' && level) return `ЕГЭ ${level}`;
  if (exam) return String(group.exam_track);
  return level ? level[0].toUpperCase() + level.slice(1) : '';
}

function groupGrade(group: ScheduleGroup | undefined) {
  return group ? audienceGrade(group) : '';
}

/** Rank only from canonical assignment/group IDs; ties deliberately have no default. */
export function rankTeachersForAudience<T extends ScheduleTeacher>(
  teachers: T[],
  groups: ScheduleGroup[],
  context: TeacherAudienceContext,
  unavailableIds: string[] = [],
  students: ScheduleStudent[] = [],
) {
  const unavailable = new Set(unavailableIds);
  const exactGroups = new Set(context.groupIds || []);
  const requestedClasses = context.classIds || [];
  const wantedSubject = normalizedText(context.subject ? subjectLabel(context.subject) : '');
  const wantedCourse = normalizedText(context.course);
  const audienceGroups = [...exactGroups].map((id) => groups.find((item) => item.id === id)).filter((item): item is ScheduleGroup => Boolean(item));
  const audienceStudentClasses = students
    .filter((student) => student.groups.some((id) => exactGroups.has(id)))
    .flatMap((student) => student.classes);
  const classCandidates = [...new Set(requestedClasses.length ? requestedClasses : audienceStudentClasses)];
  const exactClass = classCandidates.length === 1 ? classCandidates[0] : '';
  const audienceGrades = new Set([
    ...[...requestedClasses, ...audienceStudentClasses].map((id) => groupGrade(groups.find((item) => item.id === id))),
    ...audienceGroups.map((group) => groupGrade(group)),
  ].filter(Boolean));
  const grade = context.grade || (audienceGrades.size === 1 ? [...audienceGrades][0] : '');
  const wantedExamTrack = normalizedText(context.examTrack);
  const ranked = teachers.map((teacher) => {
    let rank = 40;
    for (const assignment of context.subject ? teacher.assignments : []) {
      if (!assignmentIsActive(assignment) || (wantedSubject && normalizedText(subjectLabel(assignment.subject)) !== wantedSubject)) continue;
      if (assignment.course && (!wantedCourse || normalizedText(assignment.course) !== wantedCourse)) continue;
      if (assignment.exam_track && (!wantedExamTrack || normalizedText(assignment.exam_track) !== wantedExamTrack)) continue;
      const coursePenalty = wantedCourse && !assignment.course ? 1 : 0;
      const examTrackPenalty = wantedExamTrack && !assignment.exam_track ? 1 : 0;
      const dimensionsPenalty = coursePenalty + examTrackPenalty;
      const groupId = assignment.group_id || '';
      const assignmentRank = groupId && exactGroups.has(groupId) ? dimensionsPenalty
        : groupId && exactClass && groupId === exactClass ? 10 + dimensionsPenalty
          : grade && String(assignment.grade_level || '') === grade ? 20 + dimensionsPenalty : 40;
      rank = Math.min(rank, assignmentRank);
    }
    return { teacher, rank: unavailable.has(teacher.id) ? 100 : rank };
  }).sort((a, b) => a.rank - b.rank || a.teacher.name.localeCompare(b.teacher.name, 'ru'));
  const bestRank = ranked[0]?.rank;
  const best = ranked.filter((item) => item.rank === bestRank && bestRank < 40);
  return { teachers: ranked.map((item) => item.teacher), defaultTeacherId: best.length === 1 ? best[0].teacher.id : '' };
}

const SUBJECT_ALIASES: Record<string, string[]> = {
  'Математика': ['мат', 'матем'],
  'Английский язык': ['англ', 'английский'],
  'Русский язык': ['рус', 'русский'],
  'Литература': ['лит', 'литература'],
  'География': ['геогр', 'география'],
  'История': ['ист', 'история'],
  'Обществознание': ['общ', 'обществознание'],
  'Информатика': ['инф', 'информатика'],
  'Физика': ['физ', 'физика'],
  'Биология': ['биол', 'биология'],
  'Химия': ['хим', 'химия'],
  'Естествознание': ['ест', 'естествознание'],
  'Робототехника': ['робо', 'робототехника'],
  'Медиастудия': ['медиа', 'медиастудия'],
  'Финансовая грамотность': ['финансы', 'финансовая грамотность'],
  'Цифровая грамотность': ['цифр', 'цифровая грамотность'],
};

const normalize = (value: string) => value.toLocaleLowerCase('ru').replace(/ё/g, 'е').replace(/[‐‑–—]/g, '-').replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
const groupLetter = (value: string) => normalize(value).replace(/[aа]/g, 'а').replace(/[bв]/g, 'в').replace(/[cс]/g, 'с');

export function shortSubject(subject: string) {
  const found = Object.entries(SUBJECT_ALIASES).find(([name]) => normalize(name) === normalize(subject));
  if (found) return found[1][0].replace(/^./, (letter) => letter.toLocaleUpperCase('ru'));
  const extended = Object.entries(SUBJECT_ALIASES).find(([name]) => normalize(subject).startsWith(`${normalize(name)} `));
  if (extended) return `${extended[1][0].replace(/^./, (letter) => letter.toLocaleUpperCase('ru'))} · ${subject.slice(extended[0].length).replace(/^[\s·—-]+/, '').slice(0, 9)}`;
  const words = subject.trim().split(/\s+/);
  if (subject.length <= 12) return subject;
  return words.length > 1 ? words.map((word) => word.slice(0, 4)).join(' · ') : `${subject.slice(0, 9)}…`;
}

export function shortTeacher(name: string) {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length < 2) return name;
  // Keep a confirmed short designation instead of constructing another one.
  if (words.some((word) => /^[А-ЯA-Z]{1,3}\.?$/u.test(word))) return name;
  if (words.length === 2 && /\d/.test(words[1])) return `${words[0].slice(0, 3)}. ${words[1]}`;
  return `${words[0]} ${words.slice(1).map((word) => `${word[0]}.`).join('')}`;
}

export function shortGroup(groupName: string, subject: string, grade = '') {
  let value = groupName.trim();
  if (grade) value = value.replace(new RegExp(`^${grade}(?:[а-яa-z]{1,3}(?=[\\s-]|$))?(?:[\\s-]*(?:класс|кл))?[\\s-]*`, 'iu'), '');
  const subjectWords = [subject, shortSubject(subject), ...(SUBJECT_ALIASES[subject] || [])].filter(Boolean);
  for (const word of subjectWords) value = value.replace(new RegExp(`(^|\\s)${word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?=\\s|$)`, 'iu'), ' ').trim();
  if (grade) value = value.replace(new RegExp(`(?:^|[\\s·—-])${grade}(?=$|[\\s·—-])`, 'gu'), ' ');
  value = value.replace(/^[\s·—-]+|[\s·—-]+$/g, '');
  const lane = value.match(/(?:^|[\s·—-])([ABCАВС])$/iu);
  if (lane) return lane[1].toLocaleUpperCase('ru');
  return value || (grade ? `${grade} класс` : groupName);
}

export function groupClassIds(groupId: string, groups: ScheduleGroup[], students: ScheduleStudent[], fallbackClassIds: string[]) {
  const group = groups.find((item) => item.id === groupId);
  if (!group || group.group_type === 'class') return fallbackClassIds;
  const ids = new Set(students.filter((student) => student.groups.includes(groupId)).flatMap((student) => student.classes));
  return ids.size ? [...ids] : fallbackClassIds;
}

export type QuickParse = {
  subject: string;
  subjects: string[];
  groupIds: string[];
  classIds: string[];
  teacherIds: string[];
  room: string;
  unknown: string[];
};

export type ScheduleMergeConflict = {
  key: string;
  lessonId?: string;
  field: string;
  base: unknown;
  local: unknown;
  server: unknown;
};

const MISSING = Symbol('missing-schedule-value');
const SET_FIELDS = new Set(['classIds', 'groupIds', 'teacherIds', 'excludedGroupIds', 'class_ids', 'group_ids', 'student_ids', 'classes', 'days', 'lessons', 'eligibleGrades', 'displayClassIds', 'confirmedParticipantIds']);
const VOLATILE_FIELDS = new Set(['revision', 'created_at', 'updated_at', 'resolved_at', 'saved_at', 'timestamp']);

function meaningfulValue(value: unknown, field = ''): unknown {
  if (Array.isArray(value)) {
    const items = value.map((item) => meaningfulValue(item));
    return SET_FIELDS.has(field) ? items.sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))) : items;
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>)
      .filter(([key]) => !VOLATILE_FIELDS.has(key))
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => [key, meaningfulValue(item, key)]));
  }
  return value;
}

function sameMeaningfulValue(left: unknown, right: unknown) {
  if (left === MISSING || right === MISSING) return left === right;
  return JSON.stringify(meaningfulValue(left)) === JSON.stringify(meaningfulValue(right));
}

export function sameScheduleMeaning(left: unknown, right: unknown) {
  return sameMeaningfulValue(left, right);
}

function hasOwn(value: object, key: string) {
  return Object.prototype.hasOwnProperty.call(value, key);
}

/** Three-way merge for an already-open schedule draft. Conflicts default to the local value. */
export function mergeScheduleDrafts<T extends { id: string; name: string; revision: number; payload: { lessons: { id: string }[]; [key: string]: unknown }; [key: string]: unknown }>(
  base: T,
  local: T,
  server: T,
  choices: Record<string, 'local' | 'server'> = {},
) {
  const conflicts: ScheduleMergeConflict[] = [];
  const choose = (key: string, field: string, baseValue: unknown, localValue: unknown, serverValue: unknown, lessonId?: string) => {
    const localChanged = !sameMeaningfulValue(localValue, baseValue);
    const serverChanged = !sameMeaningfulValue(serverValue, baseValue);
    if (!localChanged) return serverValue;
    if (!serverChanged || sameMeaningfulValue(localValue, serverValue)) return localValue;
    conflicts.push({ key, lessonId, field, base: baseValue === MISSING ? undefined : baseValue, local: localValue === MISSING ? undefined : localValue, server: serverValue === MISSING ? undefined : serverValue });
    return choices[key] === 'server' ? serverValue : localValue;
  };
  const basePayload = base.payload as Record<string, unknown>;
  const localPayload = local.payload as Record<string, unknown>;
  const serverPayload = server.payload as Record<string, unknown>;
  const mergedPayload: Record<string, unknown> = {};
  const payloadKeys = new Set([...Object.keys(basePayload), ...Object.keys(localPayload), ...Object.keys(serverPayload)]);
  for (const key of payloadKeys) {
    if (key === 'lessons') continue;
    const value = choose(`meta:${key}`, key,
      hasOwn(basePayload, key) ? basePayload[key] : MISSING,
      hasOwn(localPayload, key) ? localPayload[key] : MISSING,
      hasOwn(serverPayload, key) ? serverPayload[key] : MISSING);
    if (value !== MISSING) mergedPayload[key] = value;
  }

  const byId = (draft: T) => new Map(draft.payload.lessons.map((lesson) => [lesson.id, lesson]));
  const baseLessons = byId(base), localLessons = byId(local), serverLessons = byId(server);
  const lessonIds = [...new Set([...serverLessons.keys(), ...localLessons.keys(), ...baseLessons.keys()])];
  const mergedLessons: { id: string }[] = [];
  for (const id of lessonIds) {
    const baseLesson = baseLessons.has(id) ? baseLessons.get(id)! : MISSING;
    const localLesson = localLessons.has(id) ? localLessons.get(id)! : MISSING;
    const serverLesson = serverLessons.has(id) ? serverLessons.get(id)! : MISSING;
    if (baseLesson !== MISSING && (localLesson === MISSING || serverLesson === MISSING)) {
      const value = choose(`lesson:${id}:$lesson`, 'Занятие удалено или изменено', baseLesson, localLesson, serverLesson, id);
      if (value !== MISSING) mergedLessons.push(value as { id: string });
      continue;
    }
    if (baseLesson === MISSING && (localLesson === MISSING || serverLesson === MISSING)) {
      const value = localLesson === MISSING ? serverLesson : localLesson;
      if (value !== MISSING) mergedLessons.push(value as { id: string });
      continue;
    }
    if (baseLesson === MISSING) {
      if (sameMeaningfulValue(localLesson, serverLesson)) mergedLessons.push(localLesson as { id: string });
      else {
        const value = choose(`lesson:${id}:$lesson`, 'Занятие добавлено с обеих сторон', MISSING, localLesson, serverLesson, id);
        if (value !== MISSING) mergedLessons.push(value as { id: string });
      }
      continue;
    }
    if (localLesson === MISSING || serverLesson === MISSING) continue;
    const before = baseLesson as Record<string, unknown>;
    const mine = localLesson as Record<string, unknown>;
    const theirs = serverLesson as Record<string, unknown>;
    const merged: Record<string, unknown> = {};
    const lessonFields = new Set([...Object.keys(before), ...Object.keys(mine), ...Object.keys(theirs)]);
    for (const field of lessonFields) {
      const value = choose(`lesson:${id}:${field}`, field,
        hasOwn(before, field) ? before[field] : MISSING,
        hasOwn(mine, field) ? mine[field] : MISSING,
        hasOwn(theirs, field) ? theirs[field] : MISSING, id);
      if (value !== MISSING) merged[field] = value;
    }
    mergedLessons.push(merged as { id: string });
  }
  mergedPayload.lessons = mergedLessons;
  const name = choose('meta:name', 'Название черновика', base.name, local.name, server.name);
  return {
    draft: { ...server, name: name as string, payload: mergedPayload } as T,
    conflicts,
  };
}

export function parseQuick(text: string, contextClassId: string, groups: ScheduleGroup[], teachers: ScheduleTeacher[], students: ScheduleStudent[], subjects: string[]): QuickParse {
  const words = normalize(text).split(/\s+/).filter(Boolean);
  const used = new Set<number>();
  const subjectPool = [...new Set([...subjects, ...Object.keys(SUBJECT_ALIASES)])];
  const subjectMatches = subjectPool.flatMap((subject) => [subject, ...(SUBJECT_ALIASES[subject] || [])].map((alias) => ({ subject, alias: normalize(alias).split(' ') })));
  const matchedSubjects = subjectMatches.filter(({ alias }) => alias.length <= words.length && alias.every((word, index) => words[index] === word)).sort((a, b) => b.alias.join('').length - a.alias.join('').length);
  const longestSubjectMatch = matchedSubjects[0]?.alias.join('').length || 0;
  const subjectCandidates = [...new Set(matchedSubjects.filter((item) => item.alias.join('').length === longestSubjectMatch).map((item) => item.subject))];
  const subjectHit = subjectCandidates.length === 1 ? matchedSubjects.find((item) => item.subject === subjectCandidates[0]) : undefined;
  if (longestSubjectMatch) matchedSubjects.find((item) => item.alias.join('').length === longestSubjectMatch)?.alias.forEach((_, index) => used.add(index));
  const classes = groups.filter((group) => group.group_type === 'class');
  const exactClassMatches = classes.filter((group) => {
    const name = groupLetter(group.name).split(' ');
    return words.some((word) => groupLetter(word) === groupLetter(group.name)) || name.length > 1 && words.some((word, index) => name.every((part, offset) => groupLetter(words[index + offset] || '') === groupLetter(part)));
  });
  if (exactClassMatches.length === 1) {
    const nameWords = groupLetter(exactClassMatches[0].name).split(' ');
    const start = words.findIndex((_, index) => nameWords.every((part, offset) => groupLetter(words[index + offset] || '') === part));
    if (start >= 0) nameWords.forEach((_, offset) => used.add(start + offset));
  }
  const grade = classes.find((item) => item.id === contextClassId)?.name.match(/^\d+/)?.[0] || '';
  const compatibleGroups = groups.filter((group) => group.group_type !== 'class' && !group.archived_at && (!subjectHit || !group.subject || normalize(subjectLabel(group.subject)) === normalize(subjectLabel(subjectHit.subject))));
  const explicitGroupNumberIndex = words.findIndex((word, index) => !used.has(index) && /^\d{1,2}$/.test(word) && compatibleGroups.some((group) => {
    const groupNumber = group.id.match(/(?:^|:)(\d{1,2})$/u)?.[1]
      || group.subject_subgroup?.match(/^\s*(\d{1,2})\s*$/u)?.[1]
      || group.name.match(/(?:группа|group)\s*(\d{1,2})\s*$/iu)?.[1];
    return groupNumber === word;
  }));
  const gradeIndex = words.findIndex((word, index) => !used.has(index) && index !== explicitGroupNumberIndex && /^\d{1,2}$/.test(word) && Number(word) >= 5 && Number(word) <= 11);
  const trailingContextRoom = Boolean(contextClassId && gradeIndex === words.length - 1 && grade && words[gradeIndex] !== grade);
  const requestedGrade = gradeIndex >= 0 && !trailingContextRoom ? words[gradeIndex] : exactClassMatches[0]?.name.match(/^\d+/)?.[0] || (explicitGroupNumberIndex >= 0 ? '' : grade);
  const classMatches = exactClassMatches.length ? exactClassMatches : requestedGrade ? classes.filter((group) => group.name.match(/^\d+/)?.[0] === requestedGrade) : [];
  const groupSubjectMatches = (group: ScheduleGroup) => !subjectHit || !group.subject
    || normalize(subjectLabel(group.subject)) === normalize(subjectLabel(subjectHit.subject));
  const groupMarkers = (group: ScheduleGroup) => {
    const pair = group.name.match(/(?:^|\s)(\d{1,2})\s*[-–—]\s*([\p{L}\p{N}]+)\s*$/iu);
    if (pair) return [pair[1], pair[2]].map(normalize);
    const number = group.id.match(/(?:^|:)(\d{1,2})$/u)?.[1]
      || group.name.match(/(?:группа|group)\s*(\d{1,2})\s*$/iu)?.[1]
      || group.subject_subgroup?.match(/^\s*(\d{1,2})\s*$/u)?.[1];
    if (number) return [number];
    const short = groupLetter(shortGroup(group.name, subjectHit?.subject || group.subject || '', requestedGrade));
    return short && short !== groupLetter(group.name) ? short.split(' ') : groupLetter(group.name).split(' ');
  };
  const containsSequence = (tokens: string[], marker: string[]) => marker.length > 0 && tokens.some((_, index) => marker.every((part, offset) => tokens[index + offset] === part));
  const groupCandidates = groups.filter((group) => {
    if (group.group_type === 'class' || group.archived_at || !groupSubjectMatches(group)) return false;
    if (requestedGrade) {
      const belongs = group.base_class_name?.startsWith(requestedGrade)
        || group.name.startsWith(requestedGrade)
        || students.some((student) => student.groups.includes(group.id) && student.classes.some((id) => classes.find((item) => item.id === id)?.name.startsWith(requestedGrade)));
      if (!belongs && exactClassMatches.length === 0) return false;
    }
    const remainingWords = words.filter((_, index) => !used.has(index)).map(groupLetter);
    const marker = groupMarkers(group);
    return containsSequence(remainingWords, marker) || marker.length === 1 && remainingWords.includes(marker[0]);
  });
  if (groupCandidates.length) {
    const markers = new Set(groupCandidates.flatMap(groupMarkers));
    words.forEach((word, index) => { if (markers.has(groupLetter(word)) || /^(?:группа|группу|гр)$/u.test(word)) used.add(index); });
  }
  const teacherMatches = teachers.map((teacher) => {
    const parts = normalize(teacher.name).split(' ').filter(Boolean);
    const initials = parts.map((part) => part[0]).join('');
    const aliases = [normalize(teacher.name), ...parts.filter((part) => part.length > 2), initials];
    const match = aliases.map((alias) => alias.split(' ')).find((alias) => words.some((word, index) => alias.every((part, offset) => words[index + offset] === part && !used.has(index + offset))));
    return { teacher, match };
  }).filter((value) => value.match);
  const longestTeacherMatch = Math.max(0, ...teacherMatches.map((value) => value.match!.join('').length));
  const teacherCandidates = teacherMatches.filter((value) => value.match!.join('').length === longestTeacherMatch);
  let matchedTeacherStart = Number.POSITIVE_INFINITY;
  for (const candidate of teacherCandidates) {
    const match = candidate.match!;
    const start = words.findIndex((_, index) => match.every((part, offset) => words[index + offset] === part && !used.has(index + offset)));
    if (start >= 0) {
      matchedTeacherStart = Math.min(matchedTeacherStart, start);
      match.forEach((_, offset) => used.add(start + offset));
    }
  }
  if (gradeIndex >= 0 && !used.has(gradeIndex) && !trailingContextRoom && !(gradeIndex > matchedTeacherStart && gradeIndex === words.length - 1)) used.add(gradeIndex);
  const roomMarker = words.findIndex((word, index) => !used.has(index) && /^(?:каб|кабинет|ауд)$/u.test(word));
  if (roomMarker >= 0) used.add(roomMarker);
  const roomIndex = words.findLastIndex((word, index) => !used.has(index) && /^\d{1,3}$/.test(word) && (index === words.length - 1 || Number(word) > 12));
  const room = roomIndex >= 0 ? words[roomIndex] : '';
  if (roomIndex >= 0) used.add(roomIndex);
  return {
    subject: subjectHit?.subject || '',
    subjects: subjectCandidates,
    groupIds: groupCandidates.map((item) => item.id),
    classIds: classMatches.map((item) => item.id),
    teacherIds: teacherCandidates.map((item) => item.teacher.id),
    room,
    unknown: words.filter((_, index) => !used.has(index)),
  };
}
