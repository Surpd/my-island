const technicalText = /[a-f\d]{8}-(?:[a-f\d]{4}-){3}[a-f\d]{12}|\b(?:canonical|campus-?projection|blockId|teacherId|lessonId|[a-z][a-z\d]*_[a-z\d_]+|[a-z]+:[a-z\d_-]+)\b/i;

export function readableReviewNote(reason?: string) {
  const text = reason?.trim() || '';
  return /[а-яё]/iu.test(text) && !technicalText.test(text) && (text.match(/\b[a-z]{3,}\b/gi)?.length || 0) < 3 ? text : '';
}

export function legacyReviewReason(reason = '') {
  if (/групп.*другого предмета|прежняя математическая группа удалена/i.test(reason))
    return { title: 'Нужно заменить учебную группу', action: 'Выберите действующую группу этого предмета.' };
  if (/состав.*(измен|отлич)|участник.*сохран/i.test(reason))
    return { title: 'Состав участников изменился', action: 'Сверьте сохранённый и текущий списки учеников.' };
  if (/подтверждённого назначения преподавателя/i.test(reason))
    return { title: 'Не подтверждено назначение преподавателя группе', action: 'Проверьте, кто ведёт этот предмет в группе.' };
  if (/не подтверждён преподаватель|преподаватель.*неактив/i.test(reason))
    return { title: 'Не подтверждён преподаватель', action: 'Проверьте преподавателя занятия по справочнику.' };
  if (/направление.*(электив|курс).*блок|не связан.*блок/i.test(reason))
    return { title: 'Не подтверждён общий блок курса по выбору', action: 'Откройте занятие и проверьте, к какому блоку относится направление.' };
  if (/класс.*не (найден|выбран)/i.test(reason))
    return { title: 'Нужно проверить класс', action: 'Выберите действующий класс.' };
  if (/нет.*(членства|участников)|пуст.*групп/i.test(reason))
    return { title: 'Нужно проверить состав группы', action: 'Сверьте список учеников группы.' };
  if (/не.*(определ|подтверж).*состав|аудитори/i.test(reason))
    return { title: 'Не подтверждён состав занятия', action: 'Уточните, кто должен посещать занятие, и проверьте состав.' };
  const note = readableReviewNote(reason);
  return {
    title: note || 'Нужно уточнить данные занятия',
    action: 'Откройте занятие и сверьте его с расписанием.',
  };
}
