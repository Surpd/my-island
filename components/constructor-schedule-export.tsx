'use client';

import { type Ref, useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';

type Api = <T>(path: string, init?: RequestInit) => Promise<T>;
type ExportGrid = {
  rows: number;
  columns: number;
  cells: { row: number; col: number; height: number; width: number; text: string; bg: string; fg: string; bold: boolean }[];
  column_widths?: number[];
  row_heights?: number[];
  days?: { row: number; label: string }[];
};
type ExportPreview = {
  draft_id: string;
  revision: number;
  name: string;
  kind: 'template' | 'week';
  week_start: string | null;
  title: string;
  target_url?: string;
  summary: { lessons: number; placements: number; clarifications: number; rows: number; columns: number };
  sample?: ExportGrid;
  grid?: ExportGrid;
  status: 'prepared' | 'existing';
  preview_token: string;
  url?: string;
};
type ExportResult = {
  status: 'exported' | 'existing' | 'authorization_required';
  title?: string;
  url?: string;
  reconnect_available?: boolean;
  message?: string;
};

function ScheduleGrid({ grid, gridRef }: { grid: ExportGrid; gridRef?: Ref<HTMLDivElement> }) {
  const columns = grid.column_widths?.length === grid.columns
    ? grid.column_widths.map(width => `${width}px`).join(' ')
    : `repeat(${grid.columns}, minmax(118px, 1fr))`;
  const rows = grid.row_heights?.length === grid.rows
    ? grid.row_heights.map(height => `minmax(${height}px, auto)`).join(' ')
    : undefined;
  return <div ref={gridRef} className="constructor-export-sample" style={{ gridTemplateColumns: columns, gridTemplateRows: rows }}>
    {grid.cells.map(cell => <div key={`${cell.row}:${cell.col}`} data-sheet-row={cell.row} style={{
      gridColumn: `${cell.col + 1} / span ${cell.width}`, gridRow: `${cell.row + 1} / span ${cell.height}`,
      backgroundColor: cell.bg, color: cell.fg, fontWeight: cell.bold ? 650 : 400,
    }}>{cell.text}</div>)}
  </div>;
}

function FullSchedulePreview({ grid }: { grid: ExportGrid }) {
  const [expanded, setExpanded] = useState(false);
  const viewport = useRef<HTMLElement>(null);
  const sheet = useRef<HTMLDivElement>(null);
  const goToDay = (row: number) => {
    const firstCell = sheet.current?.querySelector<HTMLElement>(`[data-sheet-row="${row}"]`);
    if (firstCell) viewport.current?.scrollTo({ top: firstCell.offsetTop });
  };
  return <details className="constructor-review-detail constructor-export-full-details" onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary>Показать весь лист</summary>
    {expanded ? <>
      {grid.days?.length ? <nav aria-label="Дни в листе экспорта" className="constructor-export-day-nav">
        {grid.days.map(day => <button type="button" key={day.row} onClick={() => goToDay(day.row)}>{day.label}</button>)}
      </nav> : null}
      <p id="constructor-export-grid-help">Прокручивайте лист вниз по дням и вправо по классам.</p>
      {/* oxlint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- Enable keyboard scrolling of both axes. */}
      <section ref={viewport} className="constructor-export-sample-wrap constructor-export-full-wrap" tabIndex={0}
        aria-label="Лист экспорта целиком" aria-describedby="constructor-export-grid-help">
        <ScheduleGrid grid={grid} gridRef={sheet} />
      </section>
    </> : null}
  </details>;
}

export function ConstructorScheduleExport({ api, path, id, revision, name, readOnly, onClose }: {
  api: Api;
  path: string;
  id: string;
  revision: number;
  name: string;
  readOnly: boolean;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [preview, setPreview] = useState<ExportPreview | null>(null);
  const [result, setResult] = useState<ExportResult | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [confirmed, setConfirmed] = useState(false);
  useEffect(() => {
    if (dialog.current && !dialog.current.open) dialog.current.showModal();
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      controller.abort();
      setError('Предварительный просмотр не завершился. Попробуйте подготовить экспорт ещё раз.');
    }, 30_000);
    void api<ExportPreview>(`${path}/preview`, {
      method: 'POST', body: JSON.stringify({ expected_revision: revision }), signal: controller.signal,
    }).then(value => {
      if (!controller.signal.aborted) setPreview(value);
    }).catch(failure => {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Не удалось подготовить экспорт.');
    }).finally(() => window.clearTimeout(timeout));
    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [api, path, revision, attempt]);
  const validPreview = preview?.draft_id === id && preview.revision === revision ? preview : null;
  const refresh = () => {
    setPreview(null);
    setError('');
    setResult(null);
    setConfirmed(false);
    setAttempt(value => value + 1);
  };
  const confirm = async () => {
    if (!validPreview || validPreview.status === 'existing' || !confirmed || readOnly || busy) return;
    setBusy(true);
    setError('');
    try {
      setResult(await api<ExportResult>(path, {
        method: 'POST',
        body: JSON.stringify({ expected_revision: revision, preview_token: validPreview.preview_token, confirmed: true }),
      }));
      setConfirmed(false);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Не удалось экспортировать расписание.');
      setConfirmed(false);
    } finally {
      setBusy(false);
    }
  };
  const reconnect = async () => {
    setBusy(true);
    setError('');
    try {
      const oauth = await api<{ authorization_url: string }>('/api/admin/google/reconnect?schedule_export=true', { method: 'POST' });
      window.location.assign(oauth.authorization_url);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Не удалось подключить Google.');
      setBusy(false);
    }
  };
  const existingUrl = result?.url || validPreview?.url;
  return <div className="constructor-conflict-backdrop">
    <dialog ref={dialog} className="constructor-conflict-dialog constructor-publication-panel constructor-export-dialog" aria-labelledby="constructor-export-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
      <header><div><h2 id="constructor-export-title">Экспорт в Google Sheets</h2><small>{name}</small></div>
        <button type="button" aria-label="Закрыть экспорт" disabled={busy} onClick={onClose}><X size={20} /></button>
      </header>
      <div className="constructor-review-scroll">
        {!validPreview && !error ? <output>Готовим предварительный просмотр экспорта…</output> : null}
        {validPreview ? <>
          <p>{validPreview.kind === 'template' ? 'Школьный шаблон' : `Неделя с ${validPreview.week_start}`} · версия {revision}</p>
          <h3>{validPreview.title}</h3>
          <p>Экспорт создаёт отдельный лист в таблице для выгрузок. {validPreview.target_url ? <a href={validPreview.target_url} target="_blank" rel="noreferrer">Открыть таблицу для выгрузок</a> : null}</p>
          <div className="constructor-publication-summary" aria-label="Состав экспорта">
            <span><b>{validPreview.summary.lessons}</b> исходных занятий</span>
            <span><b>{validPreview.summary.placements}</b> размещений в сетке</span>
            <span><b>{validPreview.summary.clarifications}</b> замечаний</span>
            <span>{validPreview.summary.rows} строк × {validPreview.summary.columns} столбцов</span>
          </div>
          {validPreview.sample?.cells.length ? <details className="constructor-review-detail">
            <summary>Фрагмент готового листа</summary>
            <div className="constructor-export-sample-wrap">
              <ScheduleGrid grid={validPreview.sample} />
            </div>
          </details> : null}
          {validPreview.grid?.cells.length ? <FullSchedulePreview grid={validPreview.grid} /> : null}
          {validPreview.status === 'existing' || result?.status === 'existing' ? <p>Эта версия уже экспортирована.</p> : null}
          {result?.status === 'exported' ? <output className="constructor-publication-message">Экспорт завершён.</output> : null}
          {existingUrl ? <p><a href={existingUrl} target="_blank" rel="noreferrer">Открыть экспортированный лист</a></p> : null}
          {result?.status === 'authorization_required' ? <section>
            <p>{result.message || 'Подключите Google для записи в таблицу выгрузок.'}</p>
            {result.reconnect_available ? <button type="button" disabled={busy} onClick={() => void reconnect()}>Подключить Google для экспорта</button> : null}
          </section> : null}
          {validPreview.status === 'prepared' && !result && !readOnly ? <label className="constructor-publication-confirm">
            <input type="checkbox" checked={confirmed} disabled={busy} onChange={event => setConfirmed(event.target.checked)} />Подтверждаю создание отдельного листа экспорта
          </label> : null}
        </> : null}
        {error ? <p role="alert">{error}</p> : null}
        {error || result?.status === 'authorization_required' ? <button type="button" disabled={busy} onClick={refresh}>Подготовить экспорт ещё раз</button> : null}
      </div>
      <footer>
        <button type="button" disabled={busy} onClick={onClose}>К конструктору</button>
        {validPreview?.status === 'prepared' && !result && !readOnly ? <button type="button" className="is-primary" disabled={!confirmed || busy} onClick={() => void confirm()}>{busy ? 'Экспортируем…' : 'Создать лист экспорта'}</button> : null}
      </footer>
    </dialog>
  </div>;
}
