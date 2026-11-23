import { expect, test } from '@playwright/test';
import { source, assessments, students } from './fixtures/admin-journal';

const sources = [
  source,
  { ...source, id: 99002, subject: 'Физика', sheet_title: 'Физика' },
  { ...source, id: 99003, grade: 8, spreadsheet_id: 'synthetic-8' },
];

test.beforeEach(async ({ page }) => {
  await page.route('**/api/admin/journals**', async (route) => {
    expect(route.request().method()).toBe('GET');
    const url = new URL(route.request().url());
    const selected =
      sources.find((item) => url.pathname.includes(`/${item.id}/`)) || source;
    let data: unknown = { items: [] };
    if (url.pathname.endsWith('/detail'))
      data = { source: selected, assessments, students, groups: ['A', 'B'] };
    else if (url.pathname === '/api/admin/journals/matching')
      data = { individuals: [], teams: [] };
    else if (url.pathname === '/api/admin/journals')
      data = {
        items: sources,
        known_sources: [
          { grade: 7, connected: true },
          { grade: 8, connected: true },
        ],
        scheduler_configured: true,
      };
    await route.fulfill({ json: data });
  });
  await page.goto('/admin/journals?role=admin');
  await page
    .getByRole('button', { name: /Математика/ })
    .first()
    .click();
  await expect(page.getByRole('tab', { name: 'II триместр', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.journal-table')).toHaveCount(0);
  await page.getByRole('tab', { name: /I триместр · текущий/ }).click();
  await expect(page.locator('.journal-table')).toBeVisible();
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
  await page.getByRole('tab', { name: 'II триместр', exact: true }).click();
  await expect(
    page.getByText('Журнал за выбранный триместр ещё не создан.'),
  ).toBeVisible();
  await page.getByRole('tab', { name: /I триместр · текущий/ }).click();
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
  await page.getByRole('tab', { name: /I триместр · текущий/ }).click();
  await page
    .locator('.journal-table tbody tr')
    .first()
    .getByRole('button', { name: 'Сопоставить ученика' })
    .click();
  await expect(page.locator('.journal-table')).toHaveCount(0);
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
  let previewCalls = 0;
  await page.route('**/api/admin/journals/connect', async (route) => {
    const input = route.request().postDataJSON();
    expect(input).toMatchObject({ grade: 5, term_number: 2, dry_run: true, auto_sync_enabled: false });
    previewCalls++;
    await route.fulfill({ json: previewCalls === 1 ? {
      status: 'success', tabs: [{ sheet_id: '2', label: 'II · История', status: 'success' }],
    } : {
      status: 'blocked', tabs: [{ sheet_id: '2', label: 'II · История', status: 'error',
        reason: 'ID или название вкладки уже привязано к другому периоду.' }],
    } });
  });
  await page.getByRole('button', { name: 'Все журналы' }).click();
  await page.getByRole('tab', { name: /^5 класс/ }).click();
  await page.getByRole('button', { name: 'Подключить таблицу', exact: true }).click();
  const editor = page.locator('.journal-connect-panel');
  const source = editor.getByLabel('Ссылка на таблицу');
  const commit = editor.getByRole('button', { name: 'Подключить и импортировать' });
  const preview = editor.getByRole('button', { name: 'Проверить без сохранения' });
  await source.fill('synthetic-journal-workbook-a');
  await expect(commit).toBeDisabled();
  await preview.click();
  await expect(editor.getByText('II · История')).toBeVisible();
  await expect(commit).toBeEnabled();
  await source.fill('synthetic-journal-workbook-b');
  await expect(commit).toBeDisabled();
  await preview.click();
  await expect(editor.getByText(/привязано к другому периоду/)).toBeVisible();
  await expect(commit).toBeDisabled();
  expect(previewCalls).toBe(2);
});
