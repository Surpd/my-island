'use client';

import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, ChevronDown, Clock3, RefreshCw, ShieldCheck, Trash2, Users } from 'lucide-react';

type Item = Record<string, any>;
type AdminApi = <T>(path: string, init?: RequestInit) => Promise<T>;

const STATUS_LABELS: Record<string, string> = {
  RESOLVED: 'Решено', WARNING: 'Нужно проверить', UNRESOLVED: 'Не определено', CONFLICT: 'Конфликт',
};

const DIFF_LABELS: Record<string, string> = { added: 'Добавлено', changed: 'Изменено', removed: 'Отменено' };

function humanReason(group: Item) {
  if (group.mapping_type === 'identity') return `В расписании обозначение «${group.external_key}» не связано с одним преподавателем.`;
  if (group.mapping_type === 'group') return `Обозначение «${group.external_key}» не связано с одним каноническим классом или группой.`;
  return group.description || 'Нужно подтвердить интерпретацию данных источника.';
}

function formatDate(value: unknown) {
  if (!value) return '—';
  const parsed = new Date(String(value));
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString('ru-RU');
}

function mappingScope(mapping: Item) {
  if (mapping.mapping_type !== 'audience_rule') return 'Используется в следующих sync и неделях';
  return String(mapping.external_key || '').startsWith('case:') ? 'Только сохранённый конкретный slot' : 'Повторяющиеся случаи (сохранено явно администратором)';
}

export function ScheduleAdmin({ api }: { api: AdminApi }) {
  const [overview, setOverview] = useState<Item | null>(null);
  const [lessons, setLessons] = useState<Item[]>([]);
  const [reconciliation, setReconciliation] = useState<Item>({ issue_groups: [], mappings: [], teachers: [], groups: [] });
  const [week, setWeek] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [decisions, setDecisions] = useState<Record<string, string>>({});
  const [allocation, setAllocation] = useState<Item>({ summary: {}, slots: [], students: [], teachers: [], student_preview: [], teacher_preview: [] });
  const [grade, setGrade] = useState('9');
  const [studentPreview, setStudentPreview] = useState('');
  const [teacherPreview, setTeacherPreview] = useState('');
  const [audienceDecisions, setAudienceDecisions] = useState<Record<string, { decision_type: string; group_ids: string[]; persistent: boolean }>>({});

  const loadAllocation = async (selectedWeek: string, selectedGrade = grade, student = studentPreview, teacher = teacherPreview) => {
    if (!selectedWeek) return;
    const query = new URLSearchParams({ week_start: selectedWeek, grade: selectedGrade });
    if (student) query.set('student_id', student);
    if (teacher) query.set('teacher_id', teacher);
    setAllocation(await api<Item>(`/api/admin/schedule/allocation-qa?${query}`));
  };

  const loadWeek = async (selectedWeek: string, selectedStatus = status) => {
    setError('');
    const query = new URLSearchParams();
    if (selectedWeek) query.set('week_start', selectedWeek);
    const lessonQuery = new URLSearchParams(query);
    if (selectedStatus) lessonQuery.set('status', selectedStatus);
    const [nextOverview, nextLessons, nextReconciliation] = await Promise.all([
      api<Item>(`/api/admin/schedule/overview?${query}`),
      api<{ items: Item[] }>(`/api/admin/schedule/lessons?${lessonQuery}`),
      api<Item>(`/api/admin/schedule/reconciliation?${query}`),
    ]);
    setOverview(nextOverview);
    setLessons(nextLessons.items || []);
    setReconciliation(nextReconciliation);
  };

  const bootstrap = async () => {
    setBusy('load'); setError('');
    try {
      const initial = await api<Item>('/api/admin/schedule/overview');
      const selectedWeek = String(initial.selected_week || initial.weeks?.[0]?.week_start || '');
      setWeek(selectedWeek);
      await Promise.all([loadWeek(selectedWeek, ''), loadAllocation(selectedWeek, '9', '', '')]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Не удалось загрузить расписание');
    } finally { setBusy(''); }
  };

  useEffect(() => { void bootstrap(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const selectStatus = async (nextStatus: string) => {
    const selected = status === nextStatus ? '' : nextStatus;
    setStatus(selected); setBusy('filter');
    try { await loadWeek(week, selected); } catch (caught) { setError(caught instanceof Error ? caught.message : 'Не удалось применить фильтр'); }
    finally { setBusy(''); }
  };

  const selectWeek = async (nextWeek: string) => {
    setWeek(nextWeek); setBusy('filter');
    try { await Promise.all([loadWeek(nextWeek, status), loadAllocation(nextWeek)]); } catch (caught) { setError(caught instanceof Error ? caught.message : 'Не удалось загрузить неделю'); }
    finally { setBusy(''); }
  };

  const selectGrade = async (nextGrade: string) => {
    setGrade(nextGrade); setBusy('allocation');
    try { await loadAllocation(week, nextGrade); } catch (caught) { setError(caught instanceof Error ? caught.message : 'Не удалось загрузить распределение'); }
    finally { setBusy(''); }
  };

  const selectPreview = async (kind: 'student' | 'teacher', value: string) => {
    if (kind === 'student') setStudentPreview(value); else setTeacherPreview(value);
    setBusy('allocation');
    try { await loadAllocation(week, grade, kind === 'student' ? value : studentPreview, kind === 'teacher' ? value : teacherPreview); }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Не удалось построить preview'); }
    finally { setBusy(''); }
  };

  const refresh = async () => {
    setBusy('refresh'); setError(''); setMessage('');
    try {
      const result = await api<Item>('/api/admin/schedule/refresh', { method: 'POST' });
      if (result.status === 'blocked' || result.ok === false) setMessage(String(result.message || 'Синхронизация заблокирована настройками источника'));
      else { setMessage('Источник обновлён, baseline и изменения недели пересчитаны.'); await bootstrap(); }
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Не удалось обновить источник'); }
    finally { setBusy(''); }
  };

  const recalculate = async () => {
    setBusy('recalculate'); setError(''); setMessage('');
    try {
      const result = await api<Item>('/api/admin/schedule/recalculate', { method: 'POST' });
      if (result.status === 'blocked') setMessage(String(result.message || 'Нет сохранённого snapshot для пересчёта'));
      else { setMessage('Производные данные пересчитаны по последнему snapshot. Новый snapshot Google не создан.'); await bootstrap(); }
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Не удалось пересчитать расписание'); }
    finally { setBusy(''); }
  };

  const saveDecision = async (group: Item) => {
    const key = `${group.mapping_type}:${group.external_key}`;
    const target = decisions[key] || '';
    if (!target) { setError('Сначала выберите преподавателя или группу.'); return; }
    setBusy(key); setError(''); setMessage('');
    try {
      await api('/api/admin/schedule/mappings', { method: 'POST', body: JSON.stringify({ mapping_type: group.mapping_type, external_key: group.external_key, target_id: target }) });
      setMessage(`Решение для «${group.external_key}» сохранено и применено ко всем совпадениям.`);
      setDecisions((current) => { const next = { ...current }; delete next[key]; return next; });
      await loadWeek(week, status);
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Не удалось сохранить решение'); }
    finally { setBusy(''); }
  };

  const retireMapping = async (mapping: Item) => {
    setBusy(`retire:${mapping.id}`); setError('');
    try {
      await api(`/api/admin/schedule/mappings/${mapping.id}`, { method: 'DELETE' });
      setMessage(`Правило для «${mapping.external_key}» отключено.`);
      await loadWeek(week, status);
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Не удалось отключить правило'); }
    finally { setBusy(''); }
  };

  const audienceCaseKey = (activity: Item) => `${activity.lesson_id || 'slot'}:${activity.lesson_date || week}:${activity.grade_scope || grade}`;
  const audienceDecisionFor = (activity: Item) => audienceDecisions[audienceCaseKey(activity)] || { decision_type: 'canonical_group', group_ids: [], persistent: false };
  const decisionLabel: Record<string, string> = {
    canonical_group: 'Конкретная группа', groups: 'Несколько групп', base_class: 'Весь базовый класс',
    parallel: 'Вся параллель', complement: 'Все остальные после уже назначенных', window: 'Окно / урока нет', source_context: 'Только контекст источника',
  };
  const saveAudienceDecision = async (activity: Item) => {
    const key = audienceCaseKey(activity);
    const decision = audienceDecisionFor(activity);
    const needsGroups = ['canonical_group', 'groups', 'base_class'].includes(decision.decision_type);
    if (needsGroups && !decision.group_ids.length) { setError('Выберите группу или группы для этого решения.'); return; }
    setBusy(`audience:${key}`); setError('');
    try {
      await api('/api/admin/schedule/mappings', { method: 'POST', body: JSON.stringify({ mapping_type: 'audience_rule', external_key: String(activity.provenance?.audience_key || ''), decision_type: decision.decision_type, group_ids: decision.group_ids, lesson_id: activity.lesson_id, week_start: activity.week_start || week, grade_scope: activity.grade_scope || grade, persistent: decision.persistent }) });
      setMessage(decision.persistent ? 'Решение сохранено как отдельное правило для повторяющихся случаев.' : 'Решение сохранено только для этого конкретного slot.');
      setAudienceDecisions((current) => { const next = { ...current }; delete next[key]; return next; });
      await loadAllocation(week, grade);
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Не удалось сохранить правило аудитории'); }
    finally { setBusy(''); }
  };

  const summary = overview?.summary || {};
  const activeMappings = useMemo(() => (reconciliation.mappings || []).filter((item: Item) => !item.valid_until), [reconciliation.mappings]);
  const issueGroups = useMemo(() => (reconciliation.issue_groups || []).filter((item: Item) => !status || Number(item.statuses?.[status] || 0) > 0), [reconciliation.issue_groups, status]);
  const classDays = useMemo(() => {
    const grouped = new Map<string, Item[]>();
    for (const slot of allocation.slots || []) {
      const day = String(slot.lesson_date || '');
      grouped.set(day, [...(grouped.get(day) || []), slot]);
    }
    return [...grouped.entries()].sort(([left], [right]) => left.localeCompare(right));
  }, [allocation.slots]);
  const choicesFor = (group: Item) => group.mapping_type === 'identity' ? reconciliation.teachers || [] : reconciliation.groups || [];

  if (!overview && busy === 'load') return <div className="admin-loading"><RefreshCw className="admin-spin" size={18} />Загружаем расписание…</div>;
  if (!overview) return <div className="admin-error"><AlertTriangle size={17} /><div><strong>Не удалось загрузить расписание</strong><p>{error}</p></div><button className="admin-button admin-button--quiet" onClick={() => void bootstrap()}>Повторить</button></div>;

  return <div className="admin-stack schedule-reconciliation">
    <div className="admin-page-actions">
      <div><strong>Сверка расписания</strong><p>Шаблон — baseline. Каждая недельная вкладка показывает только изменения для своих дат.</p></div>
      <div className="admin-button-row"><button className="admin-button admin-button--quiet" disabled={!!busy} onClick={() => void refresh()}><RefreshCw size={15} className={busy === 'refresh' ? 'admin-spin' : ''} />{busy === 'refresh' ? 'Проверяем Google…' : 'Обновить из источника'}</button><button className="admin-button admin-button--quiet" disabled={!!busy} onClick={() => void recalculate()}><RefreshCw size={15} className={busy === 'recalculate' ? 'admin-spin' : ''} />{busy === 'recalculate' ? 'Пересчитываем…' : 'Пересчитать правила'}</button><button className="admin-button admin-button--quiet" disabled={!!busy} onClick={() => void loadWeek(week)}>Перечитать</button></div>
    </div>
    {error ? <div className="admin-error"><AlertTriangle size={17} /><div><strong>Не удалось выполнить действие</strong><p>{error}</p></div></div> : null}
    {message ? <div className="admin-callout admin-callout--safe"><CheckCircle2 size={16} /><span>{message}</span></div> : null}
    <section className="admin-panel schedule-source-card">
      <div className="admin-panel__heading"><div><p className="admin-eyebrow">ИСТОЧНИК / BASELINE</p><h2>{overview.source?.name || 'Расписание'}</h2></div><span className="admin-status admin-status--good">Подключено</span></div>
      <div className="schedule-source-meta"><span>Шаблон: <strong>{overview.source?.template || '—'}</strong></span><span>Неделя: <strong>{week || '—'}</strong></span><span>Последняя синхронизация: <strong>{formatDate(overview.last_sync)}</strong></span></div>
    </section>
    <section className="schedule-counter-grid" aria-label="Фильтр по состоянию сверки">
      {(['RESOLVED','WARNING','UNRESOLVED','CONFLICT'] as const).map((key) => <button key={key} className={`schedule-counter schedule-counter--${key.toLowerCase()} ${status === key ? 'is-active' : ''}`} aria-pressed={status === key} disabled={busy === 'filter'} onClick={() => void selectStatus(key)}><strong>{Number(summary[key.toLowerCase()] || 0)}</strong><span>{STATUS_LABELS[key]}</span></button>)}
      {(['added','changed','removed'] as const).map((key) => <div key={key} className="schedule-counter schedule-counter--diff"><strong>{Number(summary[key] || 0)}</strong><span>{DIFF_LABELS[key]}</span></div>)}
    </section>
    <div className="schedule-filter-row"><label>Неделя<select value={week} onChange={(event) => void selectWeek(event.target.value)}>{(overview.weeks || []).map((item: Item) => <option key={item.week_start} value={item.week_start}>{item.week_start} · {item.lessons} уроков</option>)}</select></label>{status ? <button className="admin-button admin-button--quiet" onClick={() => void selectStatus(status)}>Сбросить фильтр «{STATUS_LABELS[status]}»</button> : null}</div>
    <section className="admin-panel">
      <div className="admin-panel__heading"><div><p className="admin-eyebrow">ТРЕБУЮТ РЕШЕНИЯ</p><h2>Повторяющиеся проблемы</h2></div><span>{issueGroups.length} групп</span></div>
      {issueGroups.length ? <div className="schedule-issue-list">{issueGroups.map((group: Item) => {
        const key = `${group.mapping_type}:${group.external_key}`;
        return <article className="schedule-issue-card" key={key}>
          <div className="schedule-issue-card__copy"><div className="schedule-issue-card__title"><span className="schedule-issue-count">{group.count}</span><div><h3>{group.title}</h3><p>{humanReason(group)}</p></div></div><div className="schedule-example-row">{(group.examples || []).slice(0, 3).map((example: Item, index: number) => <span key={`${example.cell}-${index}`}>{example.date || 'Шаблон'} · {example.time || ''} · {example.subject || 'Без предмета'} · {example.audience || 'Без группы'}</span>)}</div><details><summary>Технические детали <ChevronDown size={14} /></summary><pre>{JSON.stringify(group.technical, null, 2)}</pre></details></div>
          <div className="schedule-decision"><label>{group.mapping_type === 'identity' ? 'Преподаватель' : 'Класс / группа'}<select value={decisions[key] || ''} onChange={(event) => setDecisions((current) => ({ ...current, [key]: event.target.value }))}><option value="">Выберите…</option>{choicesFor(group).map((choice: Item) => <option key={choice.id} value={choice.id}>{choice.display_name || choice.name}</option>)}</select></label><button className="admin-button admin-button--primary" disabled={busy === key || !decisions[key]} onClick={() => void saveDecision(group)}>{busy === key ? 'Применяем…' : `Применить ко всем (${group.count})`}</button></div>
        </article>;
      })}</div> : <div className="schedule-clean-state"><CheckCircle2 size={22} /><div><strong>По выбранному фильтру решений не требуется</strong><p>Детерминированные обозначения уже разрешены автоматически или сохранёнными правилами.</p></div></div>}
    </section>
    <section className="admin-panel">
      <div className="admin-panel__heading"><div><p className="admin-eyebrow">ПОСТОЯННЫЕ ПРАВИЛА</p><h2>Mappings и aliases</h2></div><span>{activeMappings.length}</span></div>
      {activeMappings.length ? <div className="schedule-mapping-list">{activeMappings.map((mapping: Item) => <div className="schedule-mapping-row" key={mapping.id}><div><strong>{mapping.mapping_type === 'audience_rule' ? 'Решение аудитории' : mapping.external_key}</strong><span>{mapping.mapping_type === 'identity' ? 'Преподаватель' : mapping.mapping_type === 'group' ? 'Группа' : mapping.mapping_type === 'audience_rule' ? 'Правило аудитории' : 'Предмет'} → {mapping.identity_name || mapping.group_name || mapping.canonical_value}</span><small>{mappingScope(mapping)}</small>{mapping.mapping_type === 'audience_rule' ? <details><summary>Техническая область</summary><code>{mapping.external_key}</code></details> : null}</div><button className="admin-button admin-button--quiet" disabled={busy === `retire:${mapping.id}`} onClick={() => void retireMapping(mapping)}><Trash2 size={14} />Отключить</button></div>)}</div> : <p className="admin-empty">Сохранённых правил пока нет.</p>}
    </section>
    <section className="admin-panel schedule-allocation-qa">
      <div className="admin-panel__heading"><div><p className="admin-eyebrow">ПРОВЕРКА РАСПИСАНИЯ КЛАССА</p><h2>{grade} класс · неделя {week}</h2><p>Откройте проблемный или интересующий слот, чтобы увидеть группы, учеников и источник решения.</p></div><Users size={20} /></div>
      <div className="schedule-allocation-summary">
        <div><strong>{Number(allocation.summary?.complete || 0)}</strong><span>Распределены полностью</span></div>
        <div><strong>{Number(allocation.summary?.with_unassigned || 0)}</strong><span>С Unassigned</span></div>
        <div><strong>{Number(allocation.summary?.with_conflicts || 0)}</strong><span>С Conflicts</span></div>
      </div>
      <div className="schedule-filter-row schedule-allocation-filters">
        <label>Класс<select value={grade} onChange={(event) => void selectGrade(event.target.value)}>{['5','6','7','8','9','10','11'].map((value) => <option key={value} value={value}>{value} класс</option>)}</select></label>
        <label>Preview ученика<select value={studentPreview} onChange={(event) => void selectPreview('student', event.target.value)}><option value="">Выберите ученика…</option>{(allocation.students || []).map((item: Item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
        <label>Preview преподавателя<select value={teacherPreview} onChange={(event) => void selectPreview('teacher', event.target.value)}><option value="">Выберите преподавателя…</option>{(allocation.teachers || []).map((item: Item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
      </div>
      {studentPreview ? <div className="schedule-preview"><h3>Неделя ученика</h3>{(allocation.student_preview || []).map((item: Item, index: number) => <div key={`${item.lesson_date}-${item.start_time}-${index}`}><span>{item.lesson_date} · {item.start_time}</span><strong>{item.subject || 'Окно / ожидание'}</strong><small>{item.reason}</small></div>)}</div> : null}
      {teacherPreview ? <div className="schedule-preview"><h3>Неделя преподавателя</h3>{(allocation.teacher_preview || []).map((item: Item) => <div key={item.id}><span>{item.lesson_date} · {item.start_time}</span><strong>{item.subject}</strong><small>{item.audience} · {item.room || 'кабинет не указан'}</small></div>)}</div> : null}
      <div className="schedule-class-week">{classDays.map(([day, slots]) => <section className="schedule-class-day" key={day}><header><strong>{new Intl.DateTimeFormat('ru-RU', { weekday: 'long' }).format(new Date(`${day}T12:00:00`))}</strong><span>{day.slice(8)}.{day.slice(5, 7)}</span></header><div className="schedule-slot-list">{slots.map((slot: Item) => <details className={`schedule-slot schedule-slot--${slot.status}`} key={`${slot.lesson_date}-${slot.start_time}-${slot.grade}`}>
        <summary><Clock3 size={15} /><strong>{String(slot.start_time).slice(0, 5)}</strong><span>{(slot.activities || []).filter((item: Item) => item.audience_kind !== 'duplicate').map((item: Item) => `${item.subject}${item.group_names?.length ? ` · ${item.group_names.join(', ')}` : ''}`).join(' / ') || 'Нет урока'}</span>{slot.unassigned?.length ? <b>{slot.unassigned.length} без назначения</b> : null}{slot.conflicts?.length ? <b>{slot.conflicts.length} конфликтов</b> : null}<ChevronDown size={15} /></summary>
        <div className="schedule-slot-grid">{(slot.activities || []).map((activity: Item, index: number) => {
          const caseKey = audienceCaseKey(activity);
          const decision = audienceDecisionFor(activity);
          const selectedGroups = (allocation.groups || []).filter((groupItem: Item) => decision.group_ids.includes(String(groupItem.id)));
          const needsGroups = ['canonical_group', 'groups', 'base_class'].includes(decision.decision_type);
          return <article key={`${activity.lesson_id || activity.synthetic_kind}-${index}`}><div><strong>{activity.subject}</strong><span>{activity.teacher_hint || activity.activity_type}</span></div><b>{activity.student_count || 0}</b><small>{activity.rule_reason}</small><details><summary>Ученики и причины</summary><ul>{(activity.students || []).map((student: Item) => <li key={student.id}><span>{student.name}</span><small>{student.reason}</small></li>)}</ul></details>{activity.status === 'ambiguous' ? <div className="schedule-audience-decision"><label>Как понимать аудиторию<select value={decision.decision_type} onChange={(event) => setAudienceDecisions((current) => ({ ...current, [caseKey]: { ...decision, decision_type: event.target.value, group_ids: ['canonical_group','groups','base_class'].includes(event.target.value) ? decision.group_ids : [] } }))}><option value="canonical_group">{decisionLabel.canonical_group}</option><option value="groups">{decisionLabel.groups}</option><option value="base_class">{decisionLabel.base_class}</option><option value="parallel">{decisionLabel.parallel}</option><option value="complement">{decisionLabel.complement}</option><option value="window">{decisionLabel.window}</option><option value="source_context">{decisionLabel.source_context}</option></select></label>{needsGroups ? <label>{decision.decision_type === 'base_class' ? 'Выберите базовый класс' : 'Выберите группу или группы'}<select multiple={decision.decision_type === 'groups'} value={decision.decision_type === 'groups' ? decision.group_ids : (decision.group_ids[0] || '')} onChange={(event) => setAudienceDecisions((current) => ({ ...current, [caseKey]: { ...decision, group_ids: decision.decision_type === 'groups' ? Array.from(event.target.selectedOptions).map((option) => option.value) : [event.target.value] } }))}>{(allocation.groups || []).filter((groupItem: Item) => decision.decision_type !== 'base_class' || groupItem.group_type === 'class').map((groupItem: Item) => <option key={groupItem.id} value={groupItem.id}>{groupItem.display_name}</option>)}</select></label> : null}<p>Это решение будет применено к: {activity.lesson_date || week}, {String(activity.start_time || '').slice(0, 5)}, «{activity.subject}», {activity.source_cell ? `исходная ячейка ${activity.source_cell}` : 'этому slot'}.</p><label className="schedule-persistent-choice"><input type="checkbox" checked={decision.persistent} onChange={(event) => setAudienceDecisions((current) => ({ ...current, [caseKey]: { ...decision, persistent: event.target.checked } }))} /> Сохранить также для повторяющихся случаев</label><button className="admin-button admin-button--primary" disabled={(needsGroups && !decision.group_ids.length) || busy === `audience:${caseKey}`} onClick={() => void saveAudienceDecision(activity)}>{busy === `audience:${caseKey}` ? 'Сохраняем…' : 'Сохранить решение'}</button>{selectedGroups.length ? <small>Выбрано: {selectedGroups.map((groupItem: Item) => groupItem.display_name).join(', ')}</small> : null}</div> : null}</article>;
        })}</div>
        {slot.unassigned?.length ? <div className="schedule-allocation-alert"><strong>Unassigned</strong>{slot.unassigned.map((student: Item) => <span key={student.id}>{student.name} — {student.reason}</span>)}</div> : null}
        {slot.conflicts?.length ? <div className="schedule-allocation-alert schedule-allocation-alert--conflict"><strong>Conflicts</strong>{slot.conflicts.map((student: Item) => <span key={student.id}>{student.name} — {(student.conflicting_activities || []).join(' ↔ ') || 'пересечение memberships'}</span>)}</div> : null}
      </details>)}</div></section>)}</div>
    </section>
    <section className="admin-panel admin-table-panel">
      <div className="admin-table-meta"><span>{lessons.length} уроков по выбранному фильтру</span><span>Weekly имеет приоритет для дат {week}</span></div>
      {lessons.length ? <div className="admin-table-wrap"><table className="admin-table"><thead><tr><th>Дата / время</th><th>Урок</th><th>Преподаватель</th><th>Класс / группа</th><th>Состояние</th><th>Изменение</th></tr></thead><tbody>{lessons.map((lesson: Item) => <tr key={lesson.id}><td><strong>{lesson.lesson_date || '—'}</strong><small>{lesson.start_time || ''}–{lesson.end_time || ''}</small></td><td><strong>{lesson.subject || '—'}</strong><small>{lesson.room || ''}</small></td><td>{lesson.teacher_name || lesson.teacher_hint || 'Не определён'}</td><td>{lesson.group_name || lesson.audience || 'Не определена'}</td><td><span className={`admin-status admin-status--${lesson.resolution_status === 'RESOLVED' ? 'good' : lesson.resolution_status === 'WARNING' ? 'warn' : 'bad'}`}>{STATUS_LABELS[lesson.resolution_status] || lesson.resolution_status}</span></td><td>{lesson.diff_status || '—'}</td></tr>)}</tbody></table></div> : <p className="admin-empty">Уроков по выбранному фильтру нет.</p>}
    </section>
    <div className="admin-callout admin-callout--muted"><ShieldCheck size={16} /><span>Решения меняют только Schedule mappings и производную сверку расписания. Directory memberships и teacher assignments не изменяются.</span></div>
  </div>;
}
