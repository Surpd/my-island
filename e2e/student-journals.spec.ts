import { expect, test, type Page } from '@playwright/test';

const SUBJECT = 'Математика';
const LONG_SUBJECT = 'История и культура островных государств и народов мира';

function journal(sourceId: string, periodStatus: string, term: number, marker: string, subject = SUBJECT) {
  const isCurrent = periodStatus === 'current';
  return {
    source: {
      source_id: sourceId,
      spreadsheet_title: isCurrent ? `Журнал ${subject} · ${marker}` : `Архив ${subject} · ${marker}`,
      grade: 7,
      subject,
      sheet_title: `7 класс · ${marker}`,
      academic_year: '2026/27',
      term_number: term,
      period_status: periodStatus,
    },
    student: { journal_student_id: `student-${sourceId}`, display_name: 'Синтетический ученик', group_marker: marker },
    // Deliberately supplied out of order to catch UI that does not sort assessments.
    assessments: isCurrent && sourceId === 'source-current'
      ? [
          { id: 'a-late', title: 'Последняя контрольная', assessed_on: '2026-10-20', weight: 2, max_score: 20, group_marker: marker },
          { id: 'a-missed', title: 'Работа со статусом', assessed_on: '2026-10-05', group_marker: marker },
          { id: 'a-zero', title: 'Нулевой результат', assessed_on: '2026-10-10', group_marker: marker },
          { id: 'a-old', title: 'Ранняя работа', assessed_on: '2026-09-25', group_marker: marker },
          { id: 'a-unpublished', title: 'Ещё не опубликовано', assessed_on: '2026-10-25', group_marker: marker },
        ]
      : [{ id: `a-${sourceId}`, title: `Работа ${marker}`, assessed_on: '2026-09-15', group_marker: marker }],
    results: isCurrent && sourceId === 'source-current'
      ? [
          { assessment_id: 'a-late', numeric_score: 15, status: null },
          { assessment_id: 'a-missed', numeric_score: null, status: 'Не был' },
          { assessment_id: 'a-zero', numeric_score: 0, status: null },
          { assessment_id: 'a-old', numeric_score: 4, status: null },
        ]
      : [{ assessment_id: `a-${sourceId}`, numeric_score: 5, status: null }],
    // A supplied summary stays identifiable as a source value; no other average is implied.
    summaries: isCurrent && sourceId === 'source-current'
      ? [{ label: 'Баллы за период', summary_type: 'points', numeric_value: 15, text_value: null }]
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
  await page.goto('/?role=student&date=2026-10-26&screen=grades');
  await page.getByRole('button', { name: 'Развернуть раздел', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Мои оценки', level: 2 })).toBeVisible();
  await expect(page.getByLabel('Период')).toBeVisible();
}

async function installLessonApi(page: Page, role: 'student' | 'teacher') {
  const lesson = {
    lesson_date: '2026-10-26',
    start_time: '09:05:00',
    end_time: '09:45:00',
    subject: 'Синтетическая математика',
    teacher: 'Педагог E2E',
    room: '12',
    audience: '7 класс',
    lesson_type: 'lesson',
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

test('student journals keep sources and periods separate and show chronological original results', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await installStudentApi(page, {
    items: [
      journal('source-current', 'current', 2, '7A'),
      journal('source-current-b', 'current', 2, '7Б', LONG_SUBJECT),
      journal('source-archive', 'archived', 1, '7A'),
    ],
  });
  await openGrades(page);

  const mathCards = page.getByRole('button', { name: `Открыть журнал: ${SUBJECT}`, exact: true });
  await expect(mathCards).toHaveCount(1);
  await expect(page.getByRole('button', { name: `Открыть журнал: ${LONG_SUBJECT}`, exact: true })).toBeVisible();
  await expect(page.getByText(/7A/).first()).toBeVisible();
  await expect(page.getByText(/II триместр/).first()).toBeVisible();
  await expect(page.getByText('15 / 20', { exact: true })).toBeVisible();
  await expect(page.getByText('Не был', { exact: true })).toBeVisible();
  await expect(page.getByText('0', { exact: true })).toBeVisible();
  // Assessment names are reserved for the detail view, including work without a result.
  for (const title of ['Последняя контрольная', 'Ещё не опубликовано', 'Работа 7A']) {
    await expect(page.getByText(title, { exact: true })).toHaveCount(0);
  }

  await page.screenshot({ path: '.local-test/grades-ui/subjects-390.png' });
  await mathCards.click();
  const detail = page.locator('.student-journal-detail');
  await expect(detail.getByRole('heading', { name: SUBJECT, level: 3 })).toBeVisible();
  await expect(detail.getByRole('button', { name: 'К предметам', exact: true })).toBeVisible();
  await expect(detail.getByText('15', { exact: true }).first()).toBeVisible(); // points remain points, never become a grade average of 5
  await expect(detail.getByText('0', { exact: true })).toBeVisible(); // a real zero is not treated as missing
  await expect(detail.getByText('Не был', { exact: true })).toBeVisible();
  await expect(detail.getByText('Баллы за период')).toBeVisible(); // only the supplied summary is shown
  await expect(detail.getByText(/вес\s+—|максимум\s+—/i)).toHaveCount(0);
  await page.screenshot({ path: '.local-test/grades-ui/journal-390.png' });
  const detailText = await detail.innerText();
  expect(detailText.indexOf('Ранняя работа')).toBeLessThan(detailText.indexOf('Работа со статусом'));
  expect(detailText.indexOf('Работа со статусом')).toBeLessThan(detailText.indexOf('Нулевой результат'));
  expect(detailText.indexOf('Нулевой результат')).toBeLessThan(detailText.indexOf('Последняя контрольная'));
  for (const width of [360, 390, 430]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await detail.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  }
  await detail.getByRole('button', { name: 'К предметам', exact: true }).click();
  await expect(page.getByRole('button', { name: `Открыть журнал: ${SUBJECT}`, exact: true })).toBeVisible();

  const period = page.getByLabel('Период');
  await period.selectOption('archived');
  await expect(page.getByRole('button', { name: `Открыть журнал: ${SUBJECT}`, exact: true })).toHaveCount(1);
  await expect(page.getByText('Последняя контрольная')).toHaveCount(0);
  await expect(page.getByText('5', { exact: true })).toBeVisible();
  await expect(page.getByText(/I триместр/).first()).toBeVisible();
  await period.selectOption('all');
  await expect(page.getByRole('button', { name: `Открыть журнал: ${SUBJECT}`, exact: true })).toHaveCount(2);

  for (const width of [360, 390, 430]) {
    await page.setViewportSize({ width, height: 900 });
    const sizes = await page.evaluate(() => ({ viewport: document.documentElement.clientWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
    expect(Math.max(sizes.document, sizes.body), `horizontal overflow at ${width}px`).toBeLessThanOrEqual(width + 1);
  }
});

test('returning from a journal restores the subject list scroll and focus', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installStudentApi(page, { items: Array.from({ length: 12 }, (_, index) => journal(`scroll-${index}`, 'current', 2, '7A', `Предмет ${index + 1}`)) });
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
  await expect(page.getByRole('heading', { name: 'Мои оценки', level: 2 })).toBeVisible();
  await expect(page.locator('.campus-schedule-notice:visible')).toHaveCount(0);
});

test('student displays seconds-bearing synthetic lesson times as HH:MM in Today and the full schedule', async ({ page }) => {
  await installLessonApi(page, 'student');
  await page.goto('/?role=student&date=2026-10-26');
  const todayCard = page.locator('.today-card--primary');
  await expect(todayCard).toBeVisible();
  await expect(todayCard).toContainText('09:05 · ближайший урок');
  await expect(todayCard).toContainText('Синтетическая математика');
  await expect(todayCard).not.toContainText('09:05:00');

  await page.getByRole('button', { name: 'Расписание', exact: true }).first().click();
  await page.getByRole('button', { name: 'Развернуть раздел', exact: true }).click();
  const fullScheduleTime = page.locator('.student-schedule-lesson time');
  await expect(fullScheduleTime).toHaveText('09:05');
  await expect(page.getByText('09:05:00', { exact: true })).toHaveCount(0);
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
