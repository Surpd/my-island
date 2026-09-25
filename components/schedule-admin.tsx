/* oxlint-disable */
'use client';

import { useEffect, useMemo, useRef, useState, type ComponentType, type CSSProperties } from 'react';
import {
  AlertTriangle, Atom, BookOpen, CalendarDays, Check, ChevronLeft, ChevronRight,
  CircleUserRound, CloudDownload, Code2, Dumbbell, FlaskConical, FolderKanban,
  GraduationCap, History, Languages, Leaf, ListFilter, Map as MapIcon, Music2, Palette,
  PanelRightClose, Plus, RefreshCw, Save, Search, Sigma, Sparkles, Star, Users, X,
} from 'lucide-react';

type Item = Record<string, any>;
type AdminApi = <T>(path: string, init?: RequestInit) => Promise<T>;
type ViewMode = 'week' | 'day' | 'class';
type ScheduleLayer = 'week' | 'template';
type AudienceMode = 'groups' | 'base_class' | 'grade' | 'remaining' | 'available_slot' | 'students';
type Lesson = { id: string; day: number; date: string; period: number; start: string; end: string; grade: number; subject: string; note?: string; teacher: string; room: string; groups: string[]; groupIds?: string[]; teacherIds?: string[]; assignmentIndex?: number; audienceMode: AudienceMode; audienceLabel: string; students: string[]; studentIds?: string[]; audienceRule?: Item; teacherColor: string; warning?: string; changed?: boolean; cancelled?: boolean; block?: Item };
type DirectoryOption = { id: string; name: string; meta?: string; subjects?: string[] };
type SaveStatus = 'saved' | 'saving' | 'error' | 'conflict';

const WEEK_START = '2026-09-21';
const DAYS = [
  { short: 'Пн', date: '21 сен', full: 'Понедельник' }, { short: 'Вт', date: '22 сен', full: 'Вторник' },
  { short: 'Ср', date: '23 сен', full: 'Среда' }, { short: 'Чт', date: '24 сен', full: 'Четверг' }, { short: 'Пт', date: '25 сен', full: 'Пятница' },
];
const BELL_SCHEDULE = [
  { start: '09:00', end: '09:45' }, { start: '09:55', end: '10:40' }, { start: '10:55', end: '11:40' },
  { start: '11:50', end: '12:35' }, { start: '12:45', end: '13:30' }, { start: '13:40', end: '14:25' },
  { start: '14:35', end: '15:20' }, { start: '15:30', end: '16:15' }, { start: '16:25', end: '17:10' },
];
const GRADES = [5, 6, 7, 8, 9, 10, 11];
const TEACHER_COLORS = ['#cfe4d2', '#cfdff5', '#f3d8ce', '#ddd7f1', '#f1e1b9', '#cbe4df', '#e7d5df'];
const periodForStart = (time: string) => {
  const [hours, minutes] = String(time || '').split(':').map(Number);
  const value = hours * 60 + minutes;
  let closest = 0;
  for (let index = 1; index < BELL_SCHEDULE.length; index += 1) {
    const [nextHours, nextMinutes] = BELL_SCHEDULE[index].start.split(':').map(Number);
    const [currentHours, currentMinutes] = BELL_SCHEDULE[closest].start.split(':').map(Number);
    if (Math.abs(nextHours * 60 + nextMinutes - value) < Math.abs(currentHours * 60 + currentMinutes - value)) closest = index;
  }
  return closest + 1;
};
const SUBJECT_ICON: Record<string, ComponentType<{ size?: number; strokeWidth?: number }>> = {
  Математика: Sigma, Русский: BookOpen, Английский: Languages, Литература: BookOpen, История: GraduationCap,
  Обществознание: Users, Физика: Atom, Химия: FlaskConical, Биология: Leaf, География: MapIcon,
  Информатика: Code2, Физкультура: Dumbbell, 'Классный час': Star, 'Курс по выбору': Sparkles,
  Тренинг: CircleUserRound, Творчество: Palette, Проект: FolderKanban, Музыка: Music2,
};
const isNoLesson = (subject: string) => /^(?:no[\s_-]*lessons?|нет\s+урока)$/i.test(String(subject || '').trim());
const SUBJECT_OPTIONS = [
  'Английский', 'Биология', 'География', 'Естествознание', 'История', 'История искусства',
  'Информатика', 'Классный час', 'Курс по выбору', 'Лаборатория', 'Лаборатория текста',
  'Литература', 'Математика', 'Музыка', 'Настоящее', 'Нет урока', 'Обществознание', 'Пластика', 'Проект',
  'Русский', 'SDEP', 'Творчество', 'Тренинг', 'Физика', 'Физкультура', 'Химия', 'Цифровой трек',
];
const SUBJECT_ALIASES = new Map([
  ['русский язык', 'Русский'], ['рус', 'Русский'], ['английский язык', 'Английский'], ['англ', 'Английский'],
  ['лит', 'Литература'], ['общ', 'Обществознание'], ['кл. час', 'Классный час'], ['кл.час', 'Классный час'],
  ['кл час', 'Классный час'], ['кл. час иван', 'Классный час'], ['история искусств', 'История искусства'],
  ['ист-иск лида', 'История искусства'], ['музыка марина', 'Музыка'], ['пластика вадим', 'Пластика'],
  ['лаб текста', 'Лаборатория текста'],
].map(([alias, subject]) => [String(alias).toLocaleLowerCase('ru'), String(subject)]));
const normalizeSubject = (subject: string) => {
  const value = String(subject || '').trim().replace(/\s+/g, ' ');
  if (isNoLesson(value)) return 'Нет урока';
  const alias = SUBJECT_ALIASES.get(value.toLocaleLowerCase('ru'));
  if (alias) return alias;
  return SUBJECT_OPTIONS.find((option) => option.toLocaleLowerCase('ru') === value.toLocaleLowerCase('ru')) || value;
};
const displaySubject = normalizeSubject;
const lessonTitle = (lesson: Lesson) => isNoLesson(lesson.subject) && lesson.note ? `${lesson.note} · Нет урока` : displaySubject(lesson.subject);

function assignmentToLesson(block: Item, assignment: Item, index: number): Lesson {
  const date = String(block.date || WEEK_START);
  const day = Math.max(0, Math.min(4, Number(block.weekday ?? new Date(`${date}T12:00:00`).getDay() - 1)));
  const groups = Array.isArray(assignment.groups) ? assignment.groups.map(String) : [];
  const teacher = Array.isArray(assignment.teachers) ? assignment.teachers.join(', ') : String(assignment.teacher || 'Преподаватель не назначен');
  const teacherIndex = Math.abs([...teacher].reduce((total, char) => total + char.charCodeAt(0), 0)) % TEACHER_COLORS.length;
  const sample = Array.isArray(block.affected_students?.sample) ? block.affected_students.sample : [];
  const rule = assignment.audience_rule || assignment.metadata?.audience_rule || {};
  const ruleKind = String(rule.kind || assignment.audience_kind || 'groups');
  const audienceMode: AudienceMode = ruleKind === 'available_slot' ? 'available_slot' : ruleKind === 'remaining' || ruleKind === 'complement' ? 'remaining' : ruleKind === 'whole_grade' ? 'grade' : ruleKind === 'base_class' ? 'base_class' : ruleKind === 'students' ? 'students' : 'groups';
  const audienceLabel = audienceMode === 'available_slot' ? `Свободные ученики ${block.grade_scope || 9} класса` : audienceMode === 'remaining' ? `Остаток деления «${String(rule.partition_dimension || 'учебная группа')}»` : groups.join(' + ') || `Весь ${block.grade_scope || 9} класс`;
  const start = String(block.start_time || '09:00').slice(0, 5);
  return { id: `${String(block.block_key || date)}-${index}`, day, date, period: periodForStart(start), start, end: String(block.end_time || '09:45').slice(0, 5), grade: Number(String(block.grade_scope || '9').split(',')[0]) || 9, subject: String(assignment.activity || 'Занятие'), note: String(assignment.note || ''), teacher, room: String(assignment.room || '—'), groups, groupIds: (assignment.canonical_group_ids || []).map(String), teacherIds: (assignment.teacher_ids || []).map(String), assignmentIndex: index, audienceMode: audienceMode === 'base_class' ? 'groups' : audienceMode, audienceRule: rule, audienceLabel, students: sample.map((item: Item) => String(item.name || item.id)).filter(Boolean), studentIds: sample.map((item: Item) => String(item.id)).filter(Boolean), teacherColor: TEACHER_COLORS[teacherIndex], warning: block.issues?.length ? `${block.issues.length} требуют внимания` : undefined, changed: String(block.change_classification || 'UNCHANGED') !== 'UNCHANGED', block };
}

function SubjectIcon({ subject, size = 18 }: { subject: string; size?: number }) {
  const match = Object.keys(SUBJECT_ICON).find((name) => subject.toLowerCase().includes(name.toLowerCase()));
  const Icon = match ? SUBJECT_ICON[match] : Sparkles;
  return <Icon size={size} strokeWidth={1.8} />;
}

function LessonCard({ lesson, compact = false, selected, onClick }: { lesson: Lesson; compact?: boolean; selected?: boolean; onClick: () => void }) {
  const warningDetails = (lesson.block?.issues || []).map((issue: Item) => String(issue.reason || issue.message || issue.issue_type || '')).filter(Boolean);
  return <button type="button" data-lesson-card={lesson.id} className={`studio-lesson ${compact ? 'is-compact' : ''} ${selected ? 'is-selected' : ''} ${lesson.warning ? 'has-warning' : ''}`} style={{ '--teacher-color': lesson.teacherColor } as CSSProperties} onClick={onClick} aria-label={`${lessonTitle(lesson)}${warningDetails.length ? `. Требуют внимания: ${warningDetails.join('; ')}` : lesson.warning ? `. Требуют внимания: ${lesson.warning}` : ''}`} title={warningDetails.length ? warningDetails.join('\n') : lesson.warning || undefined}><span className="studio-lesson__icon"><SubjectIcon subject={lesson.subject} size={compact ? 15 : 18} /></span><span className="studio-lesson__body"><strong>{lessonTitle(lesson)}</strong><small>{lesson.audienceLabel}</small>{compact ? <small>{lesson.period}-й урок · {BELL_SCHEDULE[lesson.period - 1]?.start}–{BELL_SCHEDULE[lesson.period - 1]?.end}</small> : <small>{lesson.teacher} · {lesson.room}</small>}</span>{lesson.changed ? <span className="studio-lesson__change" title="Изменено относительно шаблона" /> : null}{lesson.warning ? <AlertTriangle className="studio-lesson__warning" size={14} /> : null}</button>;
}

function ModeSwitch({ value, onChange }: { value: ViewMode; onChange: (value: ViewMode) => void }) {
  return <div className="studio-mode-switch" aria-label="Режим расписания">{(['week', 'day', 'class'] as ViewMode[]).map((mode) => <button type="button" key={mode} className={value === mode ? 'is-active' : ''} onClick={() => onChange(mode)}>{mode === 'week' ? 'Неделя' : mode === 'day' ? 'День' : 'Класс'}</button>)}</div>;
}

function LessonInspector({ lesson, api, groups, teachers, students, lessons, subjects, saveStatus, onClose, onSave, onDelete }: { lesson: Lesson; api: AdminApi; groups: DirectoryOption[]; teachers: DirectoryOption[]; students: DirectoryOption[]; lessons: Lesson[]; subjects: string[]; saveStatus: SaveStatus; onClose: () => void; onSave: (lesson: Lesson) => void; onDelete: (lesson: Lesson) => void }) {
  const initialRule = lesson.audienceRule || {};
  const [draft, setDraft] = useState(lesson); const [tab, setTab] = useState<'main' | 'students' | 'history'>('main'); const [audienceMode, setAudienceMode] = useState<AudienceMode>(lesson.audienceMode); const [partition, setPartition] = useState(String(initialRule.partition_dimension || '').split(':').pop() === 'math' ? 'Математика' : String(initialRule.partition_dimension || '').split(':').pop() === 'oge' ? 'ОГЭ' : 'Английский'); const [selectedGroups, setSelectedGroups] = useState<string[]>(lesson.audienceMode === 'students' ? lesson.students : lesson.groups); const [excluded, setExcluded] = useState<string[]>((initialRule.exclude_student_ids || []).map((id: string) => students.find((item) => item.id === String(id))?.name || String(id))); const [studentQuery, setStudentQuery] = useState('');
  const availableStudents = useMemo(() => students.map((item) => item.name), [students]);
  const subject = normalizeSubject(draft.subject).toLocaleLowerCase('ru');
  const availableGroups = useMemo(() => groups.filter((item) => item.name.startsWith(String(draft.grade)) || !/^\d/.test(item.name)).sort((left, right) => {
    const score = (item: DirectoryOption) => Number(Boolean(subject && `${item.name} ${item.meta || ''}`.toLocaleLowerCase('ru').includes(subject))) * 2 + Number(item.name.startsWith(String(draft.grade)));
    return score(right) - score(left) || left.name.localeCompare(right.name, 'ru');
  }).map((item) => item.name), [draft.grade, groups, subject]);
  const availableTeachers = useMemo(() => teachers.map((item) => item.name).sort((left, right) => {
    const score = (name: string) => Number(teachers.some((item) => item.name === name && item.subjects?.some((value) => normalizeSubject(value).toLocaleLowerCase('ru') === subject))) * 2 + Number(lessons.some((item) => item.teacher === name && normalizeSubject(item.subject).toLocaleLowerCase('ru') === subject));
    return score(right) - score(left) || left.localeCompare(right, 'ru');
  }), [lessons, subject, teachers]);
  const [remotePreview, setRemotePreview] = useState<string[] | null>(null);
  const [previewError, setPreviewError] = useState('');
  const preview = remotePreview || [];
  const initialized = useRef(false);
  useEffect(() => { const rule = lesson.audienceRule || {}; initialized.current = false; setDraft(lesson); setAudienceMode(lesson.audienceMode); setSelectedGroups(lesson.audienceMode === 'students' ? lesson.students : lesson.groups); setExcluded((rule.exclude_student_ids || []).map((id: string) => students.find((item) => item.id === String(id))?.name || String(id))); setRemotePreview(null); }, [lesson.id]);
  const toggle = (value: string, list: string[], setter: (next: string[]) => void) => setter(list.includes(value) ? list.filter((item) => item !== value) : [...list, value]);
  const audienceLabel = audienceMode === 'available_slot' ? `Свободные ученики ${draft.grade} класса` : audienceMode === 'remaining' ? 'Особый состав учеников' : audienceMode === 'grade' ? `Весь ${draft.grade} класс` : selectedGroups.join(' + ') || 'Аудитория не выбрана';
  const issueDetails: string[] = (draft.block?.issues || []).map((issue: Item) => [issue.reason, issue.message, issue.issue_type].find(Boolean)).filter(Boolean).map(String);
  const makeRule = () => {
    const groupIds = selectedGroups.map((name) => groups.find((item) => item.name === name)?.id || name);
    const includeIds = audienceMode === 'students' ? selectedGroups.map((name) => students.find((item) => item.name === name)?.id || name) : [];
    const excludeIds = excluded.map((name) => students.find((item) => item.name === name)?.id || name);
    const subjectKey = partition === 'Английский' ? 'english' : partition === 'Математика' ? 'math' : 'oge';
    const preserved = lesson.audienceMode === audienceMode && lesson.grade === draft.grade ? lesson.audienceRule || {} : {};
    const partitionGroupIds = audienceMode === 'remaining' ? (preserved.partition_group_ids || []) : [];
    return { kind: audienceMode === 'grade' ? 'whole_grade' : audienceMode, grade: String(draft.grade), class_group_id: String(preserved.class_group_id || ''), group_ids: audienceMode === 'groups' || audienceMode === 'base_class' ? groupIds : [], partition_group_ids: partitionGroupIds, partition_dimension: audienceMode === 'remaining' ? (partitionGroupIds.length ? String(preserved.partition_dimension || '') : `instructional:${draft.grade}:${subjectKey}`) : '', include_student_ids: includeIds, exclude_student_ids: excludeIds };
  };
  const save = () => { if (!draft.subject.trim()) return; const rule = makeRule(); onSave({ ...draft, audienceMode, groups: audienceMode === 'remaining' || audienceMode === 'available_slot' || audienceMode === 'students' ? [] : selectedGroups, groupIds: rule.group_ids, studentIds: rule.include_student_ids, audienceRule: rule, audienceLabel, students: preview, changed: true, warning: undefined }); };
  useEffect(() => {
    const rule = makeRule();
    const occupiedAudiences = audienceMode === 'available_slot' ? lessons.filter((item) => item.id !== draft.id && item.day === draft.day && item.period === draft.period && item.grade === draft.grade && item.subject && !isNoLesson(item.subject) && item.audienceMode !== 'available_slot').map((item) => item.audienceRule?.kind ? item.audienceRule : { kind: item.audienceMode === 'grade' ? 'whole_grade' : item.audienceMode, grade: String(item.grade), group_ids: item.groupIds || [], include_student_ids: item.studentIds || [] }) : [];
    const timer = window.setTimeout(() => api<Item>('/api/admin/schedule/audience-preview', { method: 'POST', body: JSON.stringify({ audience: rule, occupied_audiences: occupiedAudiences }) }).then((result) => { setRemotePreview((result.students || []).map((item: Item) => String(item.display_name || item.name || item.id))); setPreviewError(''); }).catch(() => { setRemotePreview(null); setPreviewError('Не удалось рассчитать аудиторию'); }), 250);
    return () => window.clearTimeout(timer);
  }, [api, audienceMode, draft.day, draft.grade, draft.id, draft.period, excluded, groups, lessons, partition, selectedGroups, students]);
  useEffect(() => {
    if (!initialized.current) { initialized.current = true; return; }
    save();
  }, [draft, audienceMode, partition, selectedGroups, excluded]);
  return <aside className="studio-inspector" aria-label="Редактор урока"><header><div className="studio-inspector__title"><span className="studio-subject-mark" style={{ '--teacher-color': draft.teacherColor } as CSSProperties}><SubjectIcon subject={draft.subject} size={22} /></span><div><h2>{displaySubject(draft.subject)}</h2><p>{draft.grade} класс · {DAYS[draft.day]?.full}, {draft.period}-й урок · {BELL_SCHEDULE[draft.period - 1]?.start}–{BELL_SCHEDULE[draft.period - 1]?.end}</p></div></div><button type="button" className="studio-icon-button" onClick={onClose} aria-label="Закрыть"><PanelRightClose size={19} /></button></header>
    <nav className="studio-inspector-tabs">{([['main', 'Основное'], ['students', 'Ученики'], ['history', 'История']] as const).map(([value, label]) => <button type="button" className={tab === value ? 'is-active' : ''} key={value} onClick={() => setTab(value)}>{label}{value === 'students' ? <span>{preview.length}</span> : null}</button>)}</nav>
    {tab === 'main' ? <div className="studio-inspector__content">{lesson.warning ? <section className="studio-warning-details"><strong><AlertTriangle size={16} /> Требует внимания</strong>{issueDetails.length ? <ul>{issueDetails.map((detail, index) => <li key={`${detail}-${index}`}>{detail}</li>)}</ul> : <p>{lesson.warning}</p>}</section> : null}<section className="studio-form-section"><h3>Занятие</h3><label>Предмет<input list="schedule-subject-options" placeholder="Выберите или введите предмет" value={displaySubject(draft.subject)} onChange={(event) => setDraft({ ...draft, subject: event.target.value === 'Нет урока' ? 'NO_LESSON' : event.target.value })} /><datalist id="schedule-subject-options">{subjects.map((subject) => <option key={subject} value={subject} />)}</datalist></label>{isNoLesson(draft.subject) ? <label>Пометка<input placeholder="Например, обед" value={draft.note || ''} onChange={(event) => setDraft({ ...draft, note: event.target.value })} /></label> : null}<label>Урок<select value={draft.period} onChange={(event) => { const period = Number(event.target.value); const slot = BELL_SCHEDULE[period - 1]; setDraft({ ...draft, period, start: slot.start, end: slot.end }); }}>{BELL_SCHEDULE.map((slot, index) => <option key={index + 1} value={index + 1}>{index + 1}-й · {slot.start}–{slot.end}</option>)}</select></label><label>Преподаватель<select value={draft.teacher} onChange={(event) => setDraft({ ...draft, teacher: event.target.value })}><option value="">Не назначен</option>{availableTeachers.map((name) => <option key={name}>{name}</option>)}</select></label><label>Кабинет<input value={draft.room} onChange={(event) => setDraft({ ...draft, room: event.target.value })} /></label></section>
      <section className="studio-form-section studio-audience-builder"><div className="studio-section-heading"><div><h3>Кто идёт на урок</h3><p>Аудитория хранится как явное правило этой версии.</p></div><Users size={18} /></div><div className="studio-audience-types">{([['groups', 'Группы'], ['grade', 'Вся параллель'], ['available_slot', 'Все остальные'], ['students', 'Ученики']] as const).map(([value, label]) => <button type="button" key={value} className={audienceMode === value ? 'is-active' : ''} onClick={() => { setAudienceMode(value); setSelectedGroups([]); }}>{label}</button>)}</div>
        {audienceMode === 'groups' || audienceMode === 'base_class' ? <div className="studio-choice-grid">{availableGroups.filter((group) => group.startsWith(String(draft.grade)) || audienceMode === 'groups' && !/^\d/.test(group)).map((group) => <label key={group}><input type="checkbox" checked={selectedGroups.includes(group)} onChange={() => toggle(group, selectedGroups, setSelectedGroups)} /> <span>{group}</span></label>)}</div> : null}
        {audienceMode === 'grade' ? <div className="studio-rule-card"><GraduationCap size={18} /><div><strong>Весь {draft.grade} класс</strong><small>Все ученики параллели по актуальным memberships</small></div></div> : null}
        {audienceMode === 'available_slot' ? <div className="studio-rule-card"><Users size={18} /><div><strong>Все остальные в этот урок</strong><small>Ученики {draft.grade} класса, не занятые в других занятиях этого времени</small></div></div> : null}
        {audienceMode === 'remaining' ? <div className="studio-rule-card is-legacy"><Users size={18} /><div><strong>Сохранено старое правило аудитории</strong><small>Оно не изменится, пока вы не выберете одну из четырёх категорий выше.</small></div></div> : null}
        {audienceMode === 'students' ? <><label>Найти ученика<input placeholder="Фамилия или имя" value={studentQuery} onChange={(event) => setStudentQuery(event.target.value)} /></label><div className="studio-choice-grid">{availableStudents.filter((name) => name.toLowerCase().includes(studentQuery.toLowerCase())).map((name) => <label key={name}><input type="checkbox" checked={selectedGroups.includes(name)} onChange={() => toggle(name, selectedGroups, setSelectedGroups)} /> <span>{name}</span></label>)}</div></> : null}
        <div className="studio-audience-preview"><div><span className="studio-preview-count">{previewError ? '—' : preview.length}</span><div><strong>{audienceLabel}</strong><small>{previewError || 'Предпросмотр по актуальному составу групп'}</small></div>{previewError ? <AlertTriangle size={18} /> : <Check size={18} />}</div><details open><summary>Показать учеников</summary><ul>{preview.map((name) => <li key={name}><span>{name}</span><button type="button" onClick={() => setExcluded([...excluded, name])}>Исключить</button></li>)}</ul></details>{excluded.length ? <div className="studio-exclusions"><strong>Исключения:</strong>{excluded.map((name) => <button type="button" key={name} onClick={() => setExcluded(excluded.filter((item) => item !== name))}>{name} <X size={12} /></button>)}</div> : null}</div>
      </section></div> : tab === 'students' ? <div className="studio-inspector__content"><section className="studio-form-section"><div className="studio-section-heading"><div><h3>{preview.length} учеников</h3><p>{audienceLabel}</p></div><Users size={18} /></div><div className="studio-student-list">{preview.map((name, index) => <div key={name}><span>{name.split(' ').map((part) => part[0]).join('')}</span><div><strong>{name}</strong><small>{draft.grade}-{index % 2 ? 'Д' : 'А'} · {selectedGroups[0] || audienceLabel}</small></div></div>)}</div></section></div> : <div className="studio-inspector__content"><section className="studio-history-list"><article><span><History size={16} /></span><div><strong>Аудитория уточнена</strong><p>Все остальные в делении «Английский»</p><small>Сегодня, 18:42 · Администратор</small></div></article><article><span><CloudDownload size={16} /></span><div><strong>Импортировано из Google Sheets</strong><p>Занятие сопоставлено с шаблоном</p><small>Сегодня, 17:58</small></div></article><article><span><CalendarDays size={16} /></span><div><strong>Создано из шаблона v12</strong><small>20 сентября, 20:14</small></div></article></section></div>}
    <footer><button type="button" className="studio-danger-button" onClick={() => onDelete(lesson)}>Удалить урок</button><span className={`studio-save-state is-${saveStatus}`}>{saveStatus === 'saving' ? 'Сохраняем…' : saveStatus === 'error' ? 'Не удалось сохранить' : saveStatus === 'conflict' ? 'Изменено в другой сессии' : 'Сохранено'}</span></footer></aside>;
}

function VersionPanel({ layer, onClose }: { layer: ScheduleLayer; onClose: () => void }) {
  const versions = layer === 'template' ? [{ name: 'Шаблон v12', date: '23 сентября, 20:14', author: 'Мария Петрова', current: true }, { name: 'Шаблон v11', date: '18 сентября, 17:36', author: 'Администратор' }, { name: 'Шаблон v10', date: '1 сентября, 08:10', author: 'Администратор' }] : [{ name: '21–25 сентября · версия 4', date: 'Сегодня, 18:42', author: 'Мария Петрова', current: true }, { name: '21–25 сентября · версия 3', date: 'Сегодня, 17:58', author: 'Импорт из Google Sheets' }, { name: '21–25 сентября · версия 2', date: '20 сентября, 20:16', author: 'Администратор' }];
  return <aside className="studio-side-dialog"><header><div><p>ИСТОРИЯ ВЕРСИЙ</p><h2>{layer === 'template' ? 'Шаблон расписания' : 'Неделя 21–25 сентября'}</h2></div><button type="button" className="studio-icon-button" onClick={onClose}><X size={19} /></button></header><div className="studio-version-list">{versions.map((version) => <button type="button" key={version.name} className={version.current ? 'is-current' : ''}><span>{version.current ? <Check size={15} /> : <History size={15} />}</span><div><strong>{version.name}</strong><small>{version.date}</small><small>{version.author}</small></div>{version.current ? <em>Текущая</em> : <ChevronRight size={16} />}</button>)}</div><footer><button type="button" className="studio-primary-button"><Save size={16} /> Сохранить новую версию</button></footer></aside>;
}

function ImportReview({ api, layer, baseVersion, groups, teachers, onClose, onApply }: { api: AdminApi; layer: ScheduleLayer; baseVersion: string; groups: DirectoryOption[]; teachers: DirectoryOption[]; onClose: () => void; onApply: (preview: Item) => Promise<void> }) {
  const [step, setStep] = useState<'loading' | 'review' | 'applying' | 'done'>('loading');
  const [preview, setPreview] = useState<Item | null>(null); const [error, setError] = useState('');
  useEffect(() => { let cancelled = false; const endpoint = layer === 'template' ? '/api/admin/schedule/template/preview' : `/api/admin/schedule/preview?week_start=${WEEK_START}`; api<Item>(endpoint, { method: 'POST', ...(layer === 'template' ? { body: JSON.stringify({ expected_version_id: baseVersion }) } : {}) }).then((result) => { if (cancelled) return; if (result.status !== 'preview') throw new Error(String(result.message || 'Импорт недоступен')); setPreview(result); setStep('review'); }).catch((reason) => { if (!cancelled) { setError(String(reason?.message || 'Не удалось прочитать Google Sheets')); setStep('review'); } }); return () => { cancelled = true; }; }, [api, layer, baseVersion]);
  const patches: Item[] = preview?.overlay_patches || []; const changes: Item[] = preview?.draft_changes || [];
  const tally = (state: string) => patches.filter((item) => item.resolution_state === state).length;
  const describe = (value: Item | null | undefined) => !value ? 'Нет урока' : (value.assignments || []).map((item: Item) => [displaySubject(String(item.activity || '')), (item.teacher_ids || []).map((id: string) => teachers.find((teacher) => teacher.id === String(id))?.name || id).join(', '), (item.group_ids || []).map((id: string) => groups.find((group) => group.id === String(id))?.name || id).join(', '), item.room].filter(Boolean).join(' · ')).join('; ') || (value.source_text || []).join('; ') || 'Нет урока';
  return <div className="studio-modal-backdrop"><section className="studio-import-modal"><header><div><p>ИМПОРТ ИЗ GOOGLE SHEETS</p><h2>{step === 'done' ? 'Изменения добавлены в черновик' : layer === 'template' ? 'Изменения шаблона' : 'Изменения недели'}</h2></div><button type="button" className="studio-icon-button" onClick={onClose}><X size={19} /></button></header>{step === 'loading' || step === 'applying' ? <div className="studio-import-loading"><RefreshCw size={24} className="admin-spin" /><strong>{step === 'applying' ? 'Сохраняем предложения в черновик…' : 'Читаем таблицу и сравниваем с текущей версией…'}</strong></div> : step === 'done' ? <div className="studio-import-done"><span><Check size={28} /></span><h3>Предложения сохранены</h3><p>Однозначные изменения готовы к публикации. Остальные отмечены для проверки и не будут опубликованы без неё.</p><button type="button" className="studio-primary-button" onClick={onClose}>Продолжить</button></div> : error ? <div className="studio-import-done"><span><AlertTriangle size={28} /></span><h3>Предпросмотр недоступен</h3><p>{error}</p><button type="button" className="studio-secondary-button" onClick={onClose}>Закрыть</button></div> : <><div className="studio-import-summary"><article className="is-good"><strong>{tally('AUTO_RESOLVED')}</strong><span>определены автоматически</span></article><article><strong>{tally('NEEDS_CONFIRMATION')}</strong><span>подтвердить</span></article><article className="is-warn"><strong>{tally('UNRESOLVED')}</strong><span>не удалось определить</span></article></div><div className="studio-import-list"><div className="studio-import-list__head"><strong>Найдено {patches.length} изменений</strong><span>Публикация — отдельное действие</span></div>{patches.slice(0, 20).map((patch) => <article key={String(patch.block_key)} className={patch.resolution_state !== 'AUTO_RESOLVED' ? 'is-attention' : ''}><span className={`studio-import-status ${patch.resolution_state !== 'AUTO_RESOLVED' ? 'is-warn' : 'is-change'}`}>{patch.resolution_state === 'AUTO_RESOLVED' ? 'Готово' : patch.resolution_state === 'NEEDS_CONFIRMATION' ? 'Подтвердить' : 'Разобрать'}</span><div><strong>{DAYS[Number(patch.weekday)]?.full || 'День'} · {patch.slot?.start || ''} · {patch.grade_scope} класс</strong><p>Было: {describe(patch.before)}</p><p>Стало: {describe(patch.after)}</p>{patch.source_issue ? <small>{patch.source_issue}</small> : null}</div></article>)}{!patches.length ? <div className="studio-import-empty">Изменений относительно подтверждённого источника нет.</div> : null}</div><footer><button type="button" className="studio-secondary-button" onClick={onClose}>Закрыть</button><button type="button" className="studio-primary-button" disabled={!changes.length} onClick={() => { if (!preview) return; setStep('applying'); void onApply(preview).then(() => setStep('done')).catch((reason) => { setError(String(reason?.message || 'Не удалось сохранить предложения')); setStep('review'); }); }}><Save size={16} /> Применить все безопасные в черновик</button></footer></>}</section></div>;
}

function lessonToChange(lesson: Lesson, groups: DirectoryOption[], teachers: DirectoryOption[], students: DirectoryOption[]): Item {
  const groupIds = (lesson.groupIds?.length ? lesson.groupIds : lesson.groups.map((name) => groups.find((item) => item.name === name)?.id || name));
  const studentIds = lesson.students.map((name) => students.find((item) => item.name === name)?.id || name);
  const teacherId = teachers.find((item) => item.name === lesson.teacher)?.id || lesson.teacherIds?.[0];
  const subjectKey = lesson.audienceLabel.includes('Матем') ? 'math' : lesson.audienceLabel.includes('ОГЭ') ? 'oge' : 'english';
  const audience = lesson.audienceRule || {
    kind: lesson.audienceMode === 'grade' ? 'whole_grade' : lesson.audienceMode,
    group_ids: lesson.audienceMode === 'groups' ? groupIds : [],
    grade: String(lesson.grade),
    partition_dimension: lesson.audienceMode === 'remaining' ? `instructional:${lesson.grade}:${subjectKey}` : '',
    include_student_ids: lesson.audienceMode === 'students' ? studentIds : [],
    exclude_student_ids: [],
  };
  return { operation: 'upsert', block_key: String(lesson.block?.block_key || lesson.id), assignment_index: lesson.assignmentIndex ?? 0, manual: true, audience_changed: true, lesson: { activity: isNoLesson(lesson.subject) ? 'NO_LESSON' : lesson.subject, note: isNoLesson(lesson.subject) ? String(lesson.note || '') : '', weekday: lesson.day, start_time: lesson.start, end_time: lesson.end, grade: String(lesson.grade), teacher_ids: teacherId && !isNoLesson(lesson.subject) ? [String(teacherId)] : [], teacher_names: lesson.teacher && !isNoLesson(lesson.subject) ? [lesson.teacher] : [], room: lesson.room, source_cells: lesson.block?.source_cells || lesson.block?.source_provenance?.source_cells || [], source_identity: lesson.block?.source_provenance || {}, audience } };
}

const sameChange = (left: Item, right: Item) => String(left.block_key) === String(right.block_key) && Number(left.assignment_index ?? 0) === Number(right.assignment_index ?? 0);

function applyDraftChanges(base: Lesson[], changes: Item[], groups: DirectoryOption[], teachers: DirectoryOption[], students: DirectoryOption[]): Lesson[] {
  const result = [...base];
  for (const change of changes) {
    const index = result.findIndex((lesson) => String(lesson.block?.block_key || lesson.id) === String(change.block_key) && Number(lesson.assignmentIndex ?? 0) === Number(change.assignment_index ?? 0));
    if (change.operation === 'delete') { if (index >= 0) result.splice(index, 1); continue; }
    const raw = change.lesson || {}; const rule = raw.audience || {}; const groupIds = (rule.group_ids || []).map(String);
    const groupNames = groupIds.map((id: string) => groups.find((item) => item.id === id)?.name || id);
    const teacherIds = (raw.teacher_ids || []).map(String); const teacherName = raw.teacher_names?.[0] || teachers.find((item) => teacherIds.includes(item.id))?.name || 'Преподаватель не назначен';
    const studentIds = (rule.include_student_ids || []).map(String); const studentNames = studentIds.map((id: string) => students.find((item) => item.id === id)?.name || id);
    const audienceMode: AudienceMode = rule.kind === 'whole_grade' ? 'grade' : rule.kind === 'remaining' ? 'remaining' : rule.kind === 'available_slot' ? 'available_slot' : rule.kind === 'students' ? 'students' : 'groups';
    const teacherIndex = Math.abs([...teacherName].reduce((total, char) => total + char.charCodeAt(0), 0)) % TEACHER_COLORS.length;
    const start = String(raw.start_time || '09:00').slice(0, 5); const end = String(raw.end_time || '09:45').slice(0, 5); const period = periodForStart(start);
    const seed: Lesson = { id: String(change.block_key), day: Number(raw.weekday || 0), date: WEEK_START, period, start, end, grade: Number(raw.grade || 9), subject: String(raw.activity || 'Занятие'), teacher: teacherName, room: String(raw.room || ''), groups: [], audienceMode: 'grade', audienceLabel: `Весь ${raw.grade || 9} класс`, students: [], teacherColor: TEACHER_COLORS[teacherIndex] };
    const next: Lesson = { ...(index >= 0 ? result[index] : seed), id: index >= 0 ? result[index].id : String(change.block_key), day: Number(raw.weekday || 0), date: `${WEEK_START}`, period, start, end, grade: Number(raw.grade || 9), subject: String(raw.activity || 'Занятие'), note: String(raw.note || ''), teacher: teacherName, teacherIds, room: String(raw.room || ''), groups: groupNames, groupIds, students: studentNames, studentIds, assignmentIndex: Number(change.assignment_index ?? 0), audienceMode, audienceRule: rule, audienceLabel: audienceMode === 'available_slot' ? `Свободные ученики ${raw.grade} класса` : audienceMode === 'remaining' ? `Остаток деления «${String(rule.partition_dimension || '').split(':').pop() || 'группа'}»` : audienceMode === 'grade' ? `Весь ${raw.grade} класс` : groupNames.join(' + '), changed: true, warning: change.review_required ? String(change.review_reason) : undefined };
    if (index < 0) next.id = `${String(change.block_key)}-${Number(change.assignment_index ?? 0)}`;
    next.block = { ...(next.block || {}), block_key: String(change.block_key), source_provenance: raw.source_identity || {}, source_cells: raw.source_cells || [] };
    if (index >= 0) result[index] = next; else result.push(next);
  }
  return result;
}

export function ScheduleAdmin({ api }: { api: AdminApi }) {
  const [lessons, setLessons] = useState<Lesson[]>([]); const [weekBaseLessons, setWeekBaseLessons] = useState<Lesson[]>([]); const [templateBaseLessons, setTemplateBaseLessons] = useState<Lesson[]>([]); const [mode, setModeState] = useState<ViewMode>('week'); const [layer, setLayerState] = useState<ScheduleLayer>('week'); const [day, setDay] = useState(1); const [grade, setGrade] = useState(9); const [selectedId, setSelectedId] = useState(''); const [loading, setLoading] = useState(true); const [notice, setNotice] = useState(''); const [query, setQuery] = useState(''); const [showImport, setShowImport] = useState(false); const [showVersions, setShowVersions] = useState(false);
  const [groups, setGroups] = useState<DirectoryOption[]>([]); const [teachers, setTeachers] = useState<DirectoryOption[]>([]); const [students, setStudents] = useState<DirectoryOption[]>([]); const [baseVersion, setBaseVersion] = useState('current'); const [draftRevision, setDraftRevision] = useState(0); const [draftChanges, setDraftChanges] = useState<Item[]>([]); const [draftDirty, setDraftDirty] = useState(false); const [saveStatus, setSaveStatus] = useState<SaveStatus>('saved'); const [draftUpdatedAt, setDraftUpdatedAt] = useState(''); const [showPublish, setShowPublish] = useState(false); const [publishing, setPublishing] = useState(false); const [confirmingTemplate, setConfirmingTemplate] = useState(false);
  const [pendingTemplate, setPendingTemplate] = useState<number | null>(null);
  const revisionRef = useRef(0); const generationRef = useRef(0); const baseVersionRef = useRef(''); const dirtyRef = useRef(false);
  const selected = lessons.find((lesson) => lesson.id === selectedId) || null;
  useEffect(() => {
    const params = new URLSearchParams(window.location.hash.split('?')[1] || ''); const savedMode = params.get('mode') as ViewMode | null; if (savedMode && ['week', 'day', 'class'].includes(savedMode)) setModeState(savedMode); const savedGrade = Number(params.get('grade')); if (GRADES.includes(savedGrade)) setGrade(savedGrade);
    let cancelled = false; Promise.allSettled([api<Item>(`/api/admin/schedule/observability?week_start=${WEEK_START}&editor=true`), api<Item>('/api/admin/groups'), api<Item>('/api/admin/people?kind=teacher'), api<Item>('/api/admin/people?kind=student')]).then(([scheduleResult, groupResult, teacherResult, studentResult]) => {
      if (cancelled) return;
      if (groupResult.status === 'fulfilled') setGroups((groupResult.value.items || []).map((item: Item) => ({ id: String(item.id), name: String(item.display_name || item.name), meta: String(item.subject || item.semantic_dimension || item.group_type || '') })));
      if (teacherResult.status === 'fulfilled') setTeachers((teacherResult.value.items || []).map((item: Item) => ({ id: String(item.id), name: String(item.display_name || item.name), meta: 'teacher', subjects: (item.teacher_assignments || []).map((assignment: Item) => String(assignment.subject || '')).filter(Boolean) })));
      if (studentResult.status === 'fulfilled') setStudents((studentResult.value.items || []).map((item: Item) => ({ id: String(item.id), name: String(item.display_name || item.name), meta: String(item.class_name || '') })));
      if (scheduleResult.status === 'fulfilled') { const data = scheduleResult.value; baseVersionRef.current = String(data.canonical?.version_id || 'current'); setBaseVersion(baseVersionRef.current); const mapped = (data.blocks || []).flatMap((block: Item) => (block.effective_assignments || []).map((assignment: Item, index: number) => assignmentToLesson(block, assignment, index))); const templateMapped = (data.blocks || []).flatMap((block: Item) => (block.baseline_assignments || []).map((assignment: Item, index: number) => assignmentToLesson({ ...block, weekday: block.baseline_weekday, start_time: block.baseline_start_time, end_time: block.baseline_end_time, grade_scope: block.baseline_grade_scope }, assignment, index))); setWeekBaseLessons(mapped); setTemplateBaseLessons(templateMapped); setLessons(mapped); setSelectedId(''); }
      else setNotice('Сервер расписания недоступен; редактирование отключено до восстановления соединения.');
    }).finally(() => { if (!cancelled) setLoading(false); }); return () => { cancelled = true; };
  }, [api]);
  useEffect(() => {
    if (baseVersion === 'current') return;
    let cancelled = false;
    api<Item>('/api/admin/schedule/template/preview', { method: 'POST', body: JSON.stringify({ expected_version_id: baseVersion }) })
      .then((result) => { if (cancelled) return; setPendingTemplate(result.status === 'preview' ? Number(result.overlay_patch_count || 0) : null); if (result.status === 'needs_confirmation') setNotice(String(result.message || 'Источник шаблона требует подтверждения.')); })
      .catch(() => { if (!cancelled) setPendingTemplate(null); });
    return () => { cancelled = true; };
  }, [api, baseVersion]);
  const baseLessons = layer === 'template' ? templateBaseLessons : weekBaseLessons;
  const scopeKey = layer === 'week' ? WEEK_START : baseVersion;
  useEffect(() => {
    if (!baseLessons.length) return;
    let cancelled = false;
    setLoading(true);
    api<Item>(`/api/admin/schedule/draft?scope_kind=${layer}&scope_key=${encodeURIComponent(scopeKey)}`).then((draft) => {
      if (cancelled) return;
      const changes = draft.payload?.changes || [];
      setDraftRevision(Number(draft.revision || 0)); revisionRef.current = Number(draft.revision || 0); setDraftChanges(changes); setDraftUpdatedAt(String(draft.updated_at || '')); setLessons(applyDraftChanges(baseLessons, changes, groups, teachers, students)); setDraftDirty(false); dirtyRef.current = false; setSaveStatus('saved');
      if (draft.has_changes) setNotice(`Продолжаем черновик · последнее изменение ${new Date(String(draft.updated_at)).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}`);
    }).catch(() => setSaveStatus('error')).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [api, baseLessons, baseVersion, layer]);
  useEffect(() => {
    if (!draftDirty) return;
    const generation = generationRef.current;
    setSaveStatus('saving');
    const timer = window.setTimeout(() => {
      api<Item>('/api/admin/schedule/draft', { method: 'PUT', body: JSON.stringify({ scope_kind: layer, scope_key: scopeKey, expected_revision: revisionRef.current, base_version_id: baseVersion === 'current' ? null : baseVersion, payload: { changes: draftChanges }, source_context: { editor: 'schedule-studio' } }) }).then((draft) => {
        revisionRef.current = Number(draft.revision); setDraftRevision(Number(draft.revision)); setDraftUpdatedAt(String(draft.updated_at || ''));
        if (generation === generationRef.current) { setDraftDirty(false); dirtyRef.current = false; setSaveStatus('saved'); }
      }).catch((error: Item) => { setSaveStatus(error?.status === 409 ? 'conflict' : 'error'); setDraftDirty(true); dirtyRef.current = true; });
    }, 700);
    return () => window.clearTimeout(timer);
  }, [api, baseVersion, draftChanges, draftDirty, layer, scopeKey]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (!dirtyRef.current) return; event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn);
  }, []);
  useEffect(() => { (window as Window & { __scheduleDraftDirty?: boolean }).__scheduleDraftDirty = draftDirty || saveStatus === 'saving' || saveStatus === 'error'; return () => { (window as Window & { __scheduleDraftDirty?: boolean }).__scheduleDraftDirty = false; }; }, [draftDirty, saveStatus]);
  const switchLayer = (next: ScheduleLayer) => { if (dirtyRef.current) { setNotice('Дождитесь сохранения черновика перед переключением.'); return; } setLayerState(next); setSelectedId(''); };
  const setMode = (next: ViewMode) => { setModeState(next); const params = new URLSearchParams(window.location.hash.split('?')[1] || ''); params.set('mode', next); params.set('grade', String(grade)); window.history.replaceState({}, '', `${window.location.pathname}${window.location.search}#/admin/schedule?${params}`); };
  const setSelectedGrade = (next: number) => { setGrade(next); const params = new URLSearchParams(window.location.hash.split('?')[1] || ''); params.set('mode', mode); params.set('grade', String(next)); window.history.replaceState({}, '', `${window.location.pathname}${window.location.search}#/admin/schedule?${params}`); };
  const visible = useMemo(() => lessons.filter((lesson) => { if (query && !`${displaySubject(lesson.subject)} ${lesson.teacher} ${lesson.room} ${lesson.audienceLabel}`.toLowerCase().includes(query.toLowerCase())) return false; if (mode === 'day' && lesson.day !== day) return false; if (mode === 'class' && lesson.grade !== grade) return false; return true; }), [day, grade, lessons, mode, query]);
  const attentionCount = new Set(lessons.filter((lesson) => lesson.warning).map((lesson) => String(lesson.block?.block_key || lesson.id))).size;
  useEffect(() => {
    if (!selected || mode !== 'class') return;
    const frame = window.requestAnimationFrame(() => {
      const board = document.querySelector<HTMLElement>('.studio-class-board');
      const card = Array.from(board?.querySelectorAll<HTMLElement>('[data-lesson-card]') || []).find((item) => item.dataset.lessonCard === selected.id);
      if (!board || !card) return;
      const boardBounds = board.getBoundingClientRect(); const cardBounds = card.getBoundingClientRect();
      if (cardBounds.left < boardBounds.left) board.scrollLeft += cardBounds.left - boardBounds.left - 12;
      else if (cardBounds.right > boardBounds.right) board.scrollLeft += cardBounds.right - boardBounds.right + 12;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [mode, selected]);
  const subjectOptions = SUBJECT_OPTIONS;
  const saveLesson = (next: Lesson) => { const slot = BELL_SCHEDULE[next.period - 1] || BELL_SCHEDULE[0]; const normalized = { ...next, start: slot.start, end: slot.end }; setLessons((items) => items.map((item) => item.id === normalized.id ? normalized : item)); const change = lessonToChange(normalized, groups, teachers, students); if (normalized.id.startsWith('manual-')) change.change_kind = 'added'; setDraftChanges((items) => [...items.filter((item) => !sameChange(item, change)), change]); generationRef.current += 1; setDraftDirty(true); dirtyRef.current = true; setSaveStatus('saving'); };
  const addLesson = (dayIndex: number, gradeNumber: number, period = 1) => { const slot = BELL_SCHEDULE[period - 1] || BELL_SCHEDULE[0]; const id = `manual-${crypto.randomUUID()}`; const lesson: Lesson = { id, day: dayIndex, date: WEEK_START, period, start: slot.start, end: slot.end, grade: gradeNumber, subject: '', teacher: '', room: '', groups: [], audienceMode: 'grade', audienceLabel: `Весь ${gradeNumber} класс`, students: [], audienceRule: { kind: 'whole_grade', grade: String(gradeNumber), group_ids: [], partition_dimension: '', include_student_ids: [], exclude_student_ids: [] }, teacherColor: TEACHER_COLORS[0], changed: true }; setLessons((items) => [...items, lesson]); setSelectedId(id); };
  const deleteLesson = (lesson: Lesson) => { if (!window.confirm(`Удалить «${lesson.subject}» из черновика?`)) return; const blockKey = String(lesson.block?.block_key || lesson.id); const change = { operation: 'delete', block_key: blockKey, assignment_index: lesson.assignmentIndex ?? 0, manual: true }; const existsInBase = baseLessons.some((item) => String(item.block?.block_key || item.id) === blockKey && Number(item.assignmentIndex ?? 0) === Number(lesson.assignmentIndex ?? 0)); setLessons((items) => items.filter((item) => item.id !== lesson.id)); setSelectedId(''); setDraftChanges((items) => { const previous = items.find((item) => sameChange(item, change)); const rest = items.filter((item) => !sameChange(item, change)); return previous?.change_kind === 'added' || !existsInBase ? rest : [...rest, change]; }); generationRef.current += 1; setDraftDirty(true); dirtyRef.current = true; setSaveStatus('saving'); };
  const publish = async () => {
    if (publishing || dirtyRef.current || !draftChanges.length) return;
    if (draftChanges.some((change) => change.review_required || change.lesson?.audience?.kind === 'unresolved')) {
      setShowPublish(false);
      setNotice('Сначала проверьте неоднозначные изменения в черновике. Они не будут опубликованы автоматически.');
      return;
    }
    setPublishing(true);
    let publishedId = '';
    try {
      const result = await api<Item>('/api/admin/schedule/draft/publish', { method: 'POST', body: JSON.stringify({ scope_kind: layer, scope_key: scopeKey, expected_revision: revisionRef.current, comment: 'Опубликовано из Schedule Studio' }) });
      publishedId = String(result.published_id || '');
      if (result.status !== 'published' || !publishedId) throw new Error('Сервер не подтвердил публикацию. Проверьте историю версий перед повтором.');
      setDraftChanges([]); setDraftRevision(0); revisionRef.current = 0; setDraftDirty(false); dirtyRef.current = false; setSaveStatus('saved'); setShowPublish(false);
      const [schedule, draft] = await Promise.all([
        api<Item>(`/api/admin/schedule/observability?week_start=${WEEK_START}&editor=true`),
        api<Item>(`/api/admin/schedule/draft?scope_kind=${layer}&scope_key=${encodeURIComponent(scopeKey)}`),
      ]);
      const readbackId = String(layer === 'template' ? schedule.canonical?.version_id : schedule.effective_week?.effective_week_id);
      if (readbackId !== publishedId || draft.has_changes) throw new Error(`Сервер создал версию ${publishedId}, но повторное чтение пока не подтвердило её. Не публикуйте повторно; обновите страницу и проверьте историю.`);
      const weekLessons = (schedule.blocks || []).flatMap((block: Item) => (block.effective_assignments || []).map((assignment: Item, index: number) => assignmentToLesson(block, assignment, index)));
      const templateLessons = (schedule.blocks || []).flatMap((block: Item) => (block.baseline_assignments || []).map((assignment: Item, index: number) => assignmentToLesson({ ...block, weekday: block.baseline_weekday, start_time: block.baseline_start_time, end_time: block.baseline_end_time, grade_scope: block.baseline_grade_scope }, assignment, index)));
      setWeekBaseLessons(weekLessons); setTemplateBaseLessons(templateLessons); setLessons(layer === 'template' ? templateLessons : weekLessons); setSelectedId('');
      baseVersionRef.current = String(schedule.canonical.version_id); setBaseVersion(baseVersionRef.current);
      setNotice(result.source_baseline?.status === 'pending' ? `Версия ${publishedId} опубликована, но источник изменился или недоступен: ${String(result.source_baseline.message || 'требуется повторная проверка')}` : `Опубликовано и проверено повторным чтением · версия ${publishedId}`);
    } catch (error: any) {
      if (!publishedId) setSaveStatus(error?.status === 409 ? 'conflict' : 'error');
      setNotice(publishedId ? String(error?.message || `Версия ${publishedId} создана, но повторное чтение не удалось.`) : `Публикация не подтверждена: ${String(error?.message || 'ошибка сервера')}`);
    } finally { setPublishing(false); }
  };
  const discard = async () => { if (!window.confirm('Отменить все изменения этого черновика? Это действие нельзя отменить.')) return; try { await api(`/api/admin/schedule/draft?scope_kind=${layer}&scope_key=${encodeURIComponent(scopeKey)}&expected_revision=${draftRevision}`, { method: 'DELETE' }); setDraftChanges([]); setLessons(baseLessons); setDraftRevision(0); revisionRef.current = 0; setDraftDirty(false); dirtyRef.current = false; setSaveStatus('saved'); setNotice('Изменения черновика отменены.'); } catch { setSaveStatus('error'); } };
  const applyImport = async (preview: Item) => { const proposed = preview.draft_changes || []; const result = await api<Item>('/api/admin/schedule/import-to-draft', { method: 'POST', body: JSON.stringify({ week_start: WEEK_START, scope_kind: layer, scope_key: layer === 'template' ? baseVersion : WEEK_START, expected_revision: revisionRef.current, proposed_changes: proposed, source_context: { source: 'google_sheet', fingerprint: preview.source_fingerprint, selected_tab: preview.selected_tab } }) }); const changes = result.payload?.changes || []; revisionRef.current = Number(result.revision); setDraftRevision(Number(result.revision)); setDraftChanges(changes); setLessons(applyDraftChanges(baseLessons, changes, groups, teachers, students)); setDraftDirty(false); dirtyRef.current = false; setSaveStatus('saved'); setDraftUpdatedAt(String(result.updated_at || '')); if (layer === 'template') setPendingTemplate(Number(preview.overlay_patch_count || 0)); };
  const confirmTemplate = async () => { if (layer !== 'template' || draftDirty || draftChanges.length || confirmingTemplate) return; setConfirmingTemplate(true); try { const result = await api<Item>('/api/admin/schedule/template/confirm', { method: 'POST', body: JSON.stringify({ expected_version_id: baseVersion }) }); setNotice(`Источник подтверждён для шаблона ${String(result.canonical_version_id)} · ${Number(result.mapped_block_count)} блоков сопоставлено`); } catch (error) { setNotice(`Не удалось подтвердить источник: ${String((error as Error)?.message || error)}`); } finally { setConfirmingTemplate(false); } };
  return <div className={`schedule-studio ${selected ? 'has-inspector' : ''}`}><section className="studio-main"><header className="studio-topbar"><div><p>MY ISLAND · УПРАВЛЕНИЕ ШКОЛОЙ</p><div className="studio-title-row"><h1>Расписание</h1><span className="studio-title-note">Неделя без хаоса <Sparkles size={14} /></span></div></div><div className="studio-topbar__actions"><button type="button" className="studio-secondary-button" onClick={() => setShowVersions(true)}><History size={16} /> История</button>{layer === 'template' ? <button type="button" className="studio-secondary-button" disabled={confirmingTemplate || draftDirty || draftChanges.length > 0 || baseVersion === 'current'} onClick={() => void confirmTemplate()}>{confirmingTemplate ? <RefreshCw size={16} className="admin-spin" /> : <Check size={16} />} {confirmingTemplate ? 'Подтверждаем…' : 'Подтвердить источник'}</button> : null}<button type="button" className="studio-import-button" onClick={() => setShowImport(true)}><CloudDownload size={17} /> Импорт из Google Sheets</button></div></header>
    <div className="studio-toolbar"><div className="studio-week-nav"><button type="button" aria-label="Предыдущая неделя"><ChevronLeft size={18} /></button><span><strong>21 — 25 сентября 2026</strong><small>{layer === 'template' ? 'Базовый шаблон' : 'Версия недели · 8 изменений'}</small></span><button type="button" aria-label="Следующая неделя"><ChevronRight size={18} /></button></div><div className="studio-search"><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Урок, учитель, кабинет…" /></div><button type="button" className="studio-filter-button"><ListFilter size={17} /> Фильтры</button><ModeSwitch value={mode} onChange={setMode} /></div>
    <div className="studio-context-bar"><div className="studio-layer-switch"><button type="button" className={layer === 'week' ? 'is-active' : ''} onClick={() => switchLayer('week')}>Неделя</button><button type="button" className={layer === 'template' ? 'is-active' : ''} onClick={() => switchLayer('template')}>Шаблон</button></div><span>{draftChanges.length ? `Черновик · ${draftChanges.length} изменений${draftUpdatedAt ? ` · ${new Date(draftUpdatedAt).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}` : ''}` : layer === 'template' ? `Шаблон ${baseVersion}` : '21–25 сентября · опубликованная версия'}</span>{pendingTemplate !== null && pendingTemplate > 0 ? <button type="button" className="studio-attention" onClick={() => { switchLayer('template'); setShowImport(true); }}>Изменения в источнике: {pendingTemplate}</button> : null}<div className={`studio-autosave is-${saveStatus}`}>{saveStatus === 'saving' ? <><RefreshCw size={14} className="admin-spin" /> Сохраняем…</> : saveStatus === 'error' ? <><AlertTriangle size={14} /> Не удалось сохранить</> : saveStatus === 'conflict' ? <><AlertTriangle size={14} /> Расписание изменилось в другой сессии</> : draftChanges.length ? <><Check size={14} /> Черновик сохранён{draftUpdatedAt ? ` · ${new Date(draftUpdatedAt).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}` : ''}</> : <><Check size={14} /> Сохранено</>}</div>{saveStatus === 'conflict' ? <div className="studio-conflict-actions"><button type="button" onClick={() => window.location.reload()}>Обновить данные</button><button type="button" onClick={() => setNotice('Сравнение: локальные изменения не отправлены; сервер содержит более новую ревизию черновика.')}>Сравнить изменения</button></div> : null}{draftChanges.length ? <button type="button" className="studio-discard-link" onClick={() => void discard()}>Отменить изменения</button> : null}<div className="studio-attention"><AlertTriangle size={15} /> Требуют внимания: {attentionCount}</div></div>
    {notice ? <div className="studio-notice"><span>{notice}</span><button type="button" onClick={() => setNotice('')}><X size={14} /></button></div> : null}{loading ? <div className="studio-loading-line"><RefreshCw size={14} className="admin-spin" /> Загружаем актуальное расписание…</div> : null}
    {mode === 'week' ? <div className="studio-week-board"><div className="studio-week-board__corner">День</div>{GRADES.map((item) => <div className="studio-grade-head" key={item}>{item} класс</div>)}{DAYS.map((date, dateIndex) => <div className="studio-week-row" key={date.short}><div className={`studio-day-label ${dateIndex === day ? 'is-current' : ''}`}><strong>{date.short}</strong><span>{date.date}</span></div>{GRADES.map((gradeNumber) => { const items = visible.filter((lesson) => lesson.day === dateIndex && lesson.grade === gradeNumber).sort((left, right) => left.period - right.period); const usedPeriods = new Set(items.map((lesson) => lesson.period)); const nextPeriod = BELL_SCHEDULE.findIndex((_, index) => !usedPeriods.has(index + 1)) + 1 || 1; return <div className="studio-week-cell" key={`${dateIndex}-${gradeNumber}`}>{items.map((lesson) => <LessonCard key={lesson.id} lesson={lesson} compact selected={selectedId === lesson.id} onClick={() => setSelectedId(lesson.id)} />)}<button type="button" className="studio-add-cell" onClick={() => addLesson(dateIndex, gradeNumber, nextPeriod)}><Plus size={14} /> Добавить урок</button></div>; })}</div>)}</div> : null}
    {mode === 'day' ? <><div className="studio-day-tabs">{DAYS.map((date, index) => <button type="button" key={date.short} className={day === index ? 'is-active' : ''} onClick={() => setDay(index)}><strong>{date.short}</strong><span>{date.date}</span></button>)}</div><div className="studio-day-board"><div className="studio-day-board__head"><span>Урок</span>{GRADES.map((item) => <strong key={item}>{item} класс</strong>)}</div>{BELL_SCHEDULE.map((slot, index) => <div className="studio-day-board__row" key={index}><time>{index + 1}-й<br />{slot.start}–{slot.end}</time>{GRADES.map((gradeNumber) => { const items = visible.filter((lesson) => lesson.period === index + 1 && lesson.grade === gradeNumber); return <div className="studio-day-slot" key={`${index}-${gradeNumber}`}>{items.map((lesson) => <LessonCard key={lesson.id} lesson={lesson} selected={selectedId === lesson.id} onClick={() => setSelectedId(lesson.id)} />)}<button type="button" className="studio-add-cell" onClick={() => addLesson(day, gradeNumber, index + 1)}><Plus size={14} /> Добавить</button></div>; })}</div>)}</div></> : null}
    {mode === 'class' ? <><div className="studio-class-selector"><div><span>Работа с параллелью</span><strong>{grade} класс</strong></div><div>{GRADES.map((item) => <button type="button" className={grade === item ? 'is-active' : ''} key={item} onClick={() => setSelectedGrade(item)}>{item}</button>)}</div></div><div className="studio-class-board"><div className="studio-class-days">{DAYS.map((date, dateIndex) => <section key={date.short}><header><div><strong>{date.full}</strong><span>{date.date}</span></div><span>{visible.filter((lesson) => lesson.day === dateIndex).length} занятий</span></header>{BELL_SCHEDULE.map((slot, index) => { const items = visible.filter((lesson) => lesson.day === dateIndex && lesson.period === index + 1); return <div className="studio-class-time" key={index}><time>{index + 1}-й<br />{slot.start}–{slot.end}</time><div>{items.map((lesson) => <LessonCard key={lesson.id} lesson={lesson} selected={selectedId === lesson.id} onClick={() => setSelectedId(lesson.id)} />)}<button type="button" className="studio-add-cell" onClick={() => addLesson(dateIndex, grade, index + 1)}><Plus size={14} /> Добавить</button></div></div>; })}</section>)}</div></div></> : null}
    <footer className="studio-footer"><div><span><i className="legend-dot is-teacher" /> Цвет преподавателя</span><span><SubjectIcon subject="Математика" size={14} /> Предмет</span><span><i className="legend-dot is-change" /> Изменено</span><span><AlertTriangle size={14} /> Требует внимания</span></div><button type="button" className="studio-primary-button" disabled={!draftChanges.length || draftDirty || saveStatus !== 'saved'} onClick={() => setShowPublish(true)}><Save size={16} /> Опубликовать изменения</button></footer>
  </section>{selected ? <LessonInspector lesson={selected} api={api} groups={groups} teachers={teachers} students={students} lessons={lessons} subjects={subjectOptions} saveStatus={saveStatus} onClose={() => { if (!selected.subject.trim() && selected.id.startsWith('manual-')) setLessons((items) => items.filter((item) => item.id !== selected.id)); setSelectedId(''); }} onSave={saveLesson} onDelete={deleteLesson} /> : null}{showVersions ? <VersionPanel layer={layer} onClose={() => setShowVersions(false)} /> : null}{showImport ? <ImportReview api={api} layer={layer} baseVersion={baseVersion} groups={groups} teachers={teachers} onClose={() => setShowImport(false)} onApply={applyImport} /> : null}{showPublish ? <div className="studio-modal-backdrop"><section className="studio-publish-modal"><header><div><p>ПУБЛИКАЦИЯ ВЕРСИИ</p><h2>{layer === 'template' ? 'Новая версия шаблона' : 'Новая версия недели'}</h2></div><button type="button" className="studio-icon-button" disabled={publishing} onClick={() => setShowPublish(false)}><X size={19} /></button></header><div className="studio-publish-summary"><strong>{publishing ? 'Публикуем и проверяем сохранённую версию…' : 'Проверьте изменения перед публикацией'}</strong><span>Изменено занятий: {draftChanges.filter((item) => item.operation === 'upsert').length}</span><span>Добавлено: {draftChanges.filter((item) => item.change_kind === 'added').length}</span><span>Удалено: {draftChanges.filter((item) => item.operation === 'delete').length}</span><span>Изменений аудитории: {draftChanges.filter((item) => item.audience_changed).length}</span><p>После публикации версия станет неизменяемой, а черновик очистится.</p></div><footer><button type="button" className="studio-secondary-button" disabled={publishing} onClick={() => setShowPublish(false)}>Продолжить редактирование</button><button type="button" className="studio-primary-button" disabled={publishing} onClick={() => void publish()}>{publishing ? <RefreshCw size={16} className="admin-spin" /> : <Save size={16} />} {publishing ? 'Публикуем…' : 'Опубликовать версию'}</button></footer></section></div> : null}</div>;
}
