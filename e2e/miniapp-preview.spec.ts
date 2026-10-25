import { expect, test, type Frame, type Page } from '@playwright/test';
import { ADMIN_HEADERS, endPreview, expectNoHorizontalOverflow, openPreviewFromAdmin, PREVIEW_API, previewPeople, type PreviewPerson } from './helpers/miniapp-preview';

const FIXTURE_MONDAY = '2026-09-21';
const FIXTURE_SUNDAY = '2026-09-27';

async function personNamed(page: Page, role: 'student' | 'teacher', name: string) {
  const person = (await previewPeople(page.request, role)).find((item) => item.display_name === name);
  expect(person, `local E2E ${role} fixture ${name}`).toBeTruthy();
  return person as PreviewPerson;
}

async function openSelectedPerson(page: Page, person: PreviewPerson) {
  await page.getByLabel('Поиск человека').fill(person.display_name);
  const identity = page.getByLabel('Личность');
  await expect(identity.locator(`option[value="${person.identity_id}"]`)).toHaveCount(1);
  await expect(identity).toBeEnabled();
  await identity.selectOption(String(person.identity_id));
  await expect(identity).toHaveValue(String(person.identity_id));
  const openButton = page.getByRole('button', { name: 'Открыть приложение' });
  await expect(openButton).toBeEnabled();
  const [response] = await Promise.all([
    page.waitForResponse((item) => new URL(item.url()).pathname === '/api/admin/previews' && item.request().method() === 'POST', { timeout: 15_000 }),
    openButton.click(),
  ]);
  expect(response.ok()).toBeTruthy();
  const payload = await response.json();
  const previewId = String(payload.id || '');
  expect(previewId).toMatch(/^[0-9a-f-]{36}$/i);
  return previewId;
}

async function previewFrame(page: Page) {
  const iframe = await page.getByTitle('Предпросмотр My Island').elementHandle();
  const frame = await iframe?.contentFrame();
  expect(frame).toBeTruthy();
  return frame as Frame;
}

async function scoreFor(page: Page, previewId: string) {
  const response = await page.request.get(`${PREVIEW_API}/api/student/journals`, {
    headers: { ...ADMIN_HEADERS, 'X-Preview-Id': previewId },
  });
  expect(response.ok()).toBeTruthy();
  const journals = (await response.json()).items as Array<{ results: Array<{ numeric_score: number | null }> }>;
  return journals.flatMap((journal) => journal.results.map((result) => result.numeric_score).filter((score): score is number => score !== null));
}

async function expandSchedule(frame: Frame) {
  await frame.getByRole('button', { name: 'Расписание', exact: true }).first().click();
  const collapse = frame.getByRole('button', { name: 'Свернуть раздел', exact: true });
  if (await collapse.isVisible().catch(() => false)) await collapse.click();
  await frame.getByRole('button', { name: 'Развернуть раздел', exact: true }).click();
}

async function expectPageNoHorizontalOverflow(page: Page) {
  const sizes = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  expect(Math.max(sizes.document, sizes.body), `admin horizontal overflow at ${sizes.viewport}px`).toBeLessThanOrEqual(sizes.viewport + 1);
}

test('preview shows isolated journal scores, a published lesson, teacher schedule, and mobile widths', async ({ page }) => {
  const peer = await personNamed(page, 'student', 'Local E2E Journal Peer');
  const student = await personNamed(page, 'student', 'Тестовый ученик');
  const teacher = await personNamed(page, 'teacher', 'Педагог 7-1');
  const previewIds: string[] = [];
  const runtimeErrors: string[] = [];
  page.on('pageerror', (error) => runtimeErrors.push(error.message));

  try {
    await openPreviewFromAdmin(page);
    await expect(page.getByLabel('Режим')).toHaveValue('student');
    await expect(page.getByLabel('Поиск человека')).toBeVisible();
    await expect(page.getByLabel('Класс')).toBeVisible();
    await expect(page.getByLabel('Группа')).toBeVisible();
    await expect(page.getByLabel('Дата')).toBeVisible();
    await expect(page.getByLabel('Ширина')).toHaveValue('390');
    await expect(page.getByLabel('Высота')).toBeVisible();

    await page.getByLabel('Дата').fill(FIXTURE_MONDAY);
    const peerPreview = await openSelectedPerson(page, peer);
    previewIds.push(peerPreview);
    let frame = await previewFrame(page);
    await expect(frame.getByRole('button', { name: 'Оценки', exact: true }).first()).toBeVisible();
    await frame.getByRole('button', { name: 'Оценки', exact: true }).first().click();
    await frame.getByRole('button', { name: 'Развернуть раздел', exact: true }).click();
    await expect(frame.getByRole('region', { name: 'Школьные журналы' })).toBeVisible();
    await expect.poll(async () => (await scoreFor(page, peerPreview)).includes(3)).toBe(true);
    await expect(frame.getByText('3', { exact: true })).toBeVisible();

    const studentPreview = await openSelectedPerson(page, student);
    previewIds.push(studentPreview);
    frame = await previewFrame(page);
    await expect(frame.getByRole('button', { name: 'Расписание', exact: true }).first()).toBeVisible();
    await expandSchedule(frame);
    await expect(frame.locator('strong:visible').filter({ hasText: /^Математика$/ }).first()).toBeVisible();
    await expect.poll(async () => (await scoreFor(page, studentPreview)).includes(8)).toBe(true);

    await page.getByLabel('Дата').fill(FIXTURE_SUNDAY);
    await expect(frame.getByLabel('Дата расписания')).toHaveValue(FIXTURE_SUNDAY);
    const expand = frame.getByRole('button', { name: 'Развернуть раздел', exact: true });
    await expect(expand).toBeVisible();
    await expand.click();
    await expect(frame.locator('strong:visible').filter({ hasText: /^Занятий нет$/ }).first()).toBeVisible();

    await page.getByLabel('Режим').selectOption('teacher');
    await expect(page.getByLabel('Группа')).toBeVisible();
    const teacherPreview = await openSelectedPerson(page, teacher);
    previewIds.push(teacherPreview);
    await page.getByLabel('Дата').fill(FIXTURE_MONDAY);
    const teacherFrame = await previewFrame(page);
    await expandSchedule(teacherFrame);
    await expect(teacherFrame.locator('strong:visible').filter({ hasText: /^Математика$/ }).first()).toBeVisible();

    for (const width of ['360', '390', '430']) {
      await page.getByLabel('Ширина').selectOption(width);
      await expect.poll(async () => page.getByTitle('Предпросмотр My Island').evaluate((frame) => Number.parseFloat((frame as HTMLIFrameElement).style.width))).toBe(Number(width));
      await expectNoHorizontalOverflow(teacherFrame);
    }

    const diagnostics = await page.request.get(`${PREVIEW_API}/api/admin/previews/${encodeURIComponent(teacherPreview)}/diagnostics`, { headers: ADMIN_HEADERS });
    expect(diagnostics.ok()).toBeTruthy();
    await page.getByRole('button', { name: 'Завершить предпросмотр' }).click();
    expect(runtimeErrors).toEqual([]);
  } finally {
    for (const previewId of new Set(previewIds)) await endPreview(page.request, previewId);
  }
});

test('preview deep link opens an accountless catalog identity in the hash routed admin app', async ({ page }) => {
  const peer = await personNamed(page, 'student', 'Local E2E Journal Peer');
  let previewId = '';
  const runtimeErrors: string[] = [];
  page.on('pageerror', (error) => runtimeErrors.push(error.message));
  try {
    const created = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/admin/previews' && response.request().method() === 'POST');
    await page.goto(`/admin?role=admin#/admin/preview?identity_id=${encodeURIComponent(String(peer.identity_id))}&kind=student`);
    const response = await created;
    expect(response.ok()).toBeTruthy();
    previewId = String((await response.json()).id);
    await expect(page.getByRole('region', { name: 'Настройки предпросмотра' })).toBeVisible();
    await expect(page.getByLabel('Режим')).toHaveValue('student');
    await expect(page.getByLabel('Личность')).toHaveValue(String(peer.identity_id));
    await expect(page.getByTitle('Предпросмотр My Island')).toBeVisible();
    expect(runtimeErrors).toEqual([]);
  } finally {
    if (previewId) await endPreview(page.request, previewId);
  }
});

test('admin menu opens preview and local admin authentication remains usable', async ({ page }) => {
  await page.goto('/admin/schedule?role=admin');
  await page.getByRole('button', { name: 'Предпросмотр', exact: true }).click();
  await expect(page).toHaveURL(/\/admin\/preview/);
  await expect(page.getByRole('region', { name: 'Настройки предпросмотра' })).toBeVisible();
  const auth = await page.request.get(`${PREVIEW_API}/api/admin/overview`, { headers: ADMIN_HEADERS });
  expect(auth.ok()).toBeTruthy();
});

test('same admin principal can switch to its confirmed teacher role and back', async ({ page }) => {
  const challenge = await page.request.post(`${PREVIEW_API}/api/admin/auth/challenge`, { headers: ADMIN_HEADERS });
  expect(challenge.ok()).toBeTruthy();
  const { code } = await challenge.json() as { code: string };
  const exchange = await page.request.post(`${PREVIEW_API}/api/admin/auth/exchange`, { data: { code } });
  expect(exchange.ok()).toBeTruthy();
  const { browser_session_token: token } = await exchange.json() as { browser_session_token: string };
  expect(token).toBeTruthy();
  await page.addInitScript((session) => sessionStorage.setItem('my_island_admin_session', session), token);

  await page.goto('/admin?role=admin');
  const role = page.getByLabel('Моя роль');
  await expect(role).toHaveValue('admin');
  const before = await page.request.get(`${PREVIEW_API}/api/admin/auth/session`, { headers: { 'X-Admin-Browser-Session': token } });
  expect(before.ok()).toBeTruthy();
  const principalId = String((await before.json()).user.id);

  await role.selectOption('teacher');
  await expect(page).toHaveURL(/\/?role=teacher/);
  await expect(page.getByLabel('Моя роль')).toHaveValue('teacher');
  await page.reload();
  await expect(page.getByLabel('Моя роль')).toHaveValue('teacher');

  const after = await page.request.get(`${PREVIEW_API}/api/admin/auth/session`, { headers: { 'X-Admin-Browser-Session': token } });
  expect(after.ok()).toBeTruthy();
  const principal = (await after.json()).user;
  expect(String(principal.id)).toBe(principalId);
  expect(principal.roles).toEqual(expect.arrayContaining(['admin', 'teacher']));
  await page.getByLabel('Моя роль').selectOption('admin');
  await expect(page).toHaveURL(/\/?role=admin/);
  await expect(page.getByRole('region', { name: 'Настройки предпросмотра' })).toHaveCount(0);
});

test('mobile admin published schedule has readable audience labels and no horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 900 });
  await page.goto('/admin/schedule?role=admin');
  await expect(page.getByRole('heading', { name: 'Опубликованная неделя' })).toBeVisible();
  await page.getByLabel('Выбрать неделю').fill(FIXTURE_MONDAY);
  await expect(page.getByText(/Математика/).first()).toBeVisible();
  await expect(page.getByText('canonical_groups', { exact: false })).toHaveCount(0);

  for (const width of [360, 390, 430]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.getByRole('heading', { name: 'Опубликованная неделя' })).toBeVisible();
    await expectPageNoHorizontalOverflow(page);
  }
});
