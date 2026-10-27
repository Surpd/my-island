import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';

const API = 'http://localhost:8011';
const ADMIN = { 'X-Dev-Auth': 'admin:1' };
const shots = resolve('.local-test/audience-architecture-shots');

test('one subject and teacher work with seventh-grade split and administrative class', async ({ page }) => {
  test.setTimeout(45_000);
  await mkdir(shots, { recursive: true });
  const groups = (await (await page.request.get(`${API}/api/admin/groups`, { headers: ADMIN })).json()).items as Array<{ id: string; name: string }>;
  const teachers = (await (await page.request.get(`${API}/api/admin/people?kind=teacher`, { headers: ADMIN })).json()).items as Array<{ id: string; display_name: string }>;
  const split = groups.find((item) => item.name === 'grade7:split:1');
  const klass = groups.find((item) => item.name === 'grade7:base:7-А');
  const teacher = teachers.find((item) => item.display_name === 'Тестовый преподаватель');
  expect(split && klass && teacher).toBeTruthy();
  const assignment = await page.request.post(`${API}/api/admin/teacher-assignments`, { headers: ADMIN, data: { teacher_identity_id: String(teacher!.id), grade_level: '7', subject: 'Литература', active: true } });
  expect(assignment.ok(), await assignment.text()).toBeTruthy();

  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto('/admin/schedule/constructor?role=admin');
  await expect(page.getByRole('heading', { name: 'Конструктор' })).toBeVisible();
  await page.getByRole('button', { name: /Пустая школьная неделя/ }).click();
  await page.getByRole('button', { name: 'Фильтры' }).click();
  await page.getByRole('combobox', { name: 'Фильтр по классу' }).selectOption(String(klass!.id));
  await page.locator('[data-cell]').first().click();
  await page.getByRole('button', { name: 'Форма', exact: true }).click();
  await page.getByRole('combobox', { name: 'Название активности' }).fill('Литература');
  const picker = page.getByRole('region', { name: 'Участники занятия' });
  await picker.getByRole('button', { name: /Учебные группы/ }).click();
  await picker.locator(`input[type="checkbox"][value="${split!.id}"]`).check();
  await expect(page.getByRole('combobox', { name: 'Преподаватель нового занятия' })).toHaveValue(teacher!.display_name);
  await page.screenshot({ path: resolve(shots, 'constructor-split-and-teacher-1920x1080.png'), fullPage: true });
  await page.getByRole('button', { name: 'Добавить занятие' }).click();
  await expect(page.locator('.constructor-inspector').getByRole('heading', { name: 'Литература' })).toBeVisible();
  await picker.getByRole('button', { name: 'Весь класс или параллель' }).click();
  await picker.getByRole('combobox', { name: 'Кто занимается' }).selectOption(String(klass!.id));
  await expect(page.locator('.constructor-inspector').getByRole('combobox', { name: 'Предмет', exact: true })).toHaveValue('Литература');
  await expect(page.locator('.constructor-inspector label').filter({ hasText: 'Преподаватель' }).locator('select')).toHaveValue(String(teacher!.id));
  await page.getByRole('button', { name: /Сохранить/ }).first().click();
  await expect(page.locator('header').getByText('Сохранено', { exact: true })).toBeVisible();
  await page.screenshot({ path: resolve(shots, 'constructor-class-and-teacher-1920x1080.png'), fullPage: true });
});

test('exact English group suggests its teacher and a lesson may override that teacher', async ({ page }) => {
  test.setTimeout(45_000);
  await mkdir(shots, { recursive: true });
  const groups = (await (await page.request.get(`${API}/api/admin/groups`, { headers: ADMIN })).json()).items as Array<{ id: string; name: string }>;
  const teachers = (await (await page.request.get(`${API}/api/admin/people?kind=teacher`, { headers: ADMIN })).json()).items as Array<{ id: string; display_name: string; teacher_assignments: Array<{ group_id: string; subject: string }> }>;
  const group = groups.find((item) => item.name === 'english:6');
  const klass = groups.find((item) => item.name === '9-Ан');
  const assigned = teachers.find((item) => item.display_name === 'Педагог 9-1');
  const substitute = teachers.find((item) => item.display_name === 'Тестовый преподаватель');
  expect(group && klass && assigned && substitute).toBeTruthy();
  const response = await page.request.post(`${API}/api/admin/teacher-assignments`, { headers: ADMIN, data: { teacher_identity_id: String(assigned!.id), group_id: String(group!.id), subject: 'Английский язык', active: true } });
  expect(response.ok(), await response.text()).toBeTruthy();

  await page.setViewportSize({ width: 1366, height: 768 });
  await page.goto('/admin/schedule/constructor?role=admin');
  await page.getByRole('button', { name: /Пустая школьная неделя/ }).click();
  await page.getByRole('button', { name: 'Фильтры' }).click();
  await page.getByRole('combobox', { name: 'Фильтр по классу' }).selectOption(String(klass!.id));
  await page.locator('[data-cell]').first().click();
  await page.getByRole('button', { name: 'Форма', exact: true }).click();
  await page.getByRole('combobox', { name: 'Название активности' }).fill('Английский язык');
  const picker = page.getByRole('region', { name: 'Участники занятия' });
  await picker.getByRole('button', { name: /Учебные группы/ }).click();
  await picker.getByRole('checkbox', { name: /^Английский язык · группа 6\b/u }).check();
  const teacherSelect = page.getByRole('combobox', { name: 'Преподаватель нового занятия' });
  await expect(teacherSelect).toHaveValue(assigned!.display_name);
  await page.screenshot({ path: resolve(shots, 'constructor-english-default-1366x768.png'), fullPage: true });
  await teacherSelect.fill(substitute!.display_name);
  await page.getByRole('button', { name: 'Добавить занятие' }).click();
  await expect(page.locator('.constructor-inspector label').filter({ hasText: 'Преподаватель' }).locator('select')).toHaveValue(String(substitute!.id));
  const current = (await (await page.request.get(`${API}/api/admin/people?kind=teacher`, { headers: ADMIN })).json()).items as typeof teachers;
  expect(current.find((item) => String(item.id) === String(assigned!.id))?.teacher_assignments.some((item) => String(item.group_id) === String(group!.id) && item.subject === 'Английский язык')).toBeTruthy();
  await page.screenshot({ path: resolve(shots, 'constructor-english-substitute-1366x768.png'), fullPage: true });
});
