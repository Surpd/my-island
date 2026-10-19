import type { APIRequestContext } from '@playwright/test';

const API = 'http://localhost:8011';
const ADMIN = { 'X-Dev-Auth': 'admin:1' };

type Experiment = {
  id: string;
  name: string;
  revision: number;
  payload: {
    days: number[];
    slots: { start: string; end: string }[];
    classes: string[];
    lessons: Record<string, unknown>[];
  };
};

/** Create a disposable API-backed experimental copy with review edge cases. */
export async function createSyntheticFilledWeek(request: APIRequestContext, registerExperiment: (id: string) => void) {
  const created = await request.post(`${API}/api/admin/schedule/experiments`, {
    headers: ADMIN,
    data: { name: `E2E filled week ${Date.now()}`, source: 'canonical', payload: { draft_kind: 'test' } },
  });
  if (!created.ok()) throw new Error(`Experiment creation failed: ${created.status()}`);
  const experiment = (await created.json()) as Experiment;
  registerExperiment(experiment.id);

  const groupsResponse = await request.get(`${API}/api/admin/groups`, { headers: ADMIN });
  if (!groupsResponse.ok()) throw new Error(`Group lookup failed: ${groupsResponse.status()}`);
  const groups = (await groupsResponse.json()).items as {
    id: string;
    group_type: string;
    base_class_name?: string;
    name?: string;
    display_name?: string;
  }[];
  // Other CRUD scenarios may leave a class without a school grade. This
  // scenario's grid covers the seven seeded parallels, not those extra rows.
  const gradedClassIds = new Set(groups.filter((group) => group.group_type === 'class'
    && /^\d/.test(group.base_class_name || group.name || '')).map((group) => String(group.id)));
  experiment.payload.classes = experiment.payload.classes.filter((id) => gradedClassIds.has(String(id)));
  const classId = String(groups.find((group) => group.group_type === 'class' && group.base_class_name === '5')?.id || '');
  const classId6 = String(groups.find((group) => group.group_type === 'class' && group.base_class_name === '6')?.id || '');
  const classId7 = String(groups.find((group) => group.group_type === 'class' && group.base_class_name === '7')?.id || '');
  const classId8 = String(groups.find((group) => group.group_type === 'class' && group.base_class_name === '8')?.id || '');
  if (!classId || !experiment.payload.classes.includes(classId)) {
    throw new Error('Synthetic grade 5 class is missing from the isolated directory');
  }

  const findFixtureGroup = (displayName: string) => String(groups.find((group) => group.display_name === displayName)?.id || '');
  const englishGroupA = findFixtureGroup('E2E Английский 5–6 · A');
  const englishGroupB = findFixtureGroup('E2E Английский 5–6 · B');
  const digitalGroup = findFixtureGroup('E2E Цифровая грамотность 7–8');
  const electiveGroups = [1, 2, 3, 4].map((index) => findFixtureGroup(`E2E Факультатив ${index}`));
  const emptyGroup = findFixtureGroup('E2E Пустая группа');
  if (![classId6, classId7, classId8, englishGroupA, englishGroupB, digitalGroup, ...electiveGroups, emptyGroup].every(Boolean)) {
    throw new Error('Synthetic scenarios require the seeded canonical E2E class and group directory');
  }
  const scenario = (id: string, day: number, period: number, classIds: string[], subject: string, groupId: string, extra: Record<string, unknown> = {}) => ({
    id, day, period, classId: classIds[0], classIds, subject, teacherId: '', teacherIds: [], groupId,
    groupIds: groupId ? [groupId] : [], room: '', start: '09:00', end: '09:45', audienceMode: 'canonical',
    importError: false, needsClarification: false, ...extra,
  });
  const teacherIds: string[] = [];
  for (let index = 0; index < 10; index++) {
    const response = await request.post(`${API}/api/admin/identities`, {
      headers: ADMIN,
      data: { display_name: `Синт ${index + 1}`, kind: 'teacher' },
    });
    if (!response.ok()) throw new Error(`Synthetic teacher creation failed: ${response.status()}`);
    teacherIds.push(String((await response.json()).id));
  }
  const replaced = new Map<string, string[]>([
    ['0:0', [classId, classId6]], ['1:0', [classId, classId6]],
    ['2:0', [classId7, classId8]], ['3:0', [classId, classId6]], ['3:3', [classId]],
    ['4:4', [classId7, classId8]], ['4:5', [classId7, classId8]],
  ]);
  const retained = experiment.payload.lessons.filter((lesson) => {
    const affected = replaced.get(`${String(lesson.day)}:${String(lesson.period)}`);
    const ids = Array.isArray(lesson.classIds) ? lesson.classIds : [lesson.classId];
    return !affected || !ids.some((id) => affected.includes(String(id)));
  });
  const lessons = [
    ...retained,
    scenario('e2e-english-56-a', 0, 0, [classId, classId6], 'Английский язык', englishGroupA, { teacherId: teacherIds[0], teacherIds: [teacherIds[0]] }),
    scenario('e2e-english-56-b', 0, 0, [classId, classId6], 'Английский язык', englishGroupB, { teacherId: teacherIds[1], teacherIds: [teacherIds[1]] }),
    ...['Робототехника', 'Шахматы', 'Медиастудия', 'Финансовая грамотность'].map((subject, index) =>
      scenario(`e2e-elective-${index + 1}`, 1, 0, [classId, classId6], subject, electiveGroups[index], { teacherId: teacherIds[index + 2], teacherIds: [teacherIds[index + 2]], blockId: 'e2e-electives', blockName: 'Курсы по выбору 5–6' })),
    scenario('e2e-digital-78', 2, 0, [classId7, classId8], 'Цифровая грамотность 7–8', digitalGroup, { teacherId: teacherIds[6], teacherIds: [teacherIds[6]] }),
    scenario('e2e-overlap-conflict-a', 3, 0, [classId, classId6], 'Конфликтный урок A', electiveGroups[0], { teacherId: teacherIds[7], teacherIds: [teacherIds[7]] }),
    scenario('e2e-overlap-conflict-b', 3, 0, [classId, classId6], 'Конфликтный урок B', electiveGroups[0], { teacherId: teacherIds[8], teacherIds: [teacherIds[8]] }),
    scenario('e2e-unknown-membership', 3, 3, [classId], 'Пустая группа без участников', emptyGroup, { teacherId: teacherIds[9], teacherIds: [teacherIds[9]] }),
    scenario('e2e-event', 4, 4, [classId7, classId8], 'Экскурсия в музей', '', { groupIds: [classId7, classId8], kind: 'event', activityType: 'event', start: '12:45', end: '14:25', place: 'музей', canonicalRole: 'special_event' }),
    { id: 'e2e-gap', day: 4, period: 5, classId, subject: 'Нет урока', teacherId: '', groupId: classId, room: '', start: '13:40', end: '14:25', canonicalRole: 'explicit_no_lesson' },
    { id: 'e2e-substitution', day: 4, period: 6, classId, subject: 'Математика · замена', teacherId: teacherIds[0], groupId: classId, room: '', start: '14:35', end: '15:20', raw: 'Замена преподавателя для E2E' },
  ];
  const updated = await request.put(`${API}/api/admin/schedule/experiments/${experiment.id}`, {
    headers: ADMIN,
    data: { expected_revision: experiment.revision, payload: { ...experiment.payload, lessons } },
  });
  if (!updated.ok()) throw new Error(`Experiment enrichment failed: ${updated.status()}`);
  return {
    ...(await updated.json()), name: experiment.name, grade5ClassId: classId,
    englishGroupIds: [englishGroupA, englishGroupB], electiveGroupIds: electiveGroups,
    digitalGroupId: digitalGroup, emptyGroupId: emptyGroup,
  } as Experiment & { grade5ClassId: string; englishGroupIds: string[]; electiveGroupIds: string[]; digitalGroupId: string; emptyGroupId: string };
}
