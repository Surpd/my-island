import { expect, test, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';

const API = 'http://localhost:8011';
const admin = { 'X-Dev-Auth': 'admin:1' };

function secondAdminFixture(): { token: string; id: string } {
  // Only the seeded, marked e2e SQLite file is eligible. No credential is
  // written to the repository or printed to the Playwright reporter.
  const script = [
    'from backend.scripts.local_test_env import DB_PATH, MARKER, MARKER_CONTENT, PROFILE',
    'from backend.database import Database',
    'import json',
    'assert PROFILE == "e2e" and DB_PATH.is_file() and MARKER.read_text() == MARKER_CONTENT',
    'database = Database(path=DB_PATH)',
    'actor = database.get_or_create_user(990010, "admin")',
    'database.ensure_bootstrap_roles(990010, ("admin",))',
    'code, _ = database.create_admin_login_challenge(actor["id"])',
    'token, _ = database.consume_admin_login_challenge(code)',
    'print(json.dumps({"token": token, "id": str(actor["id"])}))',
  ].join('\n');
  return JSON.parse(execFileSync('python', ['-c', script], {
    encoding: 'utf8', env: { ...process.env, MY_ISLAND_TEST_PROFILE: 'e2e' },
  }));
}

async function editGroup(page: Page, id: string) {
  await page.goto('http://localhost:3011/admin/people?role=admin');
  const card = page.locator(`[data-group-id="${id}"]`);
  await card.getByRole('button', { name: /^Действия группы/ }).click();
  await card.getByRole('menuitem', { name: 'Редактировать' }).click();
  return page.getByRole('dialog', { name: 'Настройки группы' });
}

test('two administrators reconnect, compare a field with its author and explicitly merge it', async ({ page, browser }) => {
  const name = `Live group ${Date.now()}`;
  const created = await page.request.post(`${API}/api/admin/groups`, { headers: admin, data: { name, group_type: 'instructional' } });
  expect(created.ok()).toBeTruthy();
  const id = String((await created.json()).id);
  const other = await browser.newContext();
  try {
    const actor = secondAdminFixture();
    await other.addInitScript((token) => sessionStorage.setItem('my_island_admin_session', token), actor.token);
    const second = await other.newPage();
    const first = await editGroup(page, id);
    const form = await editGroup(second, id);
    const initial = await page.request.get(`${API}/api/admin/directory/version`, { headers: admin });
    expect((await initial.json()).actor).not.toBe(actor.id);
    await other.setOffline(true);
    await form.getByLabel('Название').fill(name + ' second');
    await first.getByLabel('Название').fill(name + ' first');
    await first.getByRole('button', { name: 'Сохранить группу' }).click();
    await expect(first).toHaveCount(0);
    await second.bringToFront();
    await other.setOffline(false);
    await second.evaluate(() => window.dispatchEvent(new Event('focus')));
    const review = form.getByRole('region', { name: 'Изменения другого администратора' });
    await expect(review).toBeVisible({ timeout: 25000 });
    await expect(form.getByLabel('Название', { exact: true }).first()).toBeFocused();
    await expect(review).toContainText('Администратор');
    await review.getByLabel('Моё значение', { exact: false }).check();
    await review.getByRole('button', { name: 'Применить выбор к форме' }).click();
    await form.getByRole('button', { name: 'Сохранить группу' }).click();
    await expect(form).toHaveCount(0);
    const current = await page.request.get(`${API}/api/admin/groups/${id}`, { headers: admin });
    expect((await current.json()).display_name).toBe(name + ' second');
    const history = await page.request.get(`${API}/api/admin/directory/history/groups/${id}`, { headers: admin });
    expect((await history.json()).items[0].actor_id).toBe(actor.id);
    const revoked = await second.request.post(`${API}/api/admin/auth/logout`, {
      headers: { 'X-Dev-Auth': `browser:${actor.token}` },
    });
    expect(revoked.ok()).toBeTruthy();
    await second.evaluate(() => window.dispatchEvent(new Event('focus')));
    await expect(second.getByText('Доступ администратора изменился.', { exact: false })).toBeVisible({ timeout: 25000 });
    await expect(second.locator(`[data-group-id="${id}"]`)).toHaveCount(0);
  } finally { await other.close(); }
});

test('a constructor draft keeps typed text offline and opens its existing conflict dialog on reconnect', async ({ page, request }) => {
  const name = `Live draft ${Date.now()}`;
  const endpoint = API + '/api/admin/schedule/experiments';
  await page.goto('/admin/schedule/constructor?role=admin');
  const created = page.waitForResponse((r) => r.request().method() === 'POST' && r.url().endsWith('/api/admin/schedule/experiments'));
  await page.getByRole('button', { name: /Пустая школьная неделя/ }).click();
  const draft = await (await created).json();
  await page.locator('.constructor-draft-menu summary').click();
  const input = page.getByRole('textbox', { name: 'Название черновика' });
  await expect(input).toHaveValue(draft.name);
  await page.context().setOffline(true);
  await input.fill(name + ' local');
  const update = await request.put(`${endpoint}/${draft.id}`, {
    headers: admin, data: { name: name + ' remote', expected_revision: draft.revision, payload: draft.payload },
  });
  expect(update.ok()).toBeTruthy();
  await page.context().setOffline(false);
  await expect(input).toHaveValue(name + ' local');
  // Leave the buffered field. A visible/focused editor can now compare revisions.
  await input.evaluate((el) => el.blur());
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  const conflict = page.getByRole('dialog', { name: 'Расписание изменилось в другой вкладке или сеансе' });
  await expect(conflict).toBeVisible({ timeout: 30000 });
  await expect(conflict).toContainText(name + ' local');
  await expect(conflict).toContainText(name + ' remote');
});
