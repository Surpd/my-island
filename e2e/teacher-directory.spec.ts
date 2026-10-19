import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';

const API = 'http://localhost:8011';
const ADMIN = { 'X-Dev-Auth': 'admin:1' };
const shotDir = resolve('.local-test/teacher-editor-shots');

test('teacher list and profile support subjects, canonical audience assignments, schedule and availability states', async ({ page }) => {
  await mkdir(shotDir, { recursive: true });
  const groupsResponse = await page.request.get(`${API}/api/admin/groups`, { headers: ADMIN });
  expect(groupsResponse.ok()).toBeTruthy();
  const groups = (await groupsResponse.json()).items as Array<{ id: string; name: string; group_type: string }>;
  const classes = groups.filter((item) => item.group_type === 'class');
  const klass = classes[0];
  const otherClass = classes.find((item) => item.id !== klass?.id);
  expect(klass, 'isolated test profile should have an existing class group').toBeTruthy();
  expect(otherClass, 'isolated test profile should have another canonical class').toBeTruthy();

  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto('/admin/teachers?role=admin');
  await expect(page.getByRole('heading', { name: 'Преподаватели', level: 1 })).toBeVisible();
  await expect(page.getByRole('table')).toBeVisible();
  await page.screenshot({ path: resolve(shotDir, 'teachers-list-1920x1080.png'), fullPage: true });

  await page.setViewportSize({ width: 1366, height: 768 });
  expect(await page.evaluate(() => document.body.scrollWidth)).toBeLessThanOrEqual(1368);
  await page.screenshot({ path: resolve(shotDir, 'teachers-list-1366x768.png'), fullPage: true });
  await page.setViewportSize({ width: 1920, height: 1080 });

  await page.getByRole('button', { name: 'Создать преподавателя' }).click();
  await page.getByLabel('ФИО').fill('E2E преподаватель редактора');
  await page.getByLabel('Предметы школы').fill('Тестовая физика');
  await page.getByRole('button', { name: /Добавить новое название/ }).click();
  await page.getByLabel('Комментарий').fill('Проверка карточки в изолированной базе');
  await page.getByRole('button', { name: 'Создать преподавателя' }).click();
  await expect(page.getByRole('heading', { name: 'E2E преподаватель редактора', level: 2 })).toBeVisible();
  await expect(page.getByText('Тестовая физика', { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: resolve(shotDir, 'teacher-card-1920x1080.png'), fullPage: true });

  await page.getByRole('button', { name: 'Группы и назначения' }).click();
  await page.getByRole('button', { name: /Добавить назначение/ }).click();
  await page.getByLabel('Предмет').selectOption({ label: 'Тестовая физика' });
  await page.getByLabel('Административный класс').check();
  await page.locator('.teacher-assignment-form').getByRole('combobox', { name: 'Параллель', exact: true }).selectOption(String(klass!.name.match(/^\d+/u)?.[0]));
  await page.locator('.teacher-assignment-form').getByRole('combobox', { name: 'Класс', exact: true }).selectOption(String(klass!.id));
  await page.getByRole('button', { name: 'Сохранить назначение' }).click();
  await expect(page.getByRole('status')).toContainText('Назначение добавлено.');
  await expect(page.locator('.teacher-assignment-list')).toContainText('Тестовая физика');
  await page.getByRole('button', { name: 'Действия с назначением' }).click();
  await expect(page.getByRole('menuitem', { name: 'Изменить назначение' })).toBeVisible();
  await page.getByRole('menuitem', { name: 'Изменить назначение' }).click();
  await page.locator('.teacher-assignment-form').getByRole('combobox', { name: 'Параллель', exact: true }).selectOption(String(otherClass!.name.match(/^\d+/u)?.[0]));
  await page.locator('.teacher-assignment-form').getByRole('combobox', { name: 'Класс', exact: true }).selectOption(String(otherClass!.id));
  await page.getByRole('button', { name: 'Сохранить изменения' }).click();
  await expect(page.getByRole('status')).toContainText('Назначение изменено.');
  const teacherRows = (await (await page.request.get(`${API}/api/admin/people?kind=teacher&include_inactive=true`, { headers: ADMIN })).json()).items as Array<{ id: string; display_name: string; teacher_assignments: Array<{ group_id: string; active: boolean }> }>;
  const savedTeacher = teacherRows.find((item) => item.display_name === 'E2E преподаватель редактора');
  expect(savedTeacher?.teacher_assignments.some((item) => String(item.group_id) === String(otherClass!.id) && item.active)).toBeTruthy();
  await page.screenshot({ path: resolve(shotDir, 'teacher-assignments-1920x1080.png'), fullPage: true });

  await page.getByRole('navigation', { name: 'Карточка преподавателя' }).getByRole('button', { name: 'Расписание' }).click();
  await expect(page.getByText('Базовый canonical-шаблон расписания')).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: resolve(shotDir, 'teacher-schedule-1920x1080.png'), fullPage: true });
  await page.getByRole('navigation', { name: 'Карточка преподавателя' }).getByRole('button', { name: 'Ограничения' }).click();
  await expect(page.getByText('Ограничения доступности')).toBeVisible();
  await page.screenshot({ path: resolve(shotDir, 'teacher-availability-1920x1080.png'), fullPage: true });

  await page.getByRole('button', { name: 'К списку преподавателей' }).click();
  await page.getByPlaceholder('Поиск по ФИО').fill('E2E преподаватель редактора');
  const row = page.locator('.teacher-table tbody tr').filter({ hasText: 'E2E преподаватель редактора' });
  await expect(row).toBeVisible();
  await row.click();
  await expect(page.getByRole('heading', { name: 'E2E преподаватель редактора', level: 2 })).toBeVisible();
  await page.getByRole('button', { name: 'К списку преподавателей' }).click();
  await page.getByRole('button', { name: 'Карточки' }).click();
  const teacherCard = page.locator('.teacher-list-card').filter({ hasText: 'E2E преподаватель редактора' });
  await expect(teacherCard).toBeVisible();
  await teacherCard.click();
  await page.getByRole('button', { name: 'Группы и назначения' }).click();
  await page.locator('.teacher-assignment-list article').getByRole('button', { name: 'Открыть' }).click();
  const groupEditor = page.locator('.school-editor-dialog');
  await expect(groupEditor).toBeVisible();
  await expect(groupEditor.getByRole('heading', { level: 2 })).toContainText(String(otherClass!.name));
});

