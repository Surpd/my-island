import { useEffect, useState } from 'react';

type RecordFields = Record<string, unknown>;
type Entry = { actor_id?: string; changed_at: string; changed_fields: string[] };
type Props = {
  api: <T>(path: string, init?: RequestInit) => Promise<T>;
  historyPath: string;
  base: RecordFields;
  draft: RecordFields;
  remote: RecordFields;
  fields: Record<string, string>;
  apply: (fields: RecordFields) => void;
};

const equal = (left: unknown, right: unknown) => JSON.stringify(left ?? '') === JSON.stringify(right ?? '');
function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'Пусто';
  if (Array.isArray(value)) return value.map(display).join(', ') || 'Пусто';
  return typeof value === 'string' ? value : JSON.stringify(value) ?? 'Пусто';
}

export function DirectoryConflict({ api, historyPath, base, draft, remote, fields, apply }: Props) {
  const [choices, setChoices] = useState<Record<string, 'local' | 'remote'>>({});
  const [history, setHistory] = useState<Entry[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    void api<{ items: Entry[] }>(historyPath, { signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted) setHistory(result.items);
    }).catch(() => {});
    return () => controller.abort();
  }, [api, historyPath, remote.revision]);
  const changed = Object.keys(fields).filter((key) => !equal(base[key], remote[key]));
  const conflicts = changed.filter((key) => !equal(base[key], draft[key]) && !equal(remote[key], draft[key]));
  const reconcile = () => {
    const merged: RecordFields = { ...draft, ...remote };
    for (const key of Object.keys(fields)) {
      if (!changed.includes(key) || choices[key] === 'local') merged[key] = draft[key];
    }
    apply(merged);
  };
  return <section aria-label="Изменения другого администратора">
    <p>Запись изменилась, пока форма была открыта. Проверьте новую версию перед сохранением.</p>
    {changed.map((key) => {
      const tracked = key === 'comment' ? 'admin_note' : key === 'default_teacher_identity_id' ? 'assignments' : key;
      const latest = history.find((entry) => entry.changed_fields.includes(tracked));
      return <fieldset key={key}><legend>{fields[key]}</legend>
        {latest && <small>{latest.actor_id ? `Администратор ${latest.actor_id}` : 'Системное изменение'} · {new Date(latest.changed_at).toLocaleString('ru-RU')}</small>}
        {conflicts.includes(key) ? (['local', 'remote'] as const).map((side) =>
          <label key={side}><input type="radio" name={`change-${key}`} checked={choices[key] === side}
            onChange={() => setChoices((old) => ({ ...old, [key]: side }))} />
            {side === 'local' ? 'Моё значение' : 'На сервере'} · {display((side === 'local' ? draft : remote)[key])}</label>)
          : <p>{display(remote[key])}</p>}
      </fieldset>;
    })}
    {!changed.length && <p>Изменились связанные данные карточки.</p>}
    <button type="button" className="admin-button" disabled={conflicts.some((key) => !choices[key])} onClick={reconcile}>
      Применить выбор к форме
    </button>
  </section>;
}
