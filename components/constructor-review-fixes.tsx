'use client';

import { useState } from 'react';
import { ArrowRight } from 'lucide-react';
import { formatLessonTimeRange } from './lesson-time';

export type RosterScope = 'stale' | 'template' | 'day' | 'class';
export type RosterPreview = {
  revision: number;
  preview_token: string;
  summary: { selected_count: number; changed_count: number; added_count: number; removed_count: number; safe_count: number; unresolved_count: number };
  lessons: {
    id: string;
    subject: string;
    day?: number;
    start?: string;
    end?: string;
    safe: boolean;
    changed?: boolean;
    reason?: string;
    old_count: number;
    proposed_count: number | null;
  }[];
};
export type NormalizationPreview = {
  source_id: string;
  source_revision: number;
  source_hash: string;
  plan_hash: string;
  automatic_count: number;
  activity_type_change_count: number;
  teacher_warning_clear_count: number;
  changes: { lesson_id: string; field?: string; before: string; after: string; reason: string }[];
  manual: { lesson_id: string; reason: string }[];
  retained_warning_count: number;
  retained_warnings: { reason: string; count: number; examples: { lesson_id: string; subject: string }[] }[];
};
export type NormalizationCopy = {
  id: string;
  name: string;
  changed_count: number;
  changed_type_count: number;
  cleared_teacher_warning_count: number;
  lesson_count: number;
};

const rosterDays = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

function reasonLabel(reason: string) {
  if (/групп.*другого предмета|прежняя математическая группа удалена/i.test(reason)) return 'Замените старую группу на действующую группу этого предмета.';
  if (/состав.*(измен|отлич)|участник.*сохран/i.test(reason)) return 'Сверьте сохранённый и текущий списки участников.';
  if (/подтверждённого назначения преподавателя/i.test(reason)) return 'Проверьте, кто ведёт этот предмет в группе.';
  if (/не подтверждён преподаватель|преподаватель.*неактив/i.test(reason)) return 'Проверьте преподавателя занятия по справочнику.';
  if (/направление.*(электив|курс).*блок|не связан.*блок/i.test(reason)) return 'Сопоставьте направление курса по выбору с общим блоком.';
  if (/класс.*не (найден|выбран)/i.test(reason)) return 'Выберите действующий класс.';
  if (/нет.*(членства|участников)|пуст.*групп/i.test(reason)) return 'Проверьте, кто входит в эту группу.';
  if (/не.*(определ|подтверж).*состав|аудитори/i.test(reason)) return 'Уточните, кто должен посещать занятие, и проверьте состав.';
  return reason.trim() || 'Причина не сохранена.';
}

function LessonButton({ id, label, action = 'Показать в расписании', onOpen, disabled, available }: {
  id: string;
  label: string;
  action?: string;
  onOpen: (id: string) => void;
  disabled: boolean;
  available: boolean;
}) {
  return available ? <button type="button" className="constructor-review-text-button" disabled={disabled}
    aria-label={`${action} — ${label}`} data-review-fix-lesson-id={id} onClick={() => onOpen(id)}>
    {action}<ArrowRight size={14} aria-hidden="true" />
  </button> : <small>Этого занятия нет в открытом черновике.</small>;
}

function ReasonDetails({ reason }: { reason?: string }) {
  if (!reason) return null;
  return <details className="constructor-review-technical">
    <summary>Технические подробности</summary>
    <p>{reason}</p>
  </details>;
}

export function ConstructorRosterReview({ preview, error, busy, readOnly, classLabel, lessonLabels, scope, onScopeChange, onApply, onOpenLesson }: {
  preview: RosterPreview | null;
  error: string;
  busy: boolean;
  readOnly: boolean;
  classLabel?: string;
  lessonLabels: Record<string, string>;
  scope: RosterScope;
  onScopeChange: (scope: RosterScope) => void;
  onApply: () => void;
  onOpenLesson: (id: string) => void;
}) {
  const needsAttention = preview?.lessons.filter(lesson => lesson.changed || !lesson.safe) || [];
  return <section aria-labelledby="constructor-review-rosters-title">
    <h3 id="constructor-review-rosters-title">Составы групп</h3>
    {error ? <div role="alert">
      <p>Составы не удалось проверить. Повторите проверку.</p>
      <ReasonDetails reason={error} />
      <button type="button" disabled={busy} onClick={() => onScopeChange(scope)}>Проверить снова</button>
    </div> : null}
    {preview ? <>
      {needsAttention.length ? <p>
        Можно обновить составы для <strong>{preview.summary.safe_count}</strong> занятий.
        Нужны уточнения по <strong>{preview.summary.unresolved_count}</strong> занятиям.
      </p> : <p>В выбранном диапазоне составы актуальны.</p>}
      {needsAttention.length ? <details data-review-fix="roster-lessons">
        <summary>Посмотреть составы по занятиям ({needsAttention.length})</summary>
        <ul className="constructor-review-list">{needsAttention.map(lesson => <li key={lesson.id}>
          <div>
            <strong>{lesson.subject || 'Занятие'}</strong>
            <small>{lessonLabels[lesson.id] || [lesson.day != null ? rosterDays[lesson.day] : '', formatLessonTimeRange(lesson.start, lesson.end)].filter(Boolean).join(' · ')}</small>
            {lesson.safe ? <p>В черновике — {lesson.old_count}, в текущем составе — {lesson.proposed_count}.</p> :
              <p>{reasonLabel(lesson.reason || '')}</p>}
            {!lesson.safe ? <ReasonDetails reason={lesson.reason} /> : null}
          </div>
          <LessonButton id={lesson.id} label={lesson.subject || 'занятие'} action="Проверить состав"
            onOpen={onOpenLesson} disabled={busy} available={Boolean(lessonLabels[lesson.id])} />
        </li>)}</ul>
      </details> : null}
      {preview.summary.safe_count > 0 && !readOnly ? <>
        <p>Будет добавлено участников — {preview.summary.added_count}, исключено — {preview.summary.removed_count}. Изменения сохранятся в открытом черновике.</p>
        <button type="button" disabled={busy} onClick={onApply}>Подтвердить проверенные составы</button>
      </> : null}
    </> : null}
    <details className="constructor-review-advanced" data-review-fix="roster-scope">
      <summary>Проверить составы в другом диапазоне</summary>
      <label>Область проверки<select value={scope} disabled={busy} onChange={event => onScopeChange(event.target.value as RosterScope)}>
        <option value="stale">Составы, требующие внимания</option>
        <option value="template">Весь черновик</option>
        <option value="day">Выбранный день в сетке</option>
        <option value="class" disabled={!classLabel}>{classLabel || 'Выбранный класс или группа'}</option>
      </select></label>
    </details>
  </section>;
}

export function ConstructorLegacyReview({ preview, error, busy, readOnly, lessonLabels, onCopy, onOpenLesson }: {
  preview: NormalizationPreview | null;
  error: string;
  busy: boolean;
  readOnly: boolean;
  lessonLabels: Record<string, string>;
  onCopy: () => void;
  onOpenLesson: (id: string) => void;
}) {
  const [confirmedPlan, setConfirmedPlan] = useState<string | null>(null);
  const confirmed = confirmedPlan !== null && confirmedPlan === preview?.plan_hash;
  const lessonButton = (id: string, label: string) => <LessonButton id={id} label={label}
    onOpen={onOpenLesson} disabled={busy} available={Boolean(lessonLabels[id])} />;
  return <section aria-labelledby="constructor-review-legacy-title">
    <h3 id="constructor-review-legacy-title">Старые записи</h3>
    {error ? <div role="alert">
      <p>Старые записи не удалось проверить. В разделе «Проблемы» можно запустить проверку ещё раз.</p>
      <ReasonDetails reason={error} />
    </div> : null}
    {preview ? <>
      {preview.automatic_count ? <p>
        Можно внести <strong>{preview.automatic_count}</strong> исправлений в отдельной копии.
        Типы курсов — {preview.activity_type_change_count}, назначения преподавателей — {preview.teacher_warning_clear_count}.
      </p> : <p>Готовых исправлений для старых записей нет.</p>}
      {preview.changes.length ? <details data-review-fix="legacy-changes">
        <summary>Посмотреть предлагаемые исправления ({preview.changes.length})</summary>
        <ul className="constructor-review-list">{preview.changes.map(item => <li key={`${item.lesson_id}:${item.field}`}>
          <div>
            <strong>{lessonLabels[item.lesson_id] || 'Занятие'}</strong>
            {item.field === 'activity_type' ? <p>Указать тип «Курс по выбору». Направления уже входят в один блок.</p> :
              <p>Снять предупреждение о преподавателе. Назначение подтверждено по справочнику.</p>}
          </div>
          {lessonButton(item.lesson_id, lessonLabels[item.lesson_id] || 'занятие')}
        </li>)}</ul>
      </details> : null}
      {preview.manual.length ? <details data-review-fix="legacy-manual">
        <summary>Требуют решения ({preview.manual.length})</summary>
        <ul className="constructor-review-list">{preview.manual.map(item => <li key={item.lesson_id}>
          <div>
            <strong>{lessonLabels[item.lesson_id] || 'Занятие'}</strong>
            <p>{reasonLabel(item.reason)}</p>
            {reasonLabel(item.reason) !== item.reason ? <ReasonDetails reason={item.reason} /> : null}
          </div>
          {lessonButton(item.lesson_id, lessonLabels[item.lesson_id] || 'занятие')}
        </li>)}</ul>
      </details> : null}
      {preview.retained_warning_count ? <details data-review-fix="legacy-retained">
        <summary>Предупреждения, которые сохранятся ({preview.retained_warning_count})</summary>
        {preview.retained_warnings.map(warning => <div className="constructor-review-warning" key={warning.reason}>
          <strong>{warning.reason.trim() || 'Причина не сохранена.'}</strong>
          {reasonLabel(warning.reason) !== warning.reason ? <p>{reasonLabel(warning.reason)}</p> : null}
          <p>Записей — {warning.count}{warning.count > warning.examples.length ? `. Показаны первые ${warning.examples.length}` : ''}.</p>
          <ul className="constructor-review-list">{warning.examples.map(example => <li key={example.lesson_id}>
            <span>{lessonLabels[example.lesson_id] || example.subject}</span>{lessonButton(example.lesson_id, example.subject)}
          </li>)}</ul>
        </div>)}
      </details> : null}
      {preview.automatic_count > 0 && !readOnly ? <>
        <label className="constructor-publication-confirm">
          <input type="checkbox" checked={confirmed} disabled={busy} onChange={event => setConfirmedPlan(event.target.checked ? preview.plan_hash : null)} />
          Применить предложения к отдельной копии и открыть её для проверки.
        </label>
        <button type="button" disabled={busy || !confirmed} onClick={() => { setConfirmedPlan(null); onCopy(); }}>Создать исправленную копию</button>
      </> : null}
    </> : null}
  </section>;
}
