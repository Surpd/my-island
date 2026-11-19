'use client';

import { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';

export type TermComparisonSide = {
  day: number;
  start: string;
  end: string;
  classes: string[];
  subject: string;
  room: string;
  teachers: string[];
  groups: string[];
  kind: string;
  warning: string;
};
type Status = 'pending' | 'accepted' | 'rejected' | 'deferred';
export type TermComparisonAction = 'accept' | 'reject' | 'defer';
type TermComparisonItem = {
  key: string;
  cells: string[];
  kind: 'changed' | 'unlinked' | 'added' | 'removed';
  note: string;
  sheet_text?: string;
  before: TermComparisonSide | null;
  after: TermComparisonSide | null;
  lesson_id?: string;
  initial_status?: Status;
};
type Decision = { key: string; status: Status; reason: string; lesson_id?: string; confirmed_at?: string };
export type TermComparisonSource = {
  term2_preparation?: {
    comparison?: {
      items: TermComparisonItem[];
      decisions?: Decision[];
      normalizations?: { lesson_id: string; fields: string[]; before: Record<string, unknown>; after: Record<string, unknown> }[];
    };
  };
};
type Lesson = TermComparisonSide & { id: string; label: string };
type Comparison = NonNullable<NonNullable<TermComparisonSource['term2_preparation']>['comparison']>;
const statusLabels: Record<Status, string> = {
  pending: 'Требуют решения',
  accepted: 'Приняты',
  rejected: 'Отклонены',
  deferred: 'Отложены',
};
const kindLabels: Record<TermComparisonItem['kind'], string> = {
  changed: 'Изменение',
  unlinked: 'Связь не найдена',
  added: 'Новое занятие',
  removed: 'Исчезло из таблицы',
};
const days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const fieldLabels: Record<string, string> = { activityType: 'Тип занятия', needsClarification: 'Требует уточнения', clarificationReason: 'Причина уточнения' };
function normalizationValue(value: unknown) {
  if (value === 'elective') return 'курс по выбору';
  if (value === true) return 'да';
  if (value === false) return 'нет';
  return typeof value === 'string' && value ? value : 'не указано';
}

function Side({ title, side, emptyText = 'Записи нет' }: { title: string; side: TermComparisonSide | null; emptyText?: string }) {
  return <div className="constructor-term-side">
    <h4>{title}</h4>
    {side ? <>
      <strong>{side.subject || 'Запись без названия'}</strong>
      <p>{[days[side.day], side.start && side.end ? `${side.start}–${side.end}` : side.start, ...side.classes].filter(Boolean).join(' · ')}</p>
      <dl>
        <dt>Преподаватели</dt><dd>{side.teachers.join(', ') || 'Не указаны'}</dd>
        <dt>Группы</dt><dd>{side.groups.join(', ') || 'Не указаны'}</dd>
        <dt>Кабинет</dt><dd>{side.room || 'Не указан'}</dd>
      </dl>
      {side.warning ? <p className="constructor-term-warning">{side.warning}</p> : null}
    </> : <p>{emptyText}</p>}
  </div>;
}

export function ConstructorTermComparison({
  comparison, name, lessons, initialKey, dirty, readOnly, onClose, onSelect, onOpenLesson, onOpenSlot, onDecide,
}: {
  comparison: Comparison;
  name: string;
  lessons: Lesson[];
  initialKey: string | null;
  dirty: boolean;
  readOnly: boolean;
  onClose: () => void;
  onSelect: (key: string | null) => void;
  onOpenLesson: (id: string) => void;
  onOpenSlot: (side: TermComparisonSide) => void;
  onDecide: (key: string, action: TermComparisonAction, reason: string, lessonId?: string) => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const detailHeading = useRef<HTMLHeadingElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const previousKey = useRef<string | null>(initialKey);
  useEffect(() => { if (dialog.current && !dialog.current.open) dialog.current.showModal(); }, []);
  const [status, setStatus] = useState<Status>('pending');
  const [query, setQuery] = useState('');
  const [shown, setShown] = useState(20);
  const [selectedKey, setSelectedKey] = useState(initialKey);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState('');
  const [chosenId, setChosenId] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const decisions = new Map((comparison.decisions || []).map(decision => [decision.key, decision]));
  const rows = comparison.items.map(item => ({
    item,
    status: decisions.get(item.key)?.status || item.initial_status || 'pending',
  }));
  const counts = { pending: 0, accepted: 0, rejected: 0, deferred: 0 };
  for (const row of rows) counts[row.status]++;
  const search = query.trim().toLocaleLowerCase('ru');
  const current = rows.find(row => row.item.key === selectedKey);
  useEffect(() => {
    if (selectedKey) {
      previousKey.current = selectedKey;
      detailHeading.current?.focus();
    } else if (previousKey.current) {
      const row = body.current?.querySelector<HTMLElement>(`[data-term-row="${CSS.escape(previousKey.current)}"]`);
      (row || searchInput.current)?.focus();
      previousKey.current = null;
    }
  }, [selectedKey, current?.status]);
  const currentHistory = current ? (comparison.decisions || []).filter(decision => decision.key === current.item.key) : [];
  const matching = rows.filter(row => (!search ? row.status === status :
    [row.item.kind, kindLabels[row.item.kind], row.item.note, row.item.sheet_text, ...row.item.cells,
      ...[row.item.before, row.item.after].flatMap(side => side ? [side.subject, side.room, ...side.classes, ...side.teachers, ...side.groups] : [])]
      .join(' ').toLocaleLowerCase('ru').includes(search)));
  const scrollTop = () => body.current?.scrollTo({ top: 0 });
  const chooseItem = (key: string | null) => {
    setSelectedKey(key);
    onSelect(key);
    setReason('');
    setChosenId('');
    setError('');
    scrollTop();
  };
  const decide = async (action: TermComparisonAction) => {
    if (!current || dirty || readOnly || busy || !reason.trim()) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await onDecide(current.item.key, action, reason.trim(), action === 'accept' ? chosenId || current.item.lesson_id : undefined);
      setReason('');
      setChosenId('');
      setMessage('Решение сохранено в черновике.');
      scrollTop();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Не удалось сохранить решение.');
    } finally {
      setBusy(false);
    }
  };
  const linkedLessonId = current && (chosenId || decisions.get(current.item.key)?.lesson_id || current.item.lesson_id);
  const currentEdit = lessons.find(lesson => lesson.id === linkedLessonId);
  const destination = current?.item.after;
  const choices = destination ? lessons.filter(lesson =>
    destination.day === lesson.day && destination.start === lesson.start && destination.end === lesson.end) : [];
  const preventDecision = dirty || readOnly || busy || !reason.trim();

  return <div className="constructor-conflict-backdrop">
    <dialog ref={dialog} className="constructor-conflict-dialog constructor-publication-panel constructor-term-dialog" aria-labelledby="constructor-term-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
      <header><div><h2 id="constructor-term-title">Сверка с таблицей</h2><small>{name}</small></div>
        <button type="button" disabled={busy} aria-label="Закрыть сверку" onClick={onClose}><X size={20} /></button>
      </header>
      <div ref={body} className="constructor-review-scroll">
        {message ? <output className="constructor-publication-message">{message}</output> : null}
        {current ? <section className="constructor-term-detail" aria-label="Позиция в сверке">
          <button type="button" disabled={busy} onClick={() => chooseItem(null)}>К списку</button>
          <h3 ref={detailHeading} tabIndex={-1}>{current.item.cells.join(', ') || 'Настройки'} · {kindLabels[current.item.kind]}</h3>
          <p>{statusLabels[current.status]} · {current.item.note}</p>
          <div className="constructor-term-sides">
            <Side title="Сохранённый исходный шаблон" side={current.item.before} />
            <Side title="Таблица второго триместра" side={current.item.after} />
            <Side title="Текущая рабочая копия" side={currentEdit || null} emptyText={linkedLessonId
              ? 'Связанного занятия больше нет в рабочей копии.'
              : 'Для этой строки занятие ещё не сопоставлено.'} />
          </div>
          {current.item.sheet_text ? <details className="constructor-review-detail" open>
            <summary>Текст из таблицы</summary>
            <p className="constructor-term-literal">{current.item.sheet_text}</p>
          </details> : null}
          {decisions.get(current.item.key) ? <p><strong>Последнее решение.</strong> {decisions.get(current.item.key)?.reason}</p> : null}
          {currentHistory.length > 1 ? <details className="constructor-review-detail">
            <summary>История решений · {currentHistory.length}</summary>
            <ul>{currentHistory.map((decision, index) => <li key={index}>{statusLabels[decision.status]} · {decision.reason}</li>)}</ul>
          </details> : null}
          {current.status === 'accepted' || current.status === 'rejected'
            ? currentEdit ? <button type="button" onClick={() => onOpenLesson(currentEdit.id)}>Открыть связанное занятие</button> : null
            : <>
            <p>Для принятия внесите правку в конструкторе, сохраните её и вернитесь к этой позиции. Отклонение сохраняет текущий вариант расписания.</p>
            <div className="constructor-term-actions">
              {currentEdit ? <button type="button" disabled={busy} onClick={() => onOpenLesson(currentEdit.id)}>Открыть связанное занятие</button> : null}
              {!currentEdit && destination ? <button type="button" disabled={busy} onClick={() => onOpenSlot(destination)}>Открыть день и слот</button> : null}
            </div>
            {dirty ? <p role="alert">Сохраните изменения в конструкторе перед фиксацией решения.</p> : null}
            {!current.item.lesson_id && destination ? <label>Занятие после правки
              <select disabled={busy || readOnly} value={chosenId} onChange={event => setChosenId(event.target.value)}>
                <option value="">Выберите сохранённое занятие</option>
                {choices.map(lesson => <option key={lesson.id} value={lesson.id}>{lesson.label}</option>)}
              </select>
            </label> : null}
            <label>Основание решения<textarea disabled={busy || readOnly} maxLength={1000} rows={2} value={reason} onChange={event => setReason(event.target.value)} /></label>
            {error ? <p role="alert">{error}</p> : null}
            <div className="constructor-term-actions">
              <button type="button" disabled={preventDecision} onClick={() => void decide('defer')}>Отложить</button>
              <button type="button" disabled={preventDecision} onClick={() => void decide('reject')}>Отклонить</button>
              <button type="button" className="admin-button admin-button--primary" disabled={preventDecision || !(current.item.lesson_id || chosenId)} onClick={() => void decide('accept')}>Принять сохранённую правку</button>
            </div>
          </>}
        </section> : <>
          <div className="constructor-publication-filters" aria-label="Статусы сверки">
            {Object.entries(statusLabels).map(([key, label]) => <button type="button" key={key} aria-pressed={!search && status === key} onClick={() => { setStatus(key as Status); setQuery(''); setShown(20); }}>
              {label} <span>{counts[key as Status]}</span>
            </button>)}
          </div>
          <label className="constructor-term-search">Поиск по сверке<input ref={searchInput} type="search" value={query} onChange={event => { setQuery(event.target.value); setShown(20); }} placeholder="Ячейка, предмет, класс, преподаватель" /></label>
          {comparison.normalizations?.length ? <details className="constructor-review-detail">
            <summary>Безопасные исправления старых записей · {comparison.normalizations.length}</summary>
            <ul className="constructor-review-list">{comparison.normalizations.map(change => <li key={change.lesson_id}>
              <div><strong>{lessons.find(lesson => lesson.id === change.lesson_id)?.label || 'Занятие'}</strong>
                {change.fields.map(field => <p key={field}>{fieldLabels[field] || field} · {normalizationValue(change.before[field])} → {normalizationValue(change.after[field])}</p>)}
              </div>
              {lessons.some(lesson => lesson.id === change.lesson_id) ? <button type="button" onClick={() => onOpenLesson(change.lesson_id)}>Открыть занятие</button> : null}
            </li>)}</ul>
          </details> : null}
          {search ? <p>Найдено во всех статусах · {matching.length}</p> : null}
          {matching.length ? <ul className="constructor-review-list constructor-term-list">
            {matching.slice(0, shown).map(row => <li key={row.item.key}>
              <div><strong>{(row.item.after || row.item.before)?.subject || 'Занятие'}</strong><small>{[row.item.cells.join(', '), kindLabels[row.item.kind], search ? statusLabels[row.status] : ''].filter(Boolean).join(' · ')}</small></div>
              <button type="button" data-term-row={row.item.key} aria-label={`Разобрать ${row.item.cells.join(', ') || row.item.key}`} onClick={() => chooseItem(row.item.key)}>Разобрать</button>
            </li>)}
          </ul> : <p>Подходящих позиций нет.</p>}
          {matching.length > shown ? <button type="button" onClick={() => setShown(count => count + 20)}>Показать ещё</button> : null}

        </>}
      </div>
      <footer><button type="button" disabled={busy} onClick={onClose}>К конструктору</button></footer>
    </dialog>
  </div>;
}
