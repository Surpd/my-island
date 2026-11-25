import { expect, type Page } from '@playwright/test';

export async function personRevision(page: Page, id: string | number): Promise<number> {
  const response = await page.request.get(`http://localhost:8011/api/admin/people/${id}`, {
    headers: { 'X-Dev-Auth': 'admin:1' },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).revision;
}
