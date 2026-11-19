'use client';

import { useState } from 'react';
import { formatLessonTimeRange } from './lesson-time';

export type RosterScope = 'stale' | 'template' | 'day' | 'class';
export type RosterPreview = {
  revision: number;
  preview_token: string;
  summary: { selected_count: number; changed_count: number; added_count: number; removed_count: number; safe_count: number; unresolved_count: number };
  lessons: { id: string; subject: string; day?: number; start?: string; end?: string; safe: boolean; changed?: boolean; reason?: string; old_count: number; proposed_count: number | null }[];
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

function LessonButton({ id, onOpen, disabled }: { id: string; onOpen: (id: string) => void; disabled: boolean }) {
  return <button type="button" className="constructor-review-text-button" disabled={disabled} onClick={() => onOpen(id)}>Открыть занятие</button>;
}

export function ConstructorRosterReview({ preview, error, busy, readOnly, classLabel, scope, onScopeChange, onApply, onOpenLesson }: {
  preview: RosterPreview | null;
  error: string;
  busy: boolean;
  readOnly: boolean;
  classLabel?: string;
  scope: RosterScope;
  onScopeChange: (scope: RosterScope) => void;
  onApply: () => void;
  onOpenLesson: (id: string) => void;
}) {
  return <section aria-label="Составы учебных групп">
    <h4>Составы учебных групп</h4>
    {error ? <p role="alert">Не удалось проверить составы. {error}</p> : null}
    {preview ? <>
      {preview.summary.selected_count
        ? <p>Можно обновить <strong>{preview.summary.safe_count}</strong> составов. Нужны уточнения по <strong>{preview.summary.unresolved_count}</strong> занятиям.</p>
        : <p>Составы актуальны.</p>}
      {preview.lessons.length ? <details>
        <summary>Составы по занятиям · {preview.lessons.length}</summary>
        <ul className="constructor-review-list">{preview.lessons.map(lesson => <li key={lesson.id}>
          <div>
            <strong>{lesson.subject || 'Занятие'}</strong>
            <small>{[lesson.day != null ? rosterDays[lesson.day] : '', formatLessonTimeRange(lesson.start, lesson.end)].filter(Boolean).join(' · ')}</small>
            {lesson.safe ? <p>Участников {lesson.old_count} → {lesson.proposed_count} {lesson.changed ? '· можно обновить' : ''}</p> : <p>{lesson.reason || 'Уточните группу, класс или правило расчёта состава.'}</p>}
          </div>
          <LessonButton id={lesson.id} onOpen={onOpenLesson} disabled={busy} />
        </li>)}</ul>
      </details> : null}
      {preview.summary.safe_count > 0 && !readOnly ? <>
        <p>В предложенных составах добавлений {preview.summary.added_count}, исключений {preview.summary.removed_count}. Подтверждение сохранит изменения в открытом черновике.</p>
        <button type="button" disabled={busy} onClick={onApply}>Подтвердить проверенные составы</button>
      </> : null}
    </> : null}
    <details className="constructor-review-advanced">
      <summary>Проверить составы в другом диапазоне</summary>
      <label>Область проверки<select value={scope} disabled={busy} onChange={event => onScopeChange(event.target.value as RosterScope)}>
        <option value="stale">Составы, требующие внимания</option>
        <option value="template">Весь черновик</option>
        <option value="day">Выбранный день</option>
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
  return <section aria-label="Старые записи">
    <h4>Старые записи</h4>
    {error ? <p role="alert">Не удалось проверить старые записи. {error}</p> : null}
    {preview ? <>
      {preview.automatic_count ? <p>Можно внести <strong>{preview.automatic_count}</strong> исправлений в копии. Типы курсов по выбору — {preview.activity_type_change_count}, подтверждённые назначения преподавателей — {preview.teacher_warning_clear_count}.</p> : <p>Автоматические исправления для старых записей отсутствуют.</p>}
      {preview.changes.length ? <details>
        <summary>Посмотреть исправления до и после · {preview.changes.length}</summary>
        <ul className="constructor-review-list">{preview.changes.map(item => <li key={`${item.lesson_id}:${item.field}`}>
          <div>
            <strong>{lessonLabels[item.lesson_id] || 'Занятие'}</strong>
            {item.field === 'activity_type' ? <p>Тип занятия · не задан → курс по выбору</p> : <p>{item.before} → {item.after}</p>}
            <small>{item.field === 'activity_type' ? 'В исходном расписании направления уже связаны в общий блок.' : item.reason}</small>
          </div>
          <LessonButton id={item.lesson_id} onOpen={onOpenLesson} disabled={busy} />
        </li>)}</ul>
      </details> : null}
      {preview.manual.length ? <details>
        <summary>Требуют решения · {preview.manual.length}</summary>
        <ul className="constructor-review-list">{preview.manual.map(item => <li key={item.lesson_id}>
          <div><strong>{lessonLabels[item.lesson_id] || 'Занятие'}</strong><p>{item.reason}</p></div>
          <LessonButton id={item.lesson_id} onOpen={onOpenLesson} disabled={busy} />
        </li>)}</ul>
      </details> : null}
      {preview.retained_warning_count ? <details>
        <summary>Сохранённые предупреждения · {preview.retained_warning_count}</summary>
        {preview.retained_warnings.map(warning => <div className="constructor-review-warning" key={warning.reason}>
          <strong>{warning.reason} · {warning.count}</strong>
          <ul className="constructor-review-list">{warning.examples.map(example => <li key={example.lesson_id}>
            <span>{lessonLabels[example.lesson_id] || example.subject}</span><LessonButton id={example.lesson_id} onOpen={onOpenLesson} disabled={busy} />
          </li>)}</ul>
        </div>)}
      </details> : null}
      {preview.automatic_count > 0 && !readOnly ? <>
        <label className="constructor-publication-confirm"><input type="checkbox" checked={confirmed} disabled={busy} onChange={event => setConfirmedPlan(event.target.checked ? preview.plan_hash : null)} />Применить предложения к отдельному черновику и открыть его для проверки.</label>
        <button type="button" disabled={busy || !confirmed} onClick={() => { setConfirmedPlan(null); onCopy(); }}>Создать исправленную копию</button>
      </> : null}
    </> : null}
  </section>;
}
