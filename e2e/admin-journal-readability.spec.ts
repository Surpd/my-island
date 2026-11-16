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
  await page
    .locator('.journal-table tbody tr')
    .first()
    .getByRole('button', { name: 'Сопоставить ученика' })
    .click();
  await expect(page.locator('.journal-table')).toHaveCount(0);
});
