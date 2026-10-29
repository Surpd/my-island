import { readFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { expect } from '@playwright/test';
import { test } from './helpers/experiment-test';

const apiRoot = 'http://127.0.0.1:8011';
const fixturePath = process.env.MY_ISLAND_REV45_FIXTURE;
const artifactDirectory = process.env.MY_ISLAND_REV45_ARTIFACTS || '.local-test/artifacts/resolution-audit';

test('capture masked rev45 diagnostics, lesson context, and mobile confirmation', async ({ page }) => {
  test.skip(!fixturePath, 'Set MY_ISLAND_REV45_FIXTURE to a sanitized local snapshot fixture.');
  const fixture = JSON.parse(await readFile(fixturePath!, 'utf8'));
  const lessons = fixture.payload.lessons as Array<Record<string, unknown> & { id: string }>;
  const draft = { id: fixture.id, name: fixture.name, revision: fixture.revision, payload: fixture.payload };
  await mkdir(artifactDirectory, { recursive: true });

  await page.route(`${apiRoot}/api/admin/**`, async (route) => {
    const pathName = new URL(route.request().url()).pathname;
    if (pathName.endsWith('/auth/session')) return route.fulfill({ json: { user: { id: 'synthetic-admin', role: 'admin', roles: ['admin'] } } });
    if (pathName.endsWith('/experiments/capabilities')) return route.fulfill({ json: { demo_available: false, school_available: true, consolidated_schedule: true } });
    if (pathName === '/api/admin/schedule/experiments/school') {
      return route.fulfill({ json: { items: [{ id: fixture.id, name: fixture.name, revision: fixture.revision, is_primary_template: true, draft_kind: 'school', lesson_count: fixture.lesson_count, updated_at: '2026-10-29T08:00:00Z' }] } });
    }
    if (pathName.endsWith('/school/directory')) return route.fulfill({ json: { groups: fixture.groups, teachers: fixture.teachers, students: [], rooms: fixture.rooms } });
    if (pathName.endsWith('/school/audience-preview')) {
      const body = route.request().postDataJSON();
      const lessonIds = body.lesson_ids || lessons.map((lesson) => lesson.id);
      return route.fulfill({ json: { lessons: Object.fromEntries(lessonIds.map((lessonId: string) => [lessonId, { status: 'unknown', student_ids: [], reason: 'Ученические назначения отключены для визуального снимка.' }])) } });
    }
    if (pathName.endsWith(`/school/${fixture.id}`)) return route.fulfill({ json: draft });
    if (pathName.endsWith('/resolution/preview')) {
      const body = route.request().postDataJSON();
      return route.fulfill({ json: { revision: fixture.revision, preview_token: 'screenshot-only-token', lesson_ids: body.lesson_ids, lessons: lessons.filter((lesson) => body.lesson_ids.includes(lesson.id)), rooms: [], room_limitation: 'Снимок для интерфейса; помещение не проверяется.', exceptions: {}, audiences: {}, roster: { affected_lessons: [] } } });
    }
    if (pathName.includes('/teacher-ui-colors') || pathName.includes('/activities')) return route.fulfill({ json: { items: [] } });
    if (pathName.includes('/observability')) return route.fulfill({ json: { blocks: [], weeks: [], effective_weeks: [], week_publications: [] } });
    return route.fulfill({ json: { items: [] } });
  });

  const artifact = (name: string) => path.join(artifactDirectory, name);
  await page.goto('/admin/schedule/constructor?role=admin&editor=1');
  await page.getByRole('button', { name: 'Открыть и редактировать', exact: true }).click();
  await page.locator('.constructor-issue-count').click();
  await expect(page.getByText('Центр решений расписания', { exact: true })).toBeVisible();
  await page.screenshot({ path: artifact('rev45-sanitized-diagnostics.png'), fullPage: true });

  const teacherIssue = page.locator('.constructor-conflict-list article').filter({ hasText: 'Преподаватель уже ведёт урок' }).first();
  await expect(teacherIssue).toBeVisible();
  await teacherIssue.getByRole('button', { name: 'Разобрать проблему' }).click();
  const dialog = page.getByRole('dialog', { name: 'Назначение преподавателя' });
  await expect(dialog).toBeVisible();
  await page.screenshot({ path: artifact('rev45-sanitized-issue-context.png'), fullPage: true });
  await page.setViewportSize({ width: 375, height: 812 });
  await dialog.getByRole('button', { name: 'Самостоятельная практика без присутствия преподавателя' }).click();
  await dialog.locator('.constructor-resolution-confirm').scrollIntoViewIfNeeded();
  await dialog.getByLabel('Основание решения').fill('Проверка интерфейса на санитизированном снимке');
  await dialog.getByRole('checkbox').check();
  const confirm = dialog.getByRole('button', { name: 'Подтвердить решение' });
  await confirm.scrollIntoViewIfNeeded();
  await expect(confirm).toBeEnabled();
  await page.screenshot({ path: artifact('rev45-sanitized-practice-confirm-mobile.png'), fullPage: false });
  expect(lessons).toHaveLength(386);
});
