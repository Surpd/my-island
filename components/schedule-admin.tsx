'use client';

import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, ChevronDown, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react';

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
      await loadWeek(selectedWeek, '');
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
    try { await loadWeek(nextWeek, status); } catch (caught) { setError(caught instanceof Error ? caught.message : 'Не удалось загрузить неделю'); }
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

  const summary = overview?.summary || {};
  const activeMappings = useMemo(() => (reconciliation.mappings || []).filter((item: Item) => !item.valid_until), [reconciliation.mappings]);
  const issueGroups = useMemo(() => (reconciliation.issue_groups || []).filter((item: Item) => !status || Number(item.statuses?.[status] || 0) > 0), [reconciliation.issue_groups, status]);
  const choicesFor = (group: Item) => group.mapping_type === 'identity' ? reconciliation.teachers || [] : reconciliation.groups || [];

  if (!overview && busy === 'load') return <div className="admin-loading"><RefreshCw className="admin-spin" size={18} />Загружаем расписание…</div>;
  if (!overview) return <div className="admin-error"><AlertTriangle size={17} /><div><strong>Не удалось загрузить расписание</strong><p>{error}</p></div><button className="admin-button admin-button--quiet" onClick={() => void bootstrap()}>Повторить</button></div>;

  return <div className="admin-stack schedule-reconciliation">
    <div className="admin-page-actions">
      <div><strong>Сверка расписания</strong><p>Шаблон — baseline. Каждая недельная вкладка показывает только изменения для своих дат.</p></div>
      <div className="admin-button-row"><button className="admin-button admin-button--quiet" disabled={!!busy} onClick={() => void refresh()}><RefreshCw size={15} className={busy === 'refresh' ? 'admin-spin' : ''} />{busy === 'refresh' ? 'Обновляем…' : 'Обновить из источника'}</button><button className="admin-button admin-button--quiet" disabled={!!busy} onClick={() => void loadWeek(week)}>Перечитать</button></div>
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
      {activeMappings.length ? <div className="schedule-mapping-list">{activeMappings.map((mapping: Item) => <div className="schedule-mapping-row" key={mapping.id}><div><strong>{mapping.external_key}</strong><span>{mapping.mapping_type === 'identity' ? 'Преподаватель' : mapping.mapping_type === 'group' ? 'Группа' : 'Предмет'} → {mapping.identity_name || mapping.group_name || mapping.canonical_value}</span><small>Используется во всех следующих sync, snapshots и неделях</small></div><button className="admin-button admin-button--quiet" disabled={busy === `retire:${mapping.id}`} onClick={() => void retireMapping(mapping)}><Trash2 size={14} />Отключить</button></div>)}</div> : <p className="admin-empty">Сохранённых правил пока нет.</p>}
    </section>
    <section className="admin-panel admin-table-panel">
      <div className="admin-table-meta"><span>{lessons.length} уроков по выбранному фильтру</span><span>Weekly имеет приоритет для дат {week}</span></div>
      {lessons.length ? <div className="admin-table-wrap"><table className="admin-table"><thead><tr><th>Дата / время</th><th>Урок</th><th>Преподаватель</th><th>Класс / группа</th><th>Состояние</th><th>Изменение</th></tr></thead><tbody>{lessons.map((lesson: Item) => <tr key={lesson.id}><td><strong>{lesson.lesson_date || '—'}</strong><small>{lesson.start_time || ''}–{lesson.end_time || ''}</small></td><td><strong>{lesson.subject || '—'}</strong><small>{lesson.room || ''}</small></td><td>{lesson.teacher_name || lesson.teacher_hint || 'Не определён'}</td><td>{lesson.group_name || lesson.audience || 'Не определена'}</td><td><span className={`admin-status admin-status--${lesson.resolution_status === 'RESOLVED' ? 'good' : lesson.resolution_status === 'WARNING' ? 'warn' : 'bad'}`}>{STATUS_LABELS[lesson.resolution_status] || lesson.resolution_status}</span></td><td>{lesson.diff_status || '—'}</td></tr>)}</tbody></table></div> : <p className="admin-empty">Уроков по выбранному фильтру нет.</p>}
    </section>
    <div className="admin-callout admin-callout--muted"><ShieldCheck size={16} /><span>Решения меняют только Schedule mappings и производную сверку расписания. Directory memberships и teacher assignments не изменяются.</span></div>
  </div>;
}
