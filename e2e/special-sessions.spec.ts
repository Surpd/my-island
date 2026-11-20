import { test } from './helpers/experiment-test';
import { expect } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

test('лаборатория в конструкторе на трёх ширинах', async ({ page }) => {
  await page.goto('/admin/schedule/constructor?role=admin');
  await page.getByRole('button', { name: /Пустая школьная неделя/ }).click();
  await page.locator('.constructor-draft-menu summary').click();
  await page.getByRole('button', { name: 'Лаборатории', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Лаборатории' });
  await dialog
    .locator('article')
    .filter({ hasText: 'Прошлое и настоящее' })
    .getByRole('button', { name: 'Настроить' })
    .click();
  await dialog
    .getByLabel('Ведущий', { exact: true })
    .selectOption({ index: 1 });
  await dialog.getByLabel('Классы', { exact: true }).selectOption({ index: 1 });
  await dialog.getByText('Дополнительные настройки', { exact: true }).click();
  await dialog.getByLabel('Кабинет или площадка').fill('205');
  await dialog.getByText('Дополнительные настройки', { exact: true }).click();
  await mkdir('artifacts/special-sessions', { recursive: true });
  for (const width of [1366, 390, 320]) {
    await page.setViewportSize({ width, height: 850 });
    await expect(dialog.getByLabel('Тема', { exact: true })).toHaveValue(
      'Прошлое и настоящее',
    );
    expect(
      await dialog.evaluate((d) => d.scrollWidth <= d.clientWidth + 1),
    ).toBeTruthy();
    await page.screenshot({
      path: `artifacts/special-sessions/laboratory-${width}.png`,
    });
  }
  await dialog.getByRole('button', { name: 'Сохранить заготовку' }).click();
  await expect(dialog).toBeHidden();
});

test('Недельная лаборатория — предпросмотр, активация и восстановление', async ({
  page,
  request,
  registerExperiment,
}) => {
  const base = 'http://127.0.0.1:8011/api/admin';
  const headers = { 'X-Dev-Auth': 'admin:1' };
  const groups = (
    await (await request.get(base + '/groups', { headers })).json()
  ).items;
  const staff = (
    await (await request.get(base + '/people?kind=teacher', { headers })).json()
  ).items;
  const students = (
    await (await request.get(base + '/people?kind=student', { headers })).json()
  ).items;
  const classId = String(
    groups.find((group: { group_type: string }) => group.group_type === 'class')
      .id,
  );
  const pupil = String(students[0].id);
  const baseLesson = {
    id: 'history',
    subject: 'История искусства',
    classId,
    classIds: [classId],
    day: 1,
    period: 0,
    room: '101',
    teacherId: String(staff[0].id),
    audience: { kind: 'students', student_ids: [pupil] },
  };
  const blueprint = {
    ...baseLesson,
    id: 'studio',
    subject: 'Прошлое и настоящее',
    direction: 'История',
    description: 'Сравнение исторических источников',
    teacherIds: [],
    format: 'offline',
    start: '09:55',
    end: '11:40',
  };
  const name = 'Демонстрационная неделя лаборатории';
  const created = await request.post(base + '/schedule/experiments', {
    headers,
    data: {
      source: 'import',
      name,
      payload: {
        draft_kind: 'test',
        days: [1],
        classes: [classId],
        slots: [
          { start: '09:55', end: '10:40' },
          { start: '10:55', end: '11:40' },
        ],
        week_start: '2027-02-01',
        lessons: [
          baseLesson,
          { ...baseLesson, id: 'literature', subject: 'Литература', period: 1 },
          {
            ...baseLesson,
            id: 'help',
            subject: 'Консультация по истории',
            activityType: 'consultation',
          },
        ],
        laboratory_blueprints: [blueprint],
      },
    },
  });
  expect(created.ok()).toBeTruthy();
  const draft = await created.json();
  registerExperiment(draft.id);
  await page.goto('/admin/schedule/constructor?role=admin');
  await page.getByRole('button', { name: /^Тестовые ·/ }).click();
  await page.getByRole('button', { name: new RegExp(name) }).click();
  await page.locator('.constructor-draft-menu summary').click();
  await page.getByRole('button', { name: 'Лаборатории', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Лаборатории' });
  await expect(dialog.getByRole('switch')).toHaveCount(3);
  await dialog.getByRole('switch', { name: 'Прошлое и настоящее' }).click();
  await expect(
    dialog.getByRole('button', { name: 'Провести на этой неделе' }),
  ).toHaveCount(0);
  await dialog.getByRole('button', { name: 'Открыть занятие' }).first().click();
  await expect(dialog).toBeHidden();
  await page
    .getByRole('button', { name: 'К проверке', exact: true })
    .first()
    .click();
  await dialog
    .getByLabel('Ведущий', { exact: true })
    .selectOption(String(staff[1].id));
  await dialog.getByText('Дополнительные настройки', { exact: true }).click();
  await dialog.getByLabel('Кабинет или площадка').fill('205');
  await dialog
    .getByRole('button', { name: 'Посмотреть замещение уроков' })
    .click();
  await expect(dialog.locator('section article')).toHaveCount(2);
  await dialog
    .getByRole('button', { name: 'Провести на этой неделе' })
    .scrollIntoViewIfNeeded();
  await mkdir('artifacts/special-sessions', { recursive: true });
  await page.screenshot({
    path: 'artifacts/special-sessions/weekly-preview.png',
  });
  await dialog.getByRole('button', { name: 'Провести на этой неделе' }).click();
  await expect(dialog.getByText('Неделя сохранена')).toBeVisible();
  await page.screenshot({
    path: 'artifacts/special-sessions/weekly-enabled.png',
  });
  const read = async () =>
    (
      await (
        await request.get(base + '/schedule/experiments/' + draft.id, {
          headers,
        })
      ).json()
    ).payload.lessons;
  expect(
    (await read()).map((lesson: { subject: string }) => lesson.subject),
  ).toEqual(['Консультация по истории', 'Прошлое и настоящее']);
  await dialog.getByRole('switch', { name: 'Прошлое и настоящее' }).click();
  await dialog
    .getByRole('button', { name: 'Отключить и восстановить уроки' })
    .click();
  await expect
    .poll(async () => (await read()).map((lesson: { id: string }) => lesson.id))
    .toEqual(['history', 'literature', 'help']);
  await expect(
    dialog.getByRole('switch', { name: 'Прошлое и настоящее' }),
  ).toHaveAttribute('aria-checked', 'false');
  await page.screenshot({
    path: 'artifacts/special-sessions/weekly-restored.png',
  });
});

for (const role of ['student', 'teacher'] as const) {
  test(`Campus · ${role} · специальные занятия`, async ({ page }) => {
    const day = '2026-10-26';
    const consultation = {
      date: day,
      start_time: '09:55',
      end_time: '10:40',
      state: 'ACTIVITY',
      activity: 'Математика',
      activity_type: 'consultation',
      delivery_mode: 'online',
      teacher_names: ['Педагог E2E'],
    };
    const practice = {
      date: day,
      start_time: '09:00',
      end_time: '09:45',
      state: 'ACTIVITY',
      activity: 'Обществознание ОГЭ',
      activity_type: 'practice',
      activity_format: 'independent_practice',
      room: '205',
      responsible_teachers: ['Педагог E2E'],
      physical_presence: false,
    };
    const lab = {
      date: day,
      start_time: '10:55',
      end_time: '12:35',
      state: 'ACTIVITY',
      activity: 'Прошлое и настоящее',
      activity_type: 'laboratory',
      room: '102',
    };
    const rows = [
      practice,
      consultation,
      lab,
      ...(role === 'student'
        ? [
            {
              date: day,
              start_time: '12:45',
              end_time: '13:30',
              state: 'UNRESOLVED',
              activities: [{ activity: 'Английский' }, { activity: 'Физика' }],
            },
          ]
        : []),
    ];
    await page.route('**/api/**', async (route) => {
      const path = new URL(route.request().url()).pathname;
      const body = path.endsWith('/auth/session')
        ? {
            mode: 'e2e',
            state: 'approved',
            user: { id: 'synthetic', role, roles: [role] },
          }
        : path.endsWith('/profile')
          ? {
              state: 'approved',
              user: { display_name: 'Демонстрационный профиль' },
              groups: [],
            }
          : path.endsWith('/home') || path.endsWith('/today')
            ? {
                schedule: rows,
                groups: [],
                information: [],
                homework: [],
                announcements: [],
              }
            : {
                state: 'published',
                items: path.endsWith('/schedule') ? rows : [],
              };
      await route.fulfill({ json: body });
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/?role=' + role + '&date=' + day);
    await page.locator('.scene-hotspot.hotspot--schedule').click();
    await page
      .getByRole('button', { name: 'Развернуть раздел', exact: true })
      .click();
    const list = page
      .locator(role === 'student' ? '.student-schedule-stack' : '.lesson-stack')
      .last();
    await expect(list).toContainText('Лаборатория');
    await expect(list).toContainText(
      role === 'student' ? 'по желанию' : 'Консультация',
    );
    if (role === 'student')
      await expect(list.locator('.student-schedule-conflict')).toHaveCount(2);
    await mkdir('artifacts/special-sessions', { recursive: true });
    await page.screenshot({
      path: `artifacts/special-sessions/campus-${role}.png`,
    });
  });
}
