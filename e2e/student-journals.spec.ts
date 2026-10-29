import { expect, test, type Page } from '@playwright/test';

const SUBJECT = 'Математика';
const LONG_SUBJECT = 'Культура и история народов Тихоокеанского архипелага';

function journal(sourceId: string, periodStatus: string, term: number, marker: string, subject = SUBJECT, score: number | null = 5, status: string | null = null) {
  const isCurrent = periodStatus === 'current';
  return {
    source: {
      source_id: sourceId,
      spreadsheet_title: isCurrent ? `Журнал ${subject} · ${marker}` : `Архив ${subject} · ${marker}`,
      grade: 11,
      subject,
      sheet_title: `11 класс · ${marker}`,
      academic_year: '2026/27',
      term_number: term,
      period_status: periodStatus,
    },
    student: { journal_student_id: `student-${sourceId}`, display_name: 'E2E Student Fixture', group_marker: marker },
    // Deliberately supplied out of order to catch UI that does not sort assessments.
    assessments: isCurrent && sourceId === 'source-current'
      ? [
          { id: 'a-late', title: 'Последняя контрольная', assessed_on: '2026-10-20', weight: 2, max_score: 20, group_marker: marker },
          { id: 'a-missed', title: 'Работа со статусом', assessed_on: '2026-10-05', group_marker: marker },
          { id: 'a-zero', title: 'Нулевой результат', assessed_on: '2026-10-10', group_marker: marker },
          { id: 'a-old', title: 'Ранняя работа', assessed_on: '2026-09-25', group_marker: marker },
          { id: 'a-unpublished', title: 'Ещё не опубликовано', assessed_on: '2026-10-25', group_marker: marker },
          { id: 'a-undated', title: 'Работа без даты', group_marker: marker },
        ]
      : [{ id: `a-${sourceId}`, title: `Работа ${marker}`, assessed_on: '2026-09-15', group_marker: marker }],
    results: isCurrent && sourceId === 'source-current'
      ? [
          { assessment_id: 'a-late', numeric_score: 15, status: null },
          { assessment_id: 'a-missed', numeric_score: null, status: 'Не был' },
          { assessment_id: 'a-zero', numeric_score: 0, status: null },
          { assessment_id: 'a-old', numeric_score: 4, status: null },
          { assessment_id: 'a-undated', numeric_score: 3, status: null },
        ]
      : [{ assessment_id: `a-${sourceId}`, numeric_score: score, status }],
    // A supplied official summary takes precedence over the underlying points in the compact row.
    summaries: isCurrent && sourceId === 'source-current'
      ? [{ label: 'Официальная оценка', summary_type: 'average_grade', numeric_value: 4.6, text_value: null }]
      : [],
  };
}

async function installStudentApi(page: Page, journals: unknown, scheduleState = 'not_published') {
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    let body: unknown = {};
    if (url.pathname === '/api/auth/session') {
      body = { mode: 'e2e', state: 'approved', user: { id: 'synthetic-student', role: 'student', roles: ['student'] } };
    } else if (url.pathname === '/api/student/today') {
      body = { schedule: [], homework: [], announcements: [] };
    } else if (url.pathname === '/api/student/schedule') {
      body = { state: scheduleState, items: [], days: [], day_blocks: [] };
    } else if (url.pathname === '/api/student/homework') {
      body = { items: [] };
    } else if (url.pathname === '/api/student/profile') {
      body = { user: { display_name: 'Синтетический ученик', class_name: '7 класс' }, groups: [] };
    } else if (url.pathname === '/api/student/journals') {
      body = journals;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

async function openGrades(page: Page) {
  await page.goto('/?role=student&date=2026-10-26');
  await page.locator('.scene-hotspot.hotspot--grades').click();
  await page.getByRole('button', { name: 'Развернуть раздел', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Оценки', level: 2 })).toBeVisible();
  await expect(page.getByLabel('Период')).toBeVisible();
}

test('grades building opens a stable peek and browser back unwinds detail before the island', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installStudentApi(page, { items: [journal('source-current', 'current', 2, '11А'), journal('source-history', 'current', 2, '11Б', 'История', 5)] });
  await page.goto('/?role=student&date=2026-10-26');
  await expect(page.locator('.today-sheet')).toHaveCount(0); // the island starts closed
  await page.locator('.scene-hotspot.hotspot--grades').click();
  await expect(page.locator('.student-grades-peek')).toContainText('Математика');
  await expect(page.getByRole('button', { name: 'Все оценки' })).toBeVisible();
  await expect(page.locator('.student-journal-sheet')).toHaveClass(/is-partial/);
  await expect(page.locator('.sheet-full')).toHaveCSS('display', 'none');
  await page.getByRole('button', { name: 'Все оценки' }).click();
  await expect(page.locator('.student-journal-sheet')).toHaveClass(/is-full/);
  const row = page.getByRole('button', { name: `Открыть журнал: ${SUBJECT}`, exact: true }).first();
  await row.click();
  await expect(page.locator('.student-journal-detail')).toBeVisible();
  await page.goBack();
  await expect(page.locator('.student-journal-detail')).toHaveCount(0);
  await expect(page.locator('.student-journal-sheet')).toHaveClass(/is-full/);
  await page.goBack();
  await expect(page.locator('.student-journal-sheet')).toHaveClass(/is-partial/);
  await page.goBack();
  await expect(page.locator('.student-journal-sheet')).toHaveCount(0);
  await expect(page.locator('.scene-hotspot.hotspot--grades')).toBeVisible();
});

test('student reload preserves grade detail and data refresh preserves the expanded table for the same identity', async ({ page }) => {
  await installStudentApi(page, { items: [journal('source-current', 'current', 2, '11А')] });
  await openGrades(page);
  await page.getByRole('button', { name: `Открыть журнал: ${SUBJECT}`, exact: true }).click();
  await expect(page.locator('.student-journal-detail')).toBeVisible();
  await page.reload();
  await expect(page.locator('.student-journal-detail')).toBeVisible();
  await expect(page.locator('.student-journal-sheet')).toHaveClass(/is-full/);
  await page.getByRole('button', { name: 'К предметам', exact: true }).click();
  await page.getByRole('button', { name: 'Обновить данные' }).click();
  await expect(page.locator('.student-subject-row')).toHaveCount(1);
  await expect(page.locator('.student-journal-sheet')).toHaveClass(/is-full/);
});

test('sheet drag is limited to its handle and content scrolling does not collapse it', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installStudentApi(page, { items: [journal('source-current', 'current', 2, '11А')] });
  await page.goto('/?role=student&date=2026-10-26&screen=grades');
  const handle = page.locator('.student-journal-sheet .sheet-grabber');
  const bounds = await handle.boundingBox();
  expect(bounds).toBeTruthy();
  await page.mouse.move(bounds!.x + bounds!.width / 2, bounds!.y + bounds!.height / 2);
  await page.mouse.down();
  await page.mouse.move(bounds!.x + bounds!.width / 2, bounds!.y + bounds!.height / 2 - 100, { steps: 5 });
  await page.mouse.up();
  await expect(page.locator('.student-journal-sheet')).toHaveClass(/is-full/);
  await page.locator('.sheet-full').evaluate(element => element.scrollTo({ top: 100 }));
  await expect(page.locator('.student-journal-sheet')).toHaveClass(/is-full/);
  await page.getByRole('button', { name: 'Свернуть раздел' }).click();
  await expect(page.locator('.student-journal-sheet')).toHaveClass(/is-partial/);
  await page.getByRole('button', { name: 'Развернуть раздел' }).click();
  await expect(page.locator('.student-journal-sheet')).toHaveClass(/is-full/);
});

test('Today is an explicit campus level with a date, lessons, and a route to grades', async ({ page }) => {
  await installLessonApi(page, 'student');
  await page.goto('/?role=student&date=2026-10-26');
  await expect(page.locator('.today-sheet')).toHaveCount(0);
  await page.getByRole('button', { name: 'Сегодня', exact: true }).click();
  await expect(page.locator('.today-sheet')).toHaveClass(/is-partial/);
  await page.getByRole('button', { name: 'Развернуть раздел', exact: true }).click();
  await expect(page.locator('.today-expanded-date')).toBeVisible();
  await expect(page.locator('.today-expanded')).toContainText('Синтетическая математика');
  await page.getByRole('button', { name: 'Все оценки' }).click();
  await expect(page.locator('.student-journal-sheet')).toHaveClass(/is-partial/);
});

test('independent practice is distinct in Student and Teacher Campus', async ({ page }) => {
  await installLessonApi(page, 'student', true);
  await page.goto('/?role=student&date=2026-10-26');
  await page.locator('.scene-hotspot.hotspot--schedule').click();
  const studentSchedule = page.locator('.student-schedule-stack');
  await expect(studentSchedule).toContainText('Самостоятельная практика');
  await expect(studentSchedule).toContainText('Ответственный за задание: Педагог E2E');
  await expect(studentSchedule).not.toContainText('Педагог E2E · каб. 12');

  await page.unroute('**/api/**');
  await installLessonApi(page, 'teacher', true);
  await page.goto('/?role=teacher&date=2026-10-26');
  await page.locator('.scene-hotspot.hotspot--schedule').click();
  await expect(page.locator('.lesson-stack .lesson-card')).toContainText('Самостоятельная практика');
  await expect(page.locator('.lesson-stack .lesson-card')).toContainText('Вы не ведёте это занятие');
});

async function installLessonApi(page: Page, role: 'student' | 'teacher', independentPractice = false) {
  const lesson = {
    lesson_date: '2026-10-26',
    start_time: '09:05:00',
    end_time: '09:45:00',
    subject: 'Синтетическая математика',
    teacher: 'Педагог E2E',
    room: '12',
    audience: '7 класс',
    lesson_type: 'lesson',
    ...(independentPractice
      ? {
          activity_format: 'independent_practice',
          physical_presence: false,
          responsible_teachers: ['Педагог E2E'],
          teacher_names: ['Педагог E2E'],
        }
      : {}),
  };
  const lessons = [lesson];
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    let body: unknown = {};
    if (url.pathname === '/api/auth/session') {
      body = { mode: 'e2e', state: 'approved', user: { id: `synthetic-${role}`, role, roles: [role] } };
    } else if (role === 'student' && url.pathname === '/api/student/today') {
      body = { schedule: lessons, homework: [], announcements: [] };
    } else if (role === 'student' && url.pathname === '/api/student/schedule') {
      // Omit days so the client derives its display projection from these synthetic items.
      body = { state: 'published', items: lessons, day_blocks: [] };
    } else if (role === 'student' && url.pathname === '/api/student/homework') {
      body = { items: [] };
    } else if (role === 'student' && url.pathname === '/api/student/profile') {
      body = { user: { display_name: 'Синтетический ученик', class_name: '7 класс' }, groups: [] };
    } else if (role === 'student' && url.pathname === '/api/student/journals') {
      body = { items: [] };
    } else if (role === 'teacher' && url.pathname === '/api/teacher/home') {
      body = { schedule: lessons, groups: [], information: [] };
    } else if (role === 'teacher' && url.pathname === '/api/teacher/schedule') {
      body = { state: 'published', items: lessons };
    } else if (role === 'teacher' && url.pathname === '/api/teacher/courses') {
      body = { items: [] };
    } else if (role === 'teacher' && url.pathname === '/api/teacher/profile') {
      body = { state: 'approved', user: { display_name: 'Синтетический учитель' }, groups: [] };
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

test('student journals show a compact results table and preserve chronological detail', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await installStudentApi(page, {
    items: [
      journal('source-current', 'current', 2, '11A'),
      journal('source-current-b', 'current', 2, '11Б'),
      journal('source-long', 'current', 2, '11В', LONG_SUBJECT),
      journal('source-zero', 'current', 2, '11Г', 'Физика', 0),
      journal('source-status', 'current', 2, '11Д', 'Химия', null, 'Не был'),
      journal('source-archive', 'archived', 1, '11A'),
    ],
  });
  await openGrades(page);

  const mathCards = page.getByRole('button', { name: `Открыть журнал: ${SUBJECT}`, exact: true });
  await expect(mathCards).toHaveCount(2);
  await expect(page.locator('.student-subject-row')).toHaveCount(5);
  const officialRow = mathCards.first();
  const compactRowBox = await officialRow.boundingBox();
  expect(compactRowBox).toBeTruthy();
  expect(compactRowBox!.height).toBeLessThanOrEqual(100);
  await expect(officialRow.locator('.student-subject-results')).toContainText('15/20');
  await expect(officialRow.locator('.student-subject-status')).toContainText('Не был');
  await expect(officialRow.locator('.student-subject-results')).toContainText('0');
  await expect(officialRow.locator('.student-subject-results')).not.toContainText('4,6');
  await expect(mathCards.nth(1).locator('.student-subject-results')).toHaveText('5');
  await expect(mathCards.first()).toContainText('11A');
  await expect(mathCards.nth(1)).toContainText('11Б');
  await expect(page.getByRole('button', { name: `Открыть журнал: ${LONG_SUBJECT}`, exact: true })).toBeVisible();
  const longTitleBox = await page.getByRole('button', { name: `Открыть журнал: ${LONG_SUBJECT}`, exact: true }).locator('strong').boundingBox();
  expect(longTitleBox).toBeTruthy();
  expect(longTitleBox!.height).toBeGreaterThan(20);
  expect(longTitleBox!.height).toBeLessThanOrEqual(52);
  await expect(page.getByRole('button', { name: 'Открыть журнал: Физика', exact: true }).locator('.student-subject-results')).toHaveText('0');
  await expect(page.getByRole('button', { name: 'Открыть журнал: Химия', exact: true }).locator('.student-subject-results')).toContainText('—');
  await expect(page.getByRole('button', { name: 'Открыть журнал: Химия', exact: true }).locator('.student-subject-status')).toHaveText('Не был');
  for (const title of ['Последняя контрольная', 'Ещё не опубликовано', 'Работа 11A']) {
    await expect(page.getByText(title, { exact: true })).toHaveCount(0);
  }

  await mathCards.first().click();
  const detail = page.locator('.student-journal-detail');
  await expect(detail.getByRole('heading', { name: SUBJECT, level: 2 })).toBeVisible();
  await expect(detail.getByRole('heading', { name: 'Оценки', level: 2 })).toHaveCount(0);
  await expect(detail.getByRole('button', { name: 'К предметам', exact: true })).toBeVisible();
  await expect(detail.getByText(/15\s*\/\s*20/).first()).toBeVisible(); // points remain points, never become a grade average of 5
  await expect(detail.getByText('0', { exact: true })).toBeVisible(); // a real zero is not treated as missing
  await expect(detail.getByText('Не был', { exact: true })).toBeVisible();
  await detail.getByText('Показатели и сведения о журнале').click();
  await expect(detail.getByText('Показатель источника («Официальная оценка»)')).toBeVisible();
  await expect(detail.getByText(/вес\s+—|максимум\s+—/i)).toHaveCount(0);
  const detailText = await detail.innerText();
  expect(detailText.indexOf('Ещё не опубликовано')).toBeLessThan(detailText.indexOf('Последняя контрольная'));
  expect(detailText.indexOf('Последняя контрольная')).toBeLessThan(detailText.indexOf('Нулевой результат'));
  expect(detailText.indexOf('Нулевой результат')).toBeLessThan(detailText.indexOf('Работа со статусом'));
  expect(detailText.indexOf('Работа со статусом')).toBeLessThan(detailText.indexOf('Ранняя работа'));
  expect(detailText.indexOf('Ранняя работа')).toBeLessThan(detailText.indexOf('Работа без даты'));
  const sheet = page.locator('.student-journal-sheet');
  const sheetBox = await sheet.boundingBox();
  expect(sheetBox!.height).toBeGreaterThan(900 * 0.85);
  await expect(detail.locator('.student-journal-header')).toHaveCSS('position', 'sticky');
  const bottomPadding = await sheet.evaluate(element => Number.parseFloat(getComputedStyle(element).paddingBottom));
  expect(bottomPadding).toBeGreaterThanOrEqual(14);
  await page.evaluate(() => {
    document.documentElement.style.setProperty('--tg-safe-top', '47px');
    document.documentElement.style.setProperty('--tg-content-safe-top', '56px');
    document.documentElement.style.setProperty('--tg-content-safe-bottom', '34px');
  });
  expect(await sheet.evaluate(element => Number.parseFloat(getComputedStyle(element).paddingBottom))).toBeGreaterThanOrEqual(34);
  expect((await sheet.boundingBox())!.y).toBeGreaterThanOrEqual(103);
  await page.evaluate(() => {
    for (const property of ['--tg-safe-top', '--tg-content-safe-top', '--tg-content-safe-bottom']) document.documentElement.style.removeProperty(property);
  });
  for (const width of [360, 390, 430]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await detail.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  }
  await detail.getByRole('button', { name: 'К предметам', exact: true }).click();
  await expect(mathCards.first()).toBeVisible();

  const period = page.getByLabel('Период');
  await expect(period).toHaveValue('current');
  const currentPeriodLabel = await period.locator('option:checked').textContent();
  expect(currentPeriodLabel).toMatch(/II триместр/);
  expect(currentPeriodLabel).toContain('2026/27');
  await period.selectOption('archived');
  await expect(page.getByRole('button', { name: `Открыть журнал: ${SUBJECT}`, exact: true })).toHaveCount(1);
  await expect(page.getByText('Последняя контрольная')).toHaveCount(0);
  await expect(page.getByRole('button', { name: `Открыть журнал: ${SUBJECT}`, exact: true }).locator('.student-subject-results')).toHaveText('5');
  await expect(page.getByRole('heading', { name: /^I триместр.*2026/, level: 3 })).toBeVisible();
  const termOption = (await page.locator('select[aria-label="Период"] option').allTextContents()).find(label => /^I триместр(?:\s|·|$)/.test(label));
  expect(termOption).toBeTruthy();
  await period.selectOption({ label: termOption! });
  await expect(page.getByRole('button', { name: `Открыть журнал: ${SUBJECT}`, exact: true })).toHaveCount(1);
  await period.selectOption('all');
  await expect(page.getByRole('button', { name: `Открыть журнал: ${SUBJECT}`, exact: true })).toHaveCount(3);

  for (const width of [360, 390, 430]) {
    await page.setViewportSize({ width, height: 900 });
    const sizes = await page.evaluate(() => ({ viewport: document.documentElement.clientWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
    expect(Math.max(sizes.document, sizes.body), `horizontal overflow at ${width}px`).toBeLessThanOrEqual(width + 1);
  }
});

test('returning from a journal restores the subject list scroll and focus', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installStudentApi(page, { items: Array.from({ length: 12 }, (_, index) => journal(`scroll-${index}`, 'current', 2, '11А', `Предмет ${index + 1}`)) });
  await openGrades(page);
  const last = page.getByRole('button', { name: 'Открыть журнал: Предмет 12', exact: true });
  const scroll = page.locator('.sheet-full');
  await last.scrollIntoViewIfNeeded();
  const before = await scroll.evaluate(element => element.scrollTop);
  expect(before).toBeGreaterThan(0);
  await last.click();
  await expect(page.locator('.student-journal-detail')).toBeVisible();
  await page.getByRole('button', { name: 'К предметам', exact: true }).click();
  await expect(page.getByLabel('Период')).toHaveValue('current');
  await expect.poll(() => scroll.evaluate(element => element.scrollTop)).toBe(before);
  await expect(last).toBeFocused();
});

test('missing mapping is explained and unpublished schedule warning stays out of grades', async ({ page }) => {
  await installStudentApi(page, { items: [], mapping_state: 'missing_mapping' }, 'not_published');
  await openGrades(page);
  await expect(page.getByText(/нет сопоставленной записи этого ученика/i)).toBeVisible();
  await expect(page.locator('.campus-schedule-notice:visible')).toHaveCount(0);

  await page.goto('/?role=student&date=2026-10-26&screen=schedule');
  await expect(page.locator('.campus-schedule-notice:visible').first()).toBeVisible();
  await page.goto('/?role=student&date=2026-10-26&screen=grades');
  await page.getByRole('button', { name: 'Развернуть раздел', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Оценки', level: 2 })).toBeVisible();
  await expect(page.locator('.campus-schedule-notice:visible')).toHaveCount(0);
});

test('schedule notice can be dismissed for its week and does not cover grades', async ({ page }) => {
  await installStudentApi(page, { items: [] }, 'not_published');
  await page.goto('/?role=student&date=2026-10-26&screen=schedule');
  const notice = page.locator('.campus-schedule-notice:visible').first();
  await expect(notice).toBeVisible();
  await notice.getByRole('button', { name: 'Закрыть предупреждение о расписании' }).click();
  await expect(page.locator('.campus-schedule-notice:visible')).toHaveCount(0);
  await page.reload();
  await expect(page.locator('.campus-schedule-notice:visible')).toHaveCount(0);
  await page.getByRole('button', { name: 'Развернуть раздел', exact: true }).click();
  await expect(page.locator('.context-sheet .sheet-full .empty-state').getByText('Неделя не опубликована')).toBeVisible();
  await page.goto('/?role=student&date=2026-11-02&screen=schedule');
  await expect(page.locator('.campus-schedule-notice:visible').first()).toBeVisible();
  await page.goto('/?role=student&date=2026-11-02&screen=grades');
  await expect(page.locator('.campus-schedule-notice:visible')).toHaveCount(0);
});

test('canonical schedule week without a materialized publication is labeled unpublished', async ({ page }) => {
  await installStudentApi(page, { items: [] }, 'canonical_not_materialized');
  await page.goto('/?role=student&date=2026-10-26&screen=schedule');
  await page.getByRole('button', { name: 'Развернуть раздел', exact: true }).click();
  const emptyWeek = page.locator('.context-sheet .sheet-full .empty-state');
  await expect(emptyWeek).toContainText('Неделя не опубликована');
  await expect(emptyWeek).toContainText('школа ещё не опубликовала расписание');
});

test('student displays seconds-bearing synthetic lesson times as HH:MM in Today and the full schedule', async ({ page }) => {
  await installLessonApi(page, 'student');
  await page.goto('/?role=student&date=2026-10-26');
  await expect(page.locator('.today-sheet')).toHaveCount(0);
  await page.getByRole('button', { name: 'Сегодня', exact: true }).click();
  await page.getByRole('button', { name: 'Развернуть раздел', exact: true }).click();
  await expect(page.locator('.today-expanded .lesson-card time')).toHaveText('09:05');
  await page.getByRole('button', { name: 'Назад', exact: true }).click();
  await page.getByRole('button', { name: 'К острову', exact: true }).click();
  await page.getByRole('button', { name: 'Расписание', exact: true }).first().click();
  await page.getByRole('button', { name: 'Развернуть раздел', exact: true }).click();
  const fullScheduleTime = page.locator('.student-schedule-lesson time');
  await expect(fullScheduleTime).toHaveText('09:05');
  await expect(page.getByText('09:05:00', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Подробности урока: Синтетическая математика' }).click();
  await expect(page.getByRole('heading', { name: 'Синтетическая математика', level: 2 })).toBeVisible();
  await expect(page.locator('.student-lesson-detail')).toContainText('Педагог E2E');
  await expect(page.locator('.student-lesson-detail')).toContainText('Кабинет');
  await page.getByRole('button', { name: 'Назад к расписанию' }).click();
  await expect(page.locator('.student-schedule-lesson time')).toHaveText('09:05');
  await page.getByLabel('Дата расписания').fill('2026-11-02');
  await expect(page.locator('.student-lesson-detail')).toHaveCount(0);
  await expect(page.locator('.spatial-sheet.is-full')).toBeVisible();
  await expect(page.locator('.student-schedule-lesson')).toHaveCount(0);
});

test('teacher displays seconds-bearing synthetic lesson times as HH:MM in Today and the full schedule', async ({ page }) => {
  await installLessonApi(page, 'teacher');
  await page.goto('/?role=teacher&date=2026-10-26');
  const todayCard = page.locator('.today-card--primary');
  await expect(todayCard).toBeVisible();
  await expect(todayCard).toContainText('09:05 · 7 класс');
  await expect(todayCard).toContainText('Синтетическая математика');
  await expect(todayCard).not.toContainText('09:05:00');

  await page.getByRole('button', { name: 'Расписание', exact: true }).first().click();
  await page.getByRole('button', { name: 'Развернуть раздел', exact: true }).click();
  const fullScheduleTime = page.locator('.lesson-card time');
  await expect(fullScheduleTime).toHaveText('09:05');
  await expect(page.getByText('09:05:00', { exact: true })).toHaveCount(0);
});
