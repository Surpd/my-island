'use client';
import { useEffect, useState } from 'react';

type Api = <T>(path: string, init?: RequestInit) => Promise<T>;
type Account = {
  telegram_user_id: string;
  username: string | null;
  state: string;
  roles: string[];
  connected_at: string | null;
  last_active_at: string | null;
  identity: { id: string } | null;
};
export function TelegramAccessBlock({
  api,
  identityId,
  navigate,
}: {
  api: Api;
  identityId: string;
  navigate: (path: string) => void;
}) {
  const [account, setAccount] = useState<Account | null | undefined>(undefined);
  useEffect(() => {
    let active = true;
    api<{ accounts: Account[] }>('/api/admin/access')
      .then((data) => {
        if (active)
          setAccount(
            data.accounts.find(
              (item) => String(item.identity?.id) === identityId,
            ) || null,
          );
      })
      .catch(() => {
        if (active) setAccount(undefined);
      });
    return () => {
      active = false;
    };
  }, [api, identityId]);
  return (
    <section className="school-detail-section">
      <h3>Telegram</h3>
      {account === undefined ? (
        <p>Сведения о подключении недоступны.</p>
      ) : account === null ? (
        <p>Аккаунт не подключён.</p>
      ) : (
        <>
          <p>
            {account.state === 'connected'
              ? 'Подключён и подтверждён'
              : account.state === 'revoked'
                ? 'Доступ отозван'
                : 'Не подтверждён'}{' '}
            · ID {account.telegram_user_id}
            {account.username ? ` · @${account.username}` : ''}
          </p>
          <p>
            Роли:{' '}
            {account.roles
              .map(
                (role) =>
                  (
                    ({
                      student: 'ученик',
                      teacher: 'преподаватель',
                      admin: 'администратор',
                    }) as Record<string, string>
                  )[role] || role,
              )
              .join(', ') || 'нет'}
            {account.connected_at
              ? ` · Подключён ${new Date(account.connected_at).toLocaleDateString('ru-RU')}`
              : ''}
            {account.last_active_at
              ? ` · Активность ${new Date(account.last_active_at).toLocaleDateString('ru-RU')}`
              : ''}
          </p>
        </>
      )}
      <button
        className="admin-button"
        onClick={() =>
          navigate(
            `/admin/settings/access?identity_id=${encodeURIComponent(identityId)}`,
          )
        }
      >
        Управлять подключением
      </button>
    </section>
  );
}

export function useTelegramConnections(api: Api, enabled: boolean) {
  const [connections, setConnections] = useState<Record<string, string> | null>(
    null,
  );
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    api<{ accounts: Account[] }>('/api/admin/access')
      .then((data) => {
        if (active)
          setConnections(
            Object.fromEntries(
              data.accounts
                .filter((item) => item.identity)
                .map((item) => [String(item.identity!.id), item.state]),
            ),
          );
      })
      .catch(() => {
        if (active) setConnections(null);
      });
    return () => {
      active = false;
    };
  }, [api, enabled]);
  return connections;
}
