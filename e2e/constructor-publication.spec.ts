import { expect } from '@playwright/test';
import { test } from './helpers/experiment-test';

const API = 'http://127.0.0.1:8011';
const DRAFT_ID = 'publication-ui-fixture';
const VERSION_ID = 'constructor-template-ui-e2e-version';
const PUBLISHED_ID = 'published-ui-e2e-week';

type PreviewPayload = {
  preview_token: string;
  can_publish: boolean;
  publication_available: boolean;
  read_only: boolean;
  base_version_id: string;
  head_revision: number;
  scope_kind: 'template' | 'week';
  week_start?: string;
  blockers: { code: string; message: string; lesson_id?: string }[];
  deferred: {
    lesson_id: string;
    reason: string;
    action: 'excluded' | 'preserved_base';
    context: { day: number; start: string; end: string; subject: string; teachers: string[]; groups: string[] };
  }[];
  diagnostics: {
    severity: 'info' | 'conflict' | 'critical';
    code: string;
    message: string;
    lesson_id?: string;
    context?: { day: number; start: string; end: string; subject: string; teachers: string[]; groups: string[] };
  }[];
  summary: { ready_lessons: number; deferred_lessons: number; deferred_blocks: number; unchanged_lessons: number; replaced_lessons: number; info_count: number; conflict_count: number; critical_count: number };
  source_kind: 'template';
  diff: { added: number; changed: number; removed: number; before_blocks: number; after_blocks: number };
  change_items: [];
};

function preview(token: string, options: { readOnly?: boolean; critical?: boolean; scope?: 'template' | 'week'; week?: string } = {}): PreviewPayload {
  const critical = options.critical ?? false;
  const context = { day: 0, start: '09:00', end: '09:45', subject: 'Математика', teachers: ['teacher-fixture'], groups: ['group-fixture'] };
  const diagnostics: PreviewPayload['diagnostics'] = [
    { severity: 'info', code: 'teaching_note', message: 'Проверено по школьному справочнику.', lesson_id: 'lesson-info', context },
    { severity: 'conflict', code: 'schedule_overlap', message: 'Нужно проверить пересечение занятий.', lesson_id: 'lesson-conflict', context },
  ];
  if (critical) diagnostics.push({ severity: 'critical', code: 'review_required', message: 'Не подтверждена аудитория занятия.', lesson_id: 'lesson-critical', context });
  return {
    preview_token: token,
    can_publish: !critical,
    publication_available: !options.readOnly,
    read_only: Boolean(options.readOnly),
    base_version_id: 'base-version-hidden-in-ui',
    head_revision: 2,
    scope_kind: options.scope || 'template',
    week_start: options.week,
    blockers: critical ? [{ code: 'review_required', message: 'Не подтверждена аудитория занятия.', lesson_id: 'lesson-critical' }] : [],
    deferred: [
      { lesson_id: 'lesson-preserved', reason: 'Не удалось проверить замену.', action: 'preserved_base', context },
      { lesson_id: 'lesson-excluded', reason: 'Нет группы для назначения.', action: 'excluded', context },
    ],
    diagnostics,
    summary: { ready_lessons: 4, deferred_lessons: 2, deferred_blocks: 1, unchanged_lessons: 1, replaced_lessons: 2, info_count: 1, conflict_count: 1, critical_count: critical ? 1 : 0 },
    source_kind: 'template',
    diff: { added: 1, changed: 2, removed: 0, before_blocks: 4, after_blocks: 7 },
    change_items: [],
  };
}

async function openPublicationPanel(page: import('@playwright/test').Page, options: { readOnly?: boolean; critical?: boolean } = {}) {
  let previewCount = 0;
  let publishPayload: Record<string, unknown> | null = null;
  let historyItems: Record<string, unknown>[] = [];
  await page.route(`${API}/api/admin/**`, async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith('/schedule/experiments/capabilities')) {
      return route.fulfill({ json: { demo_available: false, school_available: true, consolidated_schedule: true } });
    }
    if (path === '/api/admin/schedule/experiments/school' && route.request().method() === 'GET') {
      return route.fulfill({ json: { items: [{ id: DRAFT_ID, name: 'Шаблон для публикации', revision: 1, updated_at: '2026-10-27T08:00:00Z', is_primary_template: true, draft_kind: 'school', lesson_count: 1, event_count: 0, group_count: 1, day_count: 1 }] } });
    }
    if (path.endsWith('/schedule/experiments/school/directory')) {
      return route.fulfill({ json: { groups: [{ id: 'group-fixture', name: '7А', display_name: '7А', group_type: 'class', base_class_name: '7' }], teachers: [{ id: 'teacher-fixture', display_name: 'Анна Учитель' }], students: [], rooms: [] } });
    }
    if (path === '/api/admin/schedule/experiments/school/audience-preview') return route.fulfill({json:{lessons:{}}});
    if (path === '/api/admin/school/teacher-ui-colors') return route.fulfill({json:{items:[]}});
    if (path === '/api/admin/schedule/observability') return route.fulfill({ json: { blocks: [], weeks: [], effective_weeks: [], week_publications: [], canonical: { version_id: 'base-version-hidden-in-ui', status: 'authoritative' } } });
    if (path.startsWith('/api/admin/schedule/activities')) return route.fulfill({ json: { items: [] } });
    if (path === `/api/admin/schedule/experiments/school/${DRAFT_ID}`) {
      return route.fulfill({ json: { id: DRAFT_ID, name: 'Шаблон для публикации', revision: 1, base_version_id: 'base-version-hidden-in-ui', payload: { days: [0], slots: [{ start: '09:00', end: '09:45' }], classes: ['group-fixture'], lessons: [] } } });
    }
    if (path === `/api/admin/schedule/constructor/${DRAFT_ID}/publications`) return route.fulfill({ json: { items: historyItems, heads: {} } });
    if (path === `/api/admin/schedule/constructor/${DRAFT_ID}/publication/preview` && route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as { scope_kind: 'template' | 'week'; week_start?: string };
      previewCount += 1;
      return route.fulfill({ json: preview(`ui-preview-token-${previewCount}`, { ...options, scope: body.scope_kind, week: body.week_start }) });
    }
    if (path === `/api/admin/schedule/constructor/${DRAFT_ID}/publication/publish` && route.request().method() === 'POST') {
      publishPayload = route.request().postDataJSON() as Record<string, unknown>;
      const scope = publishPayload.scope_kind === 'week' ? 'week' : 'template';
      const publishedWeek = typeof publishPayload.week_start === 'string' ? publishPayload.week_start : '';
      const historyPreview = preview('history');
      historyItems = [{
        id: 'publication-fixture', scope_kind: scope, scope_key: scope === 'week' ? `week:${publishedWeek}` : 'template',
        created_at: '2026-10-27T09:15:00Z', published_id: PUBLISHED_ID, version_id: VERSION_ID,
        runtime_readback: { verified: true, block_count: 7 }, summary: { ready_lessons: 4, deferred_lessons: 2, deferred_blocks: 1 },
        diagnostics: historyPreview.diagnostics.filter((item) => item.severity !== 'critical'), deferred: historyPreview.deferred,
      }];
      return route.fulfill({ json: { status: 'published', published_id: PUBLISHED_ID, version_id: VERSION_ID, created_at: '2026-10-27T09:15:00Z', week_start: (publishPayload.week_start as string | undefined) || null, summary: { ready_lessons: 4, deferred_lessons: 2, deferred_blocks: 1 }, runtime_readback: { verified: true, block_count: 7, assignment_count: 4 } } });
    }
    return route.continue();
  });

  await page.goto('/admin/schedule/constructor?role=admin');
  await page.getByRole('button', { name: 'Проверить публикацию', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Публикация расписания' });
  await expect(dialog).toBeVisible();
  return { dialog, getPublishPayload: () => publishPayload };
}

test('publication preview filters remarks and shows readable lesson context and deferred actions', async ({ page }) => {
  const { dialog } = await openPublicationPanel(page, { critical: true });
  await dialog.getByRole('button', { name: 'Проверить изменения' }).click();
  await expect(dialog.getByText('Не подтверждена аудитория занятия.')).toBeVisible();
  await expect(dialog.getByText('Пн · 09:00–09:45 · Математика · Преподаватели: Анна Учитель · Группы: 7 класс').first()).toBeVisible();
  await expect(dialog.getByText(/teacher-fixture|group-fixture|lesson-critical/)).toHaveCount(0);

  await dialog.getByRole('button', { name: /Замечания/ }).click();
  await expect(dialog.locator('.constructor-publication-diagnostics li')).toHaveCount(1);
  await dialog.getByRole('button', { name: /Критичные/ }).click();
  await expect(dialog.locator('.constructor-publication-diagnostics li')).toHaveCount(1);
  await dialog.getByRole('button', { name: /Отложенные/ }).click();
  await expect(dialog.getByText('Сохранится прежнее занятие из опубликованной версии.')).toBeVisible();
  await expect(dialog.getByText('Назначение исключено из Campus этой исторической версии.')).toBeVisible();
  await expect(dialog.getByRole('button', { name: /Опубликовать/ })).toBeDisabled();
});

test('read-only preview remains available and cannot publish', async ({ page }) => {
  const { dialog } = await openPublicationPanel(page, { readOnly: true });
  await dialog.getByRole('button', { name: 'Проверить изменения' }).click();
  await expect(dialog.getByText('Предварительный просмотр: эта версия открыта только для чтения.')).toBeVisible();
  await expect(dialog.getByRole('button', { name: /Опубликовать/ })).toBeDisabled();
  await expect(dialog.getByLabel('Я проверил изменения и подтверждаю публикацию шаблона')).toHaveCount(0);
});

test('changing publication scope clears confirmation and publish uses the latest exact preview token', async ({ page }) => {
  const { dialog, getPublishPayload } = await openPublicationPanel(page);
  await dialog.getByRole('button', { name: 'Проверить изменения' }).click();
  await dialog.getByRole('checkbox', { name: /Я проверил изменения/ }).check();
  await expect(dialog.getByRole('button', { name: /Опубликовать с замечаниями шаблон/ })).toBeEnabled();

  await dialog.getByLabel('Вид публикации').selectOption('week');
  await expect(dialog.getByRole('checkbox', { name: /Я проверил изменения/ })).toHaveCount(0);
  await expect(dialog.getByRole('button', { name: /Опубликовать.*неделю/ })).toBeDisabled();
  await dialog.getByLabel('Неделя публикации').fill('2026-10-26');
  await dialog.getByRole('button', { name: 'Проверить изменения' }).click();
  await expect(dialog.getByRole('checkbox', { name: /Я проверил изменения/ })).not.toBeChecked();
  await dialog.getByRole('checkbox', { name: /Я проверил изменения/ }).check();
  const publishResponse = page.waitForResponse((response) => response.url().endsWith(`/api/admin/schedule/constructor/${DRAFT_ID}/publication/publish`));
  await dialog.getByRole('button', { name: /Опубликовать с замечаниями неделю/ }).click();
  expect((await publishResponse).ok()).toBeTruthy();
  expect(getPublishPayload()).toMatchObject({ scope_kind: 'week', week_start: '2026-10-26', preview_token: 'ui-preview-token-2', confirmed: true });
  await expect(dialog.getByText(new RegExp(`Версия: ${PUBLISHED_ID}`))).toBeVisible();
  await expect(dialog.getByRole('link', {name:'Открыть опубликованное расписание'})).toHaveAttribute('href', /week_start=2026-10-26/);
  await dialog.getByText('Журнал публикаций этого черновика').click();
  await expect(dialog.getByText(`Версия недели: ${PUBLISHED_ID}`)).toBeVisible();
});
