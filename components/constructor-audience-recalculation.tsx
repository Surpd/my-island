'use client';

import { useEffect, useRef, useState } from 'react';

type Api = <T>(path: string, init?: RequestInit) => Promise<T>;
type Preview = {
  revision: number;
  preview_token: string;
  summary: { selected_count: number; changed_count: number; added_count: number; removed_count: number; safe_count: number; unresolved_count: number };
  lessons: { id: string; subject: string; safe: boolean; status: string; reason?: string; old_count: number; proposed_count: number; added_count: number; removed_count: number }[];
};

export function ConstructorAudienceRecalculation({ api, draftId, revision, dirty, day, classId, classLabel, apiPrefix = '/api/admin/schedule/constructor', onUpdated, onClose }: {
  api: Api; draftId: string; revision: number; dirty: boolean; day: number; classId?: string; classLabel?: string;
  apiPrefix?: string; onUpdated: (draft: unknown) => void; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (dialog.current && !dialog.current.open) dialog.current.showModal(); }, []);
  const [scope, setScope] = useState('stale');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewContext, setPreviewContext] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const body = { expected_revision: revision, scope, day, class_id: classId };
  const context = JSON.stringify({ ...body, apiPrefix });
  const stale = dirty || preview?.revision !== revision || previewContext !== context;
  const run = async (apply: boolean) => {
    setBusy(true); setError('');
    try {
      const result = await api<Preview | { draft?: unknown }>(`${apiPrefix}/${encodeURIComponent(draftId)}/audiences/${apply ? 'apply' : 'preview'}`, {
        method: 'POST', body: JSON.stringify({ ...body, ...(apply ? { preview_token: preview?.preview_token, reason: 'Массовое подтверждение проверенных составов' } : {}) }),
      });
      if (apply) { onUpdated((result as { draft?: unknown }).draft || result); onClose(); }
      else { setPreview(result as Preview); setPreviewContext(context); }
    } catch (failure) { setError(String((failure as Error).message || failure)); if (apply) setPreview(null); }
    finally { setBusy(false); }
  };
  return <dialog ref={dialog} className="constructor-audience-recalculation" aria-label="Пересчитать аудитории" onCancel={onClose}>
    <h2>Пересчитать аудитории</h2>
    <p>Сначала просмотрите изменения. Неизвестные составы останутся нерешёнными.</p>
    {dirty ? <p role="alert">Сначала сохраните черновик. Пересчёт выполняется отдельно от сохранения.</p> : null}
    <label>Область пересчёта <select value={scope} disabled={busy} onChange={(event) => { setScope(event.target.value); setPreview(null); }}>
      <option value="stale">Только устаревшие составы</option><option value="template">Весь шаблон</option><option value="day">Текущий день</option>
      <option value="class" disabled={!classId}>Текущий класс или группа{classLabel ? ` · ${classLabel}` : ''}</option>
    </select></label>
    <button disabled={dirty || busy} onClick={() => void run(false)}>{busy ? 'Рассчитываем…' : 'Показать изменения'}</button>
    {error ? <p role="alert">{error}</p> : null}
    {preview ? <>
      <p aria-live="polite">Проверено: {preview.summary.selected_count}. Изменено занятий: {preview.summary.changed_count}. Добавлено участников: {preview.summary.added_count}. Удалено: {preview.summary.removed_count}.</p>
      <p>Можно подтвердить: {preview.summary.safe_count}. Требуют решения: {preview.summary.unresolved_count}.</p>
      <div className="constructor-roster-results">{preview.lessons.map((lesson) => <article key={lesson.id}><strong>{lesson.subject || 'Занятие'}</strong><span> · {lesson.safe ? 'Проверено' : 'Требует решения'} · {lesson.old_count} → {lesson.proposed_count ?? '?'}</span>{lesson.reason ? <p>{lesson.reason}</p> : null}</article>)}</div>
      {stale ? <p role="alert">Черновик изменился. Запустите пересчёт заново.</p> : null}
      <button disabled={busy || stale || !preview.summary.safe_count} onClick={() => void run(true)}>Подтвердить проверенные составы</button>
    </> : null}
    <button disabled={busy} onClick={onClose}>Закрыть</button>
  </dialog>;
}
