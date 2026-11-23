import { audienceGrades } from './school-audience.ts';

const PROGRAM_KEYS = new Set(['generic:course-choice', 'generic:digital-track']);

type DirectoryRow = Parameters<typeof audienceGrades>[0];

export function schoolGroupBoardSection(group: DirectoryRow, students: DirectoryRow[]) {
  const canonicalName = String(group.name || '');
  // The canonical source identifies the two generic program rosters by their
  // technical keys (see SCHEDULE_CANONICAL_V2_ORDINARY_AUDIENCE_CORRECTED.json).
  if (PROGRAM_KEYS.has(canonicalName)) return 'Курсы по выбору';

  const grades = audienceGrades(group, students);
  return grades.length === 1 ? `${grades[0]} класс`
    : grades.length ? 'Межвозрастные группы' : 'Другие группы';
}
