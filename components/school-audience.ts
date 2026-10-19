/* oxlint-disable typescript/no-explicit-any, typescript/no-base-to-string */
type DirectoryRow = Record<string, any>;

const text = (value: unknown) => (typeof value === 'object' ? '' : String(value ?? '')).trim().replace(/\s+/gu, ' ');
const gradeFromClass = (value: unknown) => text(value).match(/^(?:[5-9]|10|11)(?=$|[^\d])/u)?.[0] || '';
const subjectAliases: Record<string, string> = {
  'русский': 'Русский язык', 'русский язык': 'Русский язык', 'russian': 'Русский язык',
  'литература': 'Литература', 'literature': 'Литература',
  'математика': 'Математика', 'math': 'Математика', 'mathematics': 'Математика',
  'алгебра': 'Алгебра', 'геометрия': 'Геометрия',
  'английский': 'Английский язык', 'английский язык': 'Английский язык', 'english': 'Английский язык',
  'физика': 'Физика', 'physics': 'Физика', 'химия': 'Химия', 'chemistry': 'Химия',
  'биология': 'Биология', 'biology': 'Биология', 'география': 'География', 'geography': 'География',
  'история': 'История', 'history': 'История', 'обществознание': 'Обществознание', 'social studies': 'Обществознание',
  'информатика': 'Информатика', 'informatics': 'Информатика', 'computer science': 'Информатика',
  'естествознание': 'Естествознание',
  'рус': 'Русский язык', 'англ': 'Английский язык', 'мат': 'Математика', 'матем': 'Математика',
  'лит': 'Литература', 'общ': 'Обществознание', 'геогр': 'География', 'физ': 'Физика', 'инф': 'Информатика',
  'кл. час': 'Классный час', 'кл.час': 'Классный час', 'кл час': 'Классный час',
  'кл. час иван': 'Классный час', 'история искусств': 'История искусства', 'ист-иск лида': 'История искусства',
  'музыка марина': 'Музыка', 'пластика вадим': 'Пластика', 'лаб текста': 'Лаборатория текста',
};
const programLabels: Record<string, string> = {
  base: 'База', базовая: 'База', база: 'База',
  advanced: 'Углублённая', угл: 'Углублённая', 'угл.': 'Углублённая', углубленная: 'Углублённая', углублённая: 'Углублённая', углубленный: 'Углублённая', углублённый: 'Углублённая',
  oge: 'ОГЭ', огэ: 'ОГЭ', ege: 'ЕГЭ', егэ: 'ЕГЭ',
};
const programLabel = (value: unknown) => {
  const source = text(value);
  return programLabels[source.toLocaleLowerCase('ru')] || source;
};

export function subjectLabel(value: unknown): string {
  const source = text(value);
  return subjectAliases[source.toLocaleLowerCase('ru')] || source;
}

export function classLabel(value: unknown): string {
  const source = text(value).replace(/\s*класс$/iu, '');
  const match = source.match(/^(5|6|7|8|9|10|11)(?:\s*[-–—]\s*([\p{L}\d]+))?$/iu);
  if (!match) return source;
  if (!match[2]) return `${match[1]} класс`;
  const suffix = match[2].replace(/^a$/iu, 'А').replace(/^b$/iu, 'Б').replace(/^c$/iu, 'В');
  return `${match[1]}-${suffix}`;
}

export function audienceGrade(group: DirectoryRow): string {
  if (String(group.semantic_dimension || '') === 'grade7_shared_split') return '7';
  return gradeFromClass(group.base_class_name || (group.group_type === 'class' ? group.name : ''));
}

const audienceParts = (group: DirectoryRow) => {
  const key = text(group.name);
  const rawName = text(group.display_name || group.name);
  const isClass = String(group.group_type || '') === 'class';
  if (isClass) return [classLabel(group.base_class_name || rawName.replace(/ · базовый класс$/iu, ''))];
  if (String(group.semantic_dimension || '') === 'grade7_shared_split' && String(group.role || '') === 'instructional_partition') {
    const split = text(group.subject_subgroup || key.match(/^grade7:split:(\d+)$/u)?.[1]);
    return [split ? `Математика · 7-${split}` : rawName];
  }
  const subject = subjectLabel(group.subject || group.primary_subject);
  const technicalKey = /[:/]/u.test(key) || /^[0-9a-f]{8}-[0-9a-f-]{27,}$/iu.test(key) || /^(?:grade\d+-|instructional:)/iu.test(key);
  const name = text(group.display_name) || (technicalKey ? '' : rawName);
  const gradeInCaption = name.match(/(?:^|[·•—-]\s*)(5|6|7|8|9|10|11)(?:\s*класс)?(?=$|[·•—-]|\s)/iu)?.[1] || '';
  const grade = classLabel(group.base_class_name || gradeInCaption);
  const subgroup = text(group.subject_subgroup || group.subgroup || group.group_number)
    || (subject === 'Английский язык' ? key.match(/^english:(\d{1,2})$/iu)?.[1] || '' : '');
  const course = programLabel(group.course_name || group.course || group.exam_track || group.program);
  const subgroupProgram = programLabel(subgroup);
  const details: string[] = [];
  const isProgramSubgroup = subgroup && subgroupProgram !== subgroup || ['База', 'ОГЭ', 'ЕГЭ', 'Углублённая'].includes(subgroupProgram);
  const primaryProgram = course || (isProgramSubgroup ? subgroupProgram : '');
  const mathSplit = subject === 'Математика' && grade && subgroup && (/^[A-CА-В]$/iu.test(subgroup) || grade === '7 класс' && /^[12]$/u.test(subgroup))
    ? `${grade.replace(/ класс$/u, '')}-${subgroup.replace(/^a$/iu, 'A').replace(/^b$/iu, 'B').replace(/^c$/iu, 'C').replace(/^а$/iu, 'A').replace(/^б$/iu, 'B').replace(/^в$/iu, 'C')}`
    : '';
  if (mathSplit) details.push(mathSplit);
  else if (grade) details.push(grade);
  if (subgroup && !isProgramSubgroup && !mathSplit) {
    const isEnglish = subject === 'Английский язык';
    const explicitGroup = /^(?:группа|group)\s/iu.test(subgroup);
    details.push(isEnglish && !explicitGroup ? `группа ${subgroup}` : explicitGroup ? subgroup : `группа ${subgroup}`);
  }
  if (primaryProgram && !details.includes(primaryProgram)) details.push(primaryProgram);

  // Keep custom human display text, while dropping metadata already represented above.
  let displayTail = name;
  if (subject) {
    const subjectStart = displayTail.match(/^[^·•—-]+/u)?.[0]?.trim() || '';
    if (subjectStart.toLocaleLowerCase('ru') === subject.toLocaleLowerCase('ru') || subjectAliases[subjectStart.toLocaleLowerCase('ru')] === subject || subjectLabel(subjectStart) === subject || subjectStart.startsWith(subject)) {
      displayTail = displayTail.slice(subjectStart.length).replace(/^\s*[·•—-]\s*/u, '').trim();
    }
  }
  let residual = displayTail;
  const removable = [
    subgroup && `группа ${subgroup}`,
    subgroup && `group ${subgroup}`,
    subgroup,
    grade,
    grade && grade.replace(/ класс$/u, ''),
    primaryProgram,
    ...(primaryProgram === 'База' ? ['Базовая', 'базовая'] : []),
    ...(primaryProgram === 'Углублённая' ? ['Углубленная', 'Углублённый', 'Углубленный'] : []),
  ].filter(Boolean) as string[];
  for (const token of removable.sort((a, b) => b.length - a.length)) {
    const escaped = token.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&');
    residual = residual.replace(new RegExp(`(?<![\\p{L}\\p{N}])${escaped}(?![\\p{L}\\p{N}])`, 'giu'), ' ');
  }
  residual = residual.replace(/(?<![\p{L}\p{N}])(?:группа|group|класс)(?![\p{L}\p{N}])/giu, ' ').replace(/[·•—–-]/gu, ' ').replace(/\s+/gu, ' ').trim();
  if (residual && !/^\d+(?: класс)?$/iu.test(residual) && !details.some((part) => part.toLocaleLowerCase('ru') === residual.toLocaleLowerCase('ru'))) details.push(residual);
  return [subject, ...details].filter(Boolean);
};

export function audienceLabel(group: DirectoryRow): string {
  return audienceParts(group).join(' · ') || 'Учебная группа';
}

export function audienceFullLabel(group: DirectoryRow): string { return audienceLabel(group); }

export function audienceShortLabel(group: DirectoryRow): string {
  const label = audienceLabel(group);
  const subject = subjectLabel(group.subject || group.primary_subject);
  if (!subject || String(group.group_type || '') === 'class') return label;
  return label.startsWith(`${subject} · `) ? label.slice(subject.length + 3) : label;
}

export function lessonLabel(subject: unknown, audience?: DirectoryRow | string | null): string {
  const lesson = subjectLabel(subject);
  const rawAudience = typeof audience === 'string' ? text(audience) : '';
  const audienceText = typeof audience === 'string'
    ? rawAudience.replace(/^(?:Русский язык|Литература|Математика|Алгебра|Геометрия|Английский язык|Физика|Химия|Биология|География|История|Обществознание|Информатика|Естествознание)\s*[·•—-]\s*/iu, '')
    : audience ? audienceShortLabel(audience) : '';
  if (!lesson) return audienceText;
  if (!audienceText) return lesson;
  return `${lesson} · ${audienceText}`;
}

export function audienceKind(group: DirectoryRow): 'class' | 'division' | 'other' {
  if (String(group.group_type || '') === 'class') return 'class';
  if (String(group.semantic_dimension || '') === 'grade7_shared_split') return 'division';
  return 'other';
}

export function membershipGroupId(membership: DirectoryRow): string {
  return String(membership.group_id || membership.id || '');
}

export function studentMemberships(student: DirectoryRow): DirectoryRow[] {
  const all = [...((student.instructional_memberships || []) as DirectoryRow[]), ...((student.memberships || []) as DirectoryRow[])];
  return all.filter((item, index) => all.findIndex((other) => membershipGroupId(other) === membershipGroupId(item)) === index);
}

export function audienceGrades(group: DirectoryRow, students: DirectoryRow[]): string[] {
  const groupId = String(group.id || '');
  const grades = new Set<string>();
  for (const student of students) {
    if (!studentMemberships(student).some((item) => membershipGroupId(item) === groupId)) continue;
    const classGroups = (student.base_classes || []) as DirectoryRow[];
    for (const value of [student.class_name, ...classGroups.map((item) => item.base_class_name || item.name)]) {
      const grade = gradeFromClass(value);
      if (grade) grades.add(grade);
    }
  }
  if (!grades.size) {
    const grade = audienceGrade(group);
    if (grade) grades.add(grade);
  }
  return [...grades].sort((a, b) => Number(a) - Number(b));
}
