/* oxlint-disable typescript/no-explicit-any, typescript/no-base-to-string, react/react-compiler, react-hooks/exhaustive-deps, jsx-a11y/prefer-tag-over-role */
'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, CalendarDays, ChevronRight, ExternalLink, Plus, RefreshCw, Search, ShieldAlert, UserRound, Users } from 'lucide-react';
import { audienceGrade, audienceGrades, audienceLabel, audienceShortLabel, classLabel as formatClassLabel, lessonLabel, subjectLabel } from './school-audience';
import { analyzeAssignmentMerges, assignmentIsActive } from './teacher-directory-logic';
import { useSchoolTeacherColors } from './use-school-teacher-colors';

type Row = Record<string, any>;
type Api = <T>(path: string, init?: RequestInit) => Promise<T>;
type Props = { api: Api; schoolMode?: boolean; connectionLabel?: string; navigate: (href: string) => void };
type Directory = { groups: Row[]; students: Row[]; teachers: Row[]; write_ready?: boolean; read_only?: boolean; grade_assignment_ready?: boolean };
type TeacherTab = 'main' | 'assignments' | 'schedule' | 'availability';
type ScheduleResult = { status?: string; source?: string; week_start?: string; available_weeks?: string[]; items?: Row[]; issues?: Row[] };

const personName = (row: Row) => String(row.display_name || row.name || '');
const normalize = (value: unknown) => String(value || '').trim().replace(/\s+/gu, ' ').toLocaleLowerCase('ru');
const classLabel = (group: Row) => formatClassLabel(group.base_class_name || group.display_name || group.name || '');
const groupTitle = (group: Row) => audienceLabel(group);
const prettyAssignment = (assignment: Row, groups: Row[]) => {
  const group = groups.find((item) => String(item.id) === String(assignment.group_id));
  if (group) return String(group.group_type || '').toLowerCase() === 'class' ? classLabel(group) : audienceShortLabel(group);
  if (assignment.grade_level) return `${assignment.grade_level} класс`;
  if (assignment.base_class_name) return formatClassLabel(assignment.base_class_name);
  return 'Аудитория не найдена в справочнике';
};
const when = (value: unknown) => value ? new Date(String(value)).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' }) : '';
const sourceLabel = (value: unknown) => ({ admin_override: 'Изменено администратором', legacy_import: 'Исторический импорт', school_import: 'Импорт школьного справочника', schedule_import: 'Импорт расписания' }[String(value || '')] || 'Источник не указан');
const dayNames = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];

const assignmentContext = (row: Row) => [normalize(subjectLabel(row.subject)), normalize(row.subject_subgroup), normalize(row.exam_track), normalize(row.capability || 'teach'), String(row.classroom_course_id || ''), String(row.valid_from || ''), String(row.valid_until || '')].join('|');

export function TeacherDirectoryAdmin({ api, schoolMode = false, connectionLabel = 'Локальная база', navigate }: Props) {
  const [directory, setDirectory] = useState<Directory>({ groups: [], students: [], teachers: [] });
  const [selectedId, setSelectedId] = useState('');
  const [tab, setTab] = useState<TeacherTab>('main');
  const [editing, setEditing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<Row | null>(null);
  const [search, setSearch] = useState('');
  const [subjectFilter, setSubjectFilter] = useState('');
  const [classFilter, setClassFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('active');
  const [listView, setListView] = useState<'table' | 'cards'>('table');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [subjectSearch, setSubjectSearch] = useState('');
  const [assignmentOpen, setAssignmentOpen] = useState(false);
  const [editingAssignmentId, setEditingAssignmentId] = useState('');
  const [openAssignmentMenuId, setOpenAssignmentMenuId] = useState('');
  const [assignment, setAssignment] = useState({ subject: '', audience: 'group', targetId: '', grade: '', course: '' });
  const [schedule, setSchedule] = useState<ScheduleResult | null>(null);
  const [scheduleLoading, setScheduleLoading] = useState(false);
  const [scheduleError, setScheduleError] = useState('');
  const [selectedWeek, setSelectedWeek] = useState('template');
  const [schoolSession, setSchoolSession] = useState(!schoolMode);
  const [authChecking, setAuthChecking] = useState(schoolMode);
  const [schoolCode, setSchoolCode] = useState('');
  const [authError, setAuthError] = useState('');

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      let next: Directory;
      if (schoolMode) {
        const result = await api<Directory>('/api/admin/school/directory');
        next = { groups: result.groups || [], students: result.students || [], teachers: result.teachers || [], write_ready: result.write_ready, read_only: result.read_only, grade_assignment_ready: result.grade_assignment_ready };
      } else {
        const [groups, teachers, system] = await Promise.all([
          api<{ items: Row[] }>('/api/admin/groups'),
          api<{ items: Row[] }>('/api/admin/people?kind=teacher&include_inactive=true'),
          api<Row>('/api/admin/system').catch(() => ({} as Row)),
        ]);
        next = { groups: groups.items || [], teachers: teachers.items || [], students: [], write_ready: system.health?.environment === 'local_sqlite' || system.settings?.database_configured === false };
      }
      setDirectory(next);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось загрузить преподавателей.'); }
    finally { setLoading(false); }
  }, [api, schoolMode]);

  useEffect(() => {
    if (!schoolMode) return;
    void api('/api/admin/school/auth/session').then(() => setSchoolSession(true)).catch(() => setSchoolSession(false)).finally(() => setAuthChecking(false));
  }, [api, schoolMode]);
  useEffect(() => { if (schoolSession) void load(); }, [load, schoolSession]);
  useEffect(() => { if (!notice) return; const timer = window.setTimeout(() => setNotice(''), 3600); return () => window.clearTimeout(timer); }, [notice]);

  const teachers = directory.teachers || [];
  const colorTeachers = useMemo(() => teachers.map((teacher) => ({ id: String(teacher.id), name: personName(teacher) })), [teachers]);
  const teacherColorScope = `my-island-school:${String((import.meta as ImportMeta & { env?: Record<string, string> }).env?.VITE_API_BASE_URL || 'same-origin')}:school`;
  const teacherColors = useSchoolTeacherColors(api, 'school', teacherColorScope, colorTeachers, [], schoolMode);
  const selected = teachers.find((person) => String(person.id) === selectedId) || null;
  const groupById = useMemo(() => new Map((directory.groups || []).map((item) => [String(item.id), item])), [directory.groups]);
  const assignmentRows = (teacher: Row) => teacher.teacher_assignments || teacher.assignments || [];
  const subjectOptions = useMemo(() => {
    const values = new Map<string, string>();
    for (const teacher of teachers) {
      for (const subject of [...(teacher.subjects || []), ...assignmentRows(teacher).map((item: Row) => item.subject)]) {
        const text = subjectLabel(subject);
        if (text && !values.has(normalize(text))) values.set(normalize(text), text);
      }
    }
    for (const group of directory.groups || []) {
      const text = subjectLabel(group.subject);
      if (text && !values.has(normalize(text))) values.set(normalize(text), text);
    }
    return [...values.values()].sort((a, b) => a.localeCompare(b, 'ru'));
  }, [directory.groups, teachers]);
  const classes = useMemo(() => (directory.groups || []).filter((item) => String(item.group_type).toLowerCase() === 'class').sort((a, b) => classLabel(a).localeCompare(classLabel(b), 'ru', { numeric: true })), [directory.groups]);
  const parallels = useMemo(() => [...new Set(classes.map((item) => classLabel(item).match(/^\d+/u)?.[0]).filter(Boolean))].sort((a, b) => Number(a) - Number(b)), [classes]);
  const list = teachers.filter((teacher) => {
    const assignments = assignmentRows(teacher);
    const text = `${personName(teacher)} ${(teacher.subjects || []).join(' ')} ${assignments.map((item: Row) => item.subject).join(' ')} ${assignments.map((item: Row) => prettyAssignment(item, directory.groups)).join(' ')}`.toLocaleLowerCase('ru');
    const active = teacher.status !== 'inactive';
    const hasSubject = (teacher.subjects || []).some((item: string) => normalize(item) === normalize(subjectFilter)) || assignments.some((item: Row) => normalize(item.subject) === normalize(subjectFilter));
    const hasClass = assignments.some((item: Row) => {
      const group = groupById.get(String(item.group_id));
      const label = group ? classLabel(group) : String(item.base_class_name || '');
      return String(item.grade_level || '') === classFilter || label === classFilter || label.startsWith(`${classFilter}-`) || label.startsWith(`${classFilter} `) || (group && audienceGrade(group) === classFilter);
    });
    return text.includes(search.toLocaleLowerCase('ru').trim()) && (!subjectFilter || hasSubject) && (!classFilter || hasClass) && (statusFilter === 'all' || (statusFilter === 'active' ? active : !active));
  }).sort((a, b) => personName(a).localeCompare(personName(b), 'ru'));

  const openTeacher = (teacher: Row) => { setSelectedId(String(teacher.id)); setDraft({ ...teacher, comment: teacher.comment ?? teacher.admin_note ?? '' }); setEditing(false); setCreating(false); setTab('main'); setAssignmentOpen(false); setError(''); };
  const openCreate = () => { setSelectedId(''); setDraft({ display_name: '', kind: 'teacher', status: 'active', subjects: [], comment: '', teacher_assignments: [] }); setEditing(true); setCreating(true); setTab('main'); setAssignmentOpen(false); setError(''); };
  const saveTeacher = async () => {
    if (!draft?.display_name?.trim()) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const body = { display_name: draft.display_name.trim(), subjects: draft.subjects || [], status: draft.status || 'active', comment: draft.comment || '', ...(creating ? { kind: 'teacher' } : {}) };
      const result = await api<{ id?: string | number }>(creating ? '/api/admin/identities' : `/api/admin/people/${encodeURIComponent(String(selectedId))}`, { method: creating ? 'POST' : 'PATCH', body: JSON.stringify(body) });
      const id = String(result.id ?? selectedId);
      setSelectedId(id); setCreating(false); setEditing(false); setNotice('Данные преподавателя сохранены.');
      await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось сохранить преподавателя.'); }
    finally { setBusy(false); }
  };
  const commitAssignment = async (row: Row, active: boolean) => {
    setBusy(true); setError(''); setNotice('');
    try {
      await api('/api/admin/teacher-assignments', { method: 'POST', body: JSON.stringify({ teacher_identity_id: selectedId, ...(active ? row.id ? { assignment_id: String(row.id), ...(row.grade_level ? { grade_level: row.grade_level } : { group_id: String(row.group_id) }) } : row.grade_level ? { grade_level: row.grade_level } : { group_id: String(row.group_id) } : { assignment_id: String(row.id) }), subject: String(row.subject || ''), exam_track: row.exam_track || null, active }) });
      setAssignmentOpen(false); setEditingAssignmentId(''); setNotice(active ? row.id ? 'Назначение изменено.' : 'Назначение добавлено.' : 'Назначение снято.');
      window.dispatchEvent(new Event('school-directory-updated'));
      await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось сохранить назначение.'); }
    finally { setBusy(false); }
  };
  const addAssignment = async () => {
    if (!selectedId || !assignment.subject) return;
    if (editingAssignmentId) return commitAssignment({ id: editingAssignmentId, ...(assignment.audience === 'parallel' ? { grade_level: assignment.grade } : { group_id: assignment.targetId }), subject: assignment.subject, exam_track: assignment.course }, true);
    if (assignment.audience === 'parallel') return commitAssignment({ subject: assignment.subject, grade_level: assignment.grade, exam_track: assignment.course }, true);
    return commitAssignment({ subject: assignment.subject, group_id: assignment.targetId, exam_track: assignment.course }, true);
  };
  const editAssignment = (item: Row) => {
    setOpenAssignmentMenuId('');
    const group = groupById.get(String(item.group_id));
    if (!group && !item.grade_level) { setError('Не удалось изменить назначение: аудитория отсутствует в canonical-справочнике.'); return; }
    const isClass = String(group?.group_type).toLowerCase() === 'class';
    setEditingAssignmentId(String(item.id));
    setAssignment({ subject: String(item.subject || ''), audience: item.grade_level ? 'parallel' : isClass ? 'class' : 'group', targetId: item.group_id ? String(item.group_id) : '', grade: String(item.grade_level || (group ? audienceGrades(group, directory.students)[0] : '') || ''), course: String(item.exam_track || '') });
    setAssignmentOpen(true);
    setError('');
  };

  useEffect(() => {
    if (!selected) return;
    let mounted = true;
    setScheduleLoading(true); setScheduleError('');
    const path = schoolMode
      ? `/api/admin/school/teachers/${encodeURIComponent(selectedId)}/schedule${selectedWeek === 'template' ? '' : `?week_start=${encodeURIComponent(selectedWeek)}`}`
      : `/api/admin/schedule/observability?editor=true${selectedWeek === 'template' ? '' : `&week_start=${encodeURIComponent(selectedWeek)}`}`;
    api<ScheduleResult>(path).then((result) => {
      if (!mounted) return;
      if (schoolMode) setSchedule(result);
      else {
        const blocks = (result as any).blocks || [];
        const items = blocks.flatMap((block: Row) => (block.assignments || []).filter((item: Row) => (item.teacher_ids || item.teacherIds || []).map(String).includes(selectedId)).map((item: Row) => ({ weekday: block.weekday, start_time: block.start_time, end_time: block.end_time, activity: item.activity, audiences: item.groups || [], room: item.room, teacher_ids: item.teacher_ids || item.teacherIds || [] })));
        setSchedule({ ...result, source: selectedWeek === 'template' ? 'canonical_template' : 'published_week', items, available_weeks: (result as any).weeks?.map((item: Row) => String(item.week_start || item)) || [] });
      }
    }).catch((cause) => { if (mounted) setScheduleError(cause instanceof Error ? cause.message : 'Расписание недоступно.'); })
      .finally(() => { if (mounted) setScheduleLoading(false); });
    return () => { mounted = false; };
  }, [api, schoolMode, selected, selectedId, selectedWeek]);

  if (schoolMode && authChecking) return <div className="admin-panel">Проверяем доступ к школьному справочнику…</div>;
  if (schoolMode && !schoolSession) return <section className="admin-panel school-editor-auth"><h2>Вход в школьный справочник</h2><p>Параметр role=admin сам по себе не даёт доступ к настоящей базе. Получите одноразовый код командой /admincode в школьном Telegram-боте.</p><form onSubmit={async (event) => { event.preventDefault(); setAuthError(''); try { const result = await api<{ browser_session_token: string }>('/api/admin/school/auth/exchange', { method: 'POST', body: JSON.stringify({ code: schoolCode.trim() }) }); window.sessionStorage.setItem('my_island_school_admin_session', result.browser_session_token); setSchoolCode(''); setSchoolSession(true); } catch (cause) { setAuthError(cause instanceof Error ? cause.message : 'Код не принят.'); } }}><label className="school-field">Одноразовый код<input value={schoolCode} onChange={(event) => setSchoolCode(event.target.value)} autoComplete="one-time-code" /></label><button className="admin-button admin-button--primary" disabled={!schoolCode.trim()}>Войти</button></form>{authError ? <p role="alert" className="admin-error-text">{authError}</p> : null}</section>;
  if (loading && !teachers.length) return <div className="admin-panel">Загружаем преподавателей…</div>;
  if (selected || creating) {
    const teacher = draft || selected || {};
    const assignments: Row[] = assignmentRows(selected || teacher);
    const activeAssignments = assignments.filter((item: Row) => assignmentIsActive(item));
    const mergeAnalysis = analyzeAssignmentMerges(assignments, directory.teachers, directory.groups, directory.students);
    const eligibleMerges = mergeAnalysis.filter((item) => item.eligible);
    const mergedAssignmentIds = new Set(eligibleMerges.flatMap((item) => item.rows.map((row) => String(row.id))));
    const displayAssignments = [
      ...activeAssignments.filter((item) => !mergedAssignmentIds.has(String(item.id))),
      ...eligibleMerges.map((merge) => ({ _display_grade: merge.grade, _display_subject: merge.subject, _source_rows: merge.rows, _merge_groups: merge.groups, _merge_summary: merge.reason })),
    ];
    const assignmentGroups = Object.entries(displayAssignments.reduce((buckets: Record<string, Row[]>, item: Row) => {
      const group = groupById.get(String(item.group_id));
      const grades = item._display_grade ? [String(item._display_grade)] : item.grade_level ? [String(item.grade_level)] : group ? audienceGrades(group, directory.students) : [];
      const key = grades.length === 1 ? `${grades[0]} класс` : grades.length > 1 ? 'Межвозрастные группы' : 'Другие назначения';
      (buckets[key] ||= []).push(item);
      return buckets;
    }, {})).sort(([left], [right]) => Number.parseInt(left, 10) - Number.parseInt(right, 10) || left.localeCompare(right, 'ru'));
    const historicalAssignments = assignments.filter((item: Row) => !assignmentIsActive(item));
    const currentTeacherSubjects = Array.isArray(teacher.subjects) ? teacher.subjects as string[] : [];
    const subjectSearchResults = subjectOptions.filter((item) => item.toLocaleLowerCase('ru').includes(subjectSearch.toLocaleLowerCase('ru')));
    const scheduled = schedule?.items || [];
    const activeScheduled = scheduled.filter((item) => String(item.activity || '').toUpperCase() !== 'NO_LESSON');
    const scheduleHasData = Boolean(schedule?.status && !['canonical_not_imported', 'canonical_backend_disabled', 'canonical_not_materialized', 'week_not_available', 'teacher_not_found'].includes(schedule.status));
    return <div className="admin-stack teacher-workspace">
      <button className="teacher-back" onClick={() => { setSelectedId(''); setDraft(null); setEditing(false); setCreating(false); setAssignmentOpen(false); setError(''); }}><ArrowLeft size={16} /> К списку преподавателей</button>
      <header className="teacher-profile-header"><div className="teacher-avatar" style={selected ? { background: teacherColors.color(String(selected.id)), color: teacherColors.accent(String(selected.id)) } : undefined}><UserRound size={24} /></div><div className="teacher-profile-title"><p className="admin-eyebrow">Карточка преподавателя</p><h2>{personName(teacher) || 'Новый преподаватель'}</h2><div className="teacher-subtitle">{(teacher.subjects || []).join(' · ') || 'Предметы не указаны'} <span className={`teacher-status ${teacher.status === 'inactive' ? 'is-inactive' : ''}`}>{teacher.status === 'inactive' ? 'Неактивен' : 'Активен'}</span></div></div>{!editing && !creating ? <button className="admin-button admin-button--quiet" onClick={() => { setDraft({ ...selected, comment: selected?.comment ?? selected?.admin_note ?? '' }); setEditing(true); }} disabled={schoolMode && !directory.write_ready}>Редактировать основное</button> : null}</header>
      {notice ? <p className="teacher-toast" role="status">{notice}</p> : null}{error ? <p className="admin-error-text" role="alert">{error}</p> : null}
      <div className="teacher-summary-grid"><div><strong>{activeAssignments.length}</strong><span>активных назначений</span></div><div><strong>{scheduleHasData ? activeScheduled.length : '—'}</strong><span>занятий за выбранную неделю</span></div><div><strong>{scheduleHasData ? `${activeScheduled.length} уроков` : 'Нет данных'}</strong><span>недельная нагрузка · по расписанию</span></div><div><strong>{(teacher.availability || []).length ? 'Есть' : 'Не заданы'}</strong><span>ограничения по времени</span></div></div>
      <nav className="teacher-tabs" aria-label="Карточка преподавателя">{([['main', 'Основное'], ['assignments', 'Группы и назначения'], ['schedule', 'Расписание'], ['availability', 'Ограничения']] as [TeacherTab, string][]).map(([key, label]) => <button key={key} className={tab === key ? 'is-active' : ''} onClick={() => { setTab(key); if (key === 'schedule') setSelectedWeek('template'); }}>{label}</button>)}</nav>
      {tab === 'main' ? <section className="admin-panel teacher-panel">
        <div className="teacher-section-heading"><div><h3>Основная информация</h3><p>Предметы и комментарий доступны для редактирования с сохранением аудита.</p></div></div>
        <div className="teacher-main-grid"><label className="school-field">ФИО<input disabled={!editing} value={teacher.display_name || ''} onChange={(event) => setDraft({ ...teacher, display_name: event.target.value })} placeholder="Имя и фамилия" /></label><label className="school-field">Статус<select disabled={!editing} value={teacher.status || 'active'} onChange={(event) => setDraft({ ...teacher, status: event.target.value })}><option value="active">Активен</option><option value="inactive">Неактивен</option></select></label>
          <div className="teacher-subject-editor"><label className="school-field">Предметы школы<input disabled={!editing} value={subjectSearch} onChange={(event) => setSubjectSearch(event.target.value)} placeholder="Найти предмет" /></label><div className="teacher-subject-options">{subjectSearchResults.map((item) => <label key={normalize(item)}><input type="checkbox" disabled={!editing} checked={currentTeacherSubjects.some((value) => normalize(value) === normalize(item))} onChange={() => { const included = currentTeacherSubjects.some((value) => normalize(value) === normalize(item)); const used = assignments.some((row: Row) => normalize(row.subject) === normalize(item)); if (included && used && !window.confirm(`Предмет «${item}» используется в назначениях. Список предметов изменится, назначения сохранятся. Продолжить?`)) return; setDraft({ ...teacher, subjects: included ? currentTeacherSubjects.filter((value) => normalize(value) !== normalize(item)) : [...currentTeacherSubjects, item] }); }} /><span>{item}</span></label>)}{!subjectSearchResults.some((item) => normalize(item) === normalize(subjectSearch)) && subjectSearch.trim() ? <button className="teacher-add-subject" disabled={!editing} onClick={() => { const value = subjectSearch.trim().replace(/\s+/gu, ' '); if (!currentTeacherSubjects.some((item) => normalize(item) === normalize(value))) setDraft({ ...teacher, subjects: [...currentTeacherSubjects, value] }); setSubjectSearch(''); }}>Добавить новое название «{subjectSearch.trim()}»</button> : null}</div><div className="teacher-chip-list">{currentTeacherSubjects.map((item) => <button type="button" key={normalize(item)} disabled={!editing} onClick={() => { const used = assignments.some((row: Row) => normalize(row.subject) === normalize(item)); if (used && !window.confirm(`Предмет «${item}» используется в назначениях. Назначения сохранятся. Удалить предмет из списка преподавателя?`)) return; setDraft({ ...teacher, subjects: currentTeacherSubjects.filter((value) => normalize(value) !== normalize(item)) }); }}>{item}{editing ? <span aria-hidden="true">×</span> : null}</button>)}</div><small>Назначения и их источники сохраняются, даже если предмет удалён из списка преподавателя.</small></div>
          <label className="school-field teacher-comment">Комментарий<textarea disabled={!editing} value={teacher.comment || ''} onChange={(event) => setDraft({ ...teacher, comment: event.target.value })} placeholder="Необязательно" /></label>
        </div>
        {editing ? <div className="teacher-actions"><button className="admin-button admin-button--primary" disabled={busy || !teacher.display_name?.trim() || (schoolMode && !directory.write_ready)} onClick={() => void saveTeacher()}>{busy ? 'Сохраняем…' : creating ? 'Создать преподавателя' : 'Сохранить'}</button><button className="admin-button admin-button--quiet" onClick={() => { if (creating) { setCreating(false); setDraft(null); } else { setEditing(false); setDraft(selected); } }}>Отмена</button></div> : null}
      </section> : null}
      {tab === 'assignments' ? <section className="admin-panel teacher-panel"><div className="teacher-section-heading"><div><h3>Группы и назначения</h3><p>Назначение задаёт преподавателя по умолчанию. Уроки в расписании можно назначить другому учителю.</p></div><button className="admin-button admin-button--primary" disabled={!selectedId || (schoolMode && !directory.write_ready)} onClick={() => { setAssignment({ subject: currentTeacherSubjects[0] || '', audience: schoolMode && !directory.grade_assignment_ready ? 'group' : 'parallel', targetId: '', grade: parallels[0] || '', course: '' }); setAssignmentOpen((value) => !value); }}><Plus size={15} /> Добавить назначение</button></div>
        {assignmentOpen ? <div className="teacher-assignment-form"><label className="school-field">Предмет<select value={assignment.subject} onChange={(event) => setAssignment({ ...assignment, subject: event.target.value })}><option value="">Выберите предмет</option>{[...new Set([...currentTeacherSubjects, ...subjectOptions])].map((value) => <option key={normalize(value)}>{value}</option>)}</select></label><fieldset disabled={Boolean(editingAssignmentId)}><legend>Кому преподаёт?</legend>{([['parallel', 'Вся параллель'], ['class', 'Административный класс'], ['group', 'Учебная группа']] as [string, string][]).map(([value, label]) => <label key={value}><input type="radio" name="teacher-audience" disabled={value === 'parallel' && schoolMode && !directory.grade_assignment_ready} checked={assignment.audience === value} onChange={() => setAssignment({ ...assignment, audience: value, targetId: '' })} />{label}</label>)}</fieldset>
          {schoolMode && !directory.grade_assignment_ready ? <small>Назначение на всю параллель станет доступно после согласования миграции 036. Класс и группу можно назначать сейчас.</small> : null}
          <label className="school-field">Параллель<select value={assignment.grade} onChange={(event) => setAssignment({ ...assignment, grade: event.target.value, targetId: '' })}><option value="">Выберите параллель</option>{parallels.map((value) => <option key={value} value={value}>{value}-й класс</option>)}</select></label>
          {assignment.audience !== 'parallel' ? <label className="school-field">{assignment.audience === 'class' ? 'Класс' : 'Учебная группа'}<select value={assignment.targetId} onChange={(event) => setAssignment({ ...assignment, targetId: event.target.value })}><option value="">Выберите аудиторию</option>{(assignment.audience === 'class' ? classes : (directory.groups || []).filter((item) => String(item.group_type).toLowerCase() !== 'class')).filter((item) => !assignment.grade || audienceGrades(item, directory.students).includes(assignment.grade)).map((item) => <option key={item.id} value={item.id}>{groupTitle(item)}</option>)}</select></label> : null}
          <label className="school-field">Курс / формат (необязательно)<select value={assignment.course} onChange={(event) => setAssignment({ ...assignment, course: event.target.value })}><option value="">Обычные занятия</option><option value="ОГЭ">Подготовка к ОГЭ</option><option value="ЕГЭ база">ЕГЭ · база</option><option value="ЕГЭ профиль">ЕГЭ · профиль</option><option value="База">Базовый курс</option><option value="Профиль">Профильный курс</option></select></label>
          <div className="teacher-actions"><button className="admin-button admin-button--primary" disabled={busy || !assignment.subject || (assignment.audience === 'parallel' ? !assignment.grade : !assignment.targetId)} onClick={() => void addAssignment()}>{editingAssignmentId ? 'Сохранить изменения' : 'Сохранить назначение'}</button><button className="admin-button admin-button--quiet" onClick={() => { setAssignmentOpen(false); setEditingAssignmentId(''); }}>Отмена</button></div>
        </div> : null}
        {mergeAnalysis.length ? <details className="teacher-merge-proposals"><summary>Предложения по покрытию параллели <small>{mergeAnalysis.filter((item) => item.eligible).length} подтверждено · {mergeAnalysis.filter((item) => !item.eligible).length} с ограничениями</small></summary><p>Это только визуальная проверка текущих назначений. Запись в базе и массовое объединение отключены; для назначения на параллель требуется отдельное согласование и миграция 036.</p>{mergeAnalysis.map((merge) => <article key={`${merge.grade}:${assignmentContext(merge.rows[0])}`} className={merge.eligible ? 'is-eligible' : 'is-blocked'}><div><strong>{merge.subject} · {merge.grade} класс</strong><small>{merge.groups.map(groupTitle).join(' + ')} · состав {merge.covered}/{merge.expected}</small><small>{merge.reason}</small></div><button disabled title="Сохранение объединённого назначения требует отдельного согласования">Предпросмотр</button></article>)}</details> : null}
        {displayAssignments.length ? <div className="teacher-assignment-list">{assignmentGroups.map(([gradeLabel, rows]) => <section key={gradeLabel} className="teacher-assignment-grade"><h4>{gradeLabel}</h4>{rows.sort((a, b) => String(a._display_subject || a.subject).localeCompare(String(b._display_subject || b.subject), 'ru') || prettyAssignment(a, directory.groups).localeCompare(prettyAssignment(b, directory.groups), 'ru')).map((item: Row, index: number) => { const sources: Row[] = item._source_rows || [item]; const aggregate = Boolean(item._source_rows); const audience = aggregate ? undefined : groupById.get(String(item.group_id)); const menuId = String(item.id || `${item.group_id}:${item.subject}:${index}`); const menuOpen = openAssignmentMenuId === menuId; return <article key={aggregate ? `aggregate:${item._display_grade}:${item._display_subject}` : item.id || `${item.group_id}:${item.subject}:${index}`}><div className="teacher-assignment-mark"><Users size={16} /></div><div className="teacher-assignment-info"><strong>{aggregate ? `${subjectLabel(item._display_subject)} · ${item._display_grade} класс` : lessonLabel(item.subject, audience || (item.grade_level ? `${item.grade_level} класс` : prettyAssignment(item, directory.groups)))}{item.exam_track ? ` · ${item.exam_track}` : ''}</strong><span>{aggregate ? `Вся ${item._display_grade}-я параллель` : prettyAssignment(item, directory.groups)}</span><small>{aggregate ? `Сводный вид из ${sources.length} действующих назначений · это не отдельная запись в базе` : `Активно${item.valid_from || item.valid_until ? ` · ${item.valid_from ? `с ${when(item.valid_from)}` : ''}${item.valid_until ? ` по ${when(item.valid_until)}` : ''}` : ''}`}</small><details className="teacher-provenance"><summary>{aggregate ? 'Группы и источники' : 'Сведения об источнике'}</summary>{sources.map((source) => <small key={source.id}>{prettyAssignment(source, directory.groups)} · {sourceLabel(source.source)}{source.valid_from || source.valid_until ? ` · ${source.valid_from ? `с ${when(source.valid_from)}` : ''}${source.valid_until ? ` по ${when(source.valid_until)}` : ''}` : ''}</small>)}</details></div><div className="teacher-assignment-actions">{aggregate ? <details><summary>Исходные записи</summary>{sources.map((source) => <div className="teacher-source-actions" key={source.id}><span>{prettyAssignment(source, directory.groups)}</span><button onClick={() => navigate(`/admin/people?group_id=${encodeURIComponent(String(source.group_id))}`)}>Открыть</button><button disabled={schoolMode && !directory.write_ready} onClick={() => editAssignment(source)}>Изменить</button><button className="is-danger" disabled={busy || (schoolMode && !directory.write_ready)} onClick={() => { if (window.confirm(`Снять назначение «${subjectLabel(source.subject)} · ${prettyAssignment(source, directory.groups)}»?`)) void commitAssignment(source, false); }}>Снять</button></div>)}</details> : <><button className="admin-button admin-button--quiet" title="Открыть аудиторию" onClick={() => navigate(`/admin/people?group_id=${encodeURIComponent(String(item.group_id))}`)} disabled={!item.group_id}><ExternalLink size={15} /> Открыть</button><div className="teacher-assignment-menu"><button type="button" className="teacher-assignment-menu-trigger" aria-label="Действия с назначением" aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setOpenAssignmentMenuId(menuOpen ? '' : menuId)}>···</button>{menuOpen ? <div className="teacher-assignment-menu-popover" role="menu"><button type="button" role="menuitem" onClick={() => navigate(`/admin/people?group_id=${encodeURIComponent(String(item.group_id))}`)} disabled={!item.group_id}>Открыть группу</button><button type="button" role="menuitem" disabled={schoolMode && !directory.write_ready} onClick={() => editAssignment(item)}>Изменить назначение</button><button type="button" role="menuitem" className="is-danger" disabled={busy || (schoolMode && !directory.write_ready)} onClick={() => { setOpenAssignmentMenuId(''); if (window.confirm(`Снять назначение «${subjectLabel(item.subject)} · ${prettyAssignment(item, directory.groups)}»?`)) void commitAssignment(item, false); }}>Снять назначение</button></div> : null}</div></>}</div></article>; })}</section>)}</div> : <div className="admin-empty">Действующих назначений пока нет</div>}
        {historicalAssignments.length ? <details className="teacher-assignment-history"><summary>История назначений <small>{historicalAssignments.length}</small></summary>{historicalAssignments.map((item: Row) => <article key={item.id}><strong>{item.subject || 'Предмет не указан'}</strong><span>{prettyAssignment(item, directory.groups)}</span><small>Неактивно · {sourceLabel(item.source)}{item.valid_from || item.valid_until ? ` · ${item.valid_from ? `с ${when(item.valid_from)}` : ''}${item.valid_until ? ` по ${when(item.valid_until)}` : ''}` : ''}</small></article>)}</details> : null}      </section> : null}
      {tab === 'schedule' ? <section className="admin-panel teacher-panel"><div className="teacher-section-heading"><div><h3>Расписание преподавателя</h3><p>{schedule?.source === 'published_week' ? `Подтверждённая датированная неделя · ${schedule.week_start || selectedWeek}` : schedule?.source === 'canonical_template' ? 'Базовый canonical-шаблон расписания' : 'Расписание конструктора My Island'}</p></div><div className="teacher-schedule-controls"><select aria-label="Шаблон или неделя" value={selectedWeek} onChange={(event) => setSelectedWeek(event.target.value)}><option value="template">Базовый шаблон</option>{(schedule?.available_weeks || []).map((week) => <option key={week} value={week}>{when(week)} · неделя с {week}</option>)}</select><button className="admin-button admin-button--quiet" onClick={() => navigate('/admin/schedule/constructor')}><CalendarDays size={15} /> Открыть конструктор</button></div></div>
        {scheduleLoading ? <div className="admin-empty">Загружаем расписание…</div> : scheduleError ? <p className="admin-error-text" role="alert">{scheduleError}</p> : schedule?.status === 'week_not_available' ? <div className="admin-empty">Для этой недели нет опубликованного снимка. Показывается только базовый шаблон.</div> : !scheduleHasData ? <div className="admin-empty">Подтверждённый canonical-шаблон расписания пока недоступен.</div> : scheduled.length ? <div className="teacher-schedule-list">{[...scheduled].sort((a, b) => Number(a.weekday) - Number(b.weekday) || String(a.start_time).localeCompare(String(b.start_time))).map((item, index) => <article key={`${item.weekday}:${item.start_time}:${item.end_time}:${item.activity}:${index}`}><strong>{dayNames[Number(item.weekday)] || when(item.date) || 'День не указан'}</strong><time>{item.start_time || '—'}–{item.end_time || '—'}</time><div><b>{item.activity || 'Предмет не указан'}</b><small>{(item.audiences || []).join(', ') || item.grade_scope || 'Аудитория не указана'}{item.room ? ` · каб. ${item.room}` : ''}</small></div><button aria-label="Открыть конструктор" title="Открыть конструктор" onClick={() => navigate('/admin/schedule/constructor')}><ChevronRight size={16} /></button></article>)}</div> : <div className="admin-empty">В выбранном источнике нет уроков этого преподавателя.</div>}
        {schedule?.issues?.length ? <p className="teacher-schedule-warning"><ShieldAlert size={15} /> В выбранной неделе найдены возможные пересечения занятий преподавателя: {schedule.issues.length}.</p> : null}<div className="teacher-load-note">Нагрузка показана как число уникальных занятий. Учебные периоды не рассчитываются без надёжного соответствия длительности звонкам.</div>
      </section> : null}
      {tab === 'availability' ? <section className="admin-panel teacher-panel"><div className="teacher-section-heading"><div><h3>Ограничения доступности</h3><p>Специальные ограничения сейчас не хранятся в схеме школьного справочника.</p></div></div><div className="teacher-availability-empty"><CalendarDays size={22} /><div><strong>Ограничения не заданы</strong><p>Отсутствие записи означает, что специальных ограничений нет в данных. Для индивидуальных ограничений пока используйте комментарий в карточке преподавателя.</p></div></div><div className="teacher-load-note">Проверяйте занятость преподавателя во вкладке «Расписание» перед назначением занятий.</div></section> : null}
    </div>;
  }

  const rowSubjects = (teacher: Row) => [...new Set([...(teacher.subjects || []), ...assignmentRows(teacher).map((item: Row) => item.subject).filter(Boolean)])];
  const rowGroups = (teacher: Row) => [...new Set(assignmentRows(teacher).filter((item: Row) => assignmentIsActive(item)).map((item: Row) => prettyAssignment(item, directory.groups)).filter(Boolean))];
  return <div className="admin-stack teacher-directory">
    <header className="teacher-directory-header"><div><p className="admin-eyebrow">Школьный справочник</p><p>Предметы, назначения и нагрузка из справочника и расписания.</p>{schoolMode && directory.read_only ? <small>Просмотр реальных данных Supabase. Изменения отключены в локальном режиме.</small> : null}</div><div className="teacher-directory-actions"><span className={`teacher-connection ${error ? 'is-down' : ''}`}><i />{error ? 'Нет связи' : schoolMode ? 'Supabase' : connectionLabel}</span><button className="admin-button admin-button--quiet" onClick={() => void load()}><RefreshCw size={15} /> Обновить</button><button className="admin-button admin-button--primary" onClick={openCreate} disabled={schoolMode && !directory.write_ready}><Plus size={15} /> Создать преподавателя</button></div></header>
    {error ? <p className="admin-error-text" role="alert">{error}</p> : null}{notice ? <p className="teacher-toast" role="status">{notice}</p> : null}
    <div className="teacher-filters"><label className="admin-search"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} aria-label="Поиск преподавателя по ФИО" placeholder="Поиск по ФИО…" /></label><select aria-label="Предмет" value={subjectFilter} onChange={(event) => setSubjectFilter(event.target.value)}><option value="">Все предметы</option>{subjectOptions.map((item) => <option key={normalize(item)}>{item}</option>)}</select><select aria-label="Класс или параллель" value={classFilter} onChange={(event) => setClassFilter(event.target.value)}><option value="">Все классы и параллели</option>{parallels.map((item) => <option key={item} value={item}>{item}-я параллель</option>)}{classes.map((item) => <option key={item.id} value={classLabel(item)}>{classLabel(item)}</option>)}</select><select aria-label="Статус" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="active">Активные</option><option value="inactive">Неактивные</option><option value="all">Все статусы</option></select><div className="teacher-view-toggle"><button className={listView === 'table' ? 'is-active' : ''} onClick={() => setListView('table')}>Таблица</button><button className={listView === 'cards' ? 'is-active' : ''} onClick={() => setListView('cards')}>Карточки</button></div></div>
    {loading ? <div className="admin-empty">Обновляем справочник…</div> : listView === 'table' ? <section className="admin-panel teacher-table-panel"><div className="teacher-table-scroll"><table className="teacher-table"><thead><tr><th>Преподаватель</th><th>Предметы</th><th>Назначения</th><th>Недельная нагрузка</th><th>Статус</th></tr></thead><tbody>{list.map((teacher) => <tr key={teacher.id} tabIndex={0} onClick={() => openTeacher(teacher)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openTeacher(teacher); } }}><td><span className="teacher-row-avatar" style={{ background: teacherColors.color(String(teacher.id)), color: teacherColors.accent(String(teacher.id)) }}>{personName(teacher).slice(0, 1)}</span><strong>{personName(teacher)}</strong></td><td>{rowSubjects(teacher).join(' · ') || 'Не указаны'}</td><td>{assignmentRows(teacher).filter((item: Row) => assignmentIsActive(item)).length}</td><td><span className="teacher-no-data">Нет данных</span></td><td><span className={`teacher-status ${teacher.status === 'inactive' ? 'is-inactive' : ''}`}>{teacher.status === 'inactive' ? 'Неактивен' : 'Активен'}</span></td></tr>)}</tbody></table>{!list.length ? <div className="admin-empty">Преподаватели не найдены</div> : null}</div></section> : <div className="teacher-card-grid">{list.map((teacher) => <button key={teacher.id} className="teacher-list-card" onClick={() => openTeacher(teacher)}><div className="teacher-list-card-heading"><span className="teacher-row-avatar" style={{ background: teacherColors.color(String(teacher.id)), color: teacherColors.accent(String(teacher.id)) }}>{personName(teacher).slice(0, 1)}</span><strong>{personName(teacher)}</strong><span className={`teacher-status ${teacher.status === 'inactive' ? 'is-inactive' : ''}`}>{teacher.status === 'inactive' ? 'Неактивен' : 'Активен'}</span></div><div className="teacher-chip-list">{rowSubjects(teacher).map((item: string) => <span key={normalize(item)}>{item}</span>)}</div><div className="teacher-card-meta"><span>{assignmentRows(teacher).filter((item: Row) => assignmentIsActive(item)).length} назначений</span><span>Нагрузка: нет данных</span></div><small>{rowGroups(teacher).slice(0, 3).join(' · ') || 'Активных групп пока нет'}</small></button>)}</div>}
    <small className="teacher-count">Показано {list.length} из {teachers.length} преподавателей</small>
  </div>;
}



