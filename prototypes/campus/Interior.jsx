import {
  ChevronRight, CircleAlert, Clock3, ExternalLink,
  FolderOpen, LoaderCircle, MapPin, Users, Video,
} from 'lucide-react'
import './interior.css'

const student = 'Александра Волкова'
const teacher = 'Мария Лебедева'
const weekdays = [
  ['Пн', '23', 'Понедельник'], ['Вт', '24', 'Вторник'], ['Ср', '25', 'Среда'],
  ['Чт', '26', 'Четверг'], ['Пт', '27', 'Пятница'], ['Сб', '28', 'Суббота'], ['Вс', '29', 'Воскресенье'],
]

const studentStages = {
  junior: {
    cohort: '5Б', range: '5–6 классы', math: 'Математика',
    lab: 'Природоведение', consult: 'Математика', russianTopic: 'Имя существительное',
    mathTopic: 'Действия с дробями', labTopic: 'Наблюдение за испарением воды',
    labHomework: 'Запишите результаты наблюдения и сравните уровень воды до опыта и после него.',
    notebook: 'Построение углов', journalLab: 'Наблюдение за погодой',
    journalMath: ['Контрольная по дробям', 'Сравнение дробей', 'Деление с остатком', 'Числовая прямая', 'Задачи на делимость'],
    journalLabTest: 'Вода в трёх состояниях', earlierLab: 'Измерение температуры',
  },
  middle: { cohort: '8Б', range: '8–9 классы', math: 'Алгебра', lab: 'Физика' },
  senior: {
    cohort: '10А', range: '10–11 классы', math: 'Алгебра и начала анализа',
    lab: 'Физика', consult: 'Геометрия', russianTopic: 'Сложносочинённое предложение',
    mathTopic: 'Тригонометрические уравнения', labTopic: 'Исследование движения тела',
    labHomework: 'Постройте график скорости по результатам измерений и найдите ускорение.',
    notebook: 'Прямые и плоскости в пространстве', journalLab: 'Равноускоренное движение',
    journalMath: ['Тригонометрические уравнения', 'Формулы приведения', 'Знаки функций по четвертям', 'Радианная мера угла', 'Единичная окружность'],
    journalLabTest: 'Перемещение и скорость', earlierLab: 'Равномерное движение',
  },
  graduate: {
    cohort: '11А', range: '10–11 классы', math: 'Алгебра и начала анализа',
    lab: 'Физика', consult: 'Геометрия', russianTopic: 'Знаки препинания в сложном предложении',
    mathTopic: 'Производная и исследование функций', labTopic: 'Изучение дифракции света',
    labHomework: 'Запишите результаты измерений и объясните, как изменилась дифракционная картина.',
    notebook: 'Объём многогранников', journalLab: 'Волновые свойства света',
    journalMath: ['Производная функции', 'Правила дифференцирования', 'Критические точки', 'График функции', 'Касательная к графику'],
    journalLabTest: 'Интерференция света', earlierLab: 'Электромагнитные волны',
  },
}

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

function getStudentWeek(study) {
  if (study === studentStages.middle) return studentWeek
  return studentWeek.map((day) => day.map(([start, end, title, room, person, group, kind, delivery]) => [
    start, end,
    { Алгебра: study.math, Физика: study.lab, Геометрия: study.consult }[title] ?? title,
    room, person, group.replace('8Б', study.cohort).replace('8–9 классы', study.range),
    study === studentStages.junior && kind === 'Лабораторная' ? 'Практикум' : kind, delivery,
  ]))
}

export function getFollowingLessons(role, stage) {
  const isTeacher = role === 'teacher'
  const week = isTeacher ? teacherWeek : getStudentWeek(studentStages[stage] ?? studentStages.middle)
  return week[1].slice(2).filter(([, , , , , , kind]) => kind !== 'Окно').slice(0, 3)
    .map(([time, , subject, room, , group, , delivery]) => ({
      time,
      subject,
      place: delivery?.mode === 'online' ? 'Онлайн' : isTeacher ? group.split(' · ')[0] : room,
    }))
}

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

function getHomework(study) {
  if (study === studentStages.middle) return homework
  const topics = [
    { name: study.mathTopic, subject: study.math, body: 'Решите задачи 152–154. Запишите ход решения и проверьте ответы.' },
    { name: study.labTopic, subject: study.lab, body: study.labHomework },
    {},
    { name: study.russianTopic, body: 'Выпишите пять примеров из текста и объясните расстановку знаков препинания.' },
    { name: study.notebook, subject: study.consult, body: 'Решите задачи 101 и 104. Приложите чертежи к каждому решению.' },
  ]
  return homework.map((item, index) => ({ ...item, ...topics[index] }))
}

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

function Empty({ icon: Icon = FolderOpen, title, children, tone }) {
  return <span className="ci-empty" data-tone={tone}><Icon size={25} aria-hidden="true" /><span><strong>{title}</strong>{children && <span className="ci-meta">{children}</span>}</span></span>
}

export function getInteriorTitle(detail) {
  return detail?.event?.[2] ?? detail?.record?.title ?? detail?.task?.name ?? detail?.person?.name
    ?? detail?.group?.name ?? detail?.subject?.name ?? detail?.title ?? detail?.name
}

function Detail({ subtitle, children }) {
  return <section className="ci-detail">
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

function Schedule({ role, study, detail, open, choices, setChoice }) {
  const { mode = 'day', day = 1 } = choices
  const week = role === 'teacher' ? teacherWeek : getStudentWeek(study)
  const next = role === 'teacher' ? 3 : 2

  if (detail) {
    const [start, end, , room, person, group, kind, delivery] = detail.event
    const meetingUrl = onlineMeetingUrl(delivery?.url)
    return <Detail subtitle={kind}>
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
        return <button type="button" key={start} className="ci-event" data-live={index === 1 && position === 1 ? 'now' : undefined} onClick={() => open({ event, index })}>
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
    <div className="ci-toolbar"><span className="ci-meta">23–29 ноября 2026</span><Tabs items={[[ 'day', 'День' ], [ 'week', 'Неделя' ]]} selected={mode} onChange={value => setChoice('mode',value)} label="Вид расписания" /></div>
    {mode === 'day' && <fieldset className="ci-days" aria-label="День недели">
      {weekdays.map(([short, date, full], index) => <button type="button" key={date} aria-label={`${full}, ${date} ноября`} aria-pressed={day === index} onClick={() => setChoice('day',index)}><span>{short}</span><strong>{date}</strong></button>)}
    </fieldset>}
    {mode === 'day' ? renderDay(day) : weekdays.map((_, index) => renderDay(index))}
  </>
}

function Homework({ study, detail, open, choices, setChoice }) {
  const { filter = 'open' } = choices
  if (detail) {
    return <Detail subtitle={detail.subject}>
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
  const list = getHomework(study).filter((item) =>
    filter === 'done' ? item.completed : filter === 'late' ? item.overdue : !item.completed)
  return <>
    <Tabs items={[[ 'open', 'К сдаче' ], [ 'late', 'Просрочено' ], [ 'done', 'Сдано' ]]} selected={filter} onChange={value => setChoice('filter',value)} label="Статус заданий" />
    {list.length ? <div className="ci-list ci-homework">{list.map((item) => <Row key={item.id} title={item.name}
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

function getSubjects(study) {
  if (study === studentStages.middle) return subjects
  const [algebra, physics, russian] = subjects
  return [
    { ...algebra, name: study.math, work: algebra.work.map((record, index) => ({
      ...record, title: study.journalMath[index] ?? record.title,
    })) },
    { ...physics, name: study.lab, work: [
      { ...physics.work[0], title: study.journalLab },
      { ...physics.work[1], title: study.journalLabTest },
    ], past: [{ ...physics.past[0], title: study.earlierLab }] },
    russian,
  ]
}

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

function Grades({ study, detail, open, choices, setChoice }) {
  const { period = 'current', onlyDebt = false } = choices
  if (detail?.record) {
    const { subject, record } = detail
    return <Detail subtitle={subject.name}>
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
    return <Detail subtitle={period === 'current' ? 'II триместр · 2026/27' : 'I триместр · 2026/27'}>
      <dl className="ci-facts">
        {subject.summaries[period].map((summary) => <div key={summary.summary_type}>
          <dt>{summary.label}</dt><dd>{summary.numeric_value ?? summary.text_value}</dd>
        </div>)}
        {period === 'current' && <div><dt>Долги к пересдаче</dt><dd>{records.filter((record) => record.retakeAssigned).length}</dd></div>}
      </dl>
      <h4>Работы</h4>
      <div className="ci-list ci-results">{records.map((record) => <Row key={record.title} title={record.title} end={formatResult(record)} onClick={() => open({ subject, record })}>
        {[record.date, record.weight && `вес ${record.weight}`, record.retakeAssigned && 'назначена пересдача'].filter(Boolean).join(' · ')}
      </Row>)}</div>
    </Detail>
  }
  const available = getSubjects(study)
  const list = onlyDebt && period === 'current' ? available.filter((subject) => subject.work.some((record) => record.retakeAssigned)) : available
  return <>
    <Tabs items={[[ 'current', 'II триместр' ], [ 'archive', 'Архив · I триместр' ]]} selected={period} onChange={value => setChoice('period',value)} label="Период оценок" />
    {period === 'current' && <label className="ci-check"><input type="checkbox" checked={onlyDebt} onChange={event => setChoice('onlyDebt',event.target.checked)} /> С задолженностями</label>}
    {list.length ? <div className="ci-list ci-results">{list.map((subject) => {
      const debt = subject.work.filter((record) => record.retakeAssigned).length
      return <Row key={subject.id} title={subject.name} end={<span className="ci-score">{subject.summaries[period].find((summary) => summary.summary_type === 'term_grade')?.numeric_value ?? '—'}</span>} onClick={() => open({ subject })}>
        {period === 'current' && debt ? `${debt} работа к пересдаче` : 'Оценка за период'}
      </Row>
    })}</div> : <Empty title="Нет задолженностей к пересдаче" />}
  </>
}

function groupScore(group, task, person) {
  return (task.scores ? task.scores[group.people.indexOf(person)] : person.result) ?? ''
}

function Groups({ detail, open, choices, setChoice }) {
  if (!detail) {
    return <div className="ci-list">{groups.map((group) => <Row key={group.id} title={group.name} onClick={() => open({ group })}>
      {group.people.length} ученика · {group.tasks.length === 1 ? '1 работа' : `${group.tasks.length} работы`} в журнале
    </Row>)}</div>
  }

  const { group, person, task } = detail
  const tab = choices[group.id] ?? 'journal'
  if (person) return <Detail subtitle={group.name}>
    <h4>Индивидуальные результаты</h4>
    <div className="ci-list ci-results">{group.tasks.map((record) => <Row key={record.id} title={record.name}
      end={formatResult({ value: groupScore(group, record, person), max: record.max })}>{record.date} · вес {record.weight}</Row>)}</div>
  </Detail>

  if (task) return <Detail subtitle={`${group.name} · ${task.date}`}>
    <dl className="ci-facts"><div><dt>Максимум</dt><dd>{task.max}</dd></div><div><dt>Вес</dt><dd>{task.weight}</dd></div></dl>
    <h4>Результаты по работе</h4>
    <div className="ci-list ci-roster ci-results">{group.people.map((entry) => <Row key={entry.id} title={entry.name}
      end={formatResult({ value: groupScore(group, task, entry), max: task.max })}
      onClick={() => open({ group, person: entry })} />)}</div>
  </Detail>

  const incomplete = group.tasks.map((record) => ({
    record, missing: group.people.filter((entry) => groupScore(group, record, entry) === '').length,
  })).filter(({ missing }) => missing > 0)
  const expected = group.tasks.length * group.people.length
  const missingTotal = incomplete.reduce((total, entry) => total + entry.missing, 0)
  return <Detail subtitle="II триместр · 2026/27">
    <Tabs items={[[ 'journal', 'Журнал' ], [ 'roster', 'Состав' ], [ 'analytics', 'Аналитика' ]]} selected={tab} onChange={value => setChoice(group.id,value)} label="Раздел группы" />
    {tab === 'journal' && <div className="ci-list ci-results">{group.tasks.map((record) => <Row key={record.id} title={record.name}
      end={`до ${record.max}`} onClick={() => open({ group, task: record })}>{record.date} · вес {record.weight}</Row>)}</div>}
    {tab === 'roster' && <div className="ci-list ci-roster">{group.people.map((entry) => <Row key={entry.id} title={entry.name} onClick={() => open({ group, person: entry })}>Индивидуальные результаты</Row>)}</div>}
    {tab === 'analytics' && <>
      <dl className="ci-facts">
        <div><dt>Учеников</dt><dd>{group.people.length}</dd></div>
        <div><dt>Работ</dt><dd>{group.tasks.length}</dd></div>
        <div><dt>Заполнено результатов</dt><dd>{expected - missingTotal} из {expected}</dd></div>
        <div><dt>Без результата</dt><dd>{missingTotal}</dd></div>
      </dl>
      <h4>Где отсутствуют результаты</h4>
      {incomplete.length ? <div className="ci-list">{incomplete.map(({ record, missing }) =>
        <Row key={record.id} title={record.name} end={`Без результата: ${missing}`}
          onClick={() => open({ group, task: record })}>{record.date}</Row>)}</div>
        : <Empty title="Все результаты заполнены" />}
    </>}
  </Detail>
}

function Courses({ detail, open }) {
  if (detail) return <Detail subtitle="Google Classroom">
    <dl className="ci-facts"><div><dt>Связанная группа</dt><dd>{detail.name}</dd></div></dl>
    <h4>Связанные работы</h4>
    <div className="ci-list">{detail.tasks.map((task) => <Row key={task.id} title={task.name}>{task.date}</Row>)}</div>
    <p className="ci-meta">Ссылка курса недоступна в демо.</p>
    <External>Открыть Google Classroom</External>
  </Detail>
  return <div className="ci-list">{groups.map((group) => <Row key={group.id} title={group.name} onClick={() => open(group)}>Связанный курс Classroom</Row>)}</div>
}

function News({ role, study, detail, open }) {
  if (detail) return <Detail subtitle={`${detail.type} · ${detail.day}`}><p>{detail.text}</p></Detail>
  const studentNews = role === 'student' && study !== studentStages.middle
  const items = announcements.filter((item) => item.role === 'all' || item.role === role).map((item) => {
    if (!studentNews) return item
    if (item.id === 'fair') return { ...item, text: item.text.replace('8–9 классов', study.range.replace('классы', 'классов')) }
    if (item.id === 'assembly') return {
      ...item,
      title: `${study.lab}: занятие в кабинете 207`,
      text: `Занятие класса ${study.cohort} пройдёт в кабинете 207. Начало в 10:55.`,
    }
    return item
  })
  return <div className="ci-list ci-news">{items.map((item) =>
    <Row key={item.id} title={item.title} end={item.day} onClick={() => open(item)}>{item.type}</Row>)}</div>
}

function Profile({ role, study, homeroom, onSettings, previewHref }) {
  const isTeacher = role === 'teacher'
  return <div>
    <div className="ci-profile">
      <span className="ci-initials" aria-hidden="true">{isTeacher ? 'МЛ' : 'АВ'}</span>
      <div><h3>{isTeacher ? teacher : student}</h3><div className="ci-meta">{isTeacher ? 'Преподаватель математики' : `Ученица · ${study.cohort}`}</div></div>
    </div>
    <div className="ci-actions">
      <button type="button" id="profile-settings" className="ci-action" onClick={onSettings}>
        Настройки <ChevronRight size={16} aria-hidden="true" />
      </button>
    </div>
    <dl className="ci-facts">
      <div><dt>Статус аккаунта</dt><dd>Активен</dd></div>
      <div><dt>{isTeacher ? 'Классы' : 'Класс'}</dt><dd>{isTeacher ? '8А, 8Б, 9А' : study.cohort}</dd></div>
      <div><dt>Учебные группы</dt><dd>{isTeacher ? '8А, 8Б и 9А · математика' : 'Математика · информатика, группа 2'}</dd></div>
      {isTeacher && homeroom && <div><dt>Классное руководство</dt><dd>8Б</dd></div>}
    </dl>
    {previewHref && <a className="ci-preview-link" id="preview-tools" href={previewHref}>Параметры предпросмотра</a>}
  </div>
}

function Homeroom({ enabled, detail, open }) {
  const group = groups[0]
  if (!enabled) return <Empty icon={Users} title="Назначенного класса нет" />
  if (detail) return <Detail subtitle="8Б">
    <dl className="ci-facts"><div><dt>Класс</dt><dd>8Б</dd></div><div><dt>Учебная группа</dt><dd>Математика</dd></div></dl>
  </Detail>
  return <>
    <p className="ci-meta">8Б · Классный руководитель — {teacher}</p>
    <h4>Ученики класса</h4>
    <div className="ci-list ci-roster">{group.people.map((person) => <Row key={person.id} title={person.name} onClick={() => open(person)} />)}</div>
    <h4>Ближайшее событие класса</h4>
    <Row title="Ярмарка школьных проектов">27 ноября, 13:00 · Актовый зал</Row>
  </>
}

function LocationContent({ role, stage, location, onSettings, previewHref, homeroom, detail, open, choices, setChoice }) {
  const study = studentStages[stage] ?? studentStages.middle
  const view = { study, detail, open, choices, setChoice }
  const panels = {
    schedule: <Schedule role={role} {...view} />,
    work: role === 'teacher' ? <Groups {...view} /> : <Homework {...view} />,
    results: role === 'teacher' ? <Courses {...view} /> : <Grades {...view} />,
    news: <News role={role} {...view} />,
    homeroom: <Homeroom enabled={role === 'teacher' && homeroom} {...view} />,
    profile: <Profile role={role} study={study} homeroom={homeroom} onSettings={onSettings} previewHref={previewHref} />,
  }
  return <div data-panel={location}>{panels[location]}</div>
}

export default function Interior({ role, stage = 'middle', location, onSettings, previewHref, homeroom = false, dataState = 'ready', onRetry, detail, open, choices, setChoice }) {
  if (location === 'profile') dataState = 'ready'
  if (dataState === 'loading') return <output className="ci-interior"><Empty icon={LoaderCircle} title="Загружаем данные" /></output>
  if (location === 'schedule' && dataState === 'unpublished') return <output className="ci-interior"><Empty icon={Clock3} title="Неделя не опубликована">Занятия появятся после публикации расписания.</Empty></output>
  if (dataState === 'empty') return <output className="ci-interior"><Empty title="Данных пока нет">Здесь появятся данные школы, когда они будут доступны.</Empty></output>
  if (dataState === 'error') return <div className="ci-interior">
    <div role="alert"><Empty icon={CircleAlert} tone="error" title="Не удалось загрузить данные">Попробуйте запросить их ещё раз.</Empty></div>
    {onRetry && <button className="ci-action" type="button" onClick={onRetry}>Повторить загрузку</button>}
  </div>
  return <div className="ci-interior"><LocationContent key={`${role}-${stage}-${location}`} {...{ role, stage, location, onSettings, previewHref, homeroom, detail, open, choices, setChoice }} /></div>
}
