import { expect, test, type Page } from '@playwright/test';

const DAY = '2026-10-26';

async function installCampusApi(page: Page) {
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    let body: unknown = {};
    if (url.pathname === '/api/auth/session') {
      body = { mode: 'e2e', state: 'approved', user: { id: 'synthetic-student', role: 'student', roles: ['student'] } };
    } else if (url.pathname === '/api/student/today') {
      body = { schedule: [], homework: [], announcements: [] };
    } else if (url.pathname === '/api/student/schedule') {
      body = {
        state: 'published',
        items: [],
        days: [{ date: DAY, state: 'scheduled', items: [{ date: DAY, state: 'lesson', slot: { start: '09:00', end: '09:45' }, canonical_title: 'Математика', teacher: 'Елена Петрова', room: '12' }] }],
        day_blocks: [],
        optional_activities: [
          { date: DAY, type: 'elective', title: 'Робототехника', start_time: '10:00', end_time: '10:45', room: '24', teachers: ['Иван Сидоров'] },
          { date: DAY, type: 'elective', title: 'Шахматы', start_time: '10:00', end_time: '10:45', room: '31', teachers: ['Анна Волкова'] },
          { date: DAY, type: 'club', title: 'Театральная студия', start_time: '14:00', end_time: '15:00', room: '18', teachers: ['Мария Смирнова'] },
        ],
      };
    } else if (url.pathname === '/api/student/homework') {
      body = { items: [] };
    } else if (url.pathname === '/api/student/profile') {
      body = { user: { display_name: 'Синтетический ученик', class_name: '7 класс' }, groups: [] };
    } else if (url.pathname === '/api/student/journals') {
      body = { items: [] };
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

async function openCampusSchedule(page: Page) {
  await page.goto(`/?role=student&date=${DAY}`);
  await page.locator('.scene-hotspot.hotspot--schedule').click();
  await page.getByRole('button', { name: 'Развернуть раздел', exact: true }).click();
  await expect(page.locator('.student-schedule-views')).toBeVisible();
}

test('Campus shows concurrent electives as one expandable block inline with lessons', async ({ page }) => {
  await installCampusApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await openCampusSchedule(page);

  const stack = page.locator('.student-schedule-stack');
  await expect(stack.locator('.student-schedule-lesson')).toHaveCount(1);
  const elective = stack.locator('.student-elective-block');
  await expect(elective).toHaveCount(1);
  await expect(elective.locator('summary')).toContainText('10:00–10:45');
  await expect(elective.locator('summary')).toContainText('Курсы по выбору');
  await expect(elective.locator('summary')).toContainText('2 варианта');
  await elective.locator('summary').click();
  await expect(elective).toContainText('Робототехника');
  await expect(elective).toContainText('Иван Сидоров');
  await expect(elective).toContainText('каб. 24');
  await expect(elective).toContainText('Шахматы');
  await expect(elective).toContainText('Анна Волкова');
  await expect(elective).toContainText('каб. 31');
  await expect(elective.getByRole('button')).toHaveCount(0);
  await expect(stack).not.toContainText('Театральная студия');
  await page.screenshot({ path: 'artifacts/campus-optional-activities/campus-electives.png', fullPage: true });
});

test('Campus places clubs in a separate schedule tab away from required lessons', async ({ page }) => {
  await installCampusApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await openCampusSchedule(page);

  const lessonsTab = page.getByRole('tab', { name: 'Уроки и курсы по выбору' });
  const clubsTab = page.getByRole('tab', { name: 'Кружки' });
  await expect(lessonsTab).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.student-schedule-stack')).not.toContainText('Театральная студия');
  await clubsTab.click();
  await expect(clubsTab).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.student-schedule-stack')).toHaveCount(0);
  const clubView = page.getByRole('tabpanel', { name: 'Кружки' });
  await expect(clubView).toContainText('Театральная студия');
  await expect(clubView).toContainText('14:00–15:00');
  await expect(clubView).toContainText('Мария Смирнова');
  await expect(clubView).toContainText('каб. 18');
  await expect(clubView).not.toContainText('Робототехника');
  await page.screenshot({ path: 'artifacts/campus-optional-activities/campus-clubs.png', fullPage: true });
});
