'use client';

import { useEffect, useState, type FormEvent } from 'react';

type Request = <T>(path: string, init?: RequestInit) => Promise<T>;
const views = {
  day: 'Оперативный день',
  teachers: 'Нагрузка преподавателей',
  classes: 'Аналитика классов',
  journals: 'Журналы',
  widgets: 'Виджеты',
};

export function ArchitectureCheck({ request }: { request: Request }) {
  const [view, setView] = useState<keyof typeof views>('day');
  const [day, setDay] = useState('2026-09-21');
  const [submitted, setSubmitted] = useState<{ href: string } | null>(null);
  const [result, setResult] = useState<{ data?: unknown; error?: string } | null>(null);

  useEffect(() => {
    if (!submitted) return;
    const controller = new AbortController();
    request(submitted.href, { signal: controller.signal }).then(
      (data) => { if (!controller.signal.aborted) setResult({ data }); },
      (error: unknown) => {
        if (!controller.signal.aborted) setResult({ error: error instanceof Error ? error.message : 'Ошибка запроса' });
      },
    );
    return () => controller.abort();
  }, [request, submitted]);

  function run(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const params = new URLSearchParams();
    if (view === 'day') params.set('day', day);
    if (view === 'teachers' || view === 'classes') params.set('start_day', day);
    const route = { day: '/api/admin/operations/day', teachers: '/api/admin/analytics/teachers',
      classes: '/api/admin/analytics/classes', journals: '/api/admin/analytics/journals',
      widgets: '/api/admin/workspace/layout' }[view];
    setResult(null);
    setSubmitted({ href: params.size ? route + '?' + params.toString() : route });
  }

  return <section>
    <h1>Проверка серверных разделов</h1>
    <form onSubmit={run}>
      <label>Раздел <select value={view} onChange={(event) => setView(event.target.value as keyof typeof views)}>
        {Object.entries(views).map(([value, title]) => <option key={value} value={value}>{title}</option>)}
      </select></label>
      <label>Дата <input type="date" value={day} required onChange={(event) => setDay(event.target.value)} /></label>
      <button type="submit">Загрузить данные</button>
    </form>
    <p role="status">{submitted && !result ? 'Загрузка…' : result?.data !== undefined ? 'Ответ получен' : ''}</p>
    {result?.error && <p role="alert">{result.error}</p>}
    {result?.data !== undefined && <pre aria-label="Ответ API" style={{ overflow: 'auto' }}>{JSON.stringify(result.data, null, 2)}</pre>}
  </section>;
}
