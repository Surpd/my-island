import { useEffect, useRef, useState } from 'react'
import {
  ArrowDownLeft, ArrowUpRight, ChevronRight, CircleAlert, Clock3, ExternalLink,
  FolderOpen, LoaderCircle, MapPin, Users, Video,
} from 'lucide-react'
import './interior.css'

const student = 'Александра Волкова'
const teacher = 'Мария Лебедева'
const weekdays = [
  ['Пн', '23', 'Понедельник'], ['Вт', '24', 'Вторник'], ['Ср', '25', 'Среда'],
  ['Чт', '26', 'Четверг'], ['Пт', '27', 'Пятница'], ['Сб', '28', 'Суббота'], ['Вс', '29', 'Воскресенье'],
]

const studentWeek = [
  [['09:00', '09:45', 'Русский язык', '214', 'Анна Белова', '8Б', 'Урок']],
  [
    ['09:00', '09:45', 'Русский язык', '214', 'Анна Белова', '8Б', 'Урок'],
    ['09:55', '10:40', 'Алгебра', '305', teacher, '8Б · математика', 'Урок'],
    ['10:55', '11:40', 'Физика', '207', 'Илья Орлов', '8Б', 'Лабораторная'],
    ['12:00', '12:45', 'Информатика', '', 'Ольга Миронова', '8Б · группа 2', 'Практикум', { mode: 'online' }],
    ['14:00', '14:30', 'Геометрия', '305', teacher, '8Б', 'Консультация'],
    ['15:10', '16:00', 'Школьная робототехника', '112', 'Илья Орлов', '8–9 классы', 'Кружок'],
  ],
  [['09:00', '09:45', 'История', '212', 'Павел Соколов', '8Б', 'Урок']],
  [['10:55', '11:40', 'Геометрия', '305', teacher, '8Б · математика', 'Урок']],
  [['13:00', '14:00', 'Ярмарка проектов', 'Актовый зал', '', '8–9 классы', 'Школьное событие']],
  [], [],
]

const teacherWeek = [
  [['09:00', '09:45', 'Геометрия', '305', '', '8А · математика', 'Урок']],
  [
    ['09:00', '09:45', 'Геометрия', '305', '', '8А · математика', 'Урок'],
    ['09:55', '10:40', 'Алгебра', '305', '', '8Б · математика', 'Урок'],
    ['10:40', '11:50', 'Окно', '', '', '', 'Окно'],
    ['11:50', '12:35', 'Алгебра', '305', '', '9А · математика', 'Урок'],
    ['14:00', '14:30', 'Геометрия', '305', '', '8Б · математика', 'Консультация'],
  ],
  [['09:55', '10:40', 'Алгебра', '305', '', '8А · математика', 'Урок']],
  [['10:55', '11:40', 'Геометрия', '305', '', '8Б · математика', 'Урок']],
  [['13:00', '14:00', 'Ярмарка проектов', 'Актовый зал', '', '8–9 классы', 'Школьное событие']],
  [], [],
]

const homeworkItems = [
  { external_coursework_id: 'hw-1', title: 'Системы линейных уравнений', course_title: 'Алгебра', due_at: '2026-11-24T18:00:00+03:00', description: 'Решите задачи 152–154. В первом примере запишите оба способа решения: подстановкой и сложением.' },
  { external_coursework_id: 'hw-2', title: 'Отчёт по лабораторной работе', course_title: 'Физика', due_at: '2026-11-23T20:00:00+03:00', description: 'Добавьте таблицу измерений и вывод о связи силы тока с напряжением.' },
  { external_coursework_id: 'hw-3', title: 'Таблицы и формулы', course_title: 'Информатика', due_at: '2026-11-25T17:00:00+03:00', alternate_link: 'https://classroom.google.com/', description: 'Подготовьте таблицу расходов и формулы для подсчёта итоговой суммы.' },
  { external_coursework_id: 'hw-4', title: 'Причастный оборот', course_title: 'Русский язык', due_at: '2026-11-20T18:00:00+03:00', max_points: 10, description: 'Выпишите пять примеров причастных оборотов и объясните расстановку запятых.' },
  { external_coursework_id: 'hw-5', title: 'Признаки равенства треугольников', course_title: 'Геометрия', due_at: null, description: 'Решите задачи 101 и 104. Отметьте равные элементы на чертежах.' },
]

const homeworkSubmissions = [
  { external_coursework_id: 'hw-1', state: 'CREATED' },
  { external_coursework_id: 'hw-2', state: 'RECLAIMED_BY_STUDENT' },
  { external_coursework_id: 'hw-3', state: 'TURNED_IN', late: false },
  { external_coursework_id: 'hw-4', state: 'RETURNED', late: true, assigned_grade: 8 },
]

const demoNow = new Date('2026-11-24T10:05:00+03:00')
const dueFormat = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'Europe/Moscow', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit',
})

function joinHomework(items, submissions) {
  const submissionByWork = new Map()
  for (const submission of submissions) {
    const id = submission.external_coursework_id
    if (!id) continue
    submissionByWork.set(id, submissionByWork.has(id) ? null : submission)
  }

  const itemCounts = new Map()
  for (const item of items) {
    const id = item.external_coursework_id
    if (id) itemCounts.set(id, (itemCounts.get(id) ?? 0) + 1)
  }

  return items.flatMap((item) => {
    const id = item.external_coursework_id
    if (itemCounts.get(id) !== 1) return []
    const submission = submissionByWork.get(id)
    const dueAt = item.due_at ? new Date(item.due_at) : null
    const hasDeadline = dueAt && Number.isFinite(dueAt.valueOf())
    const state = submission?.state
    const completed = state === 'TURNED_IN' || state === 'RETURNED'
    const overdue = Boolean(state && hasDeadline && dueAt < demoNow && !completed)
    return [{
      id, name: item.title, subject: item.course_title, body: item.description,
      due: hasDeadline ? dueFormat.format(dueAt) : 'Без срока',
      url: item.alternate_link, state, completed, overdue,
      late: completed && submission?.late === true,
      grade: submission?.assigned_grade, max: item.max_points,
    }]
  })
}

const homework = joinHomework(homeworkItems, homeworkSubmissions)

const submissionText = {
  NEW: 'Не начато', CREATED: 'В работе', RECLAIMED_BY_STUDENT: 'Отозвано',
  TURNED_IN: 'Сдано', RETURNED: 'Возвращено',
}

const announcements = [
  { id: 'assembly', type: 'Изменение', day: '24 ноября', title: 'Лаборатория по физике в кабинете 207', text: 'Сегодня лабораторная работа 8Б пройдёт в кабинете 207. Начало в 10:55.', role: 'student' },
  { id: 'grades', type: 'Для преподавателей', day: '24 ноября', title: 'Закрытие работ за ноябрь', text: 'До 30 ноября внесите вручную результаты работ, которые проводились вне Classroom. Отдельно проверьте незаполненные результаты.', role: 'teacher' },
  { id: 'fair', type: 'Школьное событие', day: '23 ноября', title: 'Ярмарка школьных проектов', text: '27 ноября в 13:00 команды 8–9 классов представят проекты в актовом зале. Сбор участников в 12:45.', role: 'all' },
  { id: 'library', type: 'Школьная новость', day: '20 ноября', title: 'Библиотека открыта до 17:00', text: 'В эту пятницу читальный зал открыт до 17:00. Учебники можно вернуть на стойке у входа.', role: 'all' },
]

function Tabs({ items, selected, onChange, label }) {
  return <fieldset className="ci-tabs" aria-label={label}>
    {items.map(([id, name]) => <button key={id} type="button" aria-pressed={selected === id} onClick={() => onChange(id)}>{name}</button>)}
  </fieldset>
}

function Row({ title, children, end, onClick }) {
  const content = <><span className="ci-row-copy"><strong>{title}</strong>{children && <span className="ci-meta">{children}</span>}</span>
    {end && <span className="ci-end">{end}</span>}{onClick && <ChevronRight aria-hidden="true" size={17} />}</>
  return onClick
    ? <button type="button" className="ci-row ci-row-button" onClick={onClick}>{content}</button>
    : <div className="ci-row">{content}</div>
}

function Empty({ icon: Icon = FolderOpen, title, children }) {
  return <span className="ci-empty"><Icon size={25} aria-hidden="true" /><span><strong>{title}</strong>{children && <span className="ci-meta">{children}</span>}</span></span>
}

function Detail({ title, subtitle, children, onBack }) {
  const heading = useRef(null)
  useEffect(() => {
    heading.current?.focus({ preventScroll: true })
  }, [title])

  return <section className="ci-detail">
    <button className="ci-back" type="button" onClick={onBack}>
      <ArrowDownLeft size={17} aria-hidden="true" /> Назад к списку
    </button>
    <h3 tabIndex={-1} ref={heading}>{title}</h3>
    {subtitle && <p className="ci-meta">{subtitle}</p>}
    {children}
  </section>
}

function External({ children, url = 'https://classroom.google.com/' }) {
  return <a className="ci-action" href={url} target="_blank" rel="noreferrer">
    {children}<ExternalLink size={15} aria-hidden="true" />
  </a>
}

function onlineMeetingUrl(value) {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}

function Schedule({ role, detail, open, back }) {
  const [mode, setMode] = useState('day')
  const [day, setDay] = useState(1)
  const week = role === 'teacher' ? teacherWeek : studentWeek
  const next = role === 'teacher' ? 3 : 2

  if (detail) {
    const [start, end, title, room, person, group, kind, delivery] = detail.event
    const meetingUrl = onlineMeetingUrl(delivery?.url)
    return <Detail title={title} subtitle={kind} onBack={back}>
      <dl className="ci-facts">
        <div><dt>Дата</dt><dd>{weekdays[detail.index][1]} ноября</dd></div>
        <div><dt>Время</dt><dd>{start}–{end}</dd></div>
        {room && <div><dt>Место</dt><dd>{room}</dd></div>}
        {group && <div><dt>Группа</dt><dd>{group}</dd></div>}
        {person && <div><dt>Преподаватель</dt><dd>{person}</dd></div>}
        {delivery?.mode === 'online' && <div><dt>Формат</dt><dd>Онлайн</dd></div>}
      </dl>
      {delivery?.mode === 'online' && <>
        {meetingUrl
          ? <External url={meetingUrl}>Открыть встречу</External>
          : <p className="ci-meta">Ссылка на встречу не указана.</p>}
      </>}
    </Detail>
  }

  const renderDay = (index) => <section key={index} className="ci-schedule-day">
    <h3>{weekdays[index][2]}, {weekdays[index][1]} ноября</h3>
    {week[index].length ? <div className="ci-agenda">
      {week[index].map((event, position) => {
        const [start, end, title, room, person, group, kind, delivery] = event
        const mark = index === 1 ? position === 1 ? 'По расписанию сейчас' : position === next ? 'Следующее занятие' : '' : ''
        const meta = [kind === 'Урок' || kind === title ? '' : kind, group, person].filter(value => typeof value === 'string').filter(Boolean).join(' · ')
        return <button type="button" key={start} className="ci-event" onClick={() => open({ event, index })}>
          <span className="ci-time"><strong>{start}</strong><small>{end}</small></span>
          <span className="ci-row-copy">
            {mark && <span className="ci-mark">{mark}</span>}
            <strong>{title}</strong>
            {meta && <span className="ci-meta">{meta}</span>}
            {room && <span className="ci-meta ci-place"><MapPin size={13} aria-hidden="true" />{room}</span>}
            {delivery?.mode === 'online' && <span className="ci-meta ci-place"><Video size={13} aria-hidden="true" />Онлайн</span>}
          </span>
          <ChevronRight size={17} aria-hidden="true" />
        </button>
      })}
    </div> : <Empty icon={Clock3} title="Занятий нет" />}
  </section>

  return <>
    <div className="ci-toolbar"><span className="ci-meta">23–29 ноября 2026</span><Tabs items={[[ 'day', 'День' ], [ 'week', 'Неделя' ]]} selected={mode} onChange={setMode} label="Вид расписания" /></div>
    {mode === 'day' && <fieldset className="ci-days" aria-label="День недели">
      {weekdays.map(([short, date, full], index) => <button type="button" key={date} aria-label={`${full}, ${date} ноября`} aria-pressed={day === index} onClick={() => setDay(index)}><span>{short}</span><strong>{date}</strong></button>)}
    </fieldset>}
    {mode === 'day' ? renderDay(day) : weekdays.map((_, index) => renderDay(index))}
  </>
}

function Homework({ detail, open, back }) {
  const [filter, setFilter] = useState('open')
  if (detail) {
    return <Detail title={detail.name} subtitle={detail.subject} onBack={back}>
      <dl className="ci-facts">
        <div><dt>Срок сдачи</dt><dd>{detail.due}</dd></div>
        <div><dt>Сдача</dt><dd>{submissionText[detail.state] || 'Статус неизвестен'}</dd></div>
        {detail.overdue && <div><dt>Срок</dt><dd>Просрочено</dd></div>}
        {detail.late && <div><dt>Срок</dt><dd>Сдано после срока</dd></div>}
        {Number.isFinite(detail.grade) && <div><dt>Баллы Classroom</dt><dd>{formatResult({ value: detail.grade, max: detail.max })}</dd></div>}
      </dl>
      <p>{detail.body}</p>
      {detail.url
        ? <External url={detail.url}>Открыть Google Classroom</External>
        : <p className="ci-meta">Ссылка задания недоступна в демо.</p>}
    </Detail>
  }
  const list = homework.filter((item) =>
    filter === 'done' ? item.completed : filter === 'late' ? item.overdue : !item.completed)
  return <>
    <Tabs items={[[ 'open', 'К сдаче' ], [ 'late', 'Просрочено' ], [ 'done', 'Сдано' ]]} selected={filter} onChange={setFilter} label="Статус заданий" />
    {list.length ? <div className="ci-list">{list.map((item) => <Row key={item.id} title={item.name}
      end={<span className={item.overdue ? 'ci-mark ci-alert' : 'ci-mark'}>{item.overdue ? 'Просрочено' : item.late ? 'Сдано после срока' : submissionText[item.state] || 'Статус неизвестен'}</span>}
      onClick={() => open(item)}>{item.subject} · {item.due}</Row>)}</div> : <Empty title="В этом разделе пока нет заданий" />}
  </>
}

const subjects = [
  { id: 'algebra', name: 'Алгебра', term: 'Текущий период', summaries: {
    current: [
      { summary_type: 'term_grade', label: 'Оценка за период', numeric_value: 4 },
      { summary_type: 'total_points', label: 'Баллы за период', numeric_value: 33 },
      { summary_type: 'admission', label: 'Допуск', text_value: 'Допущена' },
    ],
    archive: [{ summary_type: 'term_grade', label: 'Оценка за период', numeric_value: 5 }],
  }, work: [
    { title: 'Линейные уравнения', date: '23 ноября', value: 16, max: 20, weight: 2 },
    { title: 'Проверочная по многочленам', date: '16 ноября', value: '5', weight: '1' },
    { title: 'Квадратные корни', date: '12 ноября', status: 'Не был', retakeAssigned: true },
    { title: 'Преобразование выражений', date: '18 ноября', value: 12, max: 20, status: 'Пересдача' },
    { title: 'Самостоятельная по степеням', date: '14 ноября', value: 0, max: 5 },
    { title: 'Тренировка перед контрольной', date: '24 ноября', value: '' },
  ], past: [{ title: 'Входная контрольная', date: '14 октября', value: '18', max: '20', weight: '2' }] },
  { id: 'physics', name: 'Физика', term: 'Текущий период', summaries: {
    current: [{ summary_type: 'term_grade', label: 'Оценка за период', numeric_value: 5 }],
    archive: [{ summary_type: 'term_grade', label: 'Оценка за период', numeric_value: 4 }],
  }, work: [
    { title: 'Лабораторная по закону Ома', date: '19 ноября', status: 'Зачёт' },
    { title: 'Сила тока и напряжение', date: '13 ноября', value: '9', max: '10', weight: '1' },
  ], past: [{ title: 'Измерение плотности', date: '9 октября', value: '4', weight: '2' }] },
  { id: 'russian', name: 'Русский язык', term: 'Текущий период', summaries: {
    current: [{ summary_type: 'term_grade', label: 'Оценка за период', numeric_value: 4 }],
    archive: [{ summary_type: 'term_grade', label: 'Оценка за период', numeric_value: 4 }],
  }, work: [
    { title: 'Диктант с грамматическим заданием', date: '20 ноября', value: '4', weight: '2' },
  ], past: [{ title: 'Изложение', date: '12 октября', value: '4', weight: '2' }] },
]

const groups = [
  { id: '8b', name: '8Б · математика', people: [
    { id: 'a', name: student, result: '16' }, { id: 'l', name: 'Лев Громов', result: '19' },
    { id: 'k', name: 'Кира Смирнова', result: '' }, { id: 'd', name: 'Даниил Лесков', result: '12' },
  ], tasks: [
    { id: 'eq', name: 'Линейные уравнения', date: '23 ноября', max: '20', weight: '2' },
    { id: 'pol', name: 'Проверочная по многочленам', date: '16 ноября', max: '5', weight: '1', scores: ['5','4','4','3'] },
  ] },
  { id: '8a', name: '8А · математика', people: [
    { id: 'm', name: 'Мирон Рощин', result: '18' }, { id: 'o', name: 'Олеся Снегирёва', result: '' },
    { id: 'v', name: 'Вера Луговая', result: '15' },
  ], tasks: [{ id: 'tri', name: 'Равенство треугольников', date: '23 ноября', max: '20', weight: '2' }] },
  { id: '9a', name: '9А · математика', people: [
    { id: 'n', name: 'Арина Новикова', result: '9' }, { id: 's', name: 'Сергей Ильин', result: '8' },
  ], tasks: [{ id: 'func', name: 'Квадратичная функция', date: '20 ноября', max: '10', weight: '1' }] },
]

function formatResult(record) {
  const score = record.value != null && record.value !== '' ? record.value : null
  const result = score == null ? '' : record.max > 0 ? `${score} / ${record.max}` : String(score)
  const status = record.status?.toLowerCase() === 'graded' ? '' : record.status
  return [result, status].filter(Boolean).join(' · ') || 'Нет результата'
}

function Grades({ detail, open, back }) {
  const [period, setPeriod] = useState('current')
  const [onlyDebt, setOnlyDebt] = useState(false)
  if (detail?.record) {
    const { subject, record } = detail
    return <Detail title={record.title} subtitle={subject.name} onBack={back}>
      <dl className="ci-facts">
        <div><dt>Результат</dt><dd>{formatResult(record)}</dd></div>
        <div><dt>Дата работы</dt><dd>{record.date}</dd></div>
        {record.max && <div><dt>Максимум</dt><dd>{record.max}</dd></div>}
        {record.weight && <div><dt>Вес</dt><dd>{record.weight}</dd></div>}
        {record.retakeAssigned && <div><dt>Пересдача</dt><dd>Назначена в демо</dd></div>}
      </dl>
    </Detail>
  }
  if (detail?.subject) {
    const { subject } = detail
    const records = period === 'current' ? subject.work : subject.past
    return <Detail title={subject.name} subtitle={period === 'current' ? 'II триместр · 2026/27' : 'I триместр · 2026/27'} onBack={back}>
      <dl className="ci-facts">
        {subject.summaries[period].map((summary) => <div key={summary.summary_type}>
          <dt>{summary.label}</dt><dd>{summary.numeric_value ?? summary.text_value}</dd>
        </div>)}
        {period === 'current' && <div><dt>Долги к пересдаче</dt><dd>{records.filter((record) => record.retakeAssigned).length}</dd></div>}
      </dl>
      <h4>Работы</h4>
      <div className="ci-list">{records.map((record) => <Row key={record.title} title={record.title} end={formatResult(record)} onClick={() => open({ subject, record })}>
        {[record.date, record.weight && `вес ${record.weight}`, record.retakeAssigned && 'назначена пересдача'].filter(Boolean).join(' · ')}
      </Row>)}</div>
    </Detail>
  }
  const list = onlyDebt && period === 'current' ? subjects.filter((subject) => subject.work.some((record) => record.retakeAssigned)) : subjects
  return <>
    <Tabs items={[[ 'current', 'II триместр' ], [ 'archive', 'Архив · I триместр' ]]} selected={period} onChange={setPeriod} label="Период оценок" />
    {period === 'current' && <label className="ci-check"><input type="checkbox" checked={onlyDebt} onChange={(event) => setOnlyDebt(event.target.checked)} /> С задолженностями</label>}
    <div className="ci-list">{list.map((subject) => {
      const debt = subject.work.filter((record) => record.retakeAssigned).length
      return <Row key={subject.id} title={subject.name} end={<span className="ci-score">{subject.summaries[period].find((summary) => summary.summary_type === 'term_grade')?.numeric_value ?? '—'}</span>} onClick={() => open({ subject })}>
        {period === 'current' && debt ? `${debt} работа к пересдаче` : 'Оценка за период'}
      </Row>
    })}</div>
  </>
}

function groupScore(group, task, person) {
  return (task.scores ? task.scores[group.people.indexOf(person)] : person.result) ?? ''
}

function Groups({ detail, open, back }) {
  const [tab, setTab] = useState('journal')
  if (!detail) {
    return <div className="ci-list">{groups.map((group) => <Row key={group.id} title={group.name} onClick={() => open({ group })}>
      {group.people.length} ученика · {group.tasks.length === 1 ? '1 работа' : `${group.tasks.length} работы`} в журнале
    </Row>)}</div>
  }

  const { group, person, task } = detail
  if (person) return <Detail title={person.name} subtitle={group.name} onBack={back}>
    <h4>Индивидуальные результаты</h4>
    <div className="ci-list">{group.tasks.map((record) => <Row key={record.id} title={record.name}
      end={formatResult({ value: groupScore(group, record, person), max: record.max })}>{record.date} · вес {record.weight}</Row>)}</div>
  </Detail>

  if (task) return <Detail title={task.name} subtitle={`${group.name} · ${task.date}`} onBack={back}>
    <dl className="ci-facts"><div><dt>Максимум</dt><dd>{task.max}</dd></div><div><dt>Вес</dt><dd>{task.weight}</dd></div></dl>
    <h4>Результаты по работе</h4>
    <div className="ci-list">{group.people.map((entry) => <Row key={entry.id} title={entry.name}
      end={formatResult({ value: groupScore(group, task, entry), max: task.max })}
      onClick={() => open({ group, person: entry })} />)}</div>
  </Detail>

  const recorded = group.tasks.reduce((total, record) => total + group.people.filter((entry) => groupScore(group, record, entry) !== '').length, 0)
  const expected = group.tasks.length * group.people.length
  return <Detail title={group.name} subtitle="II триместр · 2026/27" onBack={back}>
    <Tabs items={[[ 'journal', 'Журнал' ], [ 'roster', 'Состав' ], [ 'analytics', 'Аналитика' ]]} selected={tab} onChange={setTab} label="Раздел группы" />
    {tab === 'journal' && <div className="ci-list">{group.tasks.map((record) => <Row key={record.id} title={record.name}
      end={`до ${record.max}`} onClick={() => open({ group, task: record })}>{record.date} · вес {record.weight}</Row>)}</div>}
    {tab === 'roster' && <div className="ci-list">{group.people.map((entry) => <Row key={entry.id} title={entry.name} onClick={() => open({ group, person: entry })}>Индивидуальные результаты</Row>)}</div>}
    {tab === 'analytics' && <>
      <dl className="ci-facts">
        <div><dt>Учеников</dt><dd>{group.people.length}</dd></div>
        <div><dt>Работ</dt><dd>{group.tasks.length}</dd></div>
        <div><dt>Заполнено результатов</dt><dd>{recorded} из {expected}</dd></div>
        <div><dt>Без результата</dt><dd>{expected - recorded}</dd></div>
      </dl>
      <h4>Где отсутствуют результаты</h4>
      <div className="ci-list">{group.tasks.filter((record) => group.people.some((entry) => groupScore(group, record, entry) === '')).map((record) =>
        <Row key={record.id} title={record.name} end={`Без результата: ${group.people.filter((entry) => groupScore(group, record, entry) === '').length}`}
          onClick={() => open({ group, task: record })}>{record.date}</Row>)}</div>
    </>}
  </Detail>
}

function Courses({ detail, open, back }) {
  if (detail) return <Detail title={detail.name} subtitle="Google Classroom" onBack={back}>
    <dl className="ci-facts"><div><dt>Связанная группа</dt><dd>{detail.name}</dd></div></dl>
    <h4>Связанные работы</h4>
    <div className="ci-list">{detail.tasks.map((task) => <Row key={task.id} title={task.name}>{task.date}</Row>)}</div>
    <p className="ci-meta">Ссылка курса недоступна в демо.</p>
    <External>Открыть Google Classroom</External>
  </Detail>
  return <div className="ci-list">{groups.map((group) => <Row key={group.id} title={group.name} onClick={() => open(group)}>Связанный курс Classroom</Row>)}</div>
}

function News({ role, detail, open, back }) {
  if (detail) return <Detail title={detail.title} subtitle={`${detail.type} · ${detail.day}`} onBack={back}><p>{detail.text}</p></Detail>
  return <div className="ci-list">{announcements.filter((item) => item.role === 'all' || item.role === role).map((item, index) =>
    <Row key={item.id} title={item.title} end={index === 0 && <span className="ci-mark">Недавнее</span>} onClick={() => open(item)}>{item.type} · {item.day}</Row>)}</div>
}

function Profile({ role, homeroom, onRoleChange }) {
  const isTeacher = role === 'teacher'
  return <div>
    <div className="ci-profile">
      <span className="ci-initials" aria-hidden="true">{isTeacher ? 'МЛ' : 'АВ'}</span>
      <div><h3>{isTeacher ? teacher : student}</h3><div className="ci-meta">{isTeacher ? 'Преподаватель математики' : 'Ученица · 8Б'}</div></div>
    </div>
    <dl className="ci-facts">
      <div><dt>Статус аккаунта</dt><dd>Активен</dd></div>
      <div><dt>{isTeacher ? 'Классы' : 'Класс'}</dt><dd>{isTeacher ? '8А, 8Б, 9А' : '8Б'}</dd></div>
      <div><dt>Учебные группы</dt><dd>{isTeacher ? '8А, 8Б и 9А · математика' : 'Математика · информатика, группа 2'}</dd></div>
      {isTeacher && homeroom && <div><dt>Классное руководство</dt><dd>8Б</dd></div>}
      <div><dt>Доступные роли в демо</dt><dd>Ученик · Учитель</dd></div>
    </dl>
    <button type="button" className="ci-action" onClick={() => onRoleChange?.(isTeacher ? 'student' : 'teacher')}>
      {isTeacher ? 'Открыть Campus ученика' : 'Открыть Campus учителя'} <ArrowUpRight size={16} aria-hidden="true" />
    </button>
  </div>
}

function Homeroom({ enabled, detail, open, back }) {
  const group = groups[0]
  if (!enabled) return <Empty icon={Users} title="Назначенного класса нет" />
  if (detail) return <Detail title={detail.name} subtitle="8Б" onBack={back}>
    <dl className="ci-facts"><div><dt>Класс</dt><dd>8Б</dd></div><div><dt>Учебная группа</dt><dd>Математика</dd></div></dl>
  </Detail>
  return <>
    <h3>8Б</h3><p className="ci-meta">Классный руководитель · {teacher}</p>
    <h4>Ученики класса</h4>
    <div className="ci-list">{group.people.map((person) => <Row key={person.id} title={person.name} onClick={() => open(person)} />)}</div>
    <h4>Ближайшее событие класса</h4>
    <Row title="Ярмарка школьных проектов">27 ноября, 13:00 · Актовый зал</Row>
  </>
}

function LocationContent({ role, location, onRoleChange, homeroom, registerBack }) {
  const [stack, setStack] = useState([])
  const detail = stack.at(-1)
  const open = (entry) => setStack((previous) => [...previous, entry])
  const back = () => setStack((previous) => previous.slice(0, -1))
  const hasDetail = stack.length > 0

  useEffect(() => {
    if (!hasDetail || !registerBack) return undefined
    registerBack(back)
    return () => registerBack(null)
  }, [hasDetail, registerBack])

  const view = { detail, open, back }
  if (location === 'schedule') return <Schedule role={role} {...view} />
  if (location === 'work') return role === 'teacher' ? <Groups {...view} /> : <Homework {...view} />
  if (location === 'results') return role === 'teacher' ? <Courses {...view} /> : <Grades {...view} />
  if (location === 'news') return <News role={role} {...view} />
  if (location === 'homeroom') return <Homeroom enabled={role === 'teacher' && homeroom} {...view} />
  if (location === 'profile') return <Profile role={role} homeroom={homeroom} onRoleChange={onRoleChange} />
  return null
}

export default function Interior({ role, location, onRoleChange, homeroom = false, dataState = 'ready', onRetry, registerBack }) {
  if (dataState === 'loading') return <output className="ci-interior"><Empty icon={LoaderCircle} title="Загружаем данные" /></output>
  if (location === 'schedule' && dataState === 'unpublished') return <output className="ci-interior"><Empty icon={Clock3} title="Неделя не опубликована">Занятия появятся после публикации расписания.</Empty></output>
  if (dataState === 'empty') return <output className="ci-interior"><Empty title="Данных пока нет">Здесь появятся данные школы, когда они будут доступны.</Empty></output>
  if (dataState === 'error') return <div className="ci-interior">
    <div role="alert"><Empty icon={CircleAlert} title="Не удалось загрузить данные">Попробуйте запросить их ещё раз.</Empty></div>
    {onRetry && <button className="ci-action" type="button" onClick={onRetry}>Повторить загрузку</button>}
  </div>
  return <div className="ci-interior"><LocationContent key={`${role}-${location}`} {...{ role, location, onRoleChange, homeroom, registerBack }} /></div>
}
