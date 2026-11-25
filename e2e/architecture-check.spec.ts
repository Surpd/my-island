import { expect, test } from '@playwright/test';

test('architecture check reads the real isolated admin sources', async ({ page }) => {
  await page.goto('/admin/architecture-check?role=admin');
  await expect(page.getByRole('heading', { name: 'Проверка серверных разделов' })).toBeVisible();
  for (const [view, responseKey] of [['day', 'publication'], ['teachers', 'teachers'],
    ['classes', 'coverage'], ['journals', 'source_count'], ['widgets', 'widgets']]) {
    await page.getByLabel('Раздел').selectOption(view);
    await page.getByRole('button', { name: 'Загрузить данные' }).click();
    await expect(page.getByLabel('Ответ API')).toContainText('"' + responseKey + '"');
  }
});
