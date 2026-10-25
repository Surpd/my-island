import { expect, type APIRequestContext, type Frame, type Page } from '@playwright/test';

export const PREVIEW_API = 'http://127.0.0.1:8011';
export const ADMIN_HEADERS = { 'X-Dev-Auth': 'admin:1' };

export type PreviewPerson = { identity_id: string | number; display_name: string; class_name?: string | null };

export async function previewPeople(request: APIRequestContext, role: 'student' | 'teacher') {
  const response = await request.get(`${PREVIEW_API}/api/admin/previews/people?role=${role}`, { headers: ADMIN_HEADERS });
  expect(response.ok(), `preview people API for ${role}`).toBeTruthy();
  const payload = await response.json();
  return (payload.items ?? payload.people ?? []) as PreviewPerson[];
}

export async function endPreview(request: APIRequestContext, previewId: string) {
  const response = await request.post(`${PREVIEW_API}/api/admin/previews/${encodeURIComponent(previewId)}/end`, { headers: ADMIN_HEADERS, timeout: 5_000 }).catch(() => null);
  if (!response) return;
  expect([200, 204, 404]).toContain(response.status());
}

export async function openPreviewFromAdmin(page: Page) {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  const failedRequests: string[] = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  page.on('requestfailed', (request) => failedRequests.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText || 'failed'}`));
  await page.goto('/admin/preview?role=admin');
  try {
    await expect(page.getByRole('region', { name: 'Настройки предпросмотра' })).toBeVisible({ timeout: 30_000 });
  } catch (cause) {
    const bootState = await page.evaluate(() => ({
      url: location.href,
      readyState: document.readyState,
      text: document.body.innerText.slice(0, 1200),
      scripts: Array.from(document.scripts).map((script) => script.src || '<inline>'),
      scriptResources: performance.getEntriesByType('resource').filter((entry) => /\.js(?:\?|$)/.test(entry.name)).map((entry) => entry.name),
    })).catch(() => null);
    throw new Error(`${cause instanceof Error ? cause.message : String(cause)}\nPreview bootstrap diagnostics: ${JSON.stringify({ pageErrors, consoleErrors, failedRequests, bootState })}`);
  }
}

export async function expectNoHorizontalOverflow(frame: Frame) {
  const sizes = await frame.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  expect(Math.max(sizes.document, sizes.body), `horizontal overflow at ${sizes.viewport}px`).toBeLessThanOrEqual(sizes.viewport + 1);
}
