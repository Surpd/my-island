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
export type TermComparisonChoice = 'original' | 'sheet' | 'current';
type PreviewLesson = Omit<TermComparisonSide, 'kind' | 'warning'>;
type ChoicePlan = { token: string; before: PreviewLesson[]; after: PreviewLesson[] };
export type TermComparisonChoices = Record<TermComparisonChoice, ChoicePlan | { unavailable: string }>;

const choiceLabels: Record<TermComparisonChoice, string> = {
  original: 'Вернуть исходный вариант',
  sheet: 'Взять вариант из таблицы',
  current: 'Оставить рабочую копию',
};
type RosterEvidence = {
  sources: { gid: string | number; range: string; label: string }[];
  historical: string;
  current: string;
  overrides: string;
  reliability: string;
  outcome: 'closed' | 'clarified' | 'context';
  reason: string;
};

type Assessment = {
  category: 'equivalent' | 'safe_fix' | 'proposal' | 'admin';
  reason: string;
  options: string[];
  type: string;
  priority: 1 | 2 | 3;
  original_candidates?: TermComparisonSide[];
  term1_text?: string;
  related_keys?: string[];
  roster_evidence?: RosterEvidence;
};
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
  assessment?: Assessment;
};
type Decision = { key: string; status: Status; reason: string; lesson_id?: string; confirmed_at?: string; choice?: TermComparisonChoice | 'manual' };
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
  accepted: 'Решённые',
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
const weekdays = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];
const priorityLabels: Partial<Record<number, string>> = {
  1: 'Сначала проверьте',
  2: 'Изменения расписания',
  3: 'Остальные позиции',
};
const itemStatusLabels: Record<Status, string> = {
  pending: 'Требует решения',
  accepted: 'Вопрос закрыт',
  rejected: 'Отклонено',
  deferred: 'Отложено',
};
const collator = new Intl.Collator('ru', { numeric: true });
const noDay = weekdays.length;

function itemClasses(item: TermComparisonItem) {
  return [...new Set([...(item.before?.classes || []), ...(item.after?.classes || []),
    ...(item.assessment?.original_candidates || []).flatMap(side => side.classes)]
    .map(label => {
      const short = label.replace(/\s*·\s*базовый класс$/iu, '').trim();
      return /^\d+$/.test(short) ? `${short} класс` : short;
    }).filter(Boolean))].sort((a, b) => collator.compare(a, b));
}
const fieldLabels: Record<string, string> = { activityType: 'Тип занятия', needsClarification: 'Требует уточнения', clarificationReason: 'Причина уточнения' };
function normalizationValue(value: unknown) {
  if (value === 'elective') return 'курс по выбору';
  if (value === true) return 'да';
  if (value === false) return 'нет';
  return typeof value === 'string' && value ? value : 'не указано';
}

const rosterWorkbook = 'https://docs.google.com/spreadsheets/d/1j5dI2VbT0RoW2A5ZkCpMYVdt7cSY4ZjNxQaMWtu--Fo/edit';
const rosterSources = new Map([
  ['189719954', 'Списки учеников по классам'],
  ['728827779', 'Списки учебных групп'],
  ['1097654333', 'Старые списки групп · дополнительная справка'],
]);
const rosterOutcomes: Record<RosterEvidence['outcome'], string> = {
  closed: 'Вопрос закрыт',
  clarified: 'Состав уточнён',
  context: 'Справочные данные',
};
const cellOrRange = /^\$?[A-Z]{1,3}\$?[1-9][0-9]{0,6}(?::\$?[A-Z]{1,3}\$?[1-9][0-9]{0,6})?$/;

function RosterEvidenceDetails({ evidence }: { evidence: RosterEvidence }) {
  const links = new Map<string, { label: string; range: string }>();
  for (const source of evidence.sources || []) {
    const gid = String(source.gid);
    const label = rosterSources.get(gid);
    if (!label) continue;
    const requestedRange = source.range?.trim().toUpperCase() || '';
    const range = cellOrRange.test(requestedRange) ? requestedRange : '';
    const fragment = new URLSearchParams({ gid, ...(range ? { range } : {}) });
    const address = `${rosterWorkbook}#${fragment}`;
    if (!links.has(address)) links.set(address, { label: source.label?.trim() || label, range });
  }
  const facts = [
    ['Исторический состав', evidence.historical],
    ['Текущий состав', evidence.current],
    ['Подтверждённые правки', evidence.overrides],
    ['Надёжность сведений', evidence.reliability],
  ].filter(([, value]) => Boolean(value));

  return <details className="constructor-review-detail constructor-term-roster-evidence">
    <summary>Сведения о составах {rosterOutcomes[evidence.outcome] ? `· ${rosterOutcomes[evidence.outcome]}` : ''}</summary>
    {evidence.reason ? <p>{evidence.reason}</p> : null}
    {facts.length ? <dl>{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl> : null}
    {links.size ? <>
      <strong>Исторические источники</strong>
      <ul>{[...links].map(([href, source]) => <li key={href}>
        <a href={href} target="_blank" rel="noreferrer">{source.label}</a>{source.range ? ` · ${source.range}` : ''}
      </li>)}</ul>
    </> : null}
  </details>;
}

function SideDetails({ side }: { side: PreviewLesson & { warning?: string } }) {
  return <>
    <strong>{side.subject || 'Запись без названия'}</strong>
    <p>{[days[side.day], side.start && side.end ? `${side.start}–${side.end}` : side.start, ...side.classes].filter(Boolean).join(' · ')}</p>
    <dl>
      <dt>Преподаватели</dt><dd>{side.teachers.join(', ') || 'Не указаны'}</dd>
      <dt>Группы</dt><dd>{side.groups.join(', ') || 'Не указаны'}</dd>
      <dt>Кабинет</dt><dd>{side.room || 'Не указан'}</dd>
    </dl>
    {side.warning ? <p className="constructor-term-warning">{side.warning}</p> : null}
  </>;
}

function Side({ title, side, emptyText = 'Записи нет', candidates, action, selected, disabled, onChoose }: {
  title: string;
  side: TermComparisonSide | null;
  emptyText?: string;
  candidates?: TermComparisonSide[];
  action?: TermComparisonChoice;
  selected?: boolean;
  disabled?: boolean;
  onChoose?: (choice: TermComparisonChoice) => void;
}) {
  return <div className="constructor-term-side" data-selected={selected || undefined}>
    <h4>{title}</h4>
    {side ? <SideDetails side={side} /> : <p>{emptyText}</p>}
    {candidates?.length ? <details className="constructor-term-candidates">
      <summary>Возможные исходные занятия · {candidates.length}</summary>
      <ul>{candidates.map((candidate, index) => <li key={index}><SideDetails side={candidate} /></li>)}</ul>
    </details> : null}
    {action && onChoose ? <button type="button" aria-pressed={Boolean(selected)}
      disabled={disabled} onClick={() => onChoose(action)}>{choiceLabels[action]}</button> : null}
  </div>;
}

export function ConstructorTermComparison({
  comparison, name, lessons, initialKey, revision, suspended = false, dirty, readOnly, onClose, onSelect, onOpenLesson, onOpenSlot, onPreview, onApply, onDefer,
}: {
  comparison: Comparison;
  name: string;
  lessons: Lesson[];
  initialKey: string | null;
  revision: number;
  suspended?: boolean;
  dirty: boolean;
  readOnly: boolean;
  onClose: () => void;
  onSelect: (key: string | null) => void;
  onOpenLesson: (id: string, related?: string[], title?: string) => void;
  onOpenSlot: (side: TermComparisonSide, mayCreate?: boolean) => void;
  onPreview: (key: string) => Promise<TermComparisonChoices>;
  onApply: (key: string, choice: TermComparisonChoice, token: string) => Promise<void>;
  onDefer: (key: string) => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const detailHeading = useRef<HTMLHeadingElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const previewHeading = useRef<HTMLHeadingElement>(null);
  const previousKey = useRef<string | null>(initialKey);
  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    if (suspended) node.close();
    else if (!node.open) node.showModal();
  }, [suspended]);
  const [status, setStatus] = useState<Status>('pending');
  const [query, setQuery] = useState('');
  const [dayFilter, setDayFilter] = useState('');
  const [classFilter, setClassFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [shown, setShown] = useState(20);
  const [selectedKey, setSelectedKey] = useState(initialKey);
  const [busy, setBusy] = useState(false);
  const [selectedChoice, setSelectedChoice] = useState<{
    key: string; revision: number; choice: TermComparisonChoice; plan?: TermComparisonChoices[TermComparisonChoice];
  } | null>(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const decisions = new Map((comparison.decisions || []).map(decision => [decision.key, decision]));
  const rows = comparison.items.map(item => {
    const decision = decisions.get(item.key);
    const status = decision?.status || item.initial_status || 'pending';
    const classes = itemClasses(item);
    return {
      item,
      decision,
      status,
      day: item.after?.day ?? item.before?.day ?? noDay,
      classes,
      classLabel: classes.join(', '),
      type: item.assessment?.type || kindLabels[item.kind],
      priority: status === 'pending' || status === 'deferred' ? item.assessment?.priority ?? 4 : 4,
    };
  });
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
  const matching = rows.filter(row => {
    if (dayFilter && String(row.day) !== dayFilter ||
        classFilter && !row.classes.includes(classFilter) ||
        typeFilter && row.type !== typeFilter) return false;
    if (row.status !== status) return false;
    if (!search) return true;
    return [row.type, row.item.note, row.item.sheet_text, row.item.assessment?.reason, row.item.assessment?.term1_text, ...row.item.cells,
      ...row.classes, weekdays[row.day],
      ...[row.item.before, row.item.after, ...(row.item.assessment?.original_candidates || [])].flatMap(side => side ? [side.subject, side.room, ...side.classes, ...side.teachers, ...side.groups] : [])]
      .join(' ').toLocaleLowerCase('ru').includes(search);
  }).sort((a, b) => a.priority - b.priority || a.day - b.day ||
    collator.compare(a.classLabel, b.classLabel) || collator.compare(a.type, b.type));
  const queueGroups: (Pick<(typeof rows)[number], 'priority' | 'day' | 'classLabel'> & { rows: typeof rows })[] = [];
  for (const row of matching.slice(0, shown)) {
    const group = queueGroups.at(-1);
    if (group && group.priority === row.priority && group.day === row.day && group.classLabel === row.classLabel) {
      group.rows.push(row);
    } else {
      queueGroups.push({ priority: row.priority, day: row.day, classLabel: row.classLabel, rows: [row] });
    }
  }
  const availableDays = [...new Set(rows.map(row => row.day))].sort((a, b) => a - b);
  const availableClasses = [...new Set(rows.flatMap(row => row.classes))].sort((a, b) => collator.compare(a, b));
  const availableTypes = [...new Set(rows.map(row => row.type))].sort((a, b) => collator.compare(a, b));
  const filtered = Boolean(dayFilter || classFilter || typeFilter);
  const assessment = current?.item.assessment;
  const relatedItems = [...new Set(assessment?.related_keys)].flatMap(key => {
    if (key === selectedKey) return [];
    const item = comparison.items.find(candidate => candidate.key === key);
    return item ? [item] : [];
  });
  const automaticallyResolved = current?.item.initial_status === 'accepted' && !current.decision;
  const canDecide = current?.status === 'pending' || current?.status === 'deferred';
  const scrollTop = () => body.current?.scrollTo({ top: 0 });
  const chooseItem = (key: string | null) => {
    setSelectedKey(key);
    onSelect(key);
    setSelectedChoice(null);
    setError('');
    setMessage('');
    scrollTop();
  };
  const selectedPlan = selectedChoice?.key === selectedKey && selectedChoice.revision === revision
    ? selectedChoice.plan : undefined;
  useEffect(() => {
    if (selectedPlan && 'token' in selectedPlan) previewHeading.current?.focus();
  }, [selectedPlan]);
  const chooseVariant = async (choice: TermComparisonChoice) => {
    if (!current || dirty || readOnly || busy) return;
    const key = current.item.key;
    setError('');
    setSelectedChoice({ key, revision, choice });
    setBusy(true);
    try {
      const previews = await onPreview(key);
      setSelectedChoice({ key, revision, choice, plan: previews[choice] });
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Не удалось показать вариант.');
    } finally {
      setBusy(false);
    }
  };
  const saveDecision = async (defer = false) => {
    if (!current || dirty || readOnly || busy) return;
    if (!defer && (!selectedChoice || !selectedPlan || !('token' in selectedPlan))) return;
    setBusy(true);
    setError('');
    try {
      if (defer) {
        await onDefer(current.item.key);
      } else if (selectedChoice && selectedPlan && 'token' in selectedPlan) {
        await onApply(current.item.key, selectedChoice.choice, selectedPlan.token);
      }
      chooseItem(null);
      setMessage(defer ? 'Вопрос отложен.' : 'Решение сохранено в рабочей копии.');
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Не удалось сохранить решение.');
    } finally {
      setBusy(false);
    }
  };
  const linkedLessonId = current && (decisions.get(current.item.key)?.lesson_id || current.item.lesson_id);
  const currentEdit = lessons.find(lesson => lesson.id === linkedLessonId);
  const destination = current?.item.after;
  const proposedMove = currentEdit && destination && (
    currentEdit.day !== destination.day || currentEdit.start !== destination.start || currentEdit.end !== destination.end
  );
  const preventDecision = dirty || readOnly || busy;
  const choose = canDecide && !readOnly ? (choice: TermComparisonChoice) => void chooseVariant(choice) : undefined;

  return <div hidden={suspended} className={suspended ? undefined : "constructor-conflict-backdrop"}>
    <dialog ref={dialog} className="constructor-conflict-dialog constructor-publication-panel constructor-term-dialog" aria-labelledby="constructor-term-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
      <header><div><h2 id="constructor-term-title">Сверка с таблицей</h2><small>{name}</small></div>
        <button type="button" disabled={busy} aria-label="Закрыть сверку" onClick={onClose}><X size={20} /></button>
      </header>
      <div ref={body} className="constructor-review-scroll">
        {message ? <output className="constructor-publication-message">{message}</output> : null}
        {current ? <section className="constructor-term-detail" aria-label="Позиция в сверке">
          <button type="button" disabled={busy} onClick={() => chooseItem(null)}>К списку</button>
          <h3 ref={detailHeading} tabIndex={-1}>{current.item.cells.join(', ') || 'Настройки'} · {current.type}</h3>
          <p className="constructor-term-state">{itemStatusLabels[current.status]}{current.priority === 1 ? ' · высокий приоритет' : ''}</p>
          {canDecide ? <p>Выберите вариант и проверьте, что изменится. Подтверждение сохраняет результат в рабочей копии.</p> : null}
          <div className="constructor-term-sides">
            <Side title="Исходный шаблон" side={current.item.before} emptyText={current.item.kind === 'added'
              ? 'Записи не было.' : 'Связь с исходным занятием не установлена.'} candidates={assessment?.original_candidates}
              action="original" selected={selectedChoice?.choice === 'original'} disabled={preventDecision} onChoose={choose} />
            <Side title="Таблица II триместра" side={current.item.after}
              action="sheet" selected={selectedChoice?.choice === 'sheet'} disabled={preventDecision} onChoose={choose} />
            <Side title="Рабочая копия" side={currentEdit || null} emptyText={linkedLessonId
              ? 'Связанного занятия больше нет в рабочей копии.'
              : 'Для этой строки занятие ещё не сопоставлено.'}
              action="current" selected={selectedChoice?.choice === 'current'} disabled={preventDecision} onChoose={choose} />
          </div>
          {dirty ? <p role="alert">Сохраните изменения в конструкторе перед подтверждением варианта.</p> : null}
          {busy && !selectedPlan && selectedChoice ? <output>Готовим предпросмотр…</output> : null}
          {selectedPlan && 'unavailable' in selectedPlan ? <output>{selectedPlan.unavailable}</output> : null}
          {error ? <p role="alert">{error}</p> : null}
          {canDecide && selectedPlan && 'token' in selectedPlan ? <section className="constructor-term-result" aria-label="Что изменится">
            <h4 ref={previewHeading} tabIndex={-1}>Что изменится</h4>
            {!selectedPlan.before.length && !selectedPlan.after.length ? <p>Расписание останется как сейчас. Вопрос закроется.</p> : <>
              {selectedPlan.before.length ? <div><strong>Сейчас</strong><ul>{selectedPlan.before.map((lesson, i) => <li key={i}><SideDetails side={lesson} /></li>)}</ul></div> : null}
              <div><strong>После подтверждения</strong>{selectedPlan.after.length
                ? <ul>{selectedPlan.after.map((lesson, i) => <li key={i}><SideDetails side={lesson} /></li>)}</ul>
                : <p>Связанное занятие будет удалено из рабочей копии.</p>}</div>
            </>}
          </section> : null}
          {current.decision ? <output>{current.decision.reason}</output> : null}
          {!canDecide
            ? currentEdit ? <button type="button" onClick={() => onOpenLesson(currentEdit.id, relatedItems.flatMap(item => item.lesson_id || []), [current.item.cells.join('/'), assessment?.type || 'Сверка с таблицей'].join(' · '))}>Открыть связанное занятие</button> : null
            : <>
            <div className="constructor-term-actions">
              {currentEdit ? <button type="button" disabled={busy} onClick={() => onOpenLesson(currentEdit.id, relatedItems.flatMap(item => item.lesson_id || []), [current.item.cells.join('/'), assessment?.type || 'Сверка с таблицей'].join(' · '))}>Изменить вручную в конструкторе</button> : null}
              {proposedMove ? <button type="button" disabled={busy} onClick={() => onOpenSlot(destination, false)}>Показать предложенный слот</button> : null}
              {!currentEdit && !destination && current.item.before ? <button type="button" disabled={busy} onClick={() => onOpenSlot(current.item.before!, false)}>Показать прежний слот</button> : null}
              {!currentEdit && destination ? <button type="button" disabled={busy} onClick={() => onOpenSlot(destination)}>Открыть день и слот</button> : null}
              {!readOnly ? <button type="button" disabled={preventDecision} onClick={() => void saveDecision(true)}>Отложить</button> : null}
            </div>
          </>}
          <details className="constructor-review-detail">
            <summary>{automaticallyResolved ? 'Почему вопрос закрыт' : 'Почему возник вопрос'}</summary>
            <p>{assessment?.reason || current.item.note}</p>
            {assessment?.options?.length && !automaticallyResolved ? <ul>{assessment.options.map(option => <li key={option}>{option}</li>)}</ul> : null}
            {relatedItems.length ? <div className="constructor-term-actions" aria-label="Связанные позиции">
              {relatedItems.map(item => {
                const cells = item.cells.join(', ') || item.key;
                return <button type="button" key={item.key} disabled={busy} aria-label={`Открыть связанную позицию ${cells}`}
                  onClick={() => chooseItem(item.key)}>{cells}</button>;
              })}
            </div> : null}
            {current.item.sheet_text ? <details className="constructor-review-detail">
              <summary>Текст из таблицы</summary>
              <p className="constructor-term-literal">{current.item.sheet_text}</p>
            </details> : null}
            {assessment?.term1_text ? <details className="constructor-review-detail">
              <summary>Ячейка в таблице I триместра</summary>
              <p className="constructor-term-literal">{assessment.term1_text}</p>
            </details> : null}
            {assessment?.roster_evidence ? <RosterEvidenceDetails evidence={assessment.roster_evidence} /> : null}
            {currentHistory.length > 1 ? <details className="constructor-review-detail">
              <summary>История решений · {currentHistory.length}</summary>
              <ul>{currentHistory.map((decision, index) => <li key={index}>{statusLabels[decision.status]} · {decision.reason}</li>)}</ul>
            </details> : null}
          </details>
        </section> : <>
          <div className="constructor-publication-filters" aria-label="Статусы сверки">
            {Object.entries(statusLabels).map(([key, label]) => <button type="button" key={key} aria-pressed={!search && status === key} onClick={() => { setStatus(key as Status); setQuery(''); setShown(20); }}>
              {label} <span>{counts[key as Status]}</span>
            </button>)}
          </div>
          <label className="constructor-term-search">Поиск по сверке<input ref={searchInput} type="search" value={query} onChange={event => { setQuery(event.target.value); setShown(20); }} placeholder="Ячейка, предмет, класс, преподаватель" /></label>
          <div className="constructor-term-filters">
            <label>День<select aria-label="День" value={dayFilter} onChange={event => { setDayFilter(event.target.value); setShown(20); }}>
              <option value="">Все дни</option>
              {availableDays.map(day => <option key={day} value={day}>{weekdays[day] || 'День не определён'}</option>)}
            </select></label>
            <label>Класс<select aria-label="Класс" value={classFilter} onChange={event => { setClassFilter(event.target.value); setShown(20); }}>
              <option value="">Все классы</option>
              {availableClasses.map(label => <option key={label}>{label}</option>)}
            </select></label>
            <label>Тип вопроса<select aria-label="Тип вопроса" value={typeFilter} onChange={event => { setTypeFilter(event.target.value); setShown(20); }}>
              <option value="">Все типы</option>
              {availableTypes.map(label => <option key={label}>{label}</option>)}
            </select></label>
          </div>
          {comparison.normalizations?.length ? <details className="constructor-review-detail">
            <summary>Безопасные исправления старых записей · {comparison.normalizations.length}</summary>
            <ul className="constructor-review-list">{comparison.normalizations.map(change => <li key={change.lesson_id}>
              <div><strong>{lessons.find(lesson => lesson.id === change.lesson_id)?.label || 'Занятие'}</strong>
                {change.fields.map(field => <p key={field}>{fieldLabels[field] || field} · {normalizationValue(change.before[field])} → {normalizationValue(change.after[field])}</p>)}
              </div>
              {lessons.some(lesson => lesson.id === change.lesson_id) ? <button type="button" onClick={() => onOpenLesson(change.lesson_id)}>Открыть занятие</button> : null}
            </li>)}</ul>
          </details> : null}
          {search || filtered ? <p aria-live="polite">
            {search ? 'Найдено во всех статусах' : 'Найдено'} · {matching.length}
          </p> : null}
          {queueGroups.map((group, index) => <div key={`${group.priority}:${group.day}:${group.classLabel}`} className="constructor-term-queue">
            {priorityLabels[group.priority] && queueGroups[index - 1]?.priority !== group.priority ? <h3 className="constructor-term-priority" data-priority={group.priority}>{priorityLabels[group.priority]}</h3> : null}
            <h4>{weekdays[group.day] || 'День не определён'} · {group.classLabel || 'Класс не определён'}</h4>
            <ul className="constructor-review-list constructor-term-list">
              {group.rows.map(row => <li key={row.item.key}>
                <div>
                  <strong>{(row.item.after || row.item.before)?.subject || 'Занятие'}</strong>
                  <small>{[row.item.cells.join(', '), row.type, search ? itemStatusLabels[row.status] : ''].filter(Boolean).join(' · ')}</small>
                  {row.item.assessment?.reason ? <p>{row.item.assessment.reason}</p> : null}
                  {row.item.assessment?.original_candidates?.length ? <small>Возможные исходные занятия · {row.item.assessment.original_candidates.length}</small> : null}
                </div>
                <button type="button" data-term-row={row.item.key} aria-label={`Разобрать ${row.item.cells.join(', ') || row.item.key}`} onClick={() => chooseItem(row.item.key)}>Разобрать</button>
              </li>)}
            </ul>
          </div>)}
          {!matching.length ? <div><p>Подходящих позиций нет.</p>
            {filtered ? <button type="button" onClick={() => { setDayFilter(''); setClassFilter(''); setTypeFilter(''); setShown(20); }}>Сбросить фильтры</button> : null}
          </div> : null}
          {matching.length > shown ? <button type="button" onClick={() => setShown(count => count + 20)}>Показать ещё</button> : null}

        </>}
      </div>
      <footer className="constructor-term-actions">
        <button type="button" disabled={busy} onClick={onClose}>К конструктору</button>
        {canDecide && selectedPlan && 'token' in selectedPlan ? <button type="button" className="admin-button admin-button--primary"
          disabled={preventDecision} onClick={() => void saveDecision()}>Подтвердить вариант</button> : null}
      </footer>
    </dialog>
  </div>;
}
