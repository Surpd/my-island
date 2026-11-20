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
  const initial = (): LaboratoryBlueprint => ({
    id: crypto.randomUUID(),
    subject: '',
    direction: '',
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
  const [form, setForm] = useState<LaboratoryBlueprint>(
    () => props.initialMeeting || props.blueprints[0] || initial(),
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
  const multiple = (options: HTMLCollectionOf<HTMLOptionElement>) =>
    Array.from(options, (o) => o.value);
  const names = (ids: string[], people: Person[]) =>
    ids.map((id) => people.find((p) => p.id === id)?.name || id).join(', ') ||
    'никто';
  const action = async (token?: string) => {
    setBusy(true);
    try {
      const result = await props.onAction(form, active, token);
      setPreview(token && !result.conflicts?.length ? undefined : result);
      setMessage(token && !result.conflicts?.length ? 'Неделя сохранена' : '');
    } catch (error) {
      setPreview(undefined);
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
      <label>
        Заготовка
        <select
          value={form.id}
          onChange={(e) =>
            patch(
              props.blueprints.find((b) => b.id === e.target.value) ||
                initial(),
            )
          }
        >
          {!props.week && (
            <option
              value={
                props.blueprints.some((b) => b.id === form.id) ? '' : form.id
              }
            >
              Новая заготовка
            </option>
          )}
          {options(
            props.blueprints.map((b) => ({ id: b.id, name: b.subject })),
          )}
        </select>
      </label>
      {props.week && !props.blueprints.length ? (
        <p>В шаблоне этой недели нет заготовок лабораторий.</p>
      ) : (
        <>
          <fieldset disabled={busy || active}>
            <legend>
              {props.week
                ? 'Встреча на неделе ' + props.week
                : 'Заготовка шаблона'}
            </legend>
            <label>
              Направление
              <input
                value={form.direction}
                onChange={(e) => patch({ direction: e.target.value })}
              />
            </label>
            <label>
              Тема
              <input
                value={form.subject}
                onChange={(e) => patch({ subject: e.target.value })}
              />
            </label>
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
                          s.start <= e.target.value && e.target.value < s.end,
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
            <label>
              Классы в сетке
              <select
                aria-label="Классы в сетке"
                multiple
                value={form.classIds}
                onChange={(e) => {
                  const classIds = multiple(e.target.selectedOptions);
                  patch({
                    classIds,
                    ...(kind === 'classes'
                      ? { audience: { kind, class_ids: classIds } }
                      : {}),
                  });
                }}
              >
                {options(
                  props.groups.filter((g) => props.classes.includes(g.id)),
                )}
              </select>
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
            <label>
              Дополнительные преподаватели
              <select
                multiple
                value={form.teacherIds}
                onChange={(e) =>
                  patch({ teacherIds: multiple(e.target.selectedOptions) })
                }
              >
                {options(props.teachers.filter((p) => p.id !== form.teacherId))}
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
          </fieldset>
          {props.week ? (
            <button
              disabled={props.dirty || busy}
              onClick={() => void action()}
            >
              {active
                ? 'Посмотреть восстановление уроков'
                : 'Посмотреть замещение уроков'}
            </button>
          ) : (
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
          {preview && (
            <section>
              <h3>{active ? 'Восстановление' : 'Замещение'}</h3>
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
      )}
      <output>{message}</output>
    </dialog>
  );
}
