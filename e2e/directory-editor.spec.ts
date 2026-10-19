import { expect, test } from '@playwright/test';

const API = 'http://localhost:8011';
const ADMIN = { 'X-Dev-Auth': 'admin:1' };

async function openBoard(page: import('@playwright/test').Page, parallel: string, subject = 'Математика') {
  await page.getByRole('combobox', { name: 'Класс или параллель' }).selectOption(parallel ? `parallel:${parallel}` : '');
  await page.getByRole('combobox', { name: 'Предмет или направление' }).selectOption(subject);
}

function groupCard(page: import('@playwright/test').Page, label: string, id?: string) {
  if (id) return page.locator(`.school-group-card[data-group-id="${id}"]`);
  return page.locator('.school-group-card').filter({ has: page.locator('.school-group-card-title').filter({ hasText: label }) }).first();
}

test('inline group cards support simultaneous expansion, transfer, and conservative unassigned lists', async ({ page }) => {
  const response = await page.request.get(`${API}/api/admin/people?kind=student`, { headers: ADMIN });
  expect(response.ok()).toBeTruthy();
  const students = (await response.json()).items as Array<{ id: string; display_name: string; class_name: string }>;
  const groupsResponse = await page.request.get(`${API}/api/admin/groups`, { headers: ADMIN });
  expect(groupsResponse.ok()).toBeTruthy();
  const groups = (await groupsResponse.json()).items as Array<{ id: string; name: string }>;
  const splitOne = groups.find((group) => group.name === 'grade7:split:1');
  expect(splitOne).toBeTruthy();

  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto('/admin/people?role=admin');
  await expect(page.getByRole('heading', { name: 'Ученики и группы', level: 1 })).toBeVisible();
  await openBoard(page, '7');

  const sevenOne = groupCard(page, 'Математика · 7-1', String(splitOne!.id));
  const sevenTwo = groupCard(page, 'Математика · 7-2');
  const sevenOneButton = sevenOne.locator('.school-group-card-main');
  const sevenTwoButton = sevenTwo.locator('.school-group-card-main');
  await sevenOneButton.click();
  await expect(sevenOneButton).toHaveAttribute('aria-expanded', 'true');
  await sevenTwoButton.click();
  await expect(sevenOneButton).toHaveAttribute('aria-expanded', 'true');
  await expect(sevenTwoButton).toHaveAttribute('aria-expanded', 'true');
  await expect(sevenOne.locator('.school-group-roster')).toBeVisible();
  await expect(sevenTwo.locator('.school-group-roster')).toBeVisible();
  const sevenStudent = sevenOne.locator('.school-group-roster-row').filter({ hasText: 'E2E ученик 7-1' });
  await sevenStudent.locator('.school-drag-handle').dragTo(sevenTwo);
  const sevenMove = page.locator('.school-move-dialog');
  await expect(sevenMove).toBeVisible();
  await sevenMove.getByRole('button', { name: /Перевести в/ }).click();
  await expect(sevenOne.locator('.school-group-roster-row').filter({ hasText: 'E2E ученик 7-1' })).toHaveCount(0);
  await expect(sevenTwo.locator('.school-group-roster-row').filter({ hasText: 'E2E ученик 7-1' })).toBeVisible();
  await expect(sevenOne).toContainText('1 учеников');
  await expect(sevenTwo).toContainText('4 учеников');

  const unassigned = page.locator('.school-unassigned');
  await expect(unassigned).toContainText('Тестовый ученик остатка');
  const unassignedCountBefore = await unassigned.locator('.school-student-chip').count();
  const remainder = unassigned.locator('.school-student-chip').filter({ hasText: 'Тестовый ученик остатка' });
  await remainder.locator('.school-drag-handle').dragTo(sevenOne);
  await expect(sevenOne.locator('.school-group-roster-row').filter({ hasText: 'Тестовый ученик остатка' })).toBeVisible();
  await expect(sevenOne).toContainText('2 учеников');
  await expect(remainder).toHaveCount(0);
  await expect(unassigned.locator('.school-student-chip')).toHaveCount(unassignedCountBefore - 1);

  await openBoard(page, '9');
  const nineA = groupCard(page, 'Математика · 9-A');
  const nineB = groupCard(page, 'Математика · 9-B');
  const nineC = groupCard(page, 'Математика · 9-C');
  await expect(nineA).toBeVisible();
  await expect(nineB).toBeVisible();
  await expect(nineC).toBeVisible();
  await nineA.locator('.school-group-card-main').click();
  await nineB.locator('.school-group-card-main').click();
  const ninthStudent = nineA.locator('.school-group-roster-row').filter({ hasText: 'Тестовый ученик 9A' });
  await ninthStudent.locator('.school-drag-handle').dragTo(nineB);
  const nineMove = page.locator('.school-move-dialog');
  await expect(nineMove).toBeVisible();
  await nineMove.getByRole('button', { name: /Перевести в/ }).click();
  await expect(nineA.locator('.school-group-roster-row').filter({ hasText: 'Тестовый ученик 9A' })).toHaveCount(0);
  await expect(nineB.locator('.school-group-roster-row').filter({ hasText: 'Тестовый ученик 9A' })).toBeVisible();

  await page.getByRole('combobox', { name: 'Класс или параллель' }).selectOption('');
  await page.getByRole('combobox', { name: 'Предмет или направление' }).selectOption('Математика');
  await expect(page.locator('.school-unassigned')).toHaveCount(0);
  await openBoard(page, '5');
  await expect(page.locator('.school-unassigned')).toContainText('не объявлено полное деление');
  await expect(page.locator('.school-unassigned .school-student-chip')).toHaveCount(0);
  const confirmRemainder = page.getByRole('checkbox', { name: /Подтверждаю, что все ученики параллели должны быть распределены/ });
  await confirmRemainder.evaluate((element: HTMLInputElement) => element.click());
  await expect(page.locator('.school-unassigned .school-student-chip').first()).toBeVisible();
  await page.getByRole('button', { name: 'Отменить подтверждение' }).click();
  await expect(page.locator('.school-unassigned .school-student-chip')).toHaveCount(0);

  await openBoard(page, '11');
  const interage = groupCard(page, 'общая');
  await expect(interage).toBeVisible();
  await interage.locator('.school-group-card-main').click();
  await expect(interage.locator('.school-group-roster-row').filter({ hasText: 'Тестовый ученик 10' })).toBeVisible();
  await expect(interage.locator('.school-group-roster-row').filter({ hasText: 'Тестовый ученик 11' })).toBeVisible();

  await page.setViewportSize({ width: 1366, height: 768 });
  expect(await page.evaluate(() => document.body.scrollWidth)).toBeLessThanOrEqual(1368);
  expect(students.length).toBeGreaterThan(0);
});

test('group CRUD, student details, and administrative class editing remain usable', async ({ page }) => {
  const transferSetup = await page.request.post(`${API}/api/admin/identities`, {
    headers: ADMIN,
    data: { kind: 'student', display_name: `E2E отдельный перевод ${Date.now()}`, class_name: '11' },
  });
  expect(transferSetup.ok(), await transferSetup.text()).toBeTruthy();
  const transferStudentId = String((await transferSetup.json()).id);
  const response = await page.request.get(`${API}/api/admin/people?kind=student`, { headers: ADMIN });
  expect(response.ok()).toBeTruthy();
  const students = (await response.json()).items as Array<{ id: string; display_name: string; class_name: string }>;
  const student = students.find((item) => item.display_name.startsWith('E2E '));
  expect(student).toBeTruthy();

  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto('/admin/people?role=admin');
  await page.getByRole('button', { name: 'Создать группу' }).click();
  const dialog = page.locator('.school-editor-dialog');
  await dialog.getByLabel('Название').fill('E2E редактор · Английский A');
  await dialog.getByLabel('Предмет или направление').fill('E2E уникальный предмет');
  const createResponsePromise = page.waitForResponse((response) => response.url().endsWith('/api/admin/groups') && response.request().method() === 'POST');
  await dialog.getByRole('button', { name: 'Создать группу' }).click();
  const createResponse = await createResponsePromise;
  expect(createResponse.ok()).toBeTruthy();
  const createdGroupId = String((await createResponse.json()).id);
  const created = groupCard(page, 'E2E редактор Английский A');
  await expect(created).toBeVisible();

  await created.getByRole('button', { name: /^Действия группы/ }).click();
  await created.getByRole('menuitem', { name: 'Редактировать' }).click();
  const settings = page.getByRole('dialog', { name: 'Настройки группы' });
  await settings.getByLabel('Название').fill('E2E отменённое изменение');
  await settings.getByRole('button', { name: 'Отмена' }).click();
  await expect(created.locator('.school-group-card-title')).toHaveText('E2E уникальный предмет · E2E редактор Английский A');

  await created.locator('.school-group-card-main').click();
  await created.getByLabel(/Добавить ученика в/).selectOption(String(student!.id));
  await expect(created.locator('.school-group-roster-row').filter({ hasText: student!.display_name })).toBeVisible();

  const teachersResponse = await page.request.get(`${API}/api/admin/people?kind=teacher`, { headers: ADMIN });
  expect(teachersResponse.ok()).toBeTruthy();
  const teachers = (await teachersResponse.json()).items as Array<{ id: string }>;
  expect(teachers.length).toBeGreaterThan(0);
  const assignmentResponse = await page.request.post(`${API}/api/admin/teacher-assignments`, {
    headers: ADMIN,
    data: { teacher_identity_id: String(teachers[0].id), group_id: createdGroupId, subject: 'E2E уникальный предмет', active: true },
  });
  expect(assignmentResponse.ok(), await assignmentResponse.text()).toBeTruthy();

  await created.getByRole('button', { name: /^Действия группы/ }).click();
  await created.getByRole('menuitem', { name: 'Архивировать группу…' }).click();
  const archiveDialog = page.getByRole('dialog', { name: 'Архивирование группы' });
  await expect(archiveDialog).toBeVisible();
  for (const label of ['Действующие ученики', 'Исторические включения', 'Действующие назначения', 'Исторические назначения', 'Ссылки в шаблонах', 'Датированные недели', 'Журналы', 'Google Classroom', 'Другие источники', 'Сохранённые ссылки']) {
    await expect(archiveDialog.getByText(label, { exact: true })).toBeVisible();
  }
  await expect(archiveDialog.getByText('Действующие ученики', { exact: true }).locator('xpath=following-sibling::dd')).toHaveText('1');
  await expect(archiveDialog.getByText('Действующие назначения', { exact: true }).locator('xpath=following-sibling::dd')).toHaveText('1');
  const archiveConfirm = archiveDialog.getByRole('button', { name: 'Подтвердить архивирование' });
  await expect(archiveConfirm).toBeDisabled();
  await archiveDialog.getByRole('checkbox', { name: /Подтверждаю архивирование/ }).check();
  await archiveConfirm.click();
  await expect(page.getByRole('status')).toContainText('Группа архивирована');
  await expect(created).toHaveCount(0);

  const activeGroups = await page.request.get(`${API}/api/admin/groups`, { headers: ADMIN });
  expect((await activeGroups.json()).items.some((item: { id: string }) => String(item.id) === createdGroupId)).toBeFalsy();
  const retainedGroups = await page.request.get(`${API}/api/admin/groups?include_archived=true`, { headers: ADMIN });
  const archivedGroup = ((await retainedGroups.json()).items as Array<{ id: string; archived_at: string; canonical: boolean; student_count: number }>).find((item) => String(item.id) === createdGroupId);
  expect(archivedGroup?.archived_at).toBeTruthy();
  expect(archivedGroup?.canonical).toBeTruthy();
  expect(archivedGroup?.student_count).toBe(1);
  const retainedStudents = await page.request.get(`${API}/api/admin/people?kind=student`, { headers: ADMIN });
  const retainedStudent = ((await retainedStudents.json()).items as Array<{ id: string; groups?: Array<{ id: string }> }>).find((item) => String(item.id) === String(student!.id));
  expect(retainedStudent?.groups?.some((item) => String(item.id) === createdGroupId)).toBeTruthy();
  const retainedTeachers = await page.request.get(`${API}/api/admin/people?kind=teacher&include_inactive=true`, { headers: ADMIN });
  expect(((await retainedTeachers.json()).items as Array<{ teacher_assignments?: Array<{ group_id: string; active: boolean }> }>).some((item) => item.teacher_assignments?.some((assignment) => String(assignment.group_id) === createdGroupId && assignment.active))).toBeTruthy();

  await page.getByRole('checkbox', { name: 'Архивные группы' }).check();
  const restoredCard = groupCard(page, 'E2E редактор Английский A');
  await expect(restoredCard).toBeVisible();
  await restoredCard.getByRole('button', { name: /^Действия группы/ }).click();
  await restoredCard.getByRole('menuitem', { name: 'Восстановить группу' }).click();
  await expect(page.getByRole('status')).toContainText('Группа восстановлена');
  await expect(restoredCard).toHaveCount(0);
  const restoredGroups = await page.request.get(`${API}/api/admin/groups?include_archived=true`, { headers: ADMIN });
  const restored = ((await restoredGroups.json()).items as Array<{ id: string; archived_at: string | null }>).find((item) => String(item.id) === createdGroupId);
  expect(restored?.archived_at).toBeNull();

  await page.getByRole('tab', { name: 'Ученики' }).click();
  const row = page.locator('.school-students-table tbody tr').filter({ hasText: student!.display_name });
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: /Открыть карточку ученика/ }).click();
  const studentDialog = page.getByRole('dialog', { name: new RegExp(student!.display_name) });
  await expect(studentDialog).toBeVisible();
  await expect(studentDialog.getByText('КАНОНИЧЕСКОЕ ЧЛЕНСТВО')).toHaveCount(0);
  await studentDialog.getByRole('button', { name: 'Закрыть карточку' }).click();

  await page.getByRole('tab', { name: 'Доска' }).click();
  expect(await page.locator('.school-class-compact').count()).toBeGreaterThan(0);
  await page.locator('.school-class-compact').first().click();
  const classDrawer = page.getByRole('dialog', { name: /Состав класса/ });
  await expect(classDrawer.locator('.school-class-roster')).toBeVisible();
  const transferSelect = classDrawer.getByLabel('Перевести существующего ученика в класс');
  const transferCandidate = students.find((item) => String(item.id) === transferStudentId);
  expect(transferCandidate).toBeTruthy();
  page.once('dialog', (browserDialog) => browserDialog.accept());
  await transferSelect.selectOption(transferStudentId);
  await expect(classDrawer.locator('.school-class-roster-row').filter({ hasText: transferCandidate!.display_name })).toBeVisible();
  await classDrawer.getByRole('button', { name: 'Новый ученик' }).click();
  const createStudent = page.locator('.school-create-student-dialog');
  await createStudent.getByLabel('ФИО').fill('E2E редактор · новый ученик');
  await createStudent.getByRole('button', { name: 'Создать ученика' }).click();
  await expect(createStudent).toBeHidden();
  await expect(classDrawer.locator('.school-class-roster-row').filter({ hasText: 'E2E редактор · новый ученик' })).toBeVisible();
});

test('directory navigation and board fit both requested desktop widths', async ({ page }) => {
  for (const viewport of [{ width: 1920, height: 1080 }, { width: 1366, height: 768 }]) {
    await page.setViewportSize(viewport);
    await page.goto('/admin/people?role=admin');
    await expect(page.getByRole('heading', { name: 'Ученики и группы', level: 1 })).toBeVisible();
    await page.getByRole('tab', { name: 'Ученики' }).click();
    await expect(page.locator('.school-students-table')).toBeVisible();
    await page.getByRole('tab', { name: 'Доска' }).click();
    await expect(page.locator('.school-class-shelf')).toBeVisible();
    await expect(page.locator('.school-board-groups')).toBeVisible();
    expect(await page.evaluate(() => document.body.scrollWidth)).toBeLessThanOrEqual(viewport.width + 2);
  }
});
