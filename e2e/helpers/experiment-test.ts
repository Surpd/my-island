import { test as base } from '@playwright/test';

const API = 'http://127.0.0.1:8011';
const ADMIN = { 'X-Dev-Auth': 'admin:1' };

type Fixtures = { registerExperiment: (id: string) => void };

/** Remove only drafts whose IDs this test observed creating. Existing drafts are untouched. */
export const test = base.extend<Fixtures>({
  registerExperiment: [async ({ page, request }, use) => {
    const created = new Set<string>();
    const pending: Promise<void>[] = [];
    page.on('response', (response) => {
      const url = new URL(response.url());
      if (url.origin !== API || response.request().method() !== 'POST' ||
          !/^\/api\/admin\/schedule\/experiments(?:\/[^/]+\/duplicate)?$/.test(url.pathname) || !response.ok()) return;
      pending.push(response.json().then((value: { id?: string }) => {
        if (value.id) created.add(value.id);
      }));
    });
    await use((id) => created.add(id));
    await Promise.allSettled(pending);
    for (const id of created) {
      const deleted = await request.delete(`${API}/api/admin/schedule/experiments/${encodeURIComponent(id)}`, { headers: ADMIN });
      if (!deleted.ok() && deleted.status() !== 404) throw new Error(`E2E cleanup failed for its own draft ${id}: ${deleted.status()}`);
    }
  }, { auto: true }],
});
