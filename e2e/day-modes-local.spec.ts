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
  await page.getByRole('button', { name: 'Неделя', exact: true }).click();

  await page.getByRole('button', { name: /6 класс, Среда: Обычный\. Изменить режим дня/ }).click();
  await page.getByLabel(/Режим дня 6 класс Среда/).selectOption('online');
  await page.getByRole('button', { name: /10 класс, Пятница: Обычный\. Изменить режим дня/ }).click();
  await page.getByLabel(/Режим дня 10 класс Пятница/).selectOption('sdep');
  await page.screenshot({ path: '.local-test/day-modes-week-view.png', fullPage: true });
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
  await expect(page.getByRole('button', { name: /6 класс, Среда: Онлайн\. Изменить режим дня/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /10 класс, Пятница: SDEP\. Изменить режим дня/ })).toBeVisible();
  await page.screenshot({ path: '.local-test/day-modes-reopened.png', fullPage: true });
});
