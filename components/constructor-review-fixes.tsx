'use client';

import { useState, type ReactNode } from 'react';
import { ArrowRight, ChevronDown } from 'lucide-react';
import { formatLessonTimeRange } from './lesson-time';
import { legacyReviewReason } from './constructor-review-language';

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
  scope_proposals?: { lesson_id: string; kind: 'lunch_verified' | 'elective_availability'; problem?: string }[];
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

export function ReviewDisclosure({ title, count, children, groupKey, secondary = false, initiallyOpen = false }: {
  title: string; count?: number; children: ReactNode; groupKey?: string; secondary?: boolean; initiallyOpen?: boolean;
}) {
  const [expanded, setExpanded] = useState(initiallyOpen);
  return <details className={`constructor-review-disclosure${secondary ? ' is-secondary' : ''}`}
    open={initiallyOpen || undefined} data-review-fix={groupKey} onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary aria-expanded={expanded}>
      <span>{title}</span>{count == null ? null : <span className="constructor-review-disclosure-count">{count}</span>}
      <ChevronDown size={17} aria-hidden="true" />
    </summary>
    <div className="constructor-review-disclosure-body">{children}</div>
  </details>;
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
  return <ReviewDisclosure title="Сведения для поддержки" secondary><p>{reason}</p></ReviewDisclosure>;
}

export function ConstructorRosterReview({ preview, error, busy, readOnly, classLabel, lessonLabels, lessonImpact, checkingScope, scope, onScopeChange, onApply, onOpenLesson }: {
  preview: RosterPreview | null;
  error: string;
  busy: boolean;
  readOnly: boolean;
  classLabel?: string;
  checkingScope: boolean;
  lessonLabels: Record<string, string>;
  lessonImpact: (id: string) => string;
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
      {needsAttention.length ? <ReviewDisclosure title="Составы по занятиям" count={needsAttention.length} groupKey="roster-lessons">
        <ul className="constructor-review-list">{needsAttention.map(lesson => <li key={lesson.id}>
          <div>
            <strong>{lesson.subject || 'Занятие'}</strong>
            <small>{lessonLabels[lesson.id] || [lesson.day != null ? rosterDays[lesson.day] : '', formatLessonTimeRange(lesson.start, lesson.end)].filter(Boolean).join(' · ')}</small>
            {lesson.safe ? <p>В черновике — {lesson.old_count}, в текущем составе — {lesson.proposed_count}.</p> :
              <p><b>{legacyReviewReason(lesson.reason).title}.</b> {legacyReviewReason(lesson.reason).action}</p>}
            <p>{lessonImpact(lesson.id)}</p>
            {!lesson.safe ? <ReasonDetails reason={lesson.reason} /> : null}
          </div>
          <LessonButton id={lesson.id} label={lesson.subject || 'занятие'} action="Проверить состав"
            onOpen={onOpenLesson} disabled={busy} available={Boolean(lessonLabels[lesson.id])} />
        </li>)}</ul>
      </ReviewDisclosure> : null}
      {preview.summary.safe_count > 0 && !readOnly ? <>
        <p>Будет добавлено участников — {preview.summary.added_count}, исключено — {preview.summary.removed_count}. Изменения сохранятся в открытом черновике.</p>
        <button type="button" disabled={busy} onClick={onApply}>Подтвердить проверенные составы</button>
      </> : null}
    </> : null}
    <ReviewDisclosure title="Проверить другой день, класс или весь черновик" groupKey="roster-scope" secondary>
      <label>Проверять при выборе<select value={scope} disabled={busy} onChange={event => onScopeChange(event.target.value as RosterScope)}>
        <option value="stale">Составы, требующие внимания</option>
        <option value="template">Весь черновик</option>
        <option value="day">Выбранный день в сетке</option>
        <option value="class" disabled={!classLabel}>{classLabel || 'Выбранный класс или группа'}</option>
      </select></label>
      <output aria-live="polite">{checkingScope ? 'Проверяем составы…' : preview ? `Проверено занятий: ${preview.summary.selected_count}. Изменения сохраняются только после подтверждения.` : ''}</output>
    </ReviewDisclosure>
  </section>;
}

export function ConstructorLegacyReview({ preview, error, busy, readOnly, lessonLabels, lessonImpact, onCopy, onOpenLesson }: {
  preview: NormalizationPreview | null;
  error: string;
  busy: boolean;
  readOnly: boolean;
  lessonLabels: Record<string, string>;
  lessonImpact: (id: string) => string;
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
      {preview.changes.length ? <ReviewDisclosure title="Можно исправить в копии" count={preview.changes.length} groupKey="legacy-changes">
        <ul className="constructor-review-list">{preview.changes.map(item => <li key={`${item.lesson_id}:${item.field}`}>
          <div>
            <strong>{lessonLabels[item.lesson_id] || 'Занятие'}</strong>
            {item.field === 'activity_type' ? <p>Указать тип «Курс по выбору». Направления уже входят в один блок.</p> :
              item.field === 'elective_scope_warning' ? <p>Снять устаревшую пометку о составе курса: подтверждены общий блок, время и классы. Зачисление учеников не меняется.</p> :
                <p>Снять предупреждение о преподавателе. Назначение подтверждено по справочнику.</p>}
          </div>
          {lessonButton(item.lesson_id, lessonLabels[item.lesson_id] || 'занятие')}
        </li>)}</ul>
      </ReviewDisclosure> : null}
      {preview.scope_proposals?.length ? <ReviewDisclosure title="Проверка охвата занятий" count={preview.scope_proposals.length} groupKey="scope-evidence">
        <p>Эти сведения не меняют черновик и не входят в исправления копии.</p>
        <ul className="constructor-review-list">{preview.scope_proposals.map(item => <li key={item.lesson_id}>
          <div>
            <strong>{lessonLabels[item.lesson_id] || 'Занятие'}</strong>
            {item.kind === 'lunch_verified' ? <p>Классы и время обеда подтверждены. Старый вопрос об охвате не требует решения; остальные замечания остаются в проверке.</p> :
              <><p>Предлагаем открыть курс для всех классов 5–6. Это доступность курса, а не зачисление учеников.</p>
                {item.problem ? <p>{item.problem}</p> : <p>Преподавателя, время и существующий состав менять не нужно.</p>}</>}
          </div>
          {lessonButton(item.lesson_id, lessonLabels[item.lesson_id] || 'занятие')}
        </li>)}</ul>
      </ReviewDisclosure> : null}
      {preview.manual.length ? <ReviewDisclosure title="Нужно решить вручную" count={preview.manual.length} groupKey="legacy-manual">
        <ul className="constructor-review-list">{preview.manual.map(item => <li key={item.lesson_id}>
          <div>
            <strong>{lessonLabels[item.lesson_id] || 'Занятие'}</strong>
            <p><b>{legacyReviewReason(item.reason).title}.</b> {legacyReviewReason(item.reason).action}</p>
            {legacyReviewReason(item.reason).title !== item.reason ? <ReasonDetails reason={item.reason} /> : null}
          </div>
          {lessonButton(item.lesson_id, lessonLabels[item.lesson_id] || 'занятие')}
        </li>)}</ul>
      </ReviewDisclosure> : null}
      {preview.retained_warning_count ? <ReviewDisclosure title="Замечания, которые сохранятся в копии" count={preview.retained_warning_count} groupKey="legacy-retained">
        <p>Это число записей со старой пометкой, для которой нет автоматического исправления. У каждого примера указан результат проверки публикации.</p>
        {preview.retained_warnings.map(warning => <div className="constructor-review-warning" key={warning.reason}>
          <strong>{legacyReviewReason(warning.reason).title}</strong>
          <p>{legacyReviewReason(warning.reason).action}</p>
          <p>Записей — {warning.count}{warning.count > warning.examples.length ? `. Показаны первые ${warning.examples.length}` : ''}.</p>
          <ul className="constructor-review-list">{warning.examples.map(example => <li key={example.lesson_id}>
            <span>{lessonLabels[example.lesson_id] || example.subject}</span><p>{lessonImpact(example.lesson_id)}</p>{lessonButton(example.lesson_id, example.subject)}
          </li>)}</ul>
          {legacyReviewReason(warning.reason).title !== warning.reason ? <ReasonDetails reason={warning.reason} /> : null}
        </div>)}
      </ReviewDisclosure> : null}
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
