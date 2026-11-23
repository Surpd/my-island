import { audienceGrades } from './school-audience.ts';

const LEGACY_PROGRAM_KEYS = new Set(['generic:course-choice', 'generic:digital-track']);

type DirectoryRow = Parameters<typeof audienceGrades>[0];

// Filter the board view only; directory lookup, memberships and pickers keep
// the original records. Match canonical keys, never their translated titles.
export function isSchoolGroupBoardVisible(group: DirectoryRow) {
  return !LEGACY_PROGRAM_KEYS.has(String(group.name || ''));
}

export function schoolGroupBoardSection(group: DirectoryRow, students: DirectoryRow[]) {
  const grades = audienceGrades(group, students);
  return grades.length === 1 ? `${grades[0]} класс`
    : grades.length ? 'Межвозрастные группы' : 'Другие группы';
}
