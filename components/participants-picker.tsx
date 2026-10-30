'use client';

import { useId, useMemo, useState } from 'react';
import styles from './participants-picker.module.css';
import { audienceGrade, audienceLabel, classLabel, subjectLabel } from './school-audience';

export type AudienceRule = {
  kind: 'groups' | 'classes' | 'available_slot' | 'students';
  group_ids?: string[];
  class_ids?: string[];
  grade?: string;
  student_ids?: string[];
};

export type AudiencePreview = {
  status: string;
  count?: number | null;
  source_count?: number | null;
  potential_count?: number | null;
  class_ids?: string[];
  student_ids?: string[];
  reason?: string | null;
  excluded_count?: number | null;
  blockers?: { lesson_id: string; subject?: string; reason: string }[];
  needs_recalculation?: boolean;
  proposed_student_ids?: string[] | null;
  audience_snapshot?: { student_ids: string[]; resolved_at: string };
};

export type AudiencePickerGroup = {
  id: string;
  name: string;
  group_type: string;
  subject?: string;
  base_class_name?: string;
  role?: string;
  semantic_dimension?: string;
  archived_at?: string | null;
};

export type AudiencePickerStudent = {
  id: string;
  name: string;
  classes: string[];
};

type Props = {
  groups: AudiencePickerGroup[];
  students: AudiencePickerStudent[];
  audience: AudienceRule;
  subject?: string;
  contextClassIds?: string[];
  gradeScope?: string;
  preview?: AudiencePreview | null;
  legacyNotice?: string;
  onRetryPreview?: () => void;
  onOpenBlockingLesson?: (id: string) => void;
  onConfirmRecalculation?: () => void;
  onChange: (next: AudienceRule) => void;
};

const modes: { kind: AudienceRule['kind']; title: string; hint: string }[] = [
  { kind: 'groups', title: 'Учебные группы', hint: 'Существующие группы' },
  { kind: 'classes', title: 'Весь класс или параллель', hint: 'Административный состав' },
  { kind: 'available_slot', title: 'Все остальные', hint: 'Свободные среди выбранных классов' },
  { kind: 'students', title: 'Отдельные ученики', hint: 'Именной состав' },
];

function normalized(value: string): string {
  return value.trim().toLocaleLowerCase('ru');
}

const classAudienceName = classLabel;

function unique(ids: string[] | undefined): string[] {
  return [...new Set(ids || [])];
}

function toggle(ids: string[] | undefined, id: string): string[] {
  const current = unique(ids);
  return current.includes(id) ? current.filter((item) => item !== id) : [...current, id];
}

export function ParticipantsPicker({
  groups,
  students,
  audience,
  subject = '',
  contextClassIds = [],
  gradeScope = '',
  preview,
  legacyNotice,
  onRetryPreview,
  onOpenBlockingLesson,
  onConfirmRecalculation,
  onChange,
}: Props) {
  const fieldId = useId();
  const [query, setQuery] = useState('');
  const classes = useMemo(() => groups.filter((item) => item.group_type === 'class' && !item.archived_at), [groups]);
  const classNames = useMemo(() => new Map(classes.map((item) => [item.id, item.name])), [classes]);
  const groupNames = useMemo(() => new Map(groups.map((item) => [item.id, item.name])), [groups]);
  const studentNames = useMemo(() => new Map(students.map((item) => [item.id, item.name])), [students]);
  const grades = useMemo(() => [...new Set(classes.map((item) => audienceGrade(item)).filter(Boolean))].sort((a, b) => Number(a) - Number(b)), [classes]);
  const contextGrades = useMemo(() => new Set(contextClassIds.map((id) => { const group = classes.find((item) => item.id === id); return group ? audienceGrade(group) : ''; }).filter(Boolean)), [contextClassIds, classes]);

  const rankedGroups = useMemo(() => {
    const needle = normalized(query);
    const wantedSubject = normalized(subject);
    return groups
      .filter((item) => item.group_type !== 'class' && !item.archived_at)
      .filter((item) => !needle || normalized(`${item.name} ${item.subject || ''} ${item.base_class_name || ''}`).includes(needle))
      .map((item) => {
        const base = normalized(item.base_class_name || '');
        const context = contextClassIds.some((id) => {
          const className = normalized(classNames.get(id) || '');
          return className && base && (base === className || base.startsWith(className) || className.startsWith(base));
        });
        const grade = audienceGrade(item);
        const subjectMatch = Boolean(wantedSubject && normalized(subjectLabel(item.subject)) === normalized(subjectLabel(subject)));
        return { item, score: Number(context) * 4 + Number(contextGrades.has(grade)) * 2 + Number(subjectMatch) };
      })
      .sort((a, b) => b.score - a.score || a.item.name.localeCompare(b.item.name, 'ru'))
      .map(({ item }) => item);
  }, [groups, query, subject, contextClassIds, contextGrades, classNames]);

  const classOptions = useMemo(() => {
    const gradesWithMultipleClasses = grades.filter((grade) => classes.filter((item) => audienceGrade(item) === grade).length > 1);
    return [
      ...gradesWithMultipleClasses.map((grade) => ({ id: `grade:${grade}`, label: `Весь ${grade} класс`, grade })),
      ...classes.map((item) => ({ id: item.id, label: classAudienceName(item.name), grade: '' })),
    ].sort((a, b) => Number(contextClassIds.includes(b.id)) - Number(contextClassIds.includes(a.id)) || a.label.localeCompare(b.label, 'ru'));
  }, [classes, grades, contextClassIds]);

  const visibleStudents = useMemo(() => {
    const needle = normalized(query);
    return students.filter((item) => !needle || normalized(`${item.name} ${item.classes.map((id) => classNames.get(id) || '').join(' ')}`).includes(needle))
      .sort((a, b) => Number(b.classes.some((id) => contextClassIds.includes(id))) - Number(a.classes.some((id) => contextClassIds.includes(id))) || a.name.localeCompare(b.name, 'ru'));
  }, [students, query, classNames, contextClassIds]);

  const selectedIds = audience.kind === 'groups' ? unique(audience.group_ids) : audience.kind === 'students' ? unique(audience.student_ids) : unique(audience.class_ids);
  const selectedNames = audience.kind === 'groups' ? groupNames : audience.kind === 'students' ? studentNames : classNames;
  const selectedClassIds = (audience.kind === 'classes' || audience.kind === 'available_slot') && !audience.class_ids?.length && audience.grade
    ? classes.filter((item) => audienceGrade(item) === audience.grade).map((item) => item.id)
    : selectedIds;
  const previewClasses = unique(preview?.class_ids).map((id) => classNames.get(id) || id);
  const statusLabel = preview?.status === 'ok' ? 'Состав определён' : preview?.status === 'ambiguous' ? 'Требует уточнения' : preview?.status === 'incomplete' ? 'Недостаточно данных' : preview?.status === 'error' ? 'Ошибка расчёта' : preview?.status || 'Состав рассчитывается';
  const contextGrade = gradeScope || (contextGrades.size === 1 ? [...contextGrades][0] : '');
  const audienceCaption = audience.kind === 'available_slot'
    ? `Все остальные · ${audience.class_ids?.length ? audience.class_ids.map((id) => classNames.get(id) || id).join(', ') : audience.grade || contextGrade ? `${audience.grade || contextGrade} класс` : 'исходная аудитория не определена'}`
    : audience.kind === 'classes'
      ? audience.grade ? `Вся параллель ${audience.grade}` : selectedIds.length ? selectedIds.map((id) => classNames.get(id) || id).join(', ') : 'Классы не выбраны'
      : audience.kind === 'groups'
        ? selectedIds.length ? selectedIds.map((id) => groupNames.get(id) || id).join(', ') : 'Группы не выбраны'
        : selectedIds.length ? `${selectedIds.length} учеников` : 'Ученики не выбраны';

  const chooseMode = (kind: AudienceRule['kind']) => {
    setQuery('');
    if (kind === audience.kind) return;
    if (kind === 'available_slot') {
      onChange({ kind, ...(contextClassIds.length ? { class_ids: unique(contextClassIds) } : { grade: contextGrade, class_ids: [] }) });
      return;
    }
    if (kind === 'classes') {
      const exactClass = unique(contextClassIds);
      onChange({ kind, class_ids: exactClass });
      return;
    }
    onChange({ kind });
  };

  return (
    <section className={styles.root} aria-label="Участники занятия">
      <div className={styles.heading}><strong>Участники</strong><span>По справочнику школы</span></div>
      {audience.kind === 'groups' || audience.kind === 'students' ? <p className={styles.audienceCaption} aria-live="polite">{audienceCaption}</p> : null}
      {legacyNotice ? <p className={styles.legacyNotice}>{legacyNotice}</p> : null}
      <fieldset className={styles.modes}><legend className={styles.visuallyHidden}>Способ выбора участников</legend>
        {modes.map((mode) => <button
          key={mode.kind}
          type="button"
          className={`${styles.mode} ${audience.kind === mode.kind ? styles.active : ''}`}
          aria-pressed={audience.kind === mode.kind}
          onClick={() => chooseMode(mode.kind)}
        ><b>{mode.title}</b><small>{mode.hint}</small></button>)}
      </fieldset>

      {audience.kind === 'available_slot' ? <div className={styles.source} aria-live="polite">
        <strong>{audienceCaption}</strong>
        {!audience.grade && !contextGrade ? <p>Не удалось определить параллель по колонке расписания.</p> : null}
      </div> : null}

      {audience.kind === 'classes' || audience.kind === 'available_slot' ? (
        <div className={styles.source}>
          <strong>Кто занимается</strong>
          <fieldset className={styles.options} aria-label="Классы участников">
            {classOptions.map((item) => {
              const gradeClasses = item.grade ? classes.filter((entry) => audienceGrade(entry) === item.grade).map((entry) => entry.id) : [];
              const checked = item.grade
                ? gradeClasses.length > 0 && gradeClasses.every((id) => selectedClassIds.includes(id))
                : selectedClassIds.includes(item.id);
              return <label className={styles.option} key={item.id} aria-label={item.label}>
                <input type="checkbox" checked={checked} onChange={() => {
                  if (item.grade) {
                    const keep = selectedClassIds.filter((id) => !gradeClasses.includes(id));
                    onChange({ kind: audience.kind, class_ids: checked ? keep : unique([...keep, ...gradeClasses]) });
                    return;
                  }
                  onChange({ kind: audience.kind, class_ids: toggle(selectedClassIds, item.id) });
                }} />
                <span><b>{item.label}</b></span>
              </label>;
            })}
          </fieldset>
          <p aria-live="polite">Аудитория: {audienceCaption}</p>
        </div>
      ) : null}

      {audience.kind === 'groups' || audience.kind === 'students' ? (
        <div className={styles.search}>
          <label htmlFor={`${fieldId}-search`}>{audience.kind === 'groups' ? 'Найти учебную группу' : audience.kind === 'students' ? 'Найти ученика' : 'Найти класс'}</label>
          <input id={`${fieldId}-search`} type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={audience.kind === 'students' ? 'Имя или класс' : 'Название или предмет'} />
        </div>
      ) : null}

      {(audience.kind === 'groups' || audience.kind === 'students') && selectedIds.length ? <div className={styles.selected} aria-label="Выбранные участники">
        {audience.grade ? <span className={styles.chip}>{audience.grade}-е классы <button type="button" aria-label="Убрать параллель" onClick={() => onChange({ kind: audience.kind, class_ids: [] })}>×</button></span> : null}
        {selectedIds.map((id) => <span key={id} className={styles.chip}>{selectedNames.get(id) || `ID ${id}`} <button type="button" aria-label={`Убрать ${selectedNames.get(id) || id}`} onClick={() => onChange({ ...audience, [audience.kind === 'groups' ? 'group_ids' : audience.kind === 'students' ? 'student_ids' : 'class_ids']: toggle(selectedIds, id) })}>×</button></span>)}
      </div> : null}

      {audience.kind === 'groups' || audience.kind === 'students' ? <fieldset className={styles.options}><legend className={styles.visuallyHidden}>Результаты поиска</legend>
        {(audience.kind === 'groups' ? rankedGroups : visibleStudents).map((item) => {
          const checked = selectedIds.includes(item.id);
          const detail = audience.kind === 'groups'
            ? groups.find((group) => group.id === item.id)?.base_class_name || ''
            : audience.kind === 'students'
              ? students.find((student) => student.id === item.id)?.classes.map((id) => classNames.get(id) || id).join(', ')
              : '';
          // Schedule-constructor groups arrive with a display label already
          // formatted by audienceLabel. Reformatting that label as a raw name
          // loses suffixes such as "группа 1" because the technical group ID
          // has already been replaced. Keep each existing group ID/name pair.
          const label = audience.kind === 'groups'
            ? /^(?:english:\d{1,2}|[0-9a-f]{8}-[0-9a-f-]{27,})$/iu.test(item.name) ? audienceLabel(item) : item.name
            : audience.kind === 'students' ? item.name : audienceLabel(item);
          return <label key={item.id} className={styles.option}>
            <input type="checkbox" value={item.id} checked={checked} onChange={() => onChange({ ...audience, grade: undefined, [audience.kind === 'groups' ? 'group_ids' : 'student_ids']: toggle(selectedIds, item.id) })} />
            <span><b>{label}</b>{detail ? <small>{detail}</small> : null}</span>
          </label>;
        })}
        {(audience.kind === 'groups' ? rankedGroups : visibleStudents).length === 0 ? <p className={styles.empty}>Ничего не найдено</p> : null}
      </fieldset> : null}

      <div className={`${styles.preview} ${preview?.status && preview.status !== 'ok' ? styles.previewAttention : ''}`} aria-live="polite">
        {preview?.needs_recalculation ? <div className={styles.legacyNotice}><strong>Изменился расчёт участников</strong><p>Сохранённый состав остаётся прежним. Сейчас по правилу получилось бы {preview.proposed_student_ids?.length ?? 'неизвестное число'} учеников.</p>{preview.proposed_student_ids ? <details><summary>Посмотреть новый состав</summary><div className={styles.previewStudents}>{preview.proposed_student_ids.map((id) => <span key={id}>{studentNames.get(id) || 'Ученик недоступен'}</span>)}</div></details> : null}{onConfirmRecalculation && preview.proposed_student_ids ? <button type="button" onClick={onConfirmRecalculation}>Пересчитать и подтвердить состав</button> : null}</div> : null}
        <div><strong>{statusLabel}</strong>{preview?.status === 'ok' && preview.count != null ? <b>{preview.count} учеников</b> : audience.kind === 'available_slot' && preview ? <b>Состав не подтверждён</b> : null}</div>
        {preview?.reason ? <p>{preview.reason}</p> : null}
        {preview?.status === 'error' && onRetryPreview ? <button type="button" onClick={onRetryPreview}>Повторить расчёт</button> : null}
        {audience.kind === 'available_slot' && preview?.source_count != null ? <p>Исходная аудитория: {preview.source_count} учеников</p> : null}
        {audience.kind === 'available_slot' && preview?.potential_count != null ? <p>Потенциально свободны: {preview.potential_count}</p> : null}
        {previewClasses.length ? <p>Затронутые классы: {previewClasses.join(', ')}</p> : null}
        {preview?.excluded_count != null && audience.kind === 'available_slot' ? <p>Достоверно заняты на пересекающихся занятиях: {preview.excluded_count}</p> : null}
        {preview?.status !== 'ok' && preview?.blockers?.length && onOpenBlockingLesson ? <ul className={styles.blockers}>{preview.blockers.map((blocker) => <li key={blocker.lesson_id}><button type="button" onClick={() => onOpenBlockingLesson(blocker.lesson_id)}>{blocker.subject || 'Занятие'} · {blocker.reason}</button></li>)}</ul> : null}
        {preview?.status === 'ok' && preview.student_ids?.length ? <details><summary>Показать состав ({preview.student_ids.length})</summary><div className={styles.previewStudents}>{preview.student_ids.map((id) => <span key={id}>{studentNames.get(id) || `ID ${id}`}</span>)}</div></details> : null}
      </div>
    </section>
  );
}
