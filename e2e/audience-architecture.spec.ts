import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';

const shots = resolve('.local-test/audience-architecture-shots');

test('grade navigation and grade-wide teacher assignment use canonical groups', async ({ page }) => {
  await mkdir(shots, { recursive: true });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto('/admin/people?role=admin');
  await page.getByRole('combobox', { name: 'Класс или параллель' }).selectOption('parallel:7');
  await expect(page.locator('.school-class-shelf > summary')).toContainText('Административные классы');
  for (const name of ['7-А', '7-Б']) await expect(page.locator('.school-class-compact').filter({ hasText: name })).toBeVisible();
  for (const name of ['Математика · 7-1', 'Математика · 7-2']) await expect(page.locator('.school-group-card-main').filter({ hasText: name }).first()).toBeVisible();
  await page.screenshot({ path: resolve(shots, 'grade-7-1920x1080.png'), fullPage: true });
  await page.setViewportSize({ width: 1366, height: 768 });
  expect(await page.evaluate(() => document.body.scrollWidth)).toBeLessThanOrEqual(1368);
  await page.screenshot({ path: resolve(shots, 'grade-7-1366x768.png'), fullPage: true });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.getByRole('combobox', { name: 'Класс или параллель' }).selectOption('parallel:9');
  for (const name of ['9-Ан', '9-Д']) await expect(page.locator('.school-class-compact').filter({ hasText: name })).toBeVisible();
  await expect(page.locator('.school-group-card-main').filter({ hasText: 'Математика · 9-A' })).toBeVisible();
  await page.screenshot({ path: resolve(shots, 'grade-9-1920x1080.png'), fullPage: true });

  await page.goto('/admin/teachers?role=admin');
  await page.getByPlaceholder('Поиск по ФИО').fill('Тестовый преподаватель');
  await page.locator('.teacher-table tbody tr').filter({ hasText: 'Тестовый преподаватель' }).click();
  await page.getByRole('button', { name: 'Группы и назначения' }).click();
  const matchingAssignments = page.locator('.teacher-assignment-list article').filter({
    has: page.locator('strong', { hasText: 'Математика · 7 класс' }),
  });
  const existingMatchingAssignments = await matchingAssignments.count();
  await page.getByRole('button', { name: 'Добавить назначение' }).click();
  await expect(page.getByRole('combobox', { name: 'Предмет', exact: true })).toHaveValue('Математика');
  await page.getByLabel('Вся параллель').check();
  await page.getByRole('combobox', { name: 'Параллель', exact: true }).selectOption('7');
  await page.screenshot({ path: resolve(shots, 'new-grade-assignment-1920x1080.png'), fullPage: true });
  await page.getByRole('button', { name: 'Сохранить назначение' }).click();
  await expect(matchingAssignments).toHaveCount(existingMatchingAssignments + 1);
  const assignment = matchingAssignments.last();
  await assignment.getByRole('button', { name: 'Действия с назначением', exact: true }).click();
  await expect(assignment.getByRole('menuitem', { name: 'Изменить назначение', exact: true })).toBeVisible();
  await expect(assignment).toContainText('Активно');
  await page.screenshot({ path: resolve(shots, 'teacher-assignments-1920x1080.png'), fullPage: true });
});
