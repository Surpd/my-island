'use client';

import { useEffect, useState } from 'react';

type JsonRecord = Record<string, unknown>;
type Api = <T>(path: string, init?: RequestInit) => Promise<T>;
type Props = { api: Api };

type Policy = {
  week_start?: string;
  manual_publication_active?: boolean;
  review_required?: boolean;
  automatic_application_enabled?: boolean;
  source_checked?: boolean;
};

export function ScheduleSourceReview({ api }: Props) {
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [policyError, setPolicyError] = useState('');
  const [preview, setPreview] = useState<JsonRecord | null>(null);
  const [previewError, setPreviewError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    api<Policy>('/api/admin/schedule/source-policy')
      .then((result) => { if (active) setPolicy(result); })
      .catch((error: unknown) => { if (active) setPolicyError(error instanceof Error ? error.message : 'Не удалось проверить режим источника.'); });
    return () => { active = false; };
  }, [api]);

  const checkWeek = async () => {
    if (!policy?.week_start) return;
    setBusy(true);
    setPreviewError('');
    try {
      const result = await api<JsonRecord>(`/api/admin/schedule/preview?week_start=${encodeURIComponent(policy.week_start)}`, { method: 'POST' });
      setPreview(result);
      if (result.status === 'preview') setPolicy(current => current ? { ...current, source_checked: true } : current);
    } catch (error) {
      setPreviewError(error instanceof Error ? error.message : 'Не удалось проверить источник.');
    } finally {
      setBusy(false);
    }
  };

  const policyMessage = policy?.manual_publication_active
    ? 'Обновления Google Sheets требуют проверки; автоматическое применение приостановлено'
    : policy?.source_checked === false
      ? 'Проверка источника не выполнена'
      : '';
  const counts = (preview?.diff_counts || {}) as JsonRecord;
  const notes = [...(Array.isArray(preview?.warnings) ? preview.warnings : []), ...(Array.isArray(preview?.errors) ? preview.errors : [])];
  const noteText = (value: unknown): string => {
    if (typeof value === 'string') return value;
    if (value && typeof value === 'object') {
      const record = value as JsonRecord;
      for (const key of ['message', 'warning', 'error']) if (typeof record[key] === 'string') return record[key];
    }
    return 'Требуется проверка источника';
  };

  return <section className="admin-panel">
    <div className="admin-panel__heading"><div><p className="admin-eyebrow">ЕЖЕНЕДЕЛЬНЫЕ ОБНОВЛЕНИЯ</p><h2>Источник Google Sheets</h2></div><span>{policy?.week_start || 'Неделя не указана'}</span></div>
    {policyError ? <output className="admin-help-text">{policyError}</output> : policyMessage ? <output className="admin-help-text">{policyMessage}</output> : null}
    {policy && <p className="admin-help-text">Проверка источника: {policy.source_checked ? 'выполнена' : 'не выполнена'} · проверка требуется: {policy.review_required ? 'да' : 'нет'} · автоматическое применение: {policy.automatic_application_enabled ? 'включено' : 'выключено'}</p>}
    <button className="admin-button admin-button--quiet" disabled={busy || !policy?.week_start} onClick={() => void checkWeek()}>{busy ? 'Проверяем…' : 'Проверить изменения недели'}</button>
    {previewError ? <p role="alert" className="admin-error-text">{previewError}</p> : null}
    {preview && <div aria-live="polite"><p>{preview.status === 'preview' ? 'Источник прочитан. Расписание не изменено.' : noteText(preview.message || 'Проверка источника недоступна.')}</p>
      {preview.status === 'preview' && <><div className="admin-chip-row">{Object.entries(counts).map(([key, value]) => <span key={key}>{({ added: 'Добавлено', changed: 'Изменено', removed: 'Удалено', unchanged: 'Без изменений', unresolved: 'Не разрешено' } as Record<string, string>)[key] || key}: {String(value)}</span>)}</div><p className="admin-help-text">Сводка сравнивается с canonical-базой источника. Она не подтверждает замену ручной публикации. Переносите нужные изменения в черновик и проверяйте diff перед публикацией.</p></>}
      {notes.length > 0 && <details><summary>Предупреждения и ошибки ({notes.length})</summary><ul>{notes.map((item, index) => <li key={index}>{noteText(item)}</li>)}</ul></details>}
    </div>}
  </section>;
}
