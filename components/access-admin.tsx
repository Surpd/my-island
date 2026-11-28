'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

type Identity = {
  id: string;
  display_name: string;
  class_name: string | null;
  kind: 'student' | 'teacher';
  linked_user_id: string | null;
};
type Account = {
  id: string;
  telegram_user_id: string;
  username: string | null;
  telegram_first_name: string | null;
  telegram_last_name: string | null;
  roles: string[];
  state: 'connected' | 'unconfirmed' | 'revoked';
  link_status?: 'confirmed' | 'revoked' | 'pending' | 'conflict' | null;
  identity: Identity | null;
  connected_at: string | null;
  last_active_at: string | null;
  can_manage_administrators: boolean;
};
type Claim = {
  id: string;
  user_id: string;
  requested_role: 'student' | 'teacher';
  submitted_name: string | null;
  submitted_class: string | null;
  status: string;
  created_at: string;
  reviewed_at: string | null;
  reason: string | null;
  identity_id: string | null;
  resolved_identity_name?: string | null;
  resolved_identity_class?: string | null;
  candidate_ids: string[];
};
type Data = {
  can_manage_administrators: boolean;
  counts: { connected: number; pending: number; unlinked_identities: number };
  accounts: Account[];
  claims: Claim[];
  identities: Identity[];
};
type Api = <T>(path: string, init?: RequestInit) => Promise<T>;
const roleLabel: Record<string, string> = {
  student: 'Ученик',
  teacher: 'Преподаватель',
  admin: 'Администратор',
};
const stateLabel: Record<string, string> = {
  connected: 'Подключён',
  unconfirmed: 'Не подтверждён',
  revoked: 'Доступ отозван',
  pending: 'Ожидает',
  approved: 'Подтверждена',
  rejected: 'Отклонена',
  identity_conflict: 'Конфликт',
};
const date = (value: string | null) =>
  value ? new Date(value).toLocaleDateString('ru-RU') : '—';
const person = (identity: Identity) =>
  `${identity.display_name}${identity.class_name ? ` · ${identity.class_name}` : ''}`;
const telegram = (account?: Account) =>
  account
    ? `${account.username ? `@${account.username} · ` : ''}ID ${account.telegram_user_id}${account.telegram_first_name || account.telegram_last_name ? ` · ${[account.telegram_first_name, account.telegram_last_name].filter(Boolean).join(' ')}` : ''}`
    : 'Аккаунт недоступен';

function AccountTable({
  accounts,
  onSelect,
  label,
}: {
  accounts: Account[];
  onSelect: (account: Account) => void;
  label: string;
}) {
  return (
    <section
      className="access-table-scroll"
      // oxlint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- Keyboard users need to focus the horizontal scroll container.
      tabIndex={0}
      aria-label={label}
    >
      <table className="admin-table access-table">
        <thead>
          <tr>
            <th scope="col">Школьная запись</th>
            <th scope="col">Telegram и роли</th>
            <th scope="col">Подключение</th>
          </tr>
        </thead>
        <tbody>
          {accounts.map((account) => (
            <tr key={account.id}>
              <th scope="row">
                <button
                  className="access-account-name"
                  onClick={() => onSelect(account)}
                >
                  {account.identity
                    ? person(account.identity)
                    : 'Без школьной записи'}
                </button>
              </th>
              <td>
                {telegram(account)}
                <small>
                  {account.roles
                    .map((role) => roleLabel[role] || role)
                    .join(', ') || 'Нет ролей'}
                </small>
              </td>
              <td>
                {stateLabel[account.state]}
                {account.can_manage_administrators && (
                  <small>Управляет администраторами</small>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function AccessAdmin({
  api,
  initialIdentityId,
}: {
  api: Api;
  initialIdentityId?: string;
}) {
  const [data, setData] = useState<Data | null>(null);
  const [tab, setTab] = useState<'claims' | 'accounts' | 'admins'>(
    initialIdentityId ? 'accounts' : 'claims',
  );
  const [query, setQuery] = useState('');
  const [role, setRole] = useState('');
  const [status, setStatus] = useState('');
  const [selectedClaim, setSelectedClaim] = useState('');
  const [selectedAccount, setSelectedAccount] = useState('');
  const [identityId, setIdentityId] = useState(initialIdentityId || '');
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const initialSelectionDone = useRef(false);
  const [adminAction, setAdminAction] = useState<
    'grant_admin' | 'remove_admin' | null
  >(null);
  const load = useCallback(async () => {
    try {
      const result = await api<Data>('/api/admin/access');
      setData(result);
      if (initialIdentityId && !initialSelectionDone.current) {
        initialSelectionDone.current = true;
        const linked = result.accounts.find(
          (item) => item.identity?.id === initialIdentityId,
        );
        if (linked) setSelectedAccount(linked.id);
      }
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось загрузить данные.');
    }
  }, [api, initialIdentityId]);
  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load]);
  const write = async (path: string, body: object) => {
    setBusy(true);
    setError('');
    try {
      await api(path, { method: 'POST', body: JSON.stringify(body) });
      setSelectedClaim('');
      setSelectedAccount('');
      setIdentityId('');
      setReason('');
      await load();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : 'Не удалось сохранить изменения.',
      );
    } finally {
      setBusy(false);
    }
  };
  if (!data)
    return (
      <section className="admin-panel">
        {error ? (
          <>
            <p role="alert" className="admin-error-text">
              {error}
            </p>
            <button className="admin-button" onClick={() => void load()}>
              Повторить
            </button>
          </>
        ) : (
          'Загружаем пользователей…'
        )}
      </section>
    );
  const claim = data.claims.find((item) => item.id === selectedClaim);
  const account = data.accounts.find((item) => item.id === selectedAccount);
  const selectedIdentity = data.identities.find(
    (item) => item.id === identityId,
  );
  const replaceUserId =
    selectedIdentity?.linked_user_id &&
    selectedIdentity.linked_user_id !== (claim?.user_id || account?.id)
      ? selectedIdentity.linked_user_id
      : undefined;
  const previousAccount = data.accounts.find(
    (item) => item.id === replaceUserId,
  );
  const confirmTransfer = () =>
    !replaceUserId ||
    window.confirm(
      `Эта школьная запись уже связана с другим Telegram-аккаунтом (${telegram(previousAccount)}). Перенести доступ на выбранный аккаунт? Старый аккаунт потеряет соответствующий доступ.`,
    );
  const mayEditAccount =
    account &&
    (data.can_manage_administrators ||
      (!account.can_manage_administrators && !account.roles.includes('admin')));
  const accounts = data.accounts.filter(
    (item) =>
      (!query ||
        `${item.identity?.display_name || ''} ${item.identity?.class_name || ''} ${item.username || ''} ${item.telegram_user_id}`
          .toLocaleLowerCase('ru')
          .includes(query.toLocaleLowerCase('ru'))) &&
      (tab !== 'accounts' ||
        ((!role || item.roles.includes(role)) &&
          (!status || item.state === status))),
  );
  const activeAdmins = accounts.filter(
    (item) => item.roles.includes('admin') && item.state !== 'revoked',
  );
  const revokedAdmins = accounts.filter(
    (item) => item.roles.includes('admin') && item.state === 'revoked',
  );
  const candidates = accounts.filter(
    (item) =>
      !item.roles.includes('admin') &&
      item.state === 'connected' &&
      item.identity,
  );
  const openAccount = (item: Account) => {
    setSelectedAccount(item.id);
    setIdentityId(
      (tab === 'accounts' ? initialIdentityId : undefined) ||
        item.identity?.id ||
        '',
    );
  };
  const submitClaim = (decision: 'approve' | 'reject') => {
    if (!claim) return;
    if (decision === 'approve' && (!identityId || !confirmTransfer())) return;
    if (decision === 'reject' && !reason.trim()) return;
    void write(`/api/admin/access/claims/${claim.id}`, {
      decision,
      ...(decision === 'approve'
        ? {
            identity_id: identityId,
            ...(replaceUserId ? { replace_user_id: replaceUserId } : {}),
          }
        : { reason: reason.trim() }),
    });
  };
  const identityPicker = (kind?: string) => (
    <label className="school-field">
      Школьная запись
      <select
        value={identityId}
        onChange={(e) => setIdentityId(e.target.value)}
      >
        <option value="">Выберите человека</option>
        {data.identities
          .filter((item) => !kind || item.kind === kind)
          .map((item) => (
            <option key={item.id} value={item.id}>
              {person(item)}
              {item.linked_user_id ? ' · уже связан' : ''}
            </option>
          ))}
      </select>
    </label>
  );
  return (
    <div className="admin-stack access-admin">
      <div className="admin-page-actions">
        <p>Подтверждайте школьную личность и управляйте доступом к Острову.</p>
        <button
          className="admin-button admin-button--quiet"
          onClick={() => void load()}
          disabled={busy}
        >
          Обновить
        </button>
      </div>
      <div className="admin-inline-stats">
        <div>
          <strong>{data.counts.connected}</strong>
          <span>подключено</span>
        </div>
        <div>
          <strong>{data.counts.pending}</strong>
          <span>ожидают решения</span>
        </div>
        <div>
          <strong>{data.counts.unlinked_identities}</strong>
          <span>без аккаунта</span>
        </div>
      </div>
      <nav className="access-tabs" aria-label="Пользователи и доступ">
        {(
          [
            ['claims', 'Заявки'],
            ['accounts', 'Аккаунты'],
            ['admins', 'Администраторы'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            className={tab === id ? 'is-active' : ''}
            aria-current={tab === id ? 'page' : undefined}
            onClick={() => {
              setTab(id);
              setSelectedClaim('');
              setSelectedAccount('');
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      {error && (
        <p role="alert" className="admin-error-text">
          {error}
        </p>
      )}
      {tab === 'claims' && (
        <section className="admin-panel">
          <h2>Заявки на подключение</h2>
          <p>
            Сверьте данные с записью в школьном справочнике перед
            подтверждением.
          </p>
          <div className="admin-list">
            {data.claims.length ? (
              data.claims.map((item) => {
                const owner = data.accounts.find((a) => a.id === item.user_id);
                return (
                  <button
                    key={item.id}
                    className="admin-list-row"
                    onClick={() => {
                      setSelectedClaim(item.id);
                      setIdentityId(
                        item.candidate_ids?.length === 1
                          ? item.candidate_ids[0]
                          : item.identity_id || '',
                      );
                      setReason('');
                    }}
                  >
                    <span>
                      <strong>
                        {item.submitted_name || 'Имя не указано'}
                        {item.submitted_class
                          ? ` · ${item.submitted_class}`
                          : ''}
                      </strong>
                      <small>
                        {roleLabel[item.requested_role]} · {telegram(owner)} ·{' '}
                        {date(item.created_at)}
                      </small>
                    </span>
                    <span>{stateLabel[item.status] || item.status}</span>
                  </button>
                );
              })
            ) : (
              <p>Заявок пока нет.</p>
            )}
          </div>
        </section>
      )}
      {tab !== 'claims' && (
        <section className="admin-panel">
          <h2>{tab === 'admins' ? 'Администраторы' : 'Аккаунты'}</h2>
          {tab === 'admins' && (
            <p>
              Назначить администратора можно только подтверждённому
              пользователю.{' '}
              {data.can_manage_administrators
                ? 'У вас есть право управлять администраторами.'
                : 'У вас нет права управлять администраторами.'}
            </p>
          )}
          <div className="access-filters">
            <label>
              Поиск
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Имя, класс, username или ID"
              />
            </label>
            {tab === 'accounts' && (
              <>
                <label>
                  Роль
                  <select
                    value={role}
                    onChange={(e) => setRole(e.target.value)}
                  >
                    <option value="">Все роли</option>
                    {Object.entries(roleLabel).map(([key, value]) => (
                      <option key={key} value={key}>
                        {value}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Подключение
                  <select
                    value={status}
                    onChange={(e) => setStatus(e.target.value)}
                  >
                    <option value="">Все состояния</option>
                    <option value="connected">Подключён</option>
                    <option value="unconfirmed">Не подтверждён</option>
                    <option value="revoked">Доступ отозван</option>
                  </select>
                </label>
              </>
            )}
          </div>
          {tab === 'accounts' ? (
            accounts.length ? (
              <AccountTable
                accounts={accounts}
                onSelect={openAccount}
                label="Аккаунты"
              />
            ) : (
              <p>Нет подходящих аккаунтов.</p>
            )
          ) : (
            <div className="admin-list">
              <h3>Действующие администраторы</h3>
              {activeAdmins.length ? (
                <AccountTable
                  accounts={activeAdmins}
                  onSelect={openAccount}
                  label="Действующие администраторы"
                />
              ) : (
                <p>Администраторы не найдены.</p>
              )}
              {revokedAdmins.length > 0 && (
                <>
                  <h3>Администраторы с отозванным доступом</h3>
                  <AccountTable
                    accounts={revokedAdmins}
                    onSelect={openAccount}
                    label="Администраторы с отозванным доступом"
                  />
                </>
              )}
              {data.can_manage_administrators && (
                <>
                  <h3>Можно назначить</h3>
                  {candidates.length ? (
                    <AccountTable
                      accounts={candidates}
                      onSelect={openAccount}
                      label="Кандидаты в администраторы"
                    />
                  ) : (
                    <p>Нет подтверждённых кандидатов.</p>
                  )}
                </>
              )}
            </div>
          )}
        </section>
      )}
      {claim && (
        <section
          className="admin-panel access-detail"
          aria-label="Рассмотрение заявки"
        >
          <div className="admin-panel__heading">
            <h2>Заявка: {claim.submitted_name || 'имя не указано'}</h2>
            <button
              className="admin-button admin-button--quiet"
              onClick={() => setSelectedClaim('')}
            >
              Закрыть
            </button>
          </div>
          <p>
            {roleLabel[claim.requested_role]}
            {claim.submitted_class ? ` · ${claim.submitted_class}` : ''} ·{' '}
            {telegram(data.accounts.find((a) => a.id === claim.user_id))}
          </p>
          <p>
            Подана: {date(claim.created_at)} ·{' '}
            {stateLabel[claim.status] || claim.status}
            {claim.reviewed_at
              ? ` · рассмотрена ${date(claim.reviewed_at)}`
              : ''}
          </p>
          {claim.resolved_identity_name && (
            <p>
              Подтверждённая запись: {claim.resolved_identity_name}
              {claim.resolved_identity_class
                ? ` · ${claim.resolved_identity_class}`
                : ''}
            </p>
          )}
          {claim.reason && <p>Причина: {claim.reason}</p>}
          {claim.status === 'pending' && (
            <>
              {claim.candidate_ids?.length > 0 && (
                <p>
                  Возможные совпадения:{' '}
                  {data.identities
                    .filter((i) => claim.candidate_ids.includes(i.id))
                    .map(person)
                    .join('; ') || 'нет доступных записей'}
                </p>
              )}
              {identityPicker(claim.requested_role)}
              {replaceUserId && (
                <p className="admin-error-text">
                  Запись уже связана с другим аккаунтом. При подтверждении
                  потребуется согласие на перенос.
                </p>
              )}
              <div className="access-actions">
                <button
                  className="admin-button admin-button--primary"
                  disabled={busy || !identityId}
                  onClick={() => submitClaim('approve')}
                >
                  Подтвердить связь
                </button>
              </div>
              <label className="school-field">
                Причина отклонения
                <textarea
                  maxLength={500}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              <button
                className="admin-button"
                disabled={busy || !reason.trim()}
                onClick={() => submitClaim('reject')}
              >
                Отклонить заявку
              </button>
            </>
          )}
        </section>
      )}
      {account && (
        <section
          className="admin-panel access-detail"
          aria-label="Карточка аккаунта"
        >
          <div className="admin-panel__heading">
            <h2>
              {account.identity
                ? person(account.identity)
                : 'Аккаунт без школьной записи'}
            </h2>
            <button
              className="admin-button admin-button--quiet"
              onClick={() => setSelectedAccount('')}
            >
              Закрыть
            </button>
          </div>
          <p>{telegram(account)}</p>
          <p>
            {stateLabel[account.state]} ·{' '}
            {account.roles.map((r) => roleLabel[r] || r).join(', ') ||
              'Нет ролей'}
            {account.can_manage_administrators
              ? ' · Управляет администраторами'
              : ''}
          </p>
          <p>
            Подключён: {date(account.connected_at)}
            {account.last_active_at
              ? ` · Последний вход или обращение к боту: ${date(account.last_active_at)}`
              : ''}
          </p>
          {tab === 'accounts' && !mayEditAccount && (
            <p>
              Управлять этим защищённым аккаунтом может только управляющий
              администратор.
            </p>
          )}
          {tab === 'accounts' && mayEditAccount && (
            <>
              {identityPicker()}
              {replaceUserId && (
                <p className="admin-error-text">
                  Выбранная запись уже связана с другим аккаунтом.
                </p>
              )}
              <div className="access-actions">
                <button
                  className="admin-button"
                  disabled={
                    busy ||
                    !identityId ||
                    (identityId === account.identity?.id &&
                      !['pending', 'conflict'].includes(
                        account.link_status || '',
                      ))
                  }
                  onClick={() => {
                    if (confirmTransfer())
                      void write(`/api/admin/access/accounts/${account.id}`, {
                        action: 'link',
                        identity_id: identityId,
                        expected_identity_id: account.identity?.id || null,
                        ...(replaceUserId
                          ? { replace_user_id: replaceUserId }
                          : {}),
                      });
                  }}
                >
                  Сохранить привязку
                </button>
                <button
                  className="admin-button"
                  disabled={busy || account.can_manage_administrators}
                  onClick={() => {
                    const action =
                      account.state === 'revoked' ? 'restore' : 'revoke';
                    if (
                      window.confirm(
                        action === 'revoke'
                          ? 'Отозвать доступ этого аккаунта?'
                          : 'Восстановить доступ этого аккаунта?',
                      )
                    )
                      void write(`/api/admin/access/accounts/${account.id}`, {
                        action,
                      });
                  }}
                >
                  {account.state === 'revoked'
                    ? 'Восстановить доступ'
                    : 'Отозвать доступ'}
                </button>
              </div>
            </>
          )}
          {tab === 'admins' && data.can_manage_administrators && (
            <button
              className="admin-button"
              disabled={
                busy ||
                account.can_manage_administrators ||
                (!account.roles.includes('admin') &&
                  account.state !== 'connected')
              }
              onClick={() =>
                setAdminAction(
                  account.roles.includes('admin')
                    ? 'remove_admin'
                    : 'grant_admin',
                )
              }
            >
              {account.roles.includes('admin')
                ? 'Снять полномочия администратора'
                : 'Назначить администратором'}
            </button>
          )}
        </section>
      )}
      {adminAction && account && (
        <div className="school-editor-backdrop">
          <dialog
            ref={(node) => {
              if (node && !node.open) node.showModal();
            }}
            onCancel={(event) => {
              event.preventDefault();
              setAdminAction(null);
            }}
            className="school-editor-dialog access-confirm"
            aria-labelledby="access-confirm-title"
          >
            <header>
              <h2 id="access-confirm-title">
                {adminAction === 'grant_admin'
                  ? 'Назначить администратора?'
                  : 'Снять полномочия?'}
              </h2>
            </header>
            <p>
              {account.identity ? person(account.identity) : telegram(account)}
            </p>
            <p>
              {adminAction === 'grant_admin'
                ? 'Пользователь получит доступ к административной панели. Его школьные роли сохранятся.'
                : 'Пользователь потеряет доступ к административной панели. Его школьные роли сохранятся.'}
            </p>
            <div className="access-actions">
              <button
                className="admin-button"
                onClick={() => setAdminAction(null)}
              >
                Отмена
              </button>
              <button
                className="admin-button admin-button--primary"
                disabled={busy}
                onClick={() => {
                  const action = adminAction;
                  setAdminAction(null);
                  void write(`/api/admin/access/accounts/${account.id}`, {
                    action,
                  });
                }}
              >
                {adminAction === 'grant_admin'
                  ? 'Назначить'
                  : 'Снять полномочия'}
              </button>
            </div>
          </dialog>
        </div>
      )}
    </div>
  );
}
