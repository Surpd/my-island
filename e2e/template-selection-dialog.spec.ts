import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

const output = resolve(process.cwd(), 'artifacts/template-selection');
mkdirSync(output, { recursive: true });

function lesson(id: number, subject = `Предмет ${id}`) {
  return { id: `lesson-${id}`, subject, start: '10:00', end: '10:45', groups: ['9А'], members: ['student-1'], teachers: ['Преподаватель'], room: '204', problems: [] };
}

async function openWithPreview(page: Page, preview: Record<string, unknown>) {
  await page.route('**/api/admin/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith('/auth/session')) return route.fulfill({ json: { state: 'approved', user: { id: 1, role: 'admin', roles: ['admin'] } } });
    if (path.endsWith('/experiments/capabilities')) return route.fulfill({ json: { demo_available: false, school_available: true, consolidated_schedule: true } });
    if (path === '/api/admin/schedule/experiments/school') return route.fulfill({ json: { items: [
      { id: 'primary-old', name: 'Основной шаблон', revision: 3, lesson_count: 1, is_primary_template: true, draft_kind: 'school' },
      { id: 'candidate-new', name: 'Шаблон на проверку', revision: 2, lesson_count: 2, is_primary_template: false, draft_kind: 'school' },
    ] } });
    if (path.endsWith('/selection-preview')) return route.fulfill({ json: preview });
    if (path.endsWith('/select-primary')) return route.fulfill({ status: 409, json: { detail: 'Шаблон изменился с момента сравнения. Обновите данные.' } });
    if (path.endsWith('/approve-legacy-baseline')) return route.fulfill({ json: { approved: true } });
    if (path.endsWith('/directory')) return route.fulfill({ json: { groups: [{ id: 1, name: '9А', display_name: '9А', group_type: 'class' }], teachers: [], students: [], rooms: [] } });
    if (path.endsWith('/observability')) return route.fulfill({ json: { blocks: [], weeks: [], effective_weeks: [], week_publications: [] } });
    return route.fulfill({ json: { items: [] } });
  });
  await page.goto('/admin/schedule/constructor?role=admin');
  await expect(page.getByRole('heading', { name: 'Основной шаблон' })).toBeVisible();
  await page.getByText('Другие черновики шаблона · 1').click();
  const candidate = page.locator('.constructor-draft').filter({ hasText: 'Шаблон на проверку' });
  await candidate.getByRole('button', { name: 'Сделать основным шаблоном' }).click();
}

function standardPreview() {
  const added = lesson(1, 'Физика');
  const removed = lesson(2, 'История');
  const changedBefore = lesson(3, 'Алгебра');
  const changedAfter = { ...changedBefore, room: '203', members: ['student-1', 'student-2'] };
  return {
    current: { id: 'primary-old', name: 'Основной шаблон', revision: 3 },
    candidate: { id: 'candidate-new', name: 'Шаблон на проверку', revision: 2 },
    current_hash: 'synthetic-current-hash', candidate_hash: 'synthetic-candidate-hash',
    added: [added], removed: [removed],
    changed: [{ id: changedAfter.id, before: changedBefore, after: changedAfter, changes: [
      { field: 'members', label: 'Участники', impact: 'Меняется персональный состав учеников.', before: ['student-1'], after: ['student-1', 'student-2'] },
      { field: 'room', label: 'Кабинет', impact: 'Меняется место проведения занятия.', before: '101', after: '203' },
    ] }],
    settings_changes: [{ field: 'grade_modes', label: 'Режимы дней по параллелям', impact: 'Меняется режим обучения учеников.', before: 'Обычный режим', after: '9 класс, Пн: Онлайн' }],
    problems: [{ lesson_id: 'lesson-conflict', details: ['Конфликт преподавателя'] }],
    requires_explicit_problem_acknowledgement: true, has_semantic_changes: true,
  };
}

test('compact template review keeps the footer visible and explains a stale confirmation', async ({ page }) => {
  await openWithPreview(page, standardPreview());
  const dialog = page.getByRole('dialog', { name: 'Подтверждение основного шаблона' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText('Добавлено', { exact: true })).toBeVisible();
  await expect(dialog.getByText('Удалено', { exact: true })).toBeVisible();
  await expect(dialog.getByText('Изменено', { exact: true })).toBeVisible();
  await expect(dialog.getByText('Календарь', { exact: true })).toBeVisible();
  await expect(dialog.getByText('Конфликты', { exact: true })).toBeVisible();
  const addDetails = dialog.locator('details').filter({ hasText: 'Добавленные занятия · 1' });
  await expect(addDetails.locator('article')).toBeHidden();
  await addDetails.locator('summary').click();
  await expect(addDetails.getByText('Добавлено · Физика')).toBeVisible();
  const confirm = dialog.getByRole('button', { name: 'Подтвердить основным' });
  await expect(confirm).toBeDisabled();
  await dialog.getByLabel('Я просмотрел проблемы и подтверждаю шаблон с ними.').check();
  await expect(confirm).toBeEnabled();
  const footerBox = await dialog.locator('footer').boundingBox();
  expect(footerBox).not.toBeNull();
  expect(footerBox!.y + footerBox!.height).toBeLessThanOrEqual(page.viewportSize()!.height);
  await page.screenshot({ path: resolve(output, 'compact-dialog-desktop.png'), fullPage: true });
  await confirm.click();
  await expect(dialog.getByRole('alert')).toContainText('Шаблон изменился с момента сравнения');
});

test('large comparison keeps every change available behind collapsed sections', async ({ page }) => {
  const base = standardPreview();
  const large = {
    ...base,
    added: Array.from({ length: 125 }, (_, index) => lesson(index, `Добавлено ${index}`)),
    removed: Array.from({ length: 100 }, (_, index) => lesson(index + 200, `Удалено ${index}`)),
    changed: Array.from({ length: 100 }, (_, index) => {
      const before = lesson(index + 400, `Изменено ${index}`);
      return { id: before.id, before, after: { ...before, room: '305' }, changes: [{ field: 'room', label: 'Кабинет', impact: 'Проверить кабинет.', before: '204', after: '305' }] };
    }),
    problems: [],
  };
  await openWithPreview(page, large);
  const dialog = page.getByRole('dialog', { name: 'Подтверждение основного шаблона' });
  const added = dialog.locator('details').filter({ hasText: 'Добавленные занятия · 125' });
  await expect(added.locator('article').first()).toBeHidden();
  await added.locator('summary').click();
  await expect(added.locator('article')).toHaveCount(125);
  await expect(added.getByText('Добавлено · Добавлено 124')).toBeAttached();
  await expect(dialog.getByRole('button', { name: 'Подтвердить основным' })).toBeVisible();
});

test('legacy baseline approval identifies the missing revision and requires a separate checkbox', async ({ page }) => {
  const preview = {
    ...standardPreview(),
    current: { id: 'primary-old', name: 'Основной шаблон', revision: 45, draft_revision: 46, snapshot_available: false },
    baseline_recovery_required: true,
    baseline_recovery: { draft_id: 'primary-old', revision: 46, payload_hash: 'current-hash', missing_revision: 45, missing_payload_hash: 'old-hash', contents: [lesson(1, 'Текущее занятие')] },
    added: [], removed: [], changed: [], settings_changes: [], problems: [{ lesson_id: 'selected-template', details: ['Историческая ревизия недоступна'] }],
  };
  await openWithPreview(page, preview);
  const dialog = page.getByRole('dialog', { name: 'Подтверждение основного шаблона' });
  await expect(dialog.getByText(/Ревизия 46 не является восстановленной копией старой версии/)).toBeVisible();
  await expect(dialog.getByText('Текущий состав · 1 занятий')).toBeVisible();
  const approve = dialog.getByRole('button', { name: 'Утвердить новую базовую версию' });
  await expect(approve).toBeDisabled();
  await dialog.getByLabel(/Я понимаю, что историческая ревизия 45 недоступна/).check();
  await expect(approve).toBeEnabled();
  await approve.click();
  await expect(page.getByRole('alert')).toContainText('Историческая ревизия 45 не восстановлена');
});
