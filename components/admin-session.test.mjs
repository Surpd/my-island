import assert from 'node:assert/strict';
import test from 'node:test';
import {selectSchoolAdminSession, TEACHER_COLORS_PATH} from './admin-session.ts';

test('consolidated endpoints select one session, preferring the primary browser login', () => {
  for (const path of [TEACHER_COLORS_PATH, `${TEACHER_COLORS_PATH}/teacher/reset?view=school`, '/api/admin/schedule/constructor/draft']) {
    assert.equal(selectSchoolAdminSession(path, 'browser', 'school'), 'browser');
    assert.equal(selectSchoolAdminSession(path, 'browser', 'expired-school'), 'browser');
    assert.equal(selectSchoolAdminSession(path, null, 'school'), 'school');
    assert.equal(selectSchoolAdminSession(path, 'expired-browser', 'school'), 'expired-browser');
    assert.equal(selectSchoolAdminSession(path, null, null), null);
  }
  assert.equal(selectSchoolAdminSession('/api/admin/school/groups', 'browser', 'school'), 'school');
});
