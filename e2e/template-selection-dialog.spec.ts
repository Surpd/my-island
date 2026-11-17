import { expect, test } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

test('synthetic primary template selection comparison dialog', async ({ page }) => {
  await page.route('**/api/admin/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith('/auth/session')) return route.fulfill({ json: { state: 'approved', user: { id: 1, role: 'admin', roles: ['admin'] } } });
    if (path.endsWith('/experiments/capabilities')) return route.fulfill({ json: { demo_available: false, school_available: true, consolidated_schedule: true } });
    if (path === '/api/admin/schedule/experiments/school') return route.fulfill({ json: { items: [
      { id: 'primary-old', name: 'Основной шаблон', revision: 3, lesson_count: 1, is_primary_template: true, draft_kind: 'school' },
      { id: 'candidate-new', name: 'Шаблон на проверку', revision: 2, lesson_count: 2, is_primary_template: false, draft_kind: 'school' },
    ] } });
    if (path.endsWith('/selection-preview')) return route.fulfill({ json: {
      current: { id: 'primary-old', name: 'Основной шаблон', revision: 3 },
      candidate: { id: 'candidate-new', name: 'Шаблон на проверку', revision: 2 },
      current_hash: 'synthetic-current-hash', candidate_hash: 'synthetic-candidate-hash',
      added: [{ id: 'lesson-added', subject: 'Физика', start: '10:00', end: '10:45', groups: ['9А'], members: ['student-1', 'student-2'], teachers: ['Анна Иванова'], room: '204', problems: [] }],
      removed: [{ id: 'lesson-removed', subject: 'История', start: '11:00', end: '11:45', groups: ['9А'], members: ['student-1'], teachers: ['Олег Петров'], room: '101', problems: [] }],
      changed: [{ id: 'lesson-changed', before: { subject: 'Алгебра', start: '09:00', end: '09:45', groups: ['9А'], members: ['student-1'], teachers: ['Анна Иванова'], room: '101', problems: [] }, after: { subject: 'Алгебра', start: '09:00', end: '09:45', groups: ['9А'], members: ['student-1', 'student-2'], teachers: ['Анна Иванова'], room: '203', problems: [] } }],
      problems: [], requires_explicit_problem_acknowledgement: false,
    } });
    if (path.endsWith('/directory')) return route.fulfill({ json: { groups: [{ id: 1, name: '9А', display_name: '9А', group_type: 'class' }], teachers: [], students: [], rooms: [] } });
    if (path.endsWith('/observability')) return route.fulfill({ json: { blocks: [], weeks: [], effective_weeks: [], week_publications: [] } });
    return route.fulfill({ json: { items: [] } });
  });
  await page.goto('/admin/schedule/constructor?role=admin');
  await expect(page.getByRole('heading', { name: 'Основной шаблон' })).toBeVisible();
  await page.getByText('Другие черновики шаблона · 1').click();
  const candidate = page.locator('.constructor-draft').filter({ hasText: 'Шаблон на проверку' });
  await candidate.getByRole('button', { name: 'Сделать основным шаблоном' }).click();
  const dialog = page.getByRole('dialog', { name: 'Сделать «Шаблон на проверку» основным?' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(/Добавлено: 1 · удалено: 1 · изменено: 1/)).toBeVisible();
  const output = resolve(process.cwd(), 'artifacts/canonical-campus-proof');
  mkdirSync(output, { recursive: true });
  await page.screenshot({ path: resolve(output, 'primary-template-comparison.png'), fullPage: true });
});
