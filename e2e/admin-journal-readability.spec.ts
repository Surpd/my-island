import { expect, test } from '@playwright/test';
import { source, assessments, students } from './fixtures/admin-journal';

const sources = [
  source,
  { ...source, id: 99002, subject: 'Физика', sheet_title: 'Физика' },
  { ...source, id: 99003, grade: 8, spreadsheet_id: 'synthetic-8' },
  { ...source, id: 99004, subject: 'Физика ', sheet_title: 'II · Физика', term_number: 2, period_status: 'archived' },
  { ...source, id: 99005, subject: 'II · Как работать', sheet_title: 'II · Как работать', status: 'error' },
  { ...source, id: 99006, grade: 11, spreadsheet_id: 'synthetic-11', subject: 'Русский язык', sheet_title: 'Русский язык' },
  { ...source, id: 99007, subject: 'Математика', sheet_title: 'II · Математика', term_number: 2, period_status: 'current', last_synced_at: '2026-11-26T08:01:00Z' },
  { ...source, id: 99008, grade: 8, spreadsheet_id: 'synthetic-8', sheet_title: 'II · Математика', term_number: 2, period_status: 'current' },
];

async function openLegacyJournal(page: import('@playwright/test').Page) {
  await page.getByRole('tab', { name: /11 класс/ }).click();
  await page.getByRole('button', { name: 'Русский язык', exact: true }).click();
  await expect(page.getByRole('tab', { name: /I полугодие · текущий/ })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.journal-table')).toBeVisible();
}

async function openModernJournal(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: 'Все журналы' }).click();
  await page.getByRole('tab', { name: /8 класс/ }).click();
  await page.getByRole('button', { name: 'Математика', exact: true }).click();
  await expect(page.getByRole('tab', { name: /^II триместр/ })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.modern-journal')).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/admin/journals**', async (route) => {
    if (route.request().method() !== 'GET') {
      if (route.request().url().endsWith('/source-refresh'))
        return route.fulfill({ json: { status: 'success' } });
      if (route.request().url().endsWith('/source-settings'))
        return route.fulfill({ json: { status: 'success' } });
      return route.fulfill({ json: {} });
    }
    const url = new URL(route.request().url());
    const selected =
      sources.find((item) => url.pathname.includes(`/${item.id}/`)) || source;
    let data: unknown = { items: [] };
    if (url.pathname.endsWith('/detail')) {
      const detailAssessments = selected.id === 99008
        ? [{ ...assessments[0], title: 'Дроби' }, ...assessments.slice(1)]
        : assessments;
      data = { source: selected, assessments: detailAssessments, students, groups: ['A', 'B'] };
    } else if (url.pathname === '/api/admin/journals/matching')
      data = { individuals: [], teams: [] };
    else if (url.pathname === '/api/admin/journals')
      data = {
        items: sources,
        known_sources: [
          { grade: 7, connected: true },
          { grade: 8, connected: true },
          { grade: 11, connected: true },
          { grade: 5, spreadsheet_id: 'synthetic-journal-workbook-a', connected: false },
        ],
        scheduler_configured: true,
      };
    await route.fulfill({ json: data });
  });
  await page.goto('/admin/journals?role=admin');
  await openLegacyJournal(page);
});

test('class cards count unique subjects and senior details use semesters', async ({ page }) => {
  await page.getByRole('button', { name: 'Все журналы' }).click();
  await expect(page.getByRole('tab', { name: /7 класс/ })).toContainText('2 предмета');
  await expect(page.getByRole('button', { name: /Как работать/ })).toHaveCount(0);
  await expect(page.getByText('Ошибки обновления', { exact: true })).toHaveCount(0);
  await page.getByRole('tab', { name: /11 класс/ }).click();
  await expect(page.getByRole('tab', { name: /11 класс/ })).toContainText('1 предмет');
  await page.getByRole('button', { name: 'Русский язык', exact: true }).click();
  const periodTabs = page.getByRole('tablist', { name: 'Учебный период' });
  await expect(periodTabs.getByRole('tab')).toHaveCount(2);
  await expect(periodTabs.getByRole('tab', { name: 'I полугодие · текущий' })).toHaveAttribute('aria-selected', 'true');
  await periodTabs.getByRole('tab', { name: 'II полугодие', exact: true }).click();
  await expect(page.getByText('Журнал за выбранное полугодие ещё не создан.')).toBeVisible();
});

test('modern II detail, archived I summary, and source menu use the intended entrypoints', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1050 });
  await openModernJournal(page);
  const table = page.locator('.modern-journal__table');
  await expect(table).toBeVisible();
  await expect(table.locator('thead th.is-required').first()).toHaveCSS('background-color', 'rgb(243, 236, 255)');
  await expect(table.locator('thead th.is-retake').first()).toHaveCSS('background-color', 'rgb(231, 251, 242)');

  const first = table.locator('tbody tr').first();
  const scroller = page.getByRole('region', { name: 'Таблица оценок с прокруткой' });
  const header = table.locator('thead');
  const titles = header.locator('.modern-journal__work-titles th strong');
  const metadata = header.locator('.modern-journal__work-meta-line');
  await expect(titles.first()).toHaveText('Дроби');
  await expect(titles).toHaveCount(18);
  await expect(metadata).toHaveCount(18);
  const shortTitle = (await titles.first().boundingBox())!;
  const longTitle = (await titles.nth(1).boundingBox())!;
  expect(shortTitle.height).toBeLessThan(longTitle.height);
  expect(shortTitle.y).toBe(longTitle.y);
  const firstMetadata = (await metadata.first().boundingBox())!;
  expect(firstMetadata.y).toBe((await metadata.nth(1).boundingBox())!.y);
  expect(firstMetadata.y).toBeGreaterThan(longTitle.y + longTitle.height);
  const headerTop = (await header.boundingBox())!.y;
  expect(await scroller.evaluate(element => {
    element.scrollTop = 180;
    return element.scrollTop;
  })).toBeGreaterThan(0);
  expect(Math.abs((await header.boundingBox())!.y - headerTop)).toBeLessThan(2);
  await scroller.evaluate(element => { element.scrollTop = 0; });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1441);
  expect(await scroller.evaluate(element => element.scrollWidth)).toBeGreaterThan(1440);
  const pinnedColumns = [first.getByRole('rowheader'), ...['learning_grade', 'required_grade', 'term_grade'].map(kind =>
    first.locator(`[data-summary="${kind}"]`))];
  const beforeScroll = await Promise.all(pinnedColumns.map(column => column.boundingBox()));
  await scroller.evaluate(element => { element.scrollLeft = 260; });
  const afterScroll = await Promise.all(pinnedColumns.map(column => column.boundingBox()));
  afterScroll.forEach((position, index) => expect(Math.abs(position!.x - beforeScroll[index]!.x)).toBeLessThan(2));
  await scroller.evaluate(element => { element.scrollLeft = 0; });
  await expect(first.locator('td').nth(5)).toHaveText('0');
  await expect(first.locator('td').nth(6)).toHaveText('н');
  await expect(table.getByRole('columnheader', { name: 'Учебные 40%' })).toBeVisible();
  await expect(first.locator('[data-summary="learning_grade"]')).toHaveText('0');
  await expect(first.locator('[data-summary="term_grade"]')).toContainText('1');
  await expect(first.locator('[data-summary="term_grade"]')).toContainText('Есть долг');
  await expect(first.locator('[data-summary="term_grade"]')).toHaveClass(/has-debt/);
  await scroller.screenshot({ path: testInfo.outputPath('modern-journal-table.png') });
  await page.getByRole('button', { name: 'Показать расчёт' }).click();
  await expect(first.locator('[data-summary="teacher_adjustment"]')).toHaveText('0');

  const search = page.getByPlaceholder('Найти ученика');
  await search.fill('Синтетический-2 Длинная');
  await expect(table.locator('tbody tr')).toHaveCount(1);
  await search.fill('');
  await page.getByRole('tab', { name: 'По работам' }).click();
  const works = page.getByRole('navigation', { name: 'Работы' });
  await expect(works.getByRole('button')).toHaveCount(18);
  await works.getByRole('button', { name: new RegExp(assessments[1].title) }).click();
  await expect(page.locator('article.modern-journal__work')).toContainText('Обязательная');
  await page.screenshot({ path: testInfo.outputPath('modern-journal-ii.png'), fullPage: true });

  const refreshRequest = page.waitForRequest(request => request.url().endsWith('/api/admin/journals/source-refresh'));
  await page.getByRole('button', { name: 'Настройки вкладки', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Отключить вкладку' })).toBeVisible();
  await page.getByRole('button', { name: 'Обновить вкладку' }).click();
  await refreshRequest;
  await expect(page.locator('.modern-journal')).toBeVisible();

  await page.getByRole('tab', { name: /^I триместр/ }).click();
  const archive = page.locator('.modern-journal--archive');
  await expect(archive).toBeVisible();
  await expect(archive.getByRole('columnheader')).toHaveCount(2);
  await expect(archive.getByRole('columnheader', { name: 'Итоговая оценка' })).toBeVisible();
  await expect(archive.getByText(assessments[0].title, { exact: true })).toHaveCount(0);
  await expect(archive.locator('tbody tr').first().locator('td').nth(0)).toHaveText('1');
  await page.screenshot({ path: testInfo.outputPath('archived-journal-i.png'), fullPage: true });
});

for (const width of [1920, 1440, 1280, 768, 430, 390, 360]) {
  test(`journal remains readable and scrollable at ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    const wrap = page.locator('.journal-table-wrap');
    const table = page.locator('.journal-table');
    await wrap.scrollIntoViewIfNeeded();
    await expect(table.locator('tbody tr')).toHaveCount(30);
    const first = table.locator('tbody tr').first();
    await expect(first.locator('td').nth(0)).toHaveText('1 / 20');
    await expect(first.locator('td').nth(3)).toHaveText('4 / 20');
    await expect(
      first.locator('td').nth(3).locator('.journal-mark'),
    ).toHaveCount(0);
    await expect(first.locator('td').nth(5)).toHaveText('0');
    await expect(first.locator('td').nth(6)).toHaveText('н');
    await expect(first.locator('td').nth(7)).toHaveText('—');
    await expect(first.locator('td').nth(8)).toHaveText('Выполнено');
    await expect(first.locator('td').nth(9)).toHaveText('Не выполнено');
    await expect(first.locator('.journal-summary--average_grade')).toHaveText(
      '4,25',
    );
    await expect(first.locator('.journal-summary--admission')).toHaveText(
      'Не допущен',
    );
    for (let mark = 1; mark <= 5; mark++)
      await expect(
        table
          .locator('tbody tr')
          .nth(mark - 1)
          .locator(`.journal-mark--${mark}`),
      ).toHaveText(String(mark));
    const before = await first.locator('th').boundingBox();
    await wrap.evaluate((element) => {
      element.scrollLeft = 1200;
    });
    const after = await first.locator('th').boundingBox();
    expect(Math.abs(after!.x - before!.x)).toBeLessThan(2);
    const sizes = await wrap.evaluate((element) => ({
      width: element.clientWidth,
      scroll: element.scrollWidth,
      left: element.scrollLeft,
    }));
    expect(sizes.scroll).toBeGreaterThan(sizes.width);
    expect(sizes.left).toBeGreaterThan(0);
    const name = await first.locator('.journal-student-name').boundingBox();
    const meta = await first.locator('.journal-student-meta').boundingBox();
    expect(meta!.y).toBeGreaterThanOrEqual(name!.y + name!.height);
    expect(name!.width).toBeLessThanOrEqual(after!.width);
    const summaries = first.locator('.journal-summary--term_grade');
    const position = await summaries.evaluate(
      (element) => getComputedStyle(element).position,
    );
    expect(position).toBe(sizes.width >= 1000 ? 'sticky' : 'static');
    if (sizes.width >= 1000) {
      const bounds = await first
        .locator('.journal-summary--average_grade')
        .boundingBox();
      expect(bounds!.x - (after!.x + after!.width)).toBeGreaterThan(300);
    }
    await page.screenshot({
      path: testInfo.outputPath(`journal-${width}-scroll.png`),
    });
    const height = after!.height;
    await first.getByRole('button', { name: 'Технические показатели' }).click();
    await expect(
      page.getByRole('region', { name: /Технические показатели:/ }),
    ).toContainText('123');
    expect((await first.locator('th').boundingBox())!.height).toBe(height);
    await page.getByRole('button', { name: 'Закрыть показатели' }).click();
    await expect(
      page.getByRole('region', { name: /Технические показатели:/ }),
    ).toHaveCount(0);
    await wrap.evaluate((element) => {
      element.scrollLeft = element.scrollWidth;
      element.scrollTop = 500;
    });
    const header = await table
      .locator('thead .journal-student-column')
      .boundingBox();
    const container = await wrap.boundingBox();
    expect(header!.y).toBeGreaterThanOrEqual(container!.y - 1);
  });
}

test('journal selectors keep existing navigation and matching actions', async ({
  page,
}) => {
  await page.getByRole('tab', { name: 'Группа B' }).click();
  await expect(page.getByRole('tab', { name: 'Группа B' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await page.getByRole('tab', { name: 'II полугодие', exact: true }).click();
  await expect(
    page.getByText('Журнал за выбранное полугодие ещё не создан.'),
  ).toBeVisible();
  await page.getByRole('tab', { name: /I полугодие · текущий/ }).click();
  await expect(page.locator('.journal-table')).toBeVisible();
  await page
    .getByRole('combobox', { name: 'Класс', exact: true })
    .selectOption('8');
  await expect(
    page.getByRole('combobox', { name: 'Класс', exact: true }),
  ).toHaveValue('8');
  await page
    .getByRole('combobox', { name: 'Класс', exact: true })
    .selectOption('7');
  await page.getByRole('combobox', { name: 'Предмет' }).selectOption('Физика');
  await expect(page.locator('.journal-detail-title')).toContainText('Физика');
  await expect(page.locator('.modern-journal')).toBeVisible();
  await page.getByRole('tab', { name: /^III триместр/ }).click();
  await expect(page.getByText('Журнал за выбранный триместр ещё не создан.')).toBeVisible();
  await page.getByRole('tab', { name: /^I триместр/ }).click();
  await expect(page.locator('.modern-journal--archive')).toBeVisible();
  await page
    .locator('.modern-journal--archive tbody tr')
    .first()
    .getByRole('button', { name: 'Сопоставить' })
    .click();
  await expect(page.locator('.modern-journal--archive')).toHaveCount(0);
});

test('compact journal menu shares layout space and can be pinned', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const sidebar = page.locator('.admin-console-sidebar');
  const wrap = page.locator('.journal-table-wrap');
  const assertLayout = async (width: number) => {
    await expect.poll(async () => Math.round((await sidebar.boundingBox())!.width)).toBe(width);
    const menu = (await sidebar.boundingBox())!;
    const main = (await page.locator('.admin-console-main').boundingBox())!;
    expect(main.x).toBeGreaterThanOrEqual(menu.x + menu.width - 1);
    const name = (await page.locator('.journal-table tbody th').first().boundingBox())!;
    const summary = (await page.locator('tbody .journal-summary--average_grade').first().boundingBox())!;
    expect(summary.x - name.x - name.width).toBeGreaterThan(300);
  };
  await page.mouse.move(800, 200);
  await assertLayout(58);
  await expect(sidebar.getByRole('button', { name: 'Журналы и оценки', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(sidebar.getByRole('navigation', { name: 'Основные разделы' }).locator('button')).toHaveCount(7);
  await expect(sidebar.locator('.admin-legacy-nav')).toHaveCount(0);
  await wrap.scrollIntoViewIfNeeded();
  const screenshot = async (name: string) => page.screenshot({ path: testInfo.outputPath(`${name}.png`) });
  const countVisible = async () => page.locator('.journal-table').evaluate(table => {
    const first = table.querySelector('tbody tr')!;
    const left = first.querySelector('th')!.getBoundingClientRect().right;
    const right = first.querySelector('.journal-summary--average_grade')!.getBoundingClientRect().left;
    return [...table.querySelectorAll('.journal-assessment-column')].filter(cell => { const r = cell.getBoundingClientRect(); return r.left >= left && r.right <= right; }).length;
  });
  console.log('Fully visible assessment columns, compact:', await countVisible());
  await screenshot('compact-1440');
  await sidebar.hover();
  await assertLayout(188);
  console.log('Fully visible assessment columns, expanded:', await countVisible());
  await screenshot('expanded-1440');
  await sidebar.getByRole('button', { name: 'Закрепить меню', exact: true }).click();
  await page.mouse.move(800, 200);
  await assertLayout(188);
  await screenshot('pinned-1440');
  await wrap.evaluate(element => { element.scrollLeft = 1200; });
  await assertLayout(188);
  await screenshot('pinned-scrolled-1440');
  await sidebar.getByRole('button', { name: 'Открепить меню', exact: true }).click();
  await page.mouse.move(800, 200);
  await assertLayout(58);
  const title = page.locator('.journal-assessment-column strong').first();
  await expect(title).toHaveAttribute('title', assessments[0].title);
  expect(await title.evaluate(e => e.clientHeight / parseFloat(getComputedStyle(e).lineHeight))).toBeLessThanOrEqual(4.1);
  await page.setViewportSize({ width: 390, height: 1000 });
  await page.mouse.move(380, 900);
  await expect.poll(async () => Math.round((await sidebar.boundingBox())!.width)).toBe(390);
  await screenshot('mobile-390');
});

test('other routes retain their sidebar mode', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/admin/people?role=admin');
  await expect(page.locator('.admin-console-sidebar')).not.toHaveClass(/constructor-sidebar/);
  await expect(page.locator('.admin-console-sidebar')).not.toHaveClass(/is-collapsed/);
  await page.goto('/admin/schedule/constructor?role=admin');
  const sidebar = page.locator('.admin-console-sidebar');
  await page.mouse.move(800, 200);
  await expect(sidebar).toHaveClass(/is-collapsed/);
  await sidebar.hover();
  await expect(sidebar).not.toHaveClass(/is-collapsed/);
  await expect.poll(async () => Math.round((await sidebar.boundingBox())!.width)).toBe(188);
  expect((await page.locator('.admin-console-main').boundingBox())!.x).toBeGreaterThanOrEqual(187);
});

test('II import is gated on fresh preflight of the exact class and book', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let previewCalls = 0;
  let commits = 0;
  const token = 'a'.repeat(64);
  await page.route('**/api/admin/journals/connect', async (route) => {
    const input = route.request().postDataJSON();
    expect(input).toMatchObject({ grade: 5, term_number: 2, auto_sync_enabled: false,
      spreadsheet_url: previewCalls === 1 && input.dry_run ? 'synthetic-journal-workbook-b' : 'synthetic-journal-workbook-a' });
    if (!input.dry_run) {
      expect(input).toMatchObject({ preflight_token: token, selected_sheet_ids: ['2'] });
      commits++;
      return route.fulfill({ json: { status: 'success', spreadsheet_id: 'synthetic-journal-workbook-a', tabs: [] } });
    }
    previewCalls++;
    await route.fulfill({ json: previewCalls !== 2 ? {
      status: 'success', preflight_token: token, tabs: [
        { sheet_id: '2', label: 'II · История', status: 'success' },
        { sheet_id: '3', label: 'II · История: практика', status: 'success', connected: true },
      ],
    } : {
      status: 'blocked', preflight_token: null, tabs: [{ sheet_id: '2', label: 'II · История', status: 'error',
        reason: 'ID или название вкладки уже привязано к другому периоду.' }],
    } });
  });
  await page.getByRole('button', { name: 'Все журналы' }).click();
  await page.getByRole('tab', { name: /^5 класс/ }).click();
  await page.getByRole('button', { name: 'Подключить Google Таблицу', exact: true }).click();
  const editor = page.locator('.journal-connect-panel');
  const source = editor.getByLabel('Ссылка на таблицу');
  const commit = editor.getByRole('button', { name: 'Подключить и импортировать' });
  const preview = editor.getByRole('button', { name: 'Проверить без сохранения' });
  await source.fill('synthetic-journal-workbook-a');
  await expect(commit).toBeDisabled();
  await preview.click();
  await expect(editor.getByRole('checkbox', { name: 'II · История', exact: true })).toBeChecked();
  await expect(editor.getByRole('checkbox', { name: /История: практика/ })).toBeChecked();
  await expect(editor.getByText('Выбрано: 2 из 2 проверенных вкладок.')).toBeVisible();
  await expect(commit).toBeEnabled();
  await editor.getByLabel('Класс', { exact: true }).selectOption('7');
  await expect(commit).toBeDisabled();
  await editor.getByLabel('Класс', { exact: true }).selectOption('5');
  await expect(commit).toBeDisabled();
  await source.fill('synthetic-journal-workbook-b');
  await expect(commit).toBeDisabled();
  await preview.click();
  await expect(editor.getByText(/привязано к другому периоду/)).toBeVisible();
  await expect(commit).toBeDisabled();
  await source.fill('synthetic-journal-workbook-a');
  await preview.click();
  await editor.getByRole('checkbox', { name: /История: практика/ }).uncheck();
  const history = editor.getByRole('checkbox', { name: 'II · История', exact: true });
  await history.uncheck();
  await expect(commit).toBeDisabled();
  await history.check();
  await expect(editor.getByText('Выбрано: 1 из 2 проверенных вкладок.')).toBeVisible();
  await commit.click();
  await expect(editor).toHaveCount(0);
  expect(previewCalls).toBe(3);
  expect(commits).toBe(1);
});

test('an administrator can archive a source, view its saved marks and enable it again', async ({ page }) => {
  const modes: string[] = [];
  await page.route('**/api/admin/journals/source-settings', async route => {
    const body = route.request().postDataJSON();
    expect(body.source_id).toBe(99007);
    modes.push(body.mode);
    await route.fulfill({ json: { status: 'success' } });
  });
  await page.getByRole('button', { name: 'Все журналы' }).click();
  await page.getByRole('tab', { name: /7 класс/ }).click();
  await page.getByText('Обновления и источники', { exact: true }).first().click();
  await page.getByText('Режимы синхронизации и отключённые вкладки', { exact: true }).click();
  const mode = page.getByLabel('Режим синхронизации: II · Математика', { exact: true });
  await mode.selectOption('auto');
  await expect(mode).toHaveValue('auto');
  await mode.selectOption('excluded');
  await expect(mode).toHaveValue('excluded');
  await page.getByRole('button', { name: 'Сохранённые оценки' }).click();
  await expect(page.locator('.modern-journal')).toBeVisible();
  await expect(page.getByText('Вкладка отключена', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Обновить вкладку' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Все журналы' }).click();
  if (!(await mode.isVisible())) await page.getByText('Режимы синхронизации и отключённые вкладки', { exact: true }).click();
  await mode.selectOption('manual');
  await expect(mode).toHaveValue('manual');
  expect(modes).toEqual(['auto', 'excluded', 'manual']);
});
