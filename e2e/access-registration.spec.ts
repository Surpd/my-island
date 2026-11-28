import { test, expect } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { execFileSync } from 'node:child_process';

const shots =
  process.env.ACCESS_SCREENSHOT_DIR || resolve('.local-test/access-shots');
const identity = {
  id: 'i1',
  display_name: 'Тестовая Анна',
  class_name: null,
  kind: 'teacher',
  linked_user_id: 'u1',
};
const fixture = {
  can_manage_administrators: true,
  counts: { connected: 1, pending: 1, unlinked_identities: 1 },
  identities: [
    identity,
    {
      id: 'i2',
      display_name: 'Тестовый Борис',
      class_name: '7А',
      kind: 'student',
      linked_user_id: null,
    },
  ],
  accounts: [
    {
      id: 'u1',
      telegram_user_id: '123456789',
      username: 'example_teacher',
      telegram_first_name: 'Анна',
      telegram_last_name: null,
      roles: ['teacher'],
      state: 'connected',
      identity,
      connected_at: '2026-11-01T12:00:00Z',
      last_active_at: null,
      can_manage_administrators: false,
    },
    {
      id: 'u2',
      telegram_user_id: '987654321',
      username: 'example_student',
      telegram_first_name: null,
      telegram_last_name: null,
      roles: [],
      state: 'unconfirmed',
      identity: null,
      connected_at: null,
      last_active_at: null,
      can_manage_administrators: false,
    },
  ],
  claims: [
    {
      id: 'c1',
      user_id: 'u2',
      requested_role: 'student',
      submitted_name: 'Тестовый Борис',
      submitted_class: '7А',
      status: 'pending',
      created_at: '2026-11-27T12:00:00Z',
      reviewed_at: null,
      reason: null,
      identity_id: null,
      candidate_ids: ['i2'],
    },
  ],
};

test('access workflows render synthetic records and send reviewed decisions', async ({
  page,
}) => {
  await mkdir(shots, { recursive: true });
  const writes: { url: string; body: Record<string, unknown> }[] = [];
  await page.route('**/api/admin/access**', async (route) => {
    if (route.request().method() === 'GET')
      return route.fulfill({ json: fixture });
    writes.push({
      url: route.request().url(),
      body: route.request().postDataJSON(),
    });
    return route.fulfill({ json: { ok: true } });
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/admin/settings/access?role=admin');
  await expect(
    page.getByRole('heading', { name: 'Заявки на подключение' }),
  ).toBeVisible();
  await expect(page.getByText('Тестовый Борис').first()).toBeVisible();
  await page.screenshot({
    path: resolve(shots, 'access-panel.png'),
    fullPage: true,
  });
  await page.getByRole('button', { name: /Тестовый Борис/ }).click();
  await expect(page.getByLabel('Школьная запись')).toHaveValue('i2');
  await page.getByRole('button', { name: 'Подтвердить связь' }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].body).toEqual({ decision: 'approve', identity_id: 'i2' });
  await page.getByRole('button', { name: 'Аккаунты', exact: true }).click();
  await page.getByRole('button', { name: /Тестовая Анна/ }).click();
  await expect(page.getByLabel('Карточка аккаунта')).toContainText(
    'example_teacher',
  );
  await page.screenshot({
    path: resolve(shots, 'access-account.png'),
    fullPage: true,
  });
  await page
    .getByRole('button', { name: 'Администраторы', exact: true })
    .click();
  await page.getByRole('button', { name: /Тестовая Анна/ }).click();
  await page.getByRole('button', { name: 'Назначить администратором' }).click();
  await expect(
    page.getByRole('dialog', { name: 'Назначить администратора?' }),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(shots, 'access-grant-admin.png'),
    fullPage: true,
  });
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Назначить', exact: true })
    .click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1].body).toEqual({ action: 'grant_admin' });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
});

test('unconfirmed Mini App offers bot registration', async ({ page }) => {
  await mkdir(shots, { recursive: true });
  await page.addInitScript(() => {
    window.sessionStorage.setItem(
      'my_island_telegram_init_data',
      'synthetic-init-data',
    );
  });
  await page.route('**/api/auth/session', async (route) =>
    route.fulfill({
      json: {
        mode: 'telegram',
        state: 'needs_identity',
        bot_username: 'example_school_bot',
        user: { id: 'u2', role: 'student', roles: [] },
      },
    }),
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'Подключитесь к Острову' }),
  ).toBeVisible();
  await expect(
    page.getByRole('link', { name: 'Открыть школьного бота' }),
  ).toHaveAttribute('href', 'https://t.me/example_school_bot');
  await page.screenshot({
    path: resolve(shots, 'access-registration.png'),
    fullPage: true,
  });
});

test('access controls require a reason and explicit transfer confirmation; ordinary admins cannot grant roles', async ({
  page,
}) => {
  const writes: Record<string, unknown>[] = [];
  await page.route('**/api/admin/access**', async (route) => {
    if (route.request().method() === 'GET')
      return route.fulfill({
        json: { ...fixture, can_manage_administrators: false },
      });
    writes.push(route.request().postDataJSON());
    return route.fulfill({ json: { ok: true } });
  });
  await page.goto('/admin/settings/access?role=admin');
  await page.getByRole('button', { name: /Тестовый Борис/ }).click();
  const reject = page.getByRole('button', { name: 'Отклонить заявку' });
  await expect(reject).toBeDisabled();
  await page.getByLabel('Причина отклонения').fill('Проверьте имя');
  await reject.click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toEqual({ decision: 'reject', reason: 'Проверьте имя' });
  await page.getByRole('button', { name: 'Аккаунты', exact: true }).click();
  await page.getByRole('button', { name: /Без школьной записи/ }).click();
  await page.getByLabel('Школьная запись').selectOption('i1');
  await expect(
    page.getByText('Выбранная запись уже связана с другим аккаунтом.'),
  ).toBeVisible();
  page.once('dialog', (dialog) => dialog.dismiss());
  await page.getByRole('button', { name: 'Сохранить привязку' }).click();
  expect(writes).toHaveLength(1);
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Сохранить привязку' }).click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1]).toEqual({
    action: 'link',
    identity_id: 'i1',
    expected_identity_id: null,
    replace_user_id: 'u1',
  });
  await page
    .getByRole('button', { name: 'Администраторы', exact: true })
    .click();
  await expect(
    page.getByText('У вас нет права управлять администраторами.'),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Назначить администратором' }),
  ).toHaveCount(0);
});

test('protected manager cannot be changed through access controls', async ({
  page,
}) => {
  const protectedAccount = {
    ...fixture.accounts[0],
    roles: ['teacher', 'admin'],
    can_manage_administrators: true,
  };
  let manager = false;
  await page.route('**/api/admin/access**', async (route) =>
    route.fulfill({
      json: {
        ...fixture,
        can_manage_administrators: manager,
        accounts: [protectedAccount],
      },
    }),
  );
  await page.goto('/admin/settings/access?role=admin');
  await page.getByRole('button', { name: 'Аккаунты', exact: true }).click();
  await page.getByRole('button', { name: /Тестовая Анна/ }).click();
  await expect(
    page.getByText(
      'Управлять этим защищённым аккаунтом может только управляющий администратор.',
    ),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Отозвать доступ' }),
  ).toHaveCount(0);
  manager = true;
  await page.getByRole('button', { name: 'Обновить' }).click();
  await page
    .getByRole('button', { name: 'Администраторы', exact: true })
    .click();
  await page.getByRole('button', { name: /Тестовая Анна/ }).click();
  await expect(
    page.getByRole('button', { name: 'Снять полномочия администратора' }),
  ).toBeDisabled();
});

test('restored access asks for a fresh Telegram launch', async ({ page }) => {
  await page.addInitScript(() => {
    window.sessionStorage.setItem(
      'my_island_telegram_init_data',
      'synthetic-old-init-data',
    );
  });
  await page.route('**/api/auth/session', async (route) =>
    route.fulfill({
      json: {
        mode: 'telegram',
        state: 'reopen_telegram',
        bot_username: 'example_school_bot',
        user: { id: 'u2', role: 'student', roles: [] },
      },
    }),
  );
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'Откройте Остров заново' }),
  ).toBeVisible();
  await expect(
    page.getByRole('link', { name: 'Открыть школьного бота' }),
  ).toHaveAttribute('href', 'https://t.me/example_school_bot');
});

test('historical conflict cannot be reviewed again and a legacy link can be repaired', async ({
  page,
}) => {
  const writes: Record<string, unknown>[] = [];
  const legacy = {
    ...fixture.accounts[0],
    link_status: 'conflict',
    state: 'unconfirmed',
  };
  await page.route('**/api/admin/access**', async (route) => {
    if (route.request().method() === 'GET')
      return route.fulfill({
        json: {
          ...fixture,
          accounts: [legacy, fixture.accounts[1]],
          claims: [{ ...fixture.claims[0], status: 'identity_conflict' }],
        },
      });
    writes.push(route.request().postDataJSON());
    return route.fulfill({ json: { ok: true } });
  });
  await page.goto('/admin/settings/access?role=admin');
  await page.getByRole('button', { name: /Тестовый Борис/ }).click();
  await expect(
    page.getByRole('button', { name: 'Подтвердить связь' }),
  ).toHaveCount(0);
  await page.getByRole('button', { name: 'Аккаунты', exact: true }).click();
  await page.getByRole('button', { name: /Тестовая Анна/ }).click();
  await page.getByRole('button', { name: 'Сохранить привязку' }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toEqual({
    action: 'link',
    identity_id: 'i1',
    expected_identity_id: 'i1',
  });
});

test('access screen loads the isolated backend response', async ({ page }) => {
  const responses: number[] = [];
  page.on('response', (response) => {
    if (response.url().endsWith('/api/admin/access'))
      responses.push(response.status());
  });
  await page.goto('/admin/settings/access?role=admin');
  await expect(
    page.getByRole('heading', { name: 'Заявки на подключение' }),
  ).toBeVisible();
  expect(responses).toContain(200);
});


test('isolated backend approves a teacher and grants an additional admin role', async ({ page }) => {
  // Guard the exact marked E2E database before inserting synthetic fixture rows.
  const seed = `
import os, sqlite3
from pathlib import Path
from backend.scripts.local_test_env import DB_PATH, MARKER, MARKER_CONTENT, ROOT, PROFILE
assert PROFILE == 'e2e'
assert DB_PATH.resolve() == (ROOT / '.local-test' / 'e2e' / 'my-island-test.sqlite3').resolve()
assert MARKER.read_text(encoding='utf-8') == MARKER_CONTENT and DB_PATH.is_file()
with sqlite3.connect(DB_PATH) as db:
    manager = db.execute('SELECT id FROM users WHERE telegram_user_id=900002').fetchone()[0]
    db.execute('UPDATE access_control SET manager_user_id=? WHERE id=1', (manager,))
    identity = db.execute("INSERT INTO identities(kind,display_name,origin,source_ref) VALUES ('teacher','Тестовая Заявка E2E','local_e2e','access-ui-teacher') RETURNING id").fetchone()[0]
    user = db.execute("INSERT INTO users(telegram_user_id,role,telegram_username) VALUES (990008881,'teacher','synthetic_access_e2e') RETURNING id").fetchone()[0]
    db.execute("INSERT INTO identity_claims(user_id,requested_role,submitted_name) VALUES (?,'teacher','Тестовая Заявка E2E')", (user,))
`;
  execFileSync('python', ['-c', seed], { env: { ...process.env, MY_ISLAND_TEST_PROFILE: 'e2e', PYTHONUTF8: '1' } });
  await page.goto('/admin/settings/access?role=admin');
  await page.getByRole('button', { name: /Тестовая Заявка E2E/ }).click();
  await expect(page.getByLabel('Школьная запись')).not.toHaveValue('');
  const review = page.waitForResponse((response) => response.url().includes('/api/admin/access/claims/') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Подтвердить связь' }).click();
  expect((await review).status()).toBe(200);
  await page.getByRole('button', { name: 'Администраторы', exact: true }).click();
  await page.getByRole('button', { name: /Тестовая Заявка E2E/ }).click();
  await page.getByRole('button', { name: 'Назначить администратором' }).click();
  const grant = page.waitForResponse((response) => response.url().includes('/api/admin/access/accounts/') && response.request().method() === 'POST');
  await page.getByRole('dialog').getByRole('button', { name: 'Назначить', exact: true }).click();
  expect((await grant).status()).toBe(200);
  await expect.poll(async () => {
    const response = await page.request.get('http://localhost:8011/api/admin/access', { headers: { 'X-Dev-Auth': 'admin:1' } });
    const payload = await response.json();
    return payload.accounts.find((item: { telegram_user_id: string }) => item.telegram_user_id === '990008881')?.roles.sort();
  }).toEqual(['admin', 'teacher']);
});
