'use client';

import { useEffect, useMemo, useState } from 'react';
import { X } from 'lucide-react';

type Api = <T>(path: string, init?: RequestInit) => Promise<T>;
type ContextLesson = {
  id: string;
  subject: string;
  day: number;
  period: number;
  start?: string;
  end?: string;
  room: string;
  teacherId: string;
  teacherIds?: string[];
  classId: string;
  classIds?: string[];
  groupId: string;
  groupIds?: string[];
};
type AffectedLesson = {
  id: string;
  subject?: string;
  day?: number;
  start?: string;
  end?: string;
  group_id?: string;
  group_ids?: string[];
  group_names?: string[];
  source_cell?: string;
  old_count?: number;
  current_count?: number;
};
type ResolutionPreview = {
  preview_token: string;
  rooms?: Array<{ room: string; available: boolean }>;
  room_limitation?: string;
  audiences?: Record<
    string,
    { student_ids?: string[]; proposed_student_ids?: string[]; reason?: string }
  >;
  exceptions?: Record<
    string,
    Array<{
      id: string;
      type: string;
      reason: string;
      confirmed_at?: string;
      status?: string;
    }>
  >;
  affected_lessons?: AffectedLesson[];
  roster?: {
    old_count?: number;
    current_count?: number;
    affected_lessons?: AffectedLesson[];
  };
  actions?: string[];
  message?: string;
};
type Action =
  | 'room'
  | 'joint'
  | 'independent_practice'
  | 'defer'
  | 'revoke'
  | 'confirm_roster';
const days = [
  'Понедельник',
  'Вторник',
  'Среда',
  'Четверг',
  'Пятница',
  'Суббота',
  'Воскресенье',
];
export function ConstructorResolutionPanel({
  api,
  draftId,
  revision,
  lessons,
  issue,
  slots,
  teacherNames,
  groupNames,
  studentNames,
  dirty,
  onClose,
  onNavigate,
  onUpdated,
}: {
  api: Api;
  draftId: string;
  revision: number;
  lessons: ContextLesson[];
  issue: { lessonId: string; related?: string; type: string; category?: string; message: string };
  slots: Array<{ start: string; end: string }>;
  teacherNames: Record<string, string>;
  groupNames: Record<string, string>;
  studentNames: Record<string, string>;
  dirty: boolean;
  onClose: () => void;
  onNavigate: (id: string) => void;
  onUpdated: (draft?: unknown) => Promise<void>;
}) {
  const ids = useMemo(
    () => [
      ...new Set(
        [issue.lessonId, issue.related].filter((id): id is string =>
          Boolean(id),
        ),
      ),
    ],
    [issue.lessonId, issue.related],
  );
  const selected = lessons.filter((l) => ids.includes(l.id));
  const [preview, setPreview] = useState<ResolutionPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [room, setRoom] = useState(selected[0]?.room || '');
  const [reason, setReason] = useState('');
  const [action, setAction] = useState<Action | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const prefix = `/api/admin/schedule/constructor/${encodeURIComponent(draftId)}/resolution`;
  const [roomPreview, setRoomPreview] = useState<ResolutionPreview | null>(
    null,
  );
  const [actionIds, setActionIds] = useState(ids);
  useEffect(() => {
    let active = true;
    void Promise.resolve().then(async () => {
      if (!active) return;
      setPreview(null);
      setRoomPreview(null);
      setError('');
      if (dirty) return;
      setBusy(true);
      try {
        const [result, roomResult] = await Promise.all([
          api<ResolutionPreview>(`${prefix}/preview`, {
            method: 'POST',
            body: JSON.stringify({
              expected_revision: revision,
              lesson_ids: ids,
            }),
          }),
          api<ResolutionPreview>(`${prefix}/preview`, {
            method: 'POST',
            body: JSON.stringify({
              expected_revision: revision,
              lesson_ids: [issue.lessonId],
            }),
          }),
        ]);
        if (active) {
          setPreview(result);
          setRoomPreview(roomResult);
          setActionIds(ids);
        }
      } catch (e) {
        if (active) setError((e as Error).message);
      } finally {
        if (active) setBusy(false);
      }
    });
    return () => {
      active = false;
    };
  }, [api, prefix, revision, ids, issue.lessonId, dirty]);
  const choose = async (next: Action) => {
    setAction(next);
    setConfirmed(false);
    setError('');
    if (next === 'revoke') return;
    let targets = next === 'independent_practice' ? [issue.lessonId] : ids;
    if (next === 'confirm_roster')
      targets = (preview?.roster?.affected_lessons || []).map((l) => l.id);
    if (!targets.length) {
      setError(
        'Нет подтверждаемого списка занятий. Уточните группу и повторите проверку.',
      );
      return;
    }
    setBusy(true);
    try {
      const result = await api<ResolutionPreview>(`${prefix}/preview`, {
        method: 'POST',
        body: JSON.stringify({
          expected_revision: revision,
          lesson_ids: targets,
        }),
      });
      setPreview(result);
      setActionIds(targets);
    } catch (e) {
      setError((e as Error).message);
      setPreview(null);
      setRoomPreview(null);
    } finally {
      setBusy(false);
    }
  };
  const [exceptionId, setExceptionId] = useState<string>();
  const apply = async (override?: Action) => {
    const nextAction = override || action;
    const chosenPreview = nextAction === 'room' ? roomPreview : preview;
    if (!chosenPreview || !nextAction || (!confirmed && !override)) return;
    setBusy(true);
    setError('');
    try {
      const updated = await api(`${prefix}/apply`, {
        method: 'POST',
        body: JSON.stringify({
          expected_revision: revision,
          preview_token: chosenPreview.preview_token,
          action: nextAction,
          lesson_ids: nextAction === 'room' ? [issue.lessonId] : actionIds,
          reason,
          room,
          ...(exceptionId ? { exception_id: exceptionId } : {}),
        }),
      });
      await onUpdated(updated);
      onClose();
    } catch (e) {
      setError((e as Error).message);
      setPreview(null);
      setRoomPreview(null);
    } finally {
      setBusy(false);
    }
  };
  const suggestions = (roomPreview?.rooms || []).filter((s) => s.available);
  const exceptions = [
    ...new Map(
      Object.values(preview?.exceptions || {})
        .flat()
        .filter((e) => e.type !== 'room')
        .map((e) => [e.id, e]),
    ).values(),
  ];
  const affected =
    preview?.roster?.affected_lessons || preview?.affected_lessons || [];
  const outcome =
    action === 'joint'
      ? 'Исходные аудитории сохранятся. Преподаватель увидит одно логическое занятие. Несовместимое время, преподаватель или кабинет не позволят подтвердить совместность.'
      : action === 'independent_practice'
        ? 'Ученическое занятие сохранится как самостоятельная практика. Ответственный преподаватель останется связан с ним, но не будет считаться физически ведущим урок.'
        : action === 'confirm_roster'
          ? 'Изменятся только сохранённые составы перечисленных занятий. Школьные группы и memberships не изменяются.'
          : action === 'defer'
            ? 'Запись останется в полном опубликованном источнике как неподтверждённая. Небезопасные назначения не появятся в Campus.'
            : action === 'revoke'
              ? 'Подтверждение будет отменено, проверки выполнятся снова.'
              : '';
  return (
    <div className="constructor-conflict-backdrop">
      <dialog
        className="constructor-conflict-dialog constructor-resolution-panel"
        aria-labelledby="resolution-title"
        ref={(node) => {
          if (node && !node.open) node.showModal();
        }}
        onCancel={(e) => {
          e.preventDefault();
          if (!busy) onClose();
        }}
      >
        <header>
          <div>
            <small>Разбор проблемы · ревизия {revision}</small>
            <h2 id="resolution-title">
              {issue.type === 'room'
                ? 'Кабинет и место проведения'
                : issue.type === 'teacher'
                  ? 'Назначение преподавателя'
                  : issue.type === 'audience'
                    ? 'Проверка участников'
                    : 'Уточнение занятия'}
            </h2>
          </div>
          <button
            aria-label="Закрыть разбор проблемы"
            disabled={busy}
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </header>
        <p className="constructor-resolution-reason">{issue.message}</p>
        <div className="constructor-resolution-context">
          {selected.map((l) => (
            <article key={l.id}>
              <h3>{l.subject || 'Без предмета'}</h3>
              <p>
                {days[l.day]} ·{' '}
                {l.start || slots[l.period]?.start || 'Время не задано'}–
                {l.end || slots[l.period]?.end || '—'}
              </p>
              <dl>
                <dt>Классы и группы</dt>
                <dd>
                  {[
                    ...new Set([
                      ...(l.classIds || [l.classId]),
                      ...(l.groupIds || [l.groupId]),
                    ]),
                  ]
                    .filter(Boolean)
                    .map((id) => groupNames[id] || 'Группа требует проверки')
                    .join(', ') || 'Участники не подтверждены'}
                </dd>
                <dt>Преподаватель</dt>
                <dd>
                  {[...new Set([...(l.teacherIds || []), l.teacherId])]
                    .filter(Boolean)
                    .map(
                      (id) =>
                        teacherNames[id] || 'Преподаватель требует проверки',
                    )
                    .join(', ') || 'Не назначен'}
                </dd>
                <dt>Кабинет</dt>
                <dd>{l.room || 'Не указан'}</dd>
              </dl>
              <button onClick={() => onNavigate(l.id)}>
                Перейти к занятию
              </button>
            </article>
          ))}
        </div>
        {dirty ? (
          <output>
            Сначала сохраните изменения черновика. Решения подтверждаются для
            конкретной сохранённой ревизии. Кабинет можно исправить локально с
            undo/redo.
          </output>
        ) : busy && !preview ? (
          <output>Проверяем варианты решения…</output>
        ) : null}
        {error ? (
          <div role="alert">
            <p>{error}</p>
            <button
              disabled={busy || dirty}
              onClick={async () => {
                setError('');
                setBusy(true);
                try {
                  await onUpdated();
                } catch (refreshError) {
                  setError((refreshError as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              Перечитать актуальную ревизию
            </button>
          </div>
        ) : null}
        {issue.type === 'room' || issue.type === 'teacher' ? (
          <section>
            <h3>Исправить кабинет</h3>
            <p>
              Свободен по расписанию не означает фактическую доступность.
              Вместимость и оборудование не предполагаются.
            </p>
            {suggestions.length ? (
              <div
                className="constructor-room-suggestions"
                aria-label="Свободные по расписанию кабинеты"
              >
                {suggestions.map((s) => {
                  const name = s.room;
                  return (
                    <button
                      key={name}
                      onClick={() => setRoom(name)}
                      aria-pressed={room === name}
                    >
                      {name}
                    </button>
                  );
                })}
              </div>
            ) : (
              <p>
                {roomPreview?.room_limitation ||
                  'Подтверждённые свободные помещения пока не найдены. Можно указать кабинет вручную и проверить его.'}
              </p>
            )}
            <label>
              Новый кабинет
              <input
                value={room}
                onChange={(e) => {
                  setRoom(e.target.value);
                  setConfirmed(false);
                }}
              />
            </label>
            <label>
              Основание изменения
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
            <button
              className="is-primary"
              disabled={
                !room.trim() || !reason.trim() || busy || dirty || !roomPreview
              }
              onClick={() => void apply('room')}
            >
              Проверить и применить кабинет
            </button>
            <small>
              Изменение поддерживает undo/redo. После сохранения сервер повторно
              проверит занятость.
            </small>
          </section>
        ) : null}
        {(issue.type === 'audience' || issue.type === 'data') &&
        preview?.audiences ? (
          <section>
            <h3>Исходный и текущий состав</h3>
            {selected.map((l) => {
              const a = preview.audiences?.[l.id];
              const old = new Set(a?.student_ids || []),
                current = new Set(a?.proposed_student_ids || []);
              const added = [...current].filter((id) => !old.has(id)),
                removed = [...old].filter((id) => !current.has(id));
              return (
                <article key={l.id}>
                  <strong>{l.subject}</strong>
                  <p>
                    Сохранено: {old.size} · по текущему правилу:{' '}
                    {a?.proposed_student_ids ? current.size : 'не определено'}
                  </p>
                  {a?.reason ? <p>{a.reason}</p> : null}
                  {added.length || removed.length ? (
                    <details>
                      <summary>
                        Что изменилось: +{added.length} / −{removed.length}
                      </summary>
                      <p>
                        Добавляются:{' '}
                        {added
                          .map(
                            (id) =>
                              studentNames[id] || 'Участник требует проверки',
                          )
                          .join(', ') || 'никто'}
                      </p>
                      <p>
                        Исключаются:{' '}
                        {removed
                          .map(
                            (id) =>
                              studentNames[id] || 'Участник требует проверки',
                          )
                          .join(', ') || 'никто'}
                      </p>
                    </details>
                  ) : null}
                  <a
                    href={`/admin/people/groups?group_id=${encodeURIComponent(l.groupId || l.classId)}`}
                  >
                    Открыть справочник группы
                  </a>
                </article>
              );
            })}
          </section>
        ) : null}
        <section>
          <h3>Решение администратора</h3>
          {issue.category === 'critical' ? (
            <p role="alert">
              Критическая ошибка структуры блокирует публикацию. Исправьте день или время занятия и повторите проверку.
            </p>
          ) : <div className="constructor-resolution-actions">
            {issue.type === 'teacher' || issue.type === 'room' ? (
              <>
                <button
                  aria-pressed={action === 'joint'}
                  disabled={dirty || !preview || busy || ids.length < 2}
                  onClick={() => choose('joint')}
                >
                  Это одно совместное занятие
                </button>
                <button
                  aria-pressed={action === 'independent_practice'}
                  disabled={dirty || !preview || busy}
                  onClick={() => choose('independent_practice')}
                >
                  Самостоятельная практика без присутствия преподавателя
                </button>
              </>
            ) : null}
            {issue.type === 'audience' || issue.type === 'data' ? (
              <button
                aria-pressed={action === 'confirm_roster'}
                disabled={dirty || !preview || busy}
                onClick={() => choose('confirm_roster')}
              >
                Проверить пересчёт участников
              </button>
            ) : null}
            <button
              aria-pressed={action === 'defer'}
              disabled={dirty || !preview || busy}
              onClick={() => choose('defer')}
            >
              Отложить решение
            </button>
          </div>}
          {action && issue.category !== 'critical' ? (
            <div className="constructor-resolution-confirm">
              <h3>
                {action === 'joint'
                  ? 'Подтвердить совместное занятие'
                  : action === 'independent_practice'
                    ? 'Подтвердить самостоятельную практику'
                    : action === 'confirm_roster'
                      ? 'Пересчитать сохранённые составы'
                      : action === 'revoke'
                        ? 'Отменить исключение'
                        : 'Отложить уточнение'}
              </h3>
              <p>{outcome}</p>
              {action === 'confirm_roster' ? (
                <>
                  <p>Полный список затрагиваемых занятий: {affected.length}</p>
                  <ul>
                    {affected.map((l) => {
                      const local = selected.find((s) => s.id === l.id);
                      const displayGroups = l.group_names?.length
                        ? l.group_names
                        : [
                            ...new Set([
                              ...(local?.classIds || [local?.classId || '']),
                              ...(local?.groupIds || [local?.groupId || '']),
                            ]),
                          ]
                            .filter(Boolean)
                            .map((id) => groupNames[id])
                            .filter(Boolean);
                      const time = l.start && l.end ? `${l.start}–${l.end}` : '';
                      const where = [
                        l.day === undefined ? '' : days[l.day] || '',
                        time,
                        displayGroups.join(', '),
                      ]
                        .filter(Boolean)
                        .join(' · ');
                      const groupId = l.group_id || l.group_ids?.[0] || local?.groupId || local?.classId;
                      return (
                        <li key={l.id}>
                          <strong>{l.subject || local?.subject || 'Занятие'}</strong>
                          {where ? <span>{` · ${where}`}</span> : null}
                          {l.source_cell ? <span>{` · Источник: ${l.source_cell}`}</span> : null}
                          <span>
                            {` · ${l.old_count ?? '—'} → ${l.current_count ?? '—'} участников`}
                          </span>
                          {groupId ? (
                            <a href={`/admin/people/groups?group_id=${encodeURIComponent(groupId)}`}>
                              Открыть группу
                            </a>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                  {!affected.length ? (
                    <p>
                      Массовое подтверждение недоступно без полного списка
                      затрагиваемых занятий.
                    </p>
                  ) : null}
                </>
              ) : null}
              <label>
                Основание решения
                <textarea
                  value={reason}
                  onChange={(e) => {
                    setReason(e.target.value);
                    setConfirmed(false);
                  }}
                  rows={3}
                />
              </label>
              <label className="constructor-resolution-check">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(e) => setConfirmed(e.target.checked)}
                />
                Я проверил связанные занятия и последствия этого решения
              </label>
              {action !== 'revoke' ? (
                <button
                  className="is-primary"
                  disabled={
                    !confirmed ||
                    !reason.trim() ||
                    busy ||
                    !preview ||
                    (action === 'confirm_roster' && !affected.length)
                  }
                  onClick={() => void apply()}
                >
                  Подтвердить решение
                </button>
              ) : null}
            </div>
          ) : null}
        </section>
        {exceptions.length ? (
          <section>
            <h3>Подтверждённые исключения</h3>
            {exceptions.map((e) => (
              <article key={e.id}>
                <strong>
                  {(
                    {
                      joint: 'Совместное занятие',
                      independent_practice: 'Самостоятельная практика',
                      defer: 'Отложенное решение',
                      confirm_roster: 'Подтверждение участников',
                    } as Record<string, string>
                  )[e.type] || 'Решение администратора'}
                </strong>
                <p>{e.reason}</p>
                {e.confirmed_at ? (
                  <small>
                    Подтверждено администратором ·{' '}
                    {new Date(e.confirmed_at).toLocaleString('ru-RU')}
                  </small>
                ) : null}
                <button
                  disabled={busy}
                  onClick={() => {
                    void choose('revoke');
                    setExceptionId(e.id);
                    setReason(`Отмена: ${e.reason}`);
                  }}
                >
                  Подготовить отмену
                </button>
                {action === 'revoke' && exceptionId === e.id ? (
                  <button
                    disabled={!confirmed || busy}
                    onClick={() => void apply()}
                  >
                    Отменить это подтверждение
                  </button>
                ) : null}
              </article>
            ))}
          </section>
        ) : null}
        <footer>
          <button disabled={busy} onClick={onClose}>
            Вернуться к проблемам
          </button>
          <button disabled={busy} onClick={() => onNavigate(issue.lessonId)}>
            Исправить занятие
          </button>
        </footer>
      </dialog>
    </div>
  );
}
