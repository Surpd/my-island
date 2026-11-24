import { SHORT_WEEKDAYS as dayNames } from './schedule-weekdays.ts';
type Target = {draftId: string; revision: number; scope: string; week: string};
type Issue = {code: string; lesson_id?: string; related_lesson_ids?: string[]};
type Outcome = {lesson_id: string; action: string};
type ReportPreview = {
  can_publish: boolean;
  blockers: Issue[];
  diagnostics?: Issue[];
  deferred: Outcome[];
  summary?: {conflict_lessons?: number};
};
type Slot = {day?: number; start?: string; end?: string};

const reasonGroups: Record<string, [string, string]> = {
  stale_group_subject_mismatch: ['Старая привязка учебной группы', 'Сверьте выбранную группу и сохранённый состав.'],
  student_overlap: ['У ученика пересекаются занятия', 'Сверьте составы или разведите занятия по времени.'],
  teacher_overlap: ['У преподавателя пересекаются занятия', 'Сверьте назначения или время занятий.'],
  room_overlap: ['Два очных занятия в одном кабинете', 'Уточните кабинеты или время.'],
  joint_activity_unconfirmed: ['Не подтверждено совместное занятие', 'Подтвердите совместный урок или разведите занятия.'],
  audience_unresolved: ['Не подтверждён состав', 'Проверьте класс, группу или список участников.'],
  roster_review: ['Состав требует проверки', 'Сверьте текущий состав с сохранённым.'],
  unknown_class: ['Класс размещения не подтверждён', 'Выберите действующий класс.'],
  missing_special_scope: ['Не указан охват отметки', 'Подтвердите, для каких классов или групп действует отметка.'],
  missing_teacher: ['Не назначен преподаватель', 'Проверьте, нужен ли преподаватель и кто ведёт занятие.'],
  unknown_teacher: ['Преподаватель не подтверждён', 'Сверьте назначение со справочником.'],
  missing_room: ['Не указан кабинет', 'Проверьте кабинет очного занятия.'],
  invalid_slot: ['Не подтверждены день или время', 'Сверьте дату и интервал занятия.'],
  elective_block_unlinked: ['Направление не связано с общим блоком', 'Выберите подтверждённый общий блок курса.'],
  elective_block_name_conflict: ['Разные названия общего блока', 'Сверьте название блока у его направлений.'],
  explicit_deferred: ['Запись отложена', 'Проверьте, устранена ли причина откладывания.'],
  review_required: ['Нужно уточнить данные занятия', 'Сверьте занятие с расписанием.'],
};


const time = /^(?:[01]\d|2[0-3]):[0-5]\d$/;

function position(slot: Slot | undefined) {
  if (!slot) return '';
  const day = slot.day !== undefined && Number.isInteger(slot.day) ? dayNames[slot.day] : '';
  const interval = slot.start && slot.end && time.test(slot.start) && time.test(slot.end) && slot.start < slot.end
    ? `${slot.start}–${slot.end}` : '';
  return [day, interval].filter(Boolean).join(' ');
}
function count(value: unknown) {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
}

export function anonymousReviewReport({target, loaded, preview, slots, dirty, legacy, rosters}: {
  target: Target; loaded: Target | null;
  preview: ReportPreview | null; slots: Record<string, Slot>; dirty: boolean;
  legacy?: {source_id: string; source_revision: number; retained_warning_count: number; automatic_count: number} | null;
  rosters?: {revision: number; summary: {safe_count: number; unresolved_count: number}} | null;
}): string | null {
  if (!loaded || !preview || dirty || !Number.isSafeInteger(target.revision) ||
      target.draftId !== loaded.draftId || target.revision !== loaded.revision || target.scope !== loaded.scope ||
      (target.scope !== 'template' && target.scope !== 'week') ||
      (target.week !== loaded.week) || (target.scope === 'week' && !/^\d{4}-\d{2}-\d{2}$/.test(target.week))) return null;
  const lines = ['Обезличенный отчёт проверки расписания', `Ревизия: ${target.revision}`,
    target.scope === 'week' ? `Область: неделя с ${target.week}` : 'Область: школьный шаблон',
    preview.can_publish ? 'Предпросмотр не блокирует публикацию. Это не опубликованное расписание.' :
      'Публикация остановлена. Ниже только последствия, подтверждённые предпросмотром.'];
  const conflictCount = count(preview.summary?.conflict_lessons);
  lines.push(conflictCount === undefined ? 'Число разных занятий с конфликтами: не подтверждено.' : `Разных занятий с конфликтами: ${conflictCount}.`);
  if (target.scope === 'template' && legacy && legacy.source_id === target.draftId && legacy.source_revision === target.revision) {
    lines.push(`Старые пометки, которые сохраняются: ${count(legacy.retained_warning_count) ?? 'неизвестно'}. Исправления для копии: ${count(legacy.automatic_count) ?? 'неизвестно'}.`);
  }
  if (target.scope === 'template' && rosters?.revision === target.revision) {
    lines.push(`Составы: можно обновить ${count(rosters.summary.safe_count) ?? 'неизвестно'}, нужно уточнить ${count(rosters.summary.unresolved_count) ?? 'неизвестно'}. Эти числа не прибавляются к конфликтам.`);
  }

  const ordinals = new Map<string, number>();
  function label(id: string) {
    if (!ordinals.has(id)) ordinals.set(id, ordinals.size + 1);
    const where = Object.hasOwn(slots, id) ? position(slots[id]) : '';
    return `Запись ${ordinals.get(id)}${where ? ` · ${where}` : ''}`;
  }
  const grouped = new Map<string, {ids: Set<string>; withoutId: boolean}>();
  for (const issue of [...(preview.diagnostics || []), ...preview.blockers]) {
    const code = Object.hasOwn(reasonGroups, issue.code) ? issue.code : 'other';
    const group = grouped.get(code) || {ids: new Set<string>(), withoutId: false};
    const ids = [issue.lesson_id, ...(issue.related_lesson_ids || [])].filter((id): id is string => Boolean(id));
    if (ids.length) ids.forEach(id => group.ids.add(id));
    else group.withoutId = true;
    grouped.set(code, group);
  }
  lines.push('', 'Причины и следующие шаги (одно занятие может встречаться в нескольких группах):');
  if (!grouped.size) lines.push('Нет данных о причинах в этом предпросмотре.');
  for (const [code, group] of grouped) {
    const [title, action] = reasonGroups[code] || ['Другая причина проверки', 'Откройте «Проблемы» в конструкторе и проверьте запись.'];
    lines.push(`• ${title}. ${action}${group.ids.size ? ` ${[...group.ids].map(label).join('; ')}.` : ''}${group.withoutId ? ' Есть замечание без привязки к занятию.' : ''}`);
  }

  const outcomes = new Map<string, Set<string>>();
  for (const item of preview.deferred) {
    if (!item.lesson_id) continue;
    const actions = outcomes.get(item.lesson_id) || new Set<string>();
    actions.add(item.action);
    outcomes.set(item.lesson_id, actions);
  }
  lines.push('', 'Подтверждённые последствия для личного расписания:');
  let known = 0;
  for (const [id, actions] of outcomes) {
    if (actions.size !== 1) continue;
    const action = actions.values().next().value;
    const result = action === 'retained_unresolved' ? 'Непроверенное назначение не попадёт в личное расписание'
      : action === 'excluded' ? 'Запись не войдёт в эту публикацию'
      : action === 'preserved_base' ? 'Сохранится предыдущая опубликованная запись' : null;
    if (!result) continue;
    lines.push(`• ${label(id)}: ${result}.`);
    known++;
  }
  if (!known) lines.push('Предпросмотр не подтвердил ни одного исключения или замены. Это не подтверждение выдачи остальных занятий.');
  else lines.push('Для остальных замечаний итог выдачи из этого отчёта не следует.');
  return lines.join('\n');
}
