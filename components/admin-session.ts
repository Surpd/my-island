export const TEACHER_COLORS_PATH = '/api/admin/school/teacher-ui-colors';

export function selectSchoolAdminSession(path: string, browserSession: string | null, schoolSession: string | null) {
  const pathname = path.split('?')[0];
  const primary = [TEACHER_COLORS_PATH, '/api/admin/schedule/constructor']
    .some((prefix) => pathname === prefix || pathname.startsWith(prefix + '/'));
  return primary ? browserSession || schoolSession : schoolSession || browserSession;
}
