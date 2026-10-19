import { expect } from '@playwright/test';
import { test } from './helpers/experiment-test';

const API = 'http://localhost:8011';
const ADMIN = { 'X-Dev-Auth': 'admin:1' };

test('available-slot roster stays factual until recalculation is confirmed, then survives reload', async ({ page, registerExperiment }) => {
  const suffix = Date.now().toString();
  const className = `E2E состав ${suffix}`;
  const subgroupName = `E2E занятые ${suffix}`;
  const firstStudentName = `E2E занят ${suffix}`;
  const secondStudentName = `E2E свободен ${suffix}`;

  const classResponse = await page.request.post(`${API}/api/admin/groups`, {
    headers: ADMIN,
    data: { name: className, group_type: 'class' },
  });
  expect(classResponse.ok()).toBe(true);
  const classId = String((await classResponse.json()).id);
  const subgroupResponse = await page.request.post(`${API}/api/admin/groups`, {
    headers: ADMIN,
    data: { name: subgroupName, group_type: 'subject_group', subject: 'Математика', base_class_name: className },
  });
  expect(subgroupResponse.ok()).toBe(true);
  const subgroupId = String((await subgroupResponse.json()).id);

  const createStudent = async (display_name: string) => {
    const response = await page.request.post(`${API}/api/admin/identities`, {
      headers: ADMIN,
      data: { display_name, class_name: className, kind: 'student' },
    });
    expect(response.ok()).toBe(true);
    return String((await response.json()).id);
  };
  const firstStudentId = await createStudent(firstStudentName);
  const secondStudentId = await createStudent(secondStudentName);
  const expectedAvailableRoster = [firstStudentId, secondStudentId].sort();
  for (const identityId of [firstStudentId, secondStudentId]) {
    const response = await page.request.post(`${API}/api/admin/memberships`, {
      headers: ADMIN,
      data: { group_id: classId, identity_id: identityId, member_role: 'student' },
    });
    expect(response.ok()).toBe(true);
  }
  const subgroupMembership = await page.request.post(`${API}/api/admin/memberships`, {
    headers: ADMIN,
    data: { group_id: subgroupId, identity_id: firstStudentId, member_role: 'student' },
  });
  expect(subgroupMembership.ok()).toBe(true);

  const draftName = `E2E roster snapshot ${suffix}`;
  const createDraft = await page.request.post(`${API}/api/admin/schedule/experiments`, {
    headers: ADMIN,
    data: {
      name: draftName,
      source: 'empty',
      payload: {
        draft_kind: 'test',
        days: [0],
        slots: [{ start: '09:00', end: '09:45' }, { start: '09:55', end: '10:40' }],
        classes: [classId],
        lessons: [
          { id: 'available-slot', day: 0, period: 0, subject: 'Свободный состав', classIds: [classId], audience: { kind: 'available_slot', class_ids: [classId] } },
          { id: 'reserved-group', day: 0, period: 0, subject: 'Математика', classIds: [classId], groupId: subgroupId, audience: { kind: 'groups', group_ids: [subgroupId] } },
        ],
      },
    },
  });
  expect(createDraft.ok()).toBe(true);
  const created = await createDraft.json();
  const draftId = String(created.id);
  registerExperiment(draftId);

  const firstSave = await page.request.put(`${API}/api/admin/schedule/experiments/${draftId}`, {
    headers: ADMIN,
    data: { expected_revision: created.revision, payload: created.payload },
  });
  expect(firstSave.ok()).toBe(true);
  const saved = await firstSave.json();
  const savedLessons = saved.payload.lessons as Array<{ id: string; audience_snapshot?: { student_ids: string[] } }>;
  expect(savedLessons.find((lesson) => lesson.id === 'available-slot')?.audience_snapshot?.student_ids).toEqual([secondStudentId]);
  expect(savedLessons.find((lesson) => lesson.id === 'reserved-group')?.audience_snapshot?.student_ids).toEqual([firstStudentId]);

  const editedPayload = {
    ...saved.payload,
    lessons: (saved.payload.lessons as Array<Record<string, unknown>>).map((lesson) =>
      lesson.id === 'reserved-group' ? { ...lesson, period: 1 } : lesson),
  };
  const staleSave = await page.request.put(`${API}/api/admin/schedule/experiments/${draftId}`, {
    headers: ADMIN,
    data: { expected_revision: saved.revision, payload: editedPayload },
  });
  expect(staleSave.ok()).toBe(true);
  const staleDraft = await staleSave.json();
  const staleLessons = staleDraft.payload.lessons as Array<{ id: string; audience_snapshot?: { student_ids: string[] } }>;
  expect(staleLessons.find((lesson) => lesson.id === 'available-slot')?.audience_snapshot?.student_ids).toEqual([secondStudentId]);

  const previewResponse = await page.request.post(`${API}/api/admin/schedule/experiments/audience-preview`, {
    headers: ADMIN,
    data: { payload: staleDraft.payload },
  });
  expect(previewResponse.ok()).toBe(true);
  const preview = await previewResponse.json();
  expect(preview.lessons['available-slot']).toMatchObject({
    status: 'ok',
    student_ids: [secondStudentId],
    proposed_student_ids: expectedAvailableRoster,
    needs_recalculation: true,
  });

  await page.goto('/admin/schedule/constructor?role=admin');
  await page.getByRole('button', { name: /Тестовые ·/ }).click();
  await page.locator('.constructor-draft').filter({ hasText: draftName })
    .getByRole('button', { name: new RegExp(draftName) }).click();
  const availableCard = page.locator('.constructor-card[data-lesson-id="available-slot"]');
  await expect(availableCard).toBeVisible();
  await availableCard.click();
  const participants = page.getByRole('region', { name: 'Участники занятия' });
  await expect(participants.getByText('Изменился расчёт участников')).toBeVisible();
  await expect(participants.getByText('Сейчас по правилу получилось бы 2 учеников')).toBeVisible();
  page.once('dialog', (dialog) => dialog.accept());
  await participants.getByRole('button', { name: 'Пересчитать и подтвердить состав' }).click();
  await expect(page.locator('.constructor-status')).toContainText('Черновик сохранён.');

  const readSavedRoster = async () => {
    const response = await page.request.get(`${API}/api/admin/schedule/experiments/${draftId}`, { headers: ADMIN });
    expect(response.ok()).toBe(true);
    const value = await response.json();
    const lesson = (value.payload.lessons as Array<{ id: string; audience_snapshot?: { student_ids: string[] } }>).find((item) => item.id === 'available-slot');
    return lesson?.audience_snapshot?.student_ids || [];
  };
  await expect.poll(readSavedRoster).toEqual(expectedAvailableRoster);

  await page.reload();
  await page.getByRole('button', { name: /Тестовые ·/ }).click();
  await page.locator('.constructor-draft').filter({ hasText: draftName })
    .getByRole('button', { name: new RegExp(draftName) }).click();
  const reopenedCard = page.locator('.constructor-card[data-lesson-id="available-slot"]');
  await expect(reopenedCard).toBeVisible();
  await reopenedCard.click();
  const reopenedParticipants = page.getByRole('region', { name: 'Участники занятия' });
  await expect(reopenedParticipants.getByText('Изменился расчёт участников')).toHaveCount(0);
  await reopenedParticipants.getByText('Показать состав (2)').click();
  await expect(reopenedParticipants.getByText(firstStudentName)).toBeVisible();
  await expect(reopenedParticipants.getByText(secondStudentName)).toBeVisible();
});
