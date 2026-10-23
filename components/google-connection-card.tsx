'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircle2, ExternalLink, LoaderCircle, RefreshCw, ShieldCheck } from 'lucide-react';

type ApiRequest = <T>(path: string, init?: RequestInit) => Promise<T>;
type GoogleStatusName = 'connected' | 'reauthorization_required' | 'configuration_missing' | 'temporary_error' | 'insufficient_permissions' | 'spreadsheet_access_denied';
type GoogleStatus = {
  status: GoogleStatusName;
  account_email?: string | null;
  reconnect_available: boolean;
  message: string;
  journal?: { status: string; title?: string | null };
  schedule_source?: { status: string; title?: string | null } | null;
  capabilities?: { sheets_read: boolean; sheets_write: boolean; classroom_read: boolean };
};

const labels: Record<GoogleStatusName, string> = {
  connected: 'Подключено и проверено',
  reauthorization_required: 'Требуется переподключение',
  configuration_missing: 'Не настроена OAuth-конфигурация',
  temporary_error: 'Временная ошибка Google API',
  insufficient_permissions: 'Недостаточно разрешений',
  spreadsheet_access_denied: 'Нет доступа к журналу',
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Не удалось проверить подключение Google.';
}

export function GoogleConnectionCard({
  api,
  apiOrigin,
  showWhenConnected = true,
}: {
  api: ApiRequest;
  apiOrigin: string;
  showWhenConnected?: boolean;
}) {
  const [status, setStatus] = useState<GoogleStatus | null>(null);
  const [statusError, setStatusError] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [reconnectError, setReconnectError] = useState('');
  const popupRef = useRef<Window | null>(null);
  const expectedApiOrigin = (() => {
    try { return new URL(apiOrigin).origin; }
    catch { return ''; }
  })();

  const loadStatus = useCallback(async () => {
    setStatusError('');
    try {
      const result = await api<GoogleStatus>('/api/admin/google/status');
      setStatus(result);
    } catch (error) {
      setStatusError(errorMessage(error));
    }
  }, [api]);

  useEffect(() => {
    let active = true;
    api<GoogleStatus>('/api/admin/google/status')
      .then((result) => { if (active) setStatus(result); })
      .catch((error: unknown) => { if (active) setStatusError(errorMessage(error)); });
    return () => { active = false; };
  }, [api]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const popup = popupRef.current;
      if (!popup || event.source !== popup || !expectedApiOrigin || event.origin !== expectedApiOrigin) return;
      const data = event.data as { type?: string; status?: string } | null;
      if (data?.type !== 'my-island-google-oauth' || !['success', 'cancelled', 'error'].includes(data.status || '')) return;
      popupRef.current = null;
      popup.close();
      setBusy(false);
      setNotice(data.status === 'success' ? 'Google подключён. Проверяю журнал…' : data.status === 'cancelled' ? 'Подключение отменено. Текущие данные не изменены.' : 'Google не удалось подключить. Проверьте настройки и повторите попытку.');
      if (data.status === 'success') void loadStatus();
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [expectedApiOrigin, loadStatus]);

  async function reconnect() {
    setBusy(true);
    setReconnectError('');
    setNotice('');
    const popup = window.open('about:blank', 'my-island-google-oauth', 'popup,width=560,height=720,resizable=yes,scrollbars=yes');
    popupRef.current = popup;
    try {
      const result = await api<{ authorization_url: string }>('/api/admin/google/reconnect?schedule_export=true', { method: 'POST' });
      if (!popup) {
        setNotice('Всплывающее окно заблокировано. Продолжаю подключение в текущей вкладке.');
        window.location.assign(result.authorization_url);
        return;
      }
      popup.location.href = result.authorization_url;
      const popupWatcher = window.setInterval(() => {
        if (!popup.closed) return;
        window.clearInterval(popupWatcher);
        if (popupRef.current !== popup) return;
        popupRef.current = null;
        setBusy(false);
        setNotice('Окно Google закрыто. Проверяю состояние подключения…');
        void loadStatus();
      }, 500);
    } catch (error) {
      popup?.close();
      popupRef.current = null;
      setBusy(false);
      setReconnectError(errorMessage(error));
    }
  }

  if (!showWhenConnected && status?.status === 'connected' && !statusError) return null;
  const variant = status?.status === 'connected' ? 'is-good' : status?.status === 'configuration_missing' ? 'is-muted' : 'is-warning';
  const noJournalAccess = status?.status === 'spreadsheet_access_denied';

  return <section className={`admin-panel google-connection-card ${variant}`} aria-live="polite">
    <div className="google-connection-icon">{status?.status === 'connected' ? <CheckCircle2 size={17} /> : status?.status === 'temporary_error' ? <RefreshCw size={17} /> : <ShieldCheck size={17} />}</div>
    <div className="google-connection-body">
      <div className="google-connection-title"><h2>Подключение Google</h2><span className="google-connection-status">{status ? labels[status.status] : statusError ? 'Не удалось проверить' : 'Проверяем подключение…'}</span></div>
      <p>{status?.message || statusError || 'Проверяем доступ к Google и журналу девятого класса.'}</p>
      {status?.account_email ? <small>Аккаунт: {status.account_email}</small> : null}
      {status?.journal?.title ? <small>Журнал: {status.journal.title}</small> : null}
      {status?.schedule_source?.status === 'access_denied' ? <small>Расписание: нет доступа. Это не мешает импорту журнала, но может блокировать обновление расписания.</small> : null}
      {status?.capabilities && !status.capabilities.sheets_write ? <small>Экспорт расписания в Google Sheets не разрешён этому подключению.</small> : null}
      {status?.capabilities && !status.capabilities.classroom_read ? <small>Не все разрешения Google Classroom выданы; синхронизация Classroom может быть ограничена.</small> : null}
      {noJournalAccess ? <small>Откройте исходную таблицу и предоставьте доступ аккаунту antischool.island@gmail.com.</small> : null}
      {notice ? <small className="google-connection-notice">{notice}</small> : null}
      {statusError || reconnectError ? <small className="google-connection-error" role="alert">{reconnectError || statusError}</small> : null}
      <div className="google-connection-actions">
        {!noJournalAccess && status?.reconnect_available ? <button className="admin-button admin-button--quiet" onClick={() => void reconnect()} disabled={busy}><ShieldCheck size={14} />{busy ? 'Ожидаем ответ Google…' : 'Переподключить Google'}</button> : null}
        <button className="admin-link" onClick={() => void loadStatus()} disabled={busy}><RefreshCw size={13} /> Проверить снова</button>
        {busy ? <LoaderCircle size={15} className="admin-spin" aria-label="Идёт подключение" /> : null}
        {status?.status === 'spreadsheet_access_denied' ? <a className="admin-link" href="https://docs.google.com/spreadsheets/d/1RaT1O-qNnbc3WnQs2Fhikcu03mPLl6JpatqhqBU5a8I/edit" target="_blank" rel="noreferrer">Открыть журнал <ExternalLink size={12} /></a> : null}
      </div>
    </div>
  </section>;
}
