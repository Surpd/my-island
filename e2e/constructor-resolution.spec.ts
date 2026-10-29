import { expect, type Page } from '@playwright/test';
import { test } from './helpers/experiment-test';
import { mkdir } from 'node:fs/promises';

const API = 'http://127.0.0.1:8011';
const id = 'resolution-ui-fixture';
async function setup(
  page: Page,
  options: { needsRosterReview?: boolean; staleNextApply?: boolean; thirdConflict?: boolean; criticalIssue?: boolean } = {},
) {
  let revision = 1;
  const lessons = [
    {
      id: 'lesson-a',
      day: 0,
      period: 0,
      classId: 'class-a',
      classIds: ['class-a'],
      subject: 'Математика',
      teacherId: 'teacher-a',
      groupId: 'class-a',
      groupIds: ['class-a'],
      room: '101',
      audience: { kind: 'classes', class_ids: ['class-a'] },
      audience_snapshot: { student_ids: ['student-a'] },
    },
    {
      id: 'lesson-b',
      day: 0,
      period: 0,
      classId: 'class-b',
      classIds: ['class-b'],
      subject: 'Математика',
      teacherId: 'teacher-a',
      groupId: 'class-b',
      groupIds: ['class-b'],
      room: '101',
      audience: { kind: 'classes', class_ids: ['class-b'] },
    },
  ];
  if (options.thirdConflict) lessons.push({
    id: 'lesson-c',
    day: 0,
    period: 0,
    classId: 'class-c',
    classIds: ['class-c'],
    subject: 'Физика',
    teacherId: 'teacher-a',
    groupId: 'class-c',
    groupIds: ['class-c'],
    room: '101',
    audience: { kind: 'classes', class_ids: ['class-c'] },
    ...(options.needsRosterReview ? { audience_snapshot: { student_ids: ['student-a'] } } : {}),
  });
  if (options.criticalIssue) lessons[0].period = 9;
  let receipts: Record<
    string,
    Array<{ id: string; type: string; reason: string; lesson_ids: string[] }>
  > = {};
  const actions: Record<string, unknown>[] = [];
  let staleNextApply = options.staleNextApply || false;
  const draft = () => ({
    id,
    name: 'Разбор школьного расписания',
    revision,
    payload: {
      days: [0],
      slots: [{ start: '09:00', end: '09:45' }],
      classes: ['class-a', 'class-b', ...(options.thirdConflict ? ['class-c'] : [])],
      lessons,
    },
  });
  await page.route(`${API}/api/admin/**`, async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/experiments/capabilities'))
      return route.fulfill({
        json: {
          demo_available: false,
          school_available: true,
          consolidated_schedule: true,
        },
      });
    if (path === '/api/admin/schedule/experiments/school')
      return route.fulfill({
        json: {
          items: [
            {
              id,
              name: 'Разбор школьного расписания',
              revision,
              is_primary_template: true,
              draft_kind: 'school',
              lesson_count: 2,
              updated_at: '2026-10-29T08:00:00Z',
            },
          ],
        },
      });
    if (path.endsWith('/school/directory'))
      return route.fulfill({
        json: {
          groups: [
            {
              id: 'class-a',
              name: '10А',
              display_name: '10А',
              group_type: 'class',
              base_class_name: '10',
            },
            {
              id: 'class-b',
              name: '11А',
              display_name: '11А',
              group_type: 'class',
              base_class_name: '11',
            },
            ...(options.thirdConflict ? [{
              id: 'class-c',
              name: '12А',
              display_name: '12А',
              group_type: 'class',
              base_class_name: '12',
            }] : []),
          ],
          teachers: [
            { id: 'teacher-a', display_name: 'Преподаватель примера' },
          ],
          students: [
            { id: 'student-a', name: 'Ученик примера' },
            { id: 'student-b', name: 'Ученица примера' },
          ],
          rooms: ['101', '202'],
        },
      });
    if (path.endsWith('/school/audience-preview'))
      return route.fulfill({
        json: {
          lessons: {
            'lesson-a': {
              status: 'ok',
              student_ids: ['student-a'],
              proposed_student_ids: options.needsRosterReview
                ? ['student-a', 'student-b']
                : ['student-a'],
              needs_recalculation: options.needsRosterReview || false,
              reason: options.needsRosterReview
                ? 'Состав по текущему правилу отличается.'
                : undefined,
            },
            'lesson-b': { status: 'ok', student_ids: ['student-b'] },
            ...(options.thirdConflict ? { 'lesson-c': { status: 'ok', student_ids: ['student-a'] } } : {}),
          },
        },
      });
    if (path.endsWith(`/school/${id}`)) return route.fulfill({ json: draft() });
    if (path.endsWith('/resolution/preview')) {
      const body = route.request().postDataJSON();
      return route.fulfill({
        json: {
          revision,
          preview_token: `token-${revision}`,
          lesson_ids: body.lesson_ids,
          lessons: lessons.filter((l) => body.lesson_ids.includes(l.id)),
          rooms:
            body.lesson_ids.length === 1
              ? [
                  { room: '101', available: false },
                  { room: '202', available: true },
                ]
              : [],
          exceptions: receipts,
          audiences: options.needsRosterReview
            ? {
                'lesson-a': {
                  status: 'ok',
                  student_ids: ['student-a'],
                  proposed_student_ids: ['student-a', 'student-b'],
                  needs_recalculation: true,
                  reason: 'Состав по текущему правилу отличается.',
                },
              }
            : {},
          roster: {
            affected_lessons: options.needsRosterReview
              ? Array.from({ length: 26 }, (_, index) => ({
                  id: index === 0 ? 'lesson-a' : `affected-${index + 1}`,
                  subject: index === 0 ? 'Алгебра' : 'Математика',
                  day: index % 5,
                  start: '09:00',
                  end: '09:45',
                  group_ids: ['class-a'],
                  group_names: ['10А'],
                  source_cell: `строка ${index + 2}, колонка ${index % 6 + 1}`,
                  old_count: 24,
                  current_count: 25,
                }))
              : [],
          },
        },
      });
    }
    if (path.endsWith('/resolution/apply')) {
      const body = route.request().postDataJSON();
      actions.push(body);
      if (staleNextApply) {
        staleNextApply = false;
        return route.fulfill({
          status: 409,
          json: { detail: 'Предпросмотр устарел. Повторите проверку.' },
        });
      }
      if (body.preview_token !== `token-${revision}`)
        return route.fulfill({
          status: 409,
          json: { detail: 'Ревизия изменилась' },
        });
      if (body.action === 'room') {
        lessons.find((l) => l.id === body.lesson_ids[0])!.room = body.room;
        receipts = {};
      } else if (body.action === 'revoke') {
        receipts = {};
      } else {
        for (const lid of body.lesson_ids)
          receipts[lid] = [
            {
              id: 'receipt-a',
              type: body.action,
              reason: body.reason,
              lesson_ids: body.lesson_ids,
            },
          ];
      }
      revision++;
      return route.fulfill({ json: draft() });
    }
    if (path.includes('/teacher-ui-colors') || path.includes('/activities'))
      return route.fulfill({ json: { items: [] } });
    if (path.includes('/observability'))
      return route.fulfill({
        json: {
          blocks: [],
          weeks: [],
          effective_weeks: [],
          week_publications: [],
        },
      });
    return route.continue();
  });
  await page.goto('/admin/schedule/constructor?role=admin&editor=1');
  await page
    .getByRole('button', { name: 'Открыть и редактировать', exact: true })
    .click();
  await page.locator('.constructor-issue-count').click();
  await expect(
    page.getByText('Центр решений расписания', { exact: true }),
  ).toBeVisible();
  return { actions };
}
async function openIssue(page: Page, type: 'room' | 'teacher') {
  const item = page
    .locator('.constructor-conflict-list article')
    .filter({
      hasText:
        type === 'room' ? 'Кабинет 101 занят' : 'Преподаватель уже ведёт урок',
    })
    .first();
  await item.getByRole('button', { name: 'Разобрать проблему' }).click();
  return page.getByRole('dialog', {
    name:
      type === 'room'
        ? 'Кабинет и место проведения'
        : 'Назначение преподавателя',
  });
}

test('room correction offers only free rooms, applies after confirmation and supports undo', async ({
  page,
}) => {
  const { actions } = await setup(page);
  const dialog = await openIssue(page, 'room');
  await expect(
    dialog.getByRole('button', { name: '202', exact: true }),
  ).toBeVisible();
  await expect(
    dialog.getByRole('button', { name: '101', exact: true }),
  ).toHaveCount(0);
  await dialog.getByRole('button', { name: '202', exact: true }).click();
  await dialog
    .getByLabel('Основание изменения')
    .fill('Проверено расписание кабинетов');
  await dialog
    .getByRole('button', { name: 'Проверить и применить кабинет' })
    .click();
  await expect(dialog).not.toBeVisible();
  expect(actions[0]).toMatchObject({
    action: 'room',
    room: '202',
    lesson_ids: ['lesson-a'],
    expected_revision: 1,
  });
  await expect(
    page.locator('.constructor-conflict-list').getByText(/Кабинет 101 занят/),
  ).toHaveCount(0);
  await page.getByRole('button', { name: 'Отменить', exact: true }).click();
  await expect(
    page
      .locator('.constructor-conflict-list')
      .getByText(/Кабинет 101 занят/)
      .first(),
  ).toBeVisible();
});

test('joint confirmation leaves unresolved list, can be reviewed and revoked', async ({
  page,
}) => {
  const { actions } = await setup(page, { thirdConflict: true });
  const dialog = await openIssue(page, 'teacher');
  await dialog
    .getByRole('button', { name: 'Это одно совместное занятие' })
    .click();
  await dialog
    .getByLabel('Основание решения')
    .fill('Совместная математика 10–11 в кабинете 101');
  await dialog.getByRole('checkbox').check();
  await dialog
    .getByRole('button', { name: 'Подтвердить решение', exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  expect(actions[0]).toMatchObject({
    action: 'joint',
    lesson_ids: ['lesson-a', 'lesson-b'],
  });
  await expect(
    page
      .locator('.constructor-issue-category')
      .filter({ hasText: 'Требуют решения администратора' }),
  ).toBeVisible();
  await expect(
    page.locator('.constructor-issue-category')
      .filter({ hasText: 'Требуют решения администратора' })
      .locator('.constructor-conflict-list article')
      .filter({ hasText: 'Преподаватель уже ведёт урок' }),
  ).toHaveCount(2);
  await expect(
    page.locator('.constructor-issue-category')
      .filter({ hasText: 'Подтверждённые исключения' })
      .locator('.constructor-conflict-list article')
      .filter({ hasText: 'Преподаватель уже ведёт урок' }),
  ).toHaveCount(1);
  await page
    .getByText('Подтверждённые решения и отложенные записи', { exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Рассмотреть или отменить' })
    .first()
    .click();
  await page
    .getByRole('button', { name: 'Подготовить отмену' })
    .first()
    .click();
  await page.getByRole('checkbox').check();
  await page
    .getByRole('button', { name: 'Отменить это подтверждение' })
    .first()
    .click();
  await expect(
    page.getByRole('dialog', { name: 'Назначение преподавателя' }),
  ).not.toBeVisible();
  await expect(
    page
      .locator('.constructor-issue-category')
      .filter({ hasText: 'Требуют решения администратора' }),
  ).toBeVisible();
});

test('independent practice is recorded only after explicit confirmation', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const { actions } = await setup(page);
  const dialog = await openIssue(page, 'teacher');
  await dialog
    .getByRole('button', { name: 'Самостоятельная практика без присутствия преподавателя' })
    .click();
  await expect(
    dialog.getByText(/Ответственный преподаватель останется связан/),
  ).toBeVisible();
  await dialog.locator('.constructor-resolution-confirm').scrollIntoViewIfNeeded();
  await page.screenshot({
    path: '.local-test/artifacts/resolution-ui/practice-confirm-mobile.png',
    fullPage: false,
  });
  await dialog.getByLabel('Основание решения').fill('Группа выполняет самостоятельную работу по плану учителя');
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: 'Подтвердить решение' }).click();
  await expect(dialog).not.toBeVisible();
  expect(actions[0]).toMatchObject({
    action: 'independent_practice',
    lesson_ids: ['lesson-a'],
    reason: 'Группа выполняет самостоятельную работу по плану учителя',
  });
});

test('audience review exposes all 26 safe bulk targets and links to the existing group directory', async ({
  page,
}) => {
  const { actions } = await setup(page, { needsRosterReview: true, thirdConflict: true });
  const item = page
    .locator('.constructor-conflict-list article')
    .filter({ hasText: 'Состав учеников отличается от сохранённого' })
    .first();
  await item.getByRole('button', { name: 'Разобрать проблему' }).click();
  const dialog = page.getByRole('dialog', { name: 'Проверка участников' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Проверить пересчёт участников' }).click();
  const affected = dialog.locator('.constructor-resolution-confirm ul li');
  await expect(affected).toHaveCount(26);
  await expect(affected.first()).toContainText('Алгебра');
  await expect(affected.first()).toContainText('Понедельник · 09:00–09:45 · 10А');
  await expect(affected.first()).toContainText('строка 2, колонка 1');
  await expect(affected.first()).toContainText('24 → 25 участников');
  await expect(affected.first().getByRole('link', { name: 'Открыть группу' })).toHaveAttribute(
    'href',
    '/admin/people/groups?group_id=class-a',
  );
  await expect(affected.first()).not.toContainText(/affected-|class-a|student-/);
  await dialog.getByLabel('Основание решения').fill('Сверено с составом группы и сохранённым снимком');
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: 'Подтвердить решение' }).click();
  await expect(dialog).not.toBeVisible();
  expect(actions[0]).toMatchObject({ action: 'confirm_roster' });
  expect((actions[0].lesson_ids as string[]).length).toBe(26);
  await expect(
    page.locator('.constructor-issue-category').filter({ hasText: 'Требуют решения администратора' })
      .getByText(/Учеников назначено одновременно/),
  ).toBeVisible();
  await expect(page.locator('.constructor-conflict-list').getByText('student-a', { exact: true })).toHaveCount(0);
});

test('defer and stale preview keep the issue visible without a false success', async ({ page }) => {
  const { actions } = await setup(page, { staleNextApply: true });
  const dialog = await openIssue(page, 'teacher');
  await dialog.getByRole('button', { name: 'Отложить решение' }).click();
  await dialog.getByLabel('Основание решения').fill('Нужно сверить исходный бумажный план');
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: 'Подтвердить решение' }).click();
  await expect(dialog.getByRole('alert')).toContainText('Предпросмотр устарел. Повторите проверку.');
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Подтвердить решение' })).toBeDisabled();
  expect(actions[0]).toMatchObject({ action: 'defer' });
  await dialog.getByRole('button', { name: 'Перечитать актуальную ревизию' }).click();
  await expect(dialog.getByRole('alert')).toHaveCount(0);
});

test('critical structural issues stay in their own category and cannot be deferred', async ({ page }) => {
  await setup(page, { criticalIssue: true });
  const critical = page.locator('.constructor-issue-category').filter({ hasText: 'Критические технические проблемы' });
  await expect(critical).toBeVisible();
  await expect(critical).toContainText('День или время занятия не входят в шаблон.');
  await critical.getByRole('button', { name: 'Разобрать проблему' }).click();
  const dialog = page.getByRole('dialog', { name: 'Уточнение занятия' });
  await expect(dialog.getByRole('alert')).toContainText('Критическая ошибка структуры блокирует публикацию.');
  await expect(dialog.getByRole('button', { name: 'Отложить решение' })).toHaveCount(0);
  await expect(dialog.getByRole('button', { name: 'Исправить занятие' })).toBeVisible();
});

test('diagnostic context renders on desktop and mobile with truthful room limits', async ({
  page,
}) => {
  await mkdir('.local-test/artifacts/resolution-ui', { recursive: true });
  await setup(page);
  await page.screenshot({
    path: '.local-test/artifacts/resolution-ui/diagnostics-desktop.png',
    fullPage: true,
  });
  const dialog = await openIssue(page, 'teacher');
  await expect(
    dialog.getByText(
      'Свободен по расписанию не означает фактическую доступность.',
      { exact: false },
    ),
  ).toBeVisible();
  await page.screenshot({
    path: '.local-test/artifacts/resolution-ui/context-desktop.png',
    fullPage: true,
  });
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(dialog).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(375);
  await page.screenshot({
    path: '.local-test/artifacts/resolution-ui/context-mobile.png',
    fullPage: true,
  });
  await page.setViewportSize({ width: 844, height: 390 });
  console.log('[resolution-ui] viewport switched to landscape');
  await expect(dialog).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(844);
  await page.screenshot({
    path: '.local-test/artifacts/resolution-ui/context-landscape.png',
    fullPage: false,
  });
});
