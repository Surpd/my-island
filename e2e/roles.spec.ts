import { expect, test } from '@playwright/test';

const API = 'http://127.0.0.1:8011';

test('Admin authenticates locally, reads API, and navigates key sections', async ({ page }) => {
  const responses: number[] = [];
  page.on('response', (response) => {
    if (response.url().startsWith(API)) responses.push(response.status());
  });
  await page.goto('/admin/schedule?role=admin');
  await expect(page.getByRole('heading', { name: 'Конструктор расписания' })).toBeVisible();
  await expect.poll(() => responses.filter((status) => status === 200).length).toBeGreaterThan(2);

  await page.locator('.admin-console-sidebar nav button').nth(2).click();
  await expect(page).toHaveURL(/\/admin\/people/);
  await expect(page.getByRole('heading', { name: 'Ученики и группы' }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Создать группу' })).toBeVisible();

  await page.getByRole('button', { name: 'Преподаватели', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Преподаватели' })).toBeVisible();

  await page.getByRole('button', { name: 'Настройки', exact: true }).click();
  await expect(page).toHaveURL(/\/admin\/settings/);
  await expect(page.getByRole('heading', { name: 'Настройки' })).toBeVisible();
  await expect(page.getByText('исправно доступен')).toBeVisible();
  await expect(page.getByText('локальный режим')).toBeVisible();
});

test('Teacher and student load their interfaces; student cannot access admin API', async ({ page }) => {
  const duplicateKeyWarnings: string[] = [];
  const pageErrors: string[] = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error' && /same key|duplicate key/i.test(message.text())) {
      duplicateKeyWarnings.push(message.text());
    }
  });
  await page.goto('/?role=teacher');
  await expect(page.getByRole('button', { name: /Расписание/ }).first()).toBeVisible();
  await page.getByRole('button', { name: /Расписание/ }).first().click();
  await expect(page.getByText('Расписание', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('Ближайших занятий нет')).toBeVisible();

  await page.goto('/');
  await expect(page.getByRole('button', { name: /Расписание/ }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Расписание', exact: true }).first().click();
  await page.getByRole('button', { name: 'Развернуть раздел', exact: true }).click();
  await expect(page.getByText('Занятий нет', { exact: true })).toBeVisible();
  await page.locator('.scene-focus-back').click();
  await page.locator('.profile-button').click();
  await expect(page.getByRole('heading', { name: 'Тестовый ученик' })).toBeVisible();
  await expect(page.getByText('Мои группы', { exact: true })).toBeVisible();
  await expect(page.locator('.group-card').first()).toBeVisible();
  await expect.poll(() => duplicateKeyWarnings).toEqual([]);
  expect(pageErrors).toEqual([]);
  const adminResponse = await page.request.get(`${API}/api/admin/groups`, { headers: { 'X-Dev-Auth': 'student:1' } });
  expect(adminResponse.status()).toBe(403);
  await expect(page.getByRole('button', { name: /Администрирование|Admin/ })).toHaveCount(0);
});
