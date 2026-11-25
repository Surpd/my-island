import { expect } from '@playwright/test';
import { test } from './helpers/experiment-test';

const API = 'http://127.0.0.1:8011';
const DRAFT_ID = 'publication-ui-fixture';
const VERSION_ID = 'constructor-template-ui-e2e-version';
const PUBLISHED_ID = 'published-ui-e2e-week';

type PreviewPayload = {
  published_source: {records: {lesson_id:string; status:string}[]};
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
    related_lesson_ids?: string[];
    affected_student_ids?: string[];
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
    { severity: 'conflict', code: 'teacher_overlap', message: 'Нужно проверить пересечение занятий.', lesson_id: 'lesson-conflict', related_lesson_ids: ['lesson-related'], context },
    { severity: 'conflict', code: 'teacher_overlap', message: 'Нужно проверить пересечение занятий.', lesson_id: 'lesson-related', related_lesson_ids: ['lesson-conflict'], context },
    { severity: 'conflict', code: 'room_overlap', message: 'Кабинет занят.', lesson_id: 'lesson-conflict', related_lesson_ids: ['lesson-related'], context },
  ];
  if (critical) diagnostics.push({ severity: 'critical', code: 'review_required', message: 'Не подтверждена аудитория занятия.', lesson_id: 'lesson-critical', context });
  return {
    published_source: { records: [{lesson_id: 'lesson-info', status: 'resolved_with_remarks'}] },
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

async function openPublicationPanel(page: import('@playwright/test').Page, options: { readOnly?: boolean; critical?: boolean; studentOverlap?: boolean; legacyReview?: boolean; applyNotes?: boolean } = {}) {
  let previewCount = 0;
  let normalizationCount = 0;
  let appliedNoteBody: unknown = null;
  let noteApplied = false;
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
      return route.fulfill({ json: { groups: [{ id: 'group-fixture', name: '7А', display_name: '7А', group_type: 'class', base_class_name: '7' }, ...(options.legacyReview ? [{ id: 'group-secondary', name: '7Б', display_name: '7Б', group_type: 'class', base_class_name: '7' }] : [])], teachers: [{ id: 'teacher-fixture', display_name: 'Анна Учитель' }], students: [{id: 'student-fixture', display_name: 'Мария Тестова'}], rooms: [] } });
    }
    if (path.endsWith('/export/link')) return route.fulfill({ json: { url: 'https://docs.google.com/spreadsheets/d/synthetic/edit#gid=1088', current: true, revision: 1, previous_revision: null } });
    if (path === '/api/admin/schedule/experiments/school/audience-preview') return route.fulfill({json:{lessons:{}}});
    if (path === '/api/admin/school/teacher-ui-colors') return route.fulfill({json:{items:[]}});
    if (path === '/api/admin/schedule/observability') return route.fulfill({ json: { blocks: [], weeks: [], effective_weeks: [], week_publications: [], canonical: { version_id: 'base-version-hidden-in-ui', status: 'authoritative' } } });
    if (path.startsWith('/api/admin/schedule/activities')) return route.fulfill({ json: { items: [] } });
    if (path === `/api/admin/schedule/experiments/school/${DRAFT_ID}`) {
      const lessons = ['lesson-info', 'lesson-conflict', 'lesson-related', 'lesson-critical', 'lesson-preserved', 'lesson-excluded'].map(id => ({
        id, day: 0, period: 0, subject: id === 'lesson-related' ? 'Физика' : 'Математика', room: '309', classId: 'group-fixture', ...(options.legacyReview && id === 'lesson-info' ? { classIds: ['group-fixture', 'group-secondary'] } : {}), teacherId: 'teacher-fixture',
      }));
      return route.fulfill({ json: { id: DRAFT_ID, name: 'Шаблон для публикации', revision: 1, base_version_id: 'base-version-hidden-in-ui', payload: { days: [0], slots: [{ start: '09:00', end: '09:45' }], classes: ['group-fixture', ...(options.legacyReview ? ['group-secondary'] : [])], lessons } } });
    }
    if (path === `/api/admin/schedule/experiments/school/${DRAFT_ID}/normalization-apply`) {
      appliedNoteBody = route.request().postDataJSON();
      noteApplied = true;
      return route.fulfill({json:{cleared_count:1,draft:{id:DRAFT_ID, name:'Шаблон для публикации',revision:2,
        payload:{days:[0], slots:[{start:'09:00',end:'09:45'}], classes:['group-fixture'], lessons:[]}}}});
    }
    if (path === `/api/admin/schedule/experiments/school/${DRAFT_ID}/normalization-preview`) {
      normalizationCount += 1;
      const examples = [{ lesson_id: 'lesson-info', subject: 'Математика' }];
      const warnings = options.legacyReview ? [
        { reason: 'Нет подтверждённого назначения преподавателя группе', count: 1, examples },
        { reason: 'Не подтверждён преподаватель', count: 1, examples },
        { reason: 'unsupported_audience_v3: check legacy roster', count: 1, examples },
      ] : [];
      const changes = options.applyNotes && !noteApplied ? [
        {lesson_id:'lesson-info',field:'teacher_identity_warning',current_note_eligible:true},
        {lesson_id:'lesson-related',field:'activity_type',current_note_eligible:false},
      ] : [];
      return route.fulfill({ json: { source_id: DRAFT_ID, source_revision: noteApplied ? 2 : 1, source_hash:'fixture-source',
        plan_hash:'fixture-plan', automatic_count: changes.length, changes, manual: [], retained_warning_count: warnings.length, retained_warnings: warnings } });
    }
    if (path === `/api/admin/schedule/constructor/${DRAFT_ID}/audiences/preview`) {
      return route.fulfill({ json: { revision: 1, summary: { selected_count: 0 }, lessons: [] } });
    }
    if (path === '/api/admin/schedule/constructor/publication/publication-fixture/sheet-retry' && route.request().method() === 'POST') {
      historyItems = historyItems.map(item => ({...item, sheet_delivery: {state: 'pending'}}));
      return route.fulfill({json: {state: 'pending'}});
    }
    if (path === `/api/admin/schedule/constructor/${DRAFT_ID}/publications`) return route.fulfill({ json: { items: historyItems, heads: {} } });
    if (path === `/api/admin/schedule/constructor/${DRAFT_ID}/publication/preview` && route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as { scope_kind: 'template' | 'week'; week_start?: string };
      previewCount += 1;
      const data = preview(`ui-preview-token-${previewCount}`, { ...options, scope: body.scope_kind, week: body.week_start });
      if (options.studentOverlap) data.diagnostics.push({severity: 'conflict', code: 'student_overlap', message: '',
        lesson_id: 'lesson-conflict', related_lesson_ids: ['lesson-related'], affected_student_ids: ['student-fixture']});
      return route.fulfill({ json: data });
    }
    if (path === `/api/admin/schedule/constructor/${DRAFT_ID}/publication/publish` && route.request().method() === 'POST') {
      publishPayload = route.request().postDataJSON() as Record<string, unknown>;
      const scope = publishPayload.scope_kind === 'week' ? 'week' : 'template';
      const publishedWeek = typeof publishPayload.week_start === 'string' ? publishPayload.week_start : '';
      const historyPreview = preview('history');
      historyItems = [{
        id: 'publication-fixture', ...(scope === 'week' ? {sheet_delivery: {state: 'retry'}} : {}), scope_kind: scope, scope_key: scope === 'week' ? `week:${publishedWeek}` : 'template',
        created_at: '2026-10-27T09:15:00Z', published_id: PUBLISHED_ID, version_id: VERSION_ID,
        runtime_readback: { verified: true, block_count: 7 }, summary: { ready_lessons: 4, deferred_lessons: 2, deferred_blocks: 1 },
        diagnostics: historyPreview.diagnostics.filter((item) => item.severity !== 'critical'), deferred: historyPreview.deferred,
      }];
      return route.fulfill({ json: { status: 'published', sheet_delivery_queued: scope === 'week', published_id: PUBLISHED_ID, version_id: VERSION_ID, created_at: '2026-10-27T09:15:00Z', week_start: (publishPayload.week_start as string | undefined) || null, summary: { ready_lessons: 4, deferred_lessons: 2, deferred_blocks: 1 }, runtime_readback: { verified: true, block_count: 7, assignment_count: 4 } } });
    }
    return route.continue();
  });

  await page.goto('/admin/schedule/constructor?role=admin');
  await page.getByRole('button', { name: 'Проверить расписание', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Проверка расписания' });
  await expect(dialog).toBeVisible();
  return { dialog, getPublishPayload: () => publishPayload, getReviewRequests: () => ({ previewCount, normalizationCount }), getAppliedNoteBody: () => appliedNoteBody };
}

test('publication preview groups pairs, separates severity and keeps the affected lesson details', async ({ page }) => {
  const { dialog } = await openPublicationPanel(page, { critical: true });
  const severity = dialog.getByLabel('Серьёзность', { exact: true });
  const issues = dialog.locator('[data-review-issue]');
  await expect(dialog.getByRole('heading', { name: 'Что требует внимания' })).toBeVisible();
  await expect(dialog.getByRole('button', { name: /Опубликовать/ })).toHaveCount(0);
  await expect(severity).toHaveValue('all');

  await severity.selectOption('conflict');
  await expect(issues).toHaveCount(1);
  const pairedIssue = issues.filter({ hasText: 'Математика / Физика' });
  await expect(pairedIssue).toHaveAttribute('data-review-issue', /teacher_overlap.*room_overlap/);
  await expect(pairedIssue).toHaveAttribute('data-review-lesson-ids', 'lesson-conflict lesson-related');
  await dialog.getByLabel('Тип проблемы', { exact: true }).selectOption('room');
  await expect(pairedIssue).toBeVisible();
  await pairedIssue.getByRole('button', { name: 'Показать оба занятия' }).click();
  await expect(pairedIssue.getByRole('button', { name: 'Показать в расписании — Математика' })).toBeVisible();
  await expect(pairedIssue.getByRole('button', { name: 'Показать в расписании — Физика' })).toBeVisible();
  await expect(pairedIssue.getByText(/Анна Учитель · каб\. 309/)).toHaveCount(2);

  await dialog.getByLabel('Тип проблемы', { exact: true }).selectOption('');
  await severity.selectOption('info');
  await expect(issues).toHaveCount(1);
  await severity.selectOption('critical');
  await expect(issues).toHaveCount(1);
  await issues.getByRole('button', { name: 'Блокирует публикацию Уточните данные занятия', exact: true }).click();
  await expect(issues.getByText(/Сверьте предмет, преподавателя и состав/)).toBeVisible();
  await expect(issues.getByText(/teacher-fixture|group-fixture|lesson-critical/)).toHaveCount(0);

  await severity.selectOption('deferred');
  await expect(issues).toHaveCount(2);
  const preserved = dialog.locator('[data-review-lesson-ids="lesson-preserved"]');
  await preserved.locator('.constructor-review-card-toggle').click();
  await expect(preserved.getByText('Сохранится прежнее занятие из опубликованной версии.')).toBeVisible();
  const excluded = dialog.locator('[data-review-lesson-ids="lesson-excluded"]');
  await excluded.locator('.constructor-review-card-toggle').click();
  await expect(excluded.getByText('Занятие не войдёт в эту публикацию.')).toBeVisible();

  await dialog.getByRole('button', { name: 'Публикация', exact: true }).click();
  await expect(dialog.locator('.constructor-publication-summary').getByText(/с пересечениями/)).toContainText('2');
  await expect(dialog.getByRole('button', { name: /Опубликовать/ })).toBeDisabled();
  await dialog.getByRole('button', { name: 'Показать блокирующие проблемы' }).click();
  await expect(severity).toHaveValue('critical');
});

test('diagnostics copy a report without names, codes or lesson links', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: async (text: string) => { document.documentElement.dataset.copiedReport = text; } } });
  });
  const { dialog } = await openPublicationPanel(page);
  await dialog.getByText('Отчёт проверки', { exact: true }).click();
  await expect(dialog.getByRole('button', { name: 'Скопировать обезличенный отчёт' })).toBeVisible();
  await expect(dialog.locator('[data-review-issue~="teacher_overlap"]')).toHaveCount(1);
  await dialog.getByRole('button', { name: 'Скопировать обезличенный отчёт' }).click();
  await expect(dialog.getByText(/Обезличенный отчёт проверки скопирован/)).toBeVisible();
  const copiedReport = await page.locator('html').getAttribute('data-copied-report');
  expect(copiedReport).toContain('У преподавателя пересекаются занятия');
  expect(copiedReport).not.toContain(DRAFT_ID);
  expect(copiedReport).not.toContain('lesson-conflict');
  expect(copiedReport).not.toContain('teacher_overlap');
  expect(copiedReport).not.toContain('Нужно проверить пересечение занятий');
  await page.screenshot({ path: 'test-results/schedule-publication-preview.png', fullPage: true });
});

test('read-only preview remains available and cannot publish', async ({ page }) => {
  const { dialog } = await openPublicationPanel(page, { readOnly: true });
  await dialog.getByRole('button', { name: 'Публикация', exact: true }).click();
  await expect(dialog.getByText('Эта версия доступна для просмотра.')).toBeVisible();
  await expect(dialog.getByRole('button', { name: /Опубликовать/ })).toHaveCount(0);
  await expect(dialog.getByRole('checkbox', { name: /Подтверждаю публикацию/ })).toHaveCount(0);
});

test('recovers a committed publication after the POST response is lost', async ({ page }) => {
  const { dialog } = await openPublicationPanel(page);
  let operationId = '';
  let writes = 0;
  await page.route(`${API}/api/admin/schedule/constructor/${DRAFT_ID}/publication/publish`, async route => {
    operationId = route.request().postDataJSON().operation_id;
    writes += 1;
    await route.abort('connectionreset');
  });
  await page.route(`${API}/api/admin/schedule/constructor/publication/receipts/*`, async route => {
    expect(operationId).toMatch(/^[a-f0-9-]{36}$/);
    expect(route.request().url()).toMatch(new RegExp(operationId + '$'));
    await route.fulfill({json: {operation_id: operationId, published_id: PUBLISHED_ID,
      runtime_readback: {verified: true, block_count: 1, assignment_count: 1}}});
  });
  await dialog.getByRole('button', { name: 'Публикация', exact: true }).click();
  await dialog.getByRole('checkbox', { name: /Подтверждаю публикацию/ }).check();
  await dialog.getByRole('button', { name: /Опубликовать с замечаниями шаблон/ }).click();
  await expect(dialog.getByText(/Проверка после публикации пройдена/)).toBeVisible();
  expect(writes).toBe(1);
});

test('changing publication scope clears confirmation and publish uses the latest exact preview token', async ({ page }) => {
  const { dialog, getPublishPayload } = await openPublicationPanel(page);
  await dialog.getByRole('button', { name: 'Публикация', exact: true }).click();
  await dialog.getByRole('checkbox', { name: /Подтверждаю публикацию/ }).check();
  await expect(dialog.getByRole('button', { name: /Опубликовать с замечаниями шаблон/ })).toBeEnabled();

  await dialog.getByLabel('Вид публикации').selectOption('week');
  await expect(dialog.getByRole('checkbox', { name: /Подтверждаю публикацию/ })).toHaveCount(0);
  await expect(dialog.getByRole('button', { name: /Опубликовать.*неделю/ })).toHaveCount(0);
  await dialog.getByLabel('Неделя публикации').fill('2026-10-26');
  await dialog.getByRole('button', { name: 'Проверить изменения' }).click();
  await expect(dialog.getByRole('checkbox', { name: /Подтверждаю публикацию/ })).not.toBeChecked();
  await dialog.getByRole('checkbox', { name: /Подтверждаю публикацию/ }).check();
  const publishResponse = page.waitForResponse((response) => response.url().endsWith(`/api/admin/schedule/constructor/${DRAFT_ID}/publication/publish`));
  await dialog.getByRole('button', { name: /Опубликовать с замечаниями неделю/ }).click();
  expect((await publishResponse).ok()).toBeTruthy();
  expect(getPublishPayload()).toMatchObject({ scope_kind: 'week', week_start: '2026-10-26', preview_token: 'ui-preview-token-2', confirmed: true });
  await expect(dialog.getByText(/Проверка после публикации пройдена/)).toBeVisible();
  await expect(dialog.getByText(/Задание на выгрузку в Google Sheets создано/)).toBeVisible();
  await expect(dialog.getByRole('link', {name:'Открыть опубликованное расписание'})).toHaveAttribute('href', /week_start=2026-10-26/);
  await dialog.getByRole('button', { name: 'Закрыть проверку' }).click();
  await page.locator('.constructor-draft-menu > summary').click();
  await page.getByRole('button', { name: 'История публикаций', exact: true }).click();
  const history = page.getByRole('dialog', { name: 'История публикаций' });
  await expect(history.getByText(`Версия недели: ${PUBLISHED_ID}`)).toBeVisible();
  await expect(history.getByText(/Google Sheets · ожидает повторной попытки/)).toBeVisible();
  await history.getByRole('button', {name: 'Повторить выгрузку'}).click();
  await expect(history.getByText(/Google Sheets · ожидает выгрузки/)).toBeVisible();
  await expect(history.getByText(`Версия недели: ${PUBLISHED_ID}`)).toBeVisible();
});

test('показывает имя из справочника в единой карточке и прямую ссылку на выгрузку', async ({page}) => {
  const {dialog} = await openPublicationPanel(page, {studentOverlap: true});
  await dialog.getByLabel('Серьёзность', {exact: true}).selectOption('conflict');
  await dialog.getByPlaceholder('Предмет, кабинет, преподаватель или фамилия ученика').fill('Тестова');
  const issue = dialog.locator('[data-review-issue]');
  await expect(issue).toHaveCount(1);
  await expect(issue).toContainText('Мария Тестова');
  await expect(issue).not.toContainText('student-fixture');
  await dialog.getByRole('button', {name: 'Закрыть проверку', exact: true}).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole('link', {name: 'Открыть таблицу', exact: true})).toHaveAttribute('href', /#gid=1088$/);
  let writes = 0;
  await page.route(`${API}/api/admin/schedule/experiments/school/${DRAFT_ID}/export`, route => {
    writes += 1;
    return route.fulfill({json: {status: 'exported', url: 'https://docs.google.com/spreadsheets/d/synthetic/edit#gid=1088'}});
  });
  await page.route(`${API}/api/admin/schedule/experiments/school/${DRAFT_ID}/export/preview`, route => {
    const { expected_revision } = route.request().postDataJSON();
    expect(expected_revision).toBe(1);
    return route.fulfill({json: {draft_id: DRAFT_ID, revision: 1, kind: 'template', title: 'Шаблон · демо',
      status: 'prepared', preview_token: 'fixture', summary: {lessons: 1, placements: 1, clarifications: 0, rows: 8, columns: 9},
      grid: {rows: 8, columns: 9, column_widths: [98, ...Array(8).fill(146)], row_heights: Array(8).fill(50),
        days: [{row: 4, label: 'Среда'}], cells: [{row: 4, col: 0, width: 1, height: 1, text: '09:00–09:45', align: 'LEFT'},
          {row: 4, col: 8, width: 1, height: 1, text: 'Физика', align: 'LEFT'}]}}});
  });
  await page.getByRole('button', {name: 'Экспортировать', exact: true}).click();
  const sheet = page.getByRole('dialog', {name: 'Экспорт в Google Sheets'});
  await sheet.getByText('Показать весь лист').click();
  const viewport = sheet.getByRole('region', {name: 'Лист экспорта целиком'});
  await expect(viewport.getByText('Физика')).toBeVisible();
  await page.setViewportSize({width: 390, height: 844});
  await viewport.evaluate(element => {element.scrollLeft = element.scrollWidth;});
  await expect.poll(() => viewport.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
  await expect(sheet.getByRole('button', {name: 'Создать лист экспорта'})).toBeDisabled();
  expect(writes).toBe(0);
  await sheet.getByRole('checkbox', {name: 'Подтверждаю создание отдельного листа экспорта'}).check();
  await sheet.getByRole('button', {name: 'Создать лист экспорта'}).click();
  await expect(sheet.getByRole('link', {name: 'Открыть экспортированный лист'})).toHaveAttribute('href', /#gid=1088$/);
  expect(writes).toBe(1);
});


test('legacy warning rows name both classes and return to the same review without rechecking', async ({ page }) => {
  const { dialog, getReviewRequests } = await openPublicationPanel(page, { legacyReview: true });
  await page.setViewportSize({width: 1200, height: 900});
  const fixesTab = dialog.getByRole('button', { name: 'Составы и старые записи', exact: true });
  await fixesTab.click();
  const held = dialog.locator('details[data-review-fix="delivery-deferred"]');
  await held.locator('summary').click();
  await expect(held.getByText('Сохранится прежнее занятие из опубликованной версии.')).toBeVisible();
  await expect(held.getByText('Занятие не войдёт в эту публикацию.')).toBeVisible();
  const retained = dialog.locator('details[data-review-fix="legacy-retained"]');
  const category = retained.locator(':scope > summary');
  await expect(category).toHaveAttribute('aria-expanded', 'false');
  await category.press('Enter');
  await expect(category).toHaveAttribute('aria-expanded', 'true');
  await expect(retained.getByText('unsupported_audience_v3: check legacy roster', { exact: true })).toBeHidden();
  await expect(retained.getByText('Нужно уточнить данные занятия', { exact: true })).toBeVisible();
  await expect(retained.getByText('Не подтверждён преподаватель', { exact: true })).toBeVisible();
  await expect(retained.getByText('Не подтверждено назначение преподавателя группе', { exact: true })).toBeVisible();
  const target = retained.locator('.constructor-review-warning').filter({ hasText: 'Нужно уточнить данные занятия' })
    .getByRole('button', { name: /Показать в расписании/ });
  await expect(target.locator('..')).toContainText(/7А\s*\/\s*7Б/);
  await expect(target.locator('..')).toContainText('по подтверждённым назначениям');
  await expect(category).toContainText('Замечания, которые сохранятся в копии');
  await target.focus();
  const checks = getReviewRequests();
  expect(checks).toEqual({ previewCount: 1, normalizationCount: 1 });
  await target.press('Enter');
  await expect(dialog).toBeHidden();
  // Reproduce a lingering grid selection after the linked warning is opened.
  await page.getByRole('button', {name:'Закрыть панель', exact:true}).click();
  await page.locator('[data-lesson-id="lesson-related"]').first().click();
  const editLinked = page.getByRole('region', {name:'Выбранная проблема'}).getByRole('button', {name:'Редактировать занятие', exact:true});
  await editLinked.click();
  await expect(page.locator('.constructor-inspector.is-open').getByRole('heading', {name:'Математика', exact:true})).toBeVisible();
  await page.getByRole('button', { name: 'Назад к проверке', exact: true }).first().click();
  await expect(fixesTab).toHaveAttribute('aria-pressed', 'true');
  await expect(category).toHaveAttribute('aria-expanded', 'true');
  await expect(target).toBeFocused();
  expect(getReviewRequests()).toEqual(checks);
});

test('checking the whole draft shows roster changes without saving them', async ({ page }) => {
  const writes: string[] = [];
  page.on('request', request => {
    if (request.method() === 'PUT' || new URL(request.url()).pathname.endsWith('/audiences/apply')) writes.push(request.url());
  });
  const { dialog } = await openPublicationPanel(page);
  const rosterBodies: unknown[] = [];
  await page.route('**/audiences/preview', route => {
    const body = route.request().postDataJSON();
    rosterBodies.push(body);
    const wholeDraft = body.scope === 'template';
    return route.fulfill({ json: { revision: 1, preview_token: 'synthetic',
      summary: wholeDraft
        ? { selected_count: 1, changed_count: 1, safe_count: 1, unresolved_count: 0, added_count: 0, removed_count: 9 }
        : { selected_count: 0, changed_count: 0, safe_count: 0, unresolved_count: 0, added_count: 0, removed_count: 0 },
      lessons: wholeDraft
        ? [{ id: 'lesson-excluded', subject: 'Математика', changed: true, safe: true, old_count: 25, proposed_count: 16 }]
        : [] } });
  });
  const initialPreview = page.waitForResponse(response => response.url().endsWith('/audiences/preview'));
  await dialog.getByRole('button', { name: 'Составы и старые записи' }).click();
  await initialPreview;
  expect(rosterBodies).toEqual([{ expected_revision: 1, scope: 'stale' }]);
  await dialog.locator('[data-review-fix="roster-scope"] > summary').click();
  const scope = dialog.getByLabel('Проверять при выборе');
  await expect(scope).toHaveValue('stale');
  const wholeDraftPreview = page.waitForResponse(response => response.url().endsWith('/audiences/preview'));
  await scope.selectOption({ label: 'Весь черновик' });
  await wholeDraftPreview;
  await expect(scope).toHaveValue('template');
  expect(rosterBodies).toEqual([{ expected_revision: 1, scope: 'stale' }, { expected_revision: 1, scope: 'template' }]);
  await expect(dialog.getByText(/Проверено занятий: 1/)).toBeVisible();
  const rosters = dialog.locator('[data-review-fix="roster-lessons"]');
  await rosters.locator('summary').click();
  await expect(rosters).toContainText('В черновике — 25, в текущем составе — 16.');
  await expect(rosters).toContainText('Занятие не войдёт в эту публикацию.');
  expect(writes).toEqual([]);
});


test('applies only the explicitly checked verified note to the same draft revision', async ({ page }) => {
  const {dialog, getAppliedNoteBody, getPublishPayload} = await openPublicationPanel(page, {applyNotes:true});
  await dialog.getByRole('button', {name:'Составы и старые записи',exact:true}).click();
  await dialog.getByText('Можно исправить в копии', {exact:true}).click();
  const checkbox = dialog.getByRole('checkbox', {name:'Снять эту пометку в открытом черновике'});
  await expect(checkbox).toHaveCount(1);
  await expect(dialog.getByRole('button', {name:'Снять выбранные пометки в этом черновике (0)'})).toBeDisabled();
  await checkbox.check();
  await dialog.getByRole('button', {name:'Снять выбранные пометки в этом черновике (1)'}).click();
  await expect(dialog.getByText(/Снято старых пометок: 1/)).toBeVisible();
  expect(getAppliedNoteBody()).toEqual({expected_revision:1,expected_source_hash:'fixture-source',
    expected_plan_hash:'fixture-plan',selected:[{lesson_id:'lesson-info',field:'teacher_identity_warning'}]});
  expect(getPublishPayload()).toBeNull();
});
