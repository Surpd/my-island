import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

const API = 'http://localhost:8011';
const admin = { 'X-Dev-Auth': 'admin:1' };

test('weekday modes remain saved after reopening the constructor draft', async ({ page, request }) => {
  await mkdir('.local-test', { recursive: true });
  await page.goto('/admin/schedule?role=admin');
  const createdDraftResponse = page.waitForResponse(response => response.url().endsWith('/api/admin/schedule/experiments') && response.request().method() === 'POST');
  await page.getByRole('button', { name: /Пустая школьная неделя/ }).click();
  const createdDraft = await (await createdDraftResponse).json();
  await expect(page.locator('.constructor-grid-wrap')).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.screenshot({ path: '.local-test/day-modes-day-1280.png', fullPage: true });
  await expect(page.locator('.constructor-head .constructor-day-mode-button[aria-label^="7 класс,"]')).toHaveCount(1);
  await expect(page.locator('.constructor-head .constructor-day-mode-button[aria-label^="9 класс,"]')).toHaveCount(1);

  await page.getByRole('button', { name: 'Среда', exact: true }).click();
  await page.getByRole('button', { name: /6 класс, Среда: Обычный\. Изменить режим дня/ }).click();
  await page.getByRole('group', { name: 'Выбор режима 6 класс · Среда' }).getByRole('button', { name: 'Онлайн' }).click();
  await page.getByRole('button', { name: 'Неделя', exact: true }).click();
  await expect(page.locator('.constructor-head .constructor-day-mode-button')).toHaveCount(0);
  await page.screenshot({ path: '.local-test/day-modes-week-1280.png', fullPage: true });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole('button', { name: 'Режимы дней', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Режимы дней по параллелям' })).toBeVisible();
  await expect(page.locator('#constructor-day-modes-matrix tbody tr')).toHaveCount(7);
  await page.screenshot({ path: '.local-test/day-modes-matrix-1440.png', fullPage: true });
  await page.getByRole('button', { name: /10 класс, Пятница: Обычный\. Изменить режим дня/ }).click();
  await page.getByRole('group', { name: 'Выбор режима 10 класс · Пятница' }).getByRole('button', { name: 'SDEP' }).click();
  await page.getByRole('button', { name: 'Сохранить черновик' }).click();
  await expect(page.locator('header').getByText('Сохранено', { exact: true })).toBeVisible();

  await page.reload();
  await page.getByRole('button', { name: 'Мои черновики', exact: false }).click();
  const draftRow = page.locator('.constructor-draft').filter({ hasText: `ID ${createdDraft.id}` });
  const draftOpen = draftRow.locator('.constructor-draft-open');
  await expect(draftOpen).toBeVisible();
  await draftOpen.click();
  await expect(page.locator('.constructor-grid-wrap')).toBeVisible();
  await page.getByRole('button', { name: 'Неделя', exact: true }).click();
  await page.getByRole('button', { name: 'Режимы дней', exact: true }).click();
  await expect(page.getByRole('button', { name: /6 класс, Среда: Онлайн\. Изменить режим дня/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /10 класс, Пятница: SDEP\. Изменить режим дня/ })).toBeVisible();
  await page.screenshot({ path: '.local-test/day-modes-reopened-1440.png', fullPage: true });
});
