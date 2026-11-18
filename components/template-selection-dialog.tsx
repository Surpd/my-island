'use client';

import { X } from 'lucide-react';

type Lesson = { id: string; subject: string; start: string; end: string; groups: string[]; members: string[]; teachers: string[]; room: string; activity_type?: string; special_mark?: string; audience_kind?: string; audience_mode?: string; excluded_groups?: string[]; problems: string[] };
type Change = { field: string; label: string; impact: string; before: string | string[] | number | boolean | null; after: string | string[] | number | boolean | null; before_text?: string; after_text?: string };
type Preview = {
  current: { id: string; name: string; revision: number; draft_revision?: number; has_unreviewed_changes?: boolean; snapshot_available?: boolean } | null;
  candidate: { id: string; name: string; revision: number };
  current_hash: string | null; candidate_hash: string;
  added: Lesson[];
  removed: Lesson[];
  changed: { id: string; before: Omit<Lesson, 'id'>; after: Omit<Lesson, 'id'>; changes: Change[] }[];
  settings_changes: { field: string; label: string; impact: string; before: string; after: string }[];
  problems: { lesson_id: string; details: string[] }[];
  has_semantic_changes: boolean;
  baseline_recovery_required?: boolean;
  baseline_recovery?: { draft_id: string; revision: number; payload_hash: string; missing_revision: number; missing_payload_hash: string; contents: Lesson[] } | null;
};

const labels: Record<string, string> = { lesson: 'Урок', club: 'Клуб', elective: 'Курс по выбору', event: 'Событие', lunch: 'Обед', no_lesson: 'Нет урока' };
const valueText = (value: string | string[] | number | boolean | null) => Array.isArray(value) ? value.join(', ') || 'нет' : value === null || value === '' ? 'нет' : typeof value === 'boolean' ? value ? 'да' : 'нет' : String(value);
const lessonTitle = (item: Lesson) => `${item.subject || 'Без названия'} · ${item.start || 'время не указано'}–${item.end || ''}`;

export function TemplateSelectionDialog({ preview, busy, readOnly, actionError, acknowledgeProblems, acknowledgeBaseline, onAcknowledgeProblems, onAcknowledgeBaseline, onCancel, onConfirm, onApproveBaseline }: {
  preview: Preview; busy: boolean; readOnly: boolean; actionError?: string; acknowledgeProblems: boolean; acknowledgeBaseline: boolean;
  onAcknowledgeProblems: (value: boolean) => void; onAcknowledgeBaseline: (value: boolean) => void;
  onCancel: () => void; onConfirm: () => void; onApproveBaseline: () => void;
}) {
  const recoveryRequired = Boolean(preview.baseline_recovery_required && preview.baseline_recovery);
  const blockedReason = readOnly ? 'Недостаточно прав для подтверждения.' : recoveryRequired ? 'Предыдущая версия недоступна. Требуется отдельное утверждение новой базовой версии.' : preview.problems.length && !acknowledgeProblems ? 'Отметьте подтверждение проблем.' : '';
  const changeList = (items: Lesson[], kind: string) => items.map(item => <article className="constructor-history-lesson" key={`${kind}-${item.id}`}>
    <strong>{kind} · {lessonTitle(item)}</strong>
    <p>Тип: {labels[item.activity_type || 'lesson'] || item.activity_type || 'урок'}{item.special_mark ? ` · ${labels[item.special_mark] || item.special_mark}` : ''} · группы: {item.groups.join(', ') || '—'} · участников: {item.members.length} · преподаватели: {item.teachers.join(', ') || '—'} · кабинет: {item.room || '—'}</p>
    {item.audience_mode || item.audience_kind ? <p>Охват: {item.audience_kind || 'не указан'}{item.audience_mode ? ` · режим ${item.audience_mode}` : ''}{item.excluded_groups?.length ? ` · исключены группы ${item.excluded_groups.join(', ')}` : ''}</p> : null}
    {item.problems.length ? <p role="alert">Проблемы: {item.problems.join('; ')}</p> : null}
  </article>);

  return <div className="constructor-conflict-backdrop">
    <dialog ref={node => { if (node && !node.open) node.showModal(); }} onCancel={event => { event.preventDefault(); onCancel(); }} className="constructor-conflict-dialog constructor-week-history constructor-selection-dialog" aria-labelledby="template-selection-title" aria-describedby="template-selection-status">
      <header><div><small>СРАВНЕНИЕ ОСНОВНОГО ШАБЛОНА</small><h2 id="template-selection-title">Подтверждение основного шаблона</h2></div><button aria-label="Закрыть сравнение" onClick={onCancel}><X size={20} /></button></header>
      <div className="constructor-selection-content">
        <p>Текущий шаблон: {preview.current ? `${preview.current.name} · подтверждена ревизия ${preview.current.revision}` : 'не назначен'}. Проверяемая редакция: {preview.candidate.name} · ревизия {preview.candidate.revision}. Созданные недели останутся без изменений.</p>
        {preview.current?.has_unreviewed_changes && !recoveryRequired ? <p role="alert">В редакторе есть неподтверждённые правки. Сравнение выполнено с последней подтверждённой ревизией {preview.current.revision}.</p> : null}
        <dl className="constructor-selection-summary" aria-label="Сводка изменений">
          <div><dt>Добавлено</dt><dd>{preview.added.length}</dd></div><div><dt>Удалено</dt><dd>{preview.removed.length}</dd></div>
          <div><dt>Изменено</dt><dd>{preview.changed.length}</dd></div><div><dt>Календарь</dt><dd>{preview.settings_changes.length}</dd></div>
          <div><dt>Конфликты</dt><dd>{preview.problems.length}</dd></div>
        </dl>
        <output id="template-selection-status" className={`constructor-selection-readiness${blockedReason ? ' is-blocked' : ''}`} aria-live="polite">
          {recoveryRequired ? 'Нужно утвердить новую базовую версию' : preview.problems.length ? acknowledgeProblems ? 'Проблемы отмечены для подтверждения' : 'Требуется подтверждение проблем' : 'Можно подтвердить'}
          {blockedReason ? <span>{blockedReason}</span> : null}
        </output>
        {actionError ? <p className="constructor-selection-action-error" role="alert">{actionError}</p> : null}
        {recoveryRequired ? <section className="constructor-selection-recovery" aria-labelledby="legacy-baseline-title">
          <h3 id="legacy-baseline-title">Историческая ревизия недоступна</h3>
          <p>Подтверждённый снимок ревизии {preview.baseline_recovery!.missing_revision} и архив публикации не найдены. Ревизия {preview.baseline_recovery!.revision} не является восстановленной копией старой версии. Ниже показан текущий состав, который администратор может отдельно утвердить как новую базу.</p>
          <details><summary>Текущий состав · {preview.baseline_recovery!.contents.length} занятий</summary>{changeList(preview.baseline_recovery!.contents, 'Сейчас')}</details>
        </section> : null}
        <details className="constructor-selection-details"><summary>Добавленные занятия · {preview.added.length}</summary>{changeList(preview.added, 'Добавлено')}</details>
        <details className="constructor-selection-details"><summary>Удалённые занятия · {preview.removed.length}</summary>{changeList(preview.removed, 'Удалено')}</details>
        <details className="constructor-selection-details"><summary>Изменённые занятия · {preview.changed.length}</summary>{preview.changed.map(item => <article className="constructor-history-lesson" key={`changed-${item.id}`}>
          <strong>Изменено · {lessonTitle({ ...item.after, id: item.id })}</strong>
          <p>Группы: {item.after.groups.join(', ') || '—'} · участников: {item.after.members.length} · преподаватели: {item.after.teachers.join(', ') || '—'} · кабинет: {item.after.room || '—'}</p>
          <ul>{item.changes.map(change => <li key={change.field}><strong>{change.label}:</strong> {change.before_text || valueText(change.before)} → {change.after_text || valueText(change.after)}. {change.impact}</li>)}</ul>
        </article>)}</details>
        <details className="constructor-selection-details"><summary>Календарь и режимы · {preview.settings_changes.length}</summary>{preview.settings_changes.map(item => <article className="constructor-history-lesson" key={item.field}><strong>{item.label}</strong><p>Было: {item.before}</p><p>Стало: {item.after}</p><p>{item.impact}</p></article>)}</details>
        <details className="constructor-selection-details" open={preview.problems.length > 0}><summary>Проблемы и конфликты · {preview.problems.length}</summary>{preview.problems.map(item => <article className="constructor-history-lesson" key={item.lesson_id}><strong>{item.lesson_id}</strong><p>{item.details.join(', ')}</p></article>)}</details>
      </div>
      <footer>
        <div className="constructor-selection-confirmations">
          {recoveryRequired ? <label><input type="checkbox" checked={acknowledgeBaseline} onChange={event => onAcknowledgeBaseline(event.target.checked)} />Я понимаю, что историческая ревизия {preview.baseline_recovery!.missing_revision} недоступна, и утверждаю текущую ревизию {preview.baseline_recovery!.revision} как новую базовую версию.</label> : preview.problems.length ? <label><input type="checkbox" checked={acknowledgeProblems} onChange={event => onAcknowledgeProblems(event.target.checked)} />Я просмотрел проблемы и подтверждаю шаблон с ними.</label> : <span>{blockedReason || 'Проверьте сводку перед подтверждением.'}</span>}
        </div>
        <div className="constructor-selection-actions"><button disabled={busy} onClick={onCancel}>Отмена</button>
          {recoveryRequired ? <button className="is-primary" disabled={busy || readOnly || !acknowledgeBaseline} onClick={onApproveBaseline}>{busy ? 'Сохраняем…' : 'Утвердить новую базовую версию'}</button> : <button className="is-primary" disabled={busy || readOnly || Boolean(preview.problems.length && !acknowledgeProblems)} onClick={onConfirm}>{busy ? 'Сохраняем…' : 'Подтвердить основным'}</button>}
        </div>
      </footer>
    </dialog>
  </div>;
}
