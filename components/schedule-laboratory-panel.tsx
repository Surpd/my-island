'use client';
import styles from './schedule-laboratory-panel.module.css';
import { useState, useRef, useEffect } from 'react';
import type { AudienceRule } from './participants-picker';

type Person = { id: string; name: string };
export type LaboratoryBlueprint = {
  id: string;
  subject: string;
  direction: string;
  description: string;
  day: number;
  period: number;
  start: string;
  end: string;
  teacherId: string;
  teacherIds: string[];
  classIds: string[];
  room: string;
  format: string;
  audience: AudienceRule;
};
type Change = {
  subject: string;
  interval: number[];
  attending: string[];
  continuing: string[];
  teachers: string[];
  room: string;
};
export type LaboratoryPreview = {
  changes?: Change[];
  token?: string;
  conflicts?: { lesson_id: string; message: string }[];
};
type Props = {
  week?: string;
  blueprints: LaboratoryBlueprint[];
  active: Record<string, unknown>;
  days: number[];
  slots: { start: string; end: string }[];
  classes: string[];
  groups: (Person & { group_type: string })[];
  students: Person[];
  teachers: Person[];
  dirty: boolean;
  initialMeeting?: LaboratoryBlueprint;
  onLesson: (id: string, title: string, meeting: LaboratoryBlueprint) => void;
  onClose: () => void;
  onSave(value: LaboratoryBlueprint): void;
  onAction(
    meeting: LaboratoryBlueprint,
    deactivate: boolean,
    token?: string,
  ): Promise<LaboratoryPreview>;
};
const days = [
  'Понедельник',
  'Вторник',
  'Среда',
  'Четверг',
  'Пятница',
  'Суббота',
  'Воскресенье',
];
const time = (minutes: number) =>
  [Math.floor(minutes / 60), minutes % 60]
    .map((n) => String(n).padStart(2, '0'))
    .join(':');
export function LaboratoryPanel(props: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  const directions = ['Текст и смысл', 'Прошлое и настоящее', 'Мир вокруг нас'];
  const initial = (direction: string, index: number): LaboratoryBlueprint => ({
    id: 'school-lab-' + index,
    subject: direction,
    direction,
    description: '',
    day: props.days[0],
    period: 0,
    start: props.slots[0]?.start || '',
    end: props.slots[0]?.end || '',
    teacherId: '',
    teacherIds: [],
    classIds: [],
    room: '',
    format: '',
    audience: { kind: 'classes', class_ids: [] },
  });
  const legacy = new Map(props.blueprints.map((b) => [b.id, b]));
  const defaults = directions.map((direction, i) => {
    const candidates = [...legacy.values()];
    const blueprint =
      legacy.get('school-lab-' + i) ||
      candidates.find((b) => b.direction === direction) ||
      candidates.find(
        (b) => b.subject === direction && !directions.includes(b.direction),
      );
    if (blueprint) legacy.delete(blueprint.id);
    return blueprint || initial(direction, i);
  });
  const [missingFrom, setMissingFrom] = useState<LaboratoryBlueprint>();
  const [editing, setEditing] = useState(Boolean(props.initialMeeting));
  const [form, setForm] = useState<LaboratoryBlueprint>(
    () => props.initialMeeting || defaults[0],
  );
  const [preview, setPreview] = useState<LaboratoryPreview>();
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const active = Boolean(props.active[form.id]);
  const patch = (value: Partial<LaboratoryBlueprint>) => {
    setForm({ ...form, ...value });
    setPreview(undefined);
    setMessage('');
  };
  const selectClasses = (classIds: string[]) =>
    patch({
      classIds,
      ...(form.audience.kind === 'classes'
        ? { audience: { kind: 'classes', class_ids: classIds } }
        : {}),
    });
  const multiple = (options: HTMLCollectionOf<HTMLOptionElement>) =>
    Array.from(options, (o) => o.value);
  const names = (ids: string[], people: Person[]) =>
    ids
      .map((id) => people.find((p) => p.id === id)?.name || 'Нет в справочнике')
      .join(', ') || 'никто';
  const action = async (token?: string, meeting = form) => {
    setBusy(true);
    try {
      const result = await props.onAction(
        meeting,
        Boolean(props.active[meeting.id]),
        token,
      );
      setPreview(token && !result.conflicts?.length ? undefined : result);
      if (token && !result.conflicts?.length) setEditing(false);
      setMessage(token && !result.conflicts?.length ? 'Неделя сохранена' : '');
    } catch (error) {
      setPreview(undefined);
      setMissingFrom(undefined);
      setEditing(true);
      setMessage(String((error as Error).message));
    } finally {
      setBusy(false);
    }
  };
  const options = (people: Person[]) =>
    people.map((p) => (
      <option key={p.id} value={p.id}>
        {p.name}
      </option>
    ));
  const kind = form.audience.kind;
  const selector =
    kind === 'students'
      ? 'student_ids'
      : kind === 'groups'
        ? 'group_ids'
        : 'class_ids';
  const renderRow = (row: LaboratoryBlueprint, label: string) => {
    const enabled = Boolean(props.active[row.id]);
    return (
      <article key={row.id}>
        <strong>{label}</strong>
        {props.week ? (
          <button
            role="switch"
            aria-checked={enabled}
            aria-label={label}
            disabled={busy || props.dirty}
            onClick={() => {
              patch(row);
              const missing =
                !row.teacherId ||
                !row.classIds.length ||
                !row.start ||
                !row.end;
              setMissingFrom(missing ? row : undefined);
              setEditing(missing);
              if (enabled || !missing) void action(undefined, row);
            }}
          >
            {enabled ? 'Включена' : 'Выключена'}
          </button>
        ) : (
          <button
            onClick={() => {
              patch(row);
              setEditing(true);
            }}
          >
            Настроить
          </button>
        )}
      </article>
    );
  };
  return (
    <dialog
      ref={dialog}
      className={styles.dialog}
      aria-label="Лаборатории"
      onCancel={props.onClose}
    >
      <header>
        <h2>Лаборатории</h2>
        <button onClick={props.onClose}>Закрыть</button>
      </header>
      {defaults.map((row, i) => renderRow(row, directions[i]))}
      {legacy.size > 0 && (
        <details>
          <summary>Другие сохранённые заготовки</summary>
          {[...legacy.values()].map((row) =>
            renderRow(row, row.direction || row.subject),
          )}
        </details>
      )}
      <>
        {editing && (
          <fieldset disabled={busy || active}>
            <legend>
              {props.week
                ? 'Встреча на неделе ' + props.week
                : 'Заготовка шаблона'}{' '}
              · {form.direction || form.subject}
            </legend>
            {(!missingFrom || !missingFrom.start || !missingFrom.end) && (
              <>
                <label>
                  День
                  <select
                    value={form.day}
                    onChange={(e) => patch({ day: Number(e.target.value) })}
                  >
                    {props.days.map((d) => (
                      <option key={d} value={d}>
                        {days[d]}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Начало
                  <input
                    type="time"
                    value={form.start}
                    onChange={(e) =>
                      patch({
                        start: e.target.value,
                        period: Math.max(
                          0,
                          props.slots.findIndex(
                            (s) =>
                              s.start <= e.target.value &&
                              e.target.value < s.end,
                          ),
                        ),
                      })
                    }
                  />
                </label>
                <label>
                  Окончание
                  <input
                    type="time"
                    value={form.end}
                    onChange={(e) => patch({ end: e.target.value })}
                  />
                </label>
              </>
            )}
            {(!missingFrom || !missingFrom.classIds.length) && (
              <div>
                <label>
                  Классы
                  <select
                    aria-label="Классы"
                    value=""
                    onChange={(e) =>
                      selectClasses([...form.classIds, e.target.value])
                    }
                  >
                    <option value="" disabled>
                      Добавить класс
                    </option>
                    {options(
                      props.groups.filter(
                        (g) =>
                          props.classes.includes(g.id) &&
                          !form.classIds.includes(g.id),
                      ),
                    )}
                  </select>
                </label>
                {form.classIds.map((id) => {
                  const name =
                    props.groups.find((g) => g.id === id)?.name ||
                    'Класс вне справочника';
                  return (
                    <button
                      key={id}
                      aria-label={'Убрать ' + name}
                      onClick={() =>
                        selectClasses(
                          form.classIds.filter((selected) => selected !== id),
                        )
                      }
                    >
                      {name} ×
                    </button>
                  );
                })}
              </div>
            )}
            {(!missingFrom || !missingFrom.teacherId) && (
              <label>
                Ведущий
                <select
                  aria-label="Ведущий"
                  value={form.teacherId}
                  onChange={(e) => patch({ teacherId: e.target.value })}
                >
                  <option value="">Выберите преподавателя</option>
                  {options(props.teachers)}
                </select>
              </label>
            )}
            <details>
              <summary>Дополнительные настройки</summary>
              <label>
                Тема
                <input
                  value={form.subject}
                  onChange={(e) => patch({ subject: e.target.value })}
                />
              </label>
              <label>
                Участники
                <select
                  value={kind}
                  onChange={(e) =>
                    patch({
                      audience: {
                        kind: e.target.value as AudienceRule['kind'],
                        ...(e.target.value === 'classes'
                          ? { class_ids: form.classIds }
                          : {}),
                      },
                    })
                  }
                >
                  <option value="classes">Выбранные классы</option>
                  <option value="groups">Учебные группы</option>
                  <option value="students">Отдельные ученики</option>
                </select>
              </label>
              {kind !== 'classes' && (
                <select
                  aria-label="Состав лаборатории"
                  multiple
                  value={form.audience[selector] || []}
                  onChange={(e) =>
                    patch({
                      audience: {
                        kind,
                        [selector]: multiple(e.target.selectedOptions),
                      },
                    })
                  }
                >
                  {options(
                    kind === 'students'
                      ? props.students
                      : props.groups.filter((g) => g.group_type !== 'class'),
                  )}
                </select>
              )}
              <label>
                Дополнительные преподаватели
                <select
                  multiple
                  value={form.teacherIds}
                  onChange={(e) =>
                    patch({ teacherIds: multiple(e.target.selectedOptions) })
                  }
                >
                  {options(
                    props.teachers.filter((p) => p.id !== form.teacherId),
                  )}
                </select>
              </label>
              <label>
                Формат
                <select
                  value={form.format}
                  onChange={(e) => patch({ format: e.target.value })}
                >
                  <option value="">По режиму дня</option>
                  <option value="offline">Очно</option>
                  <option value="online">Онлайн</option>
                </select>
              </label>
              <label>
                Кабинет или площадка
                <input
                  value={form.room}
                  onChange={(e) => patch({ room: e.target.value })}
                />
              </label>
              <label>
                Описание
                <textarea
                  value={form.description}
                  onChange={(e) => patch({ description: e.target.value })}
                />
              </label>
            </details>
          </fieldset>
        )}
        {props.week
          ? editing && (
              <button
                disabled={
                  props.dirty ||
                  busy ||
                  !form.classIds.length ||
                  !form.teacherId ||
                  !form.start ||
                  form.end <= form.start
                }
                onClick={() => void action()}
              >
                {active
                  ? 'Посмотреть восстановление уроков'
                  : 'Посмотреть замещение уроков'}
              </button>
            )
          : editing && (
              <button
                disabled={
                  !form.subject.trim() ||
                  !form.teacherId ||
                  !form.classIds.length ||
                  form.end <= form.start
                }
                onClick={() => {
                  props.onSave(form);
                  props.onClose();
                }}
              >
                Сохранить заготовку
              </button>
            )}
        {props.dirty && props.week && (
          <p>Сначала сохраните недельный черновик.</p>
        )}
        {props.week && preview && !editing && !active && (
          <button
            onClick={() => {
              setMissingFrom(undefined);
              setEditing(true);
            }}
          >
            Изменить встречу
          </button>
        )}
        {preview && (
          <section>
            <h3>
              {active ? 'Восстановление' : 'Замещение'} · {form.subject}
            </h3>
            {preview.changes?.length ? (
              preview.changes.map((r, i) => (
                <article key={i}>
                  <strong>
                    {r.subject} · {time(r.interval[0])}–{time(r.interval[1])}
                  </strong>
                  <p>
                    На лаборатории — {names(r.attending, props.students)}. На
                    уроке — {names(r.continuing, props.students)}.
                  </p>
                  <p>
                    {names(r.teachers, props.teachers)} · {r.room} ·{' '}
                    {r.continuing.length
                      ? 'занятие продолжается'
                      : 'время освобождается'}
                  </p>
                </article>
              ))
            ) : (
              <p>Обычные уроки не затронуты.</p>
            )}
            {preview.conflicts?.map((c, i) => (
              <p key={i}>
                {c.message}{' '}
                <button
                  onClick={() => props.onLesson(c.lesson_id, c.message, form)}
                >
                  Открыть занятие
                </button>
              </p>
            ))}
            {preview.token && (
              <button
                disabled={busy || props.dirty}
                onClick={() => void action(preview.token)}
              >
                {active
                  ? 'Отключить и восстановить уроки'
                  : 'Провести на этой неделе'}
              </button>
            )}
          </section>
        )}
      </>
      <output>{message}</output>
    </dialog>
  );
}
