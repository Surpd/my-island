/* oxlint-disable typescript/no-explicit-any -- Existing admin API accepts extensible directory rows. */
'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { GripVertical, Plus, RefreshCw, Search, X, ChevronRight } from 'lucide-react';
import { audienceGrades, audienceKind, audienceLabel, classLabel as formatClassLabel, subjectLabel, studentMemberships } from './school-audience';
import { assignmentIsActive } from './teacher-directory-logic';

type Row = Record<string, any>;
type Api = <T>(path: string, init?: RequestInit) => Promise<T>;
type Props = { api: Api; section: 'groups' | 'teachers'; schoolMode?: boolean; connectionLabel?: string; initialGroupId?: string };
type Directory = { groups: Row[]; students: Row[]; teachers: Row[]; write_ready?: boolean; read_only?: boolean; archive_ready?: boolean };

const title = (item: Row) => String(item.display_name || item.name || '');
const classLabel = (item: Row) => String(item.base_class_name || item.name || item.display_name || '');
const membershipRows = studentMemberships;
const groupIdOf = (item: Row) => String(item.group_id || item.id || '');
const subjectOf = (group: Row) => subjectLabel(group.subject || '');

export function SchoolDirectoryAdmin({ api, section, schoolMode = false, connectionLabel = 'Локальная база', initialGroupId }: Props) {
  const [data, setData] = useState<Directory>({ groups: [], students: [], teachers: [] });
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [query, setQuery] = useState('');
  const [boardQuery, setBoardQuery] = useState('');
  const [studentQuery, setStudentQuery] = useState('');
  const [classQuery, setClassQuery] = useState('');
  const [studentClassFilter, setStudentClassFilter] = useState('');
  const [studentGroupFilter, setStudentGroupFilter] = useState('');
  const [classDraft, setClassDraft] = useState<Row | null>(null);
  const [newStudentDraft, setNewStudentDraft] = useState<Row | null>(null);
  const [groupStudentQuery, setGroupStudentQuery] = useState('');
  const [expandMemberships, setExpandMemberships] = useState<string[]>([]);
  const [teacherSubject, setTeacherSubject] = useState('');
  const [grade, setGrade] = useState('');
  const [subject, setSubject] = useState('');
  const [view, setView] = useState<'board' | 'students'>('board');
  const [openGroupIds, setOpenGroupIds] = useState<string[]>([]);
  const [groupQueries, setGroupQueries] = useState<Record<string, string>>({});
  const [groupMenuId, setGroupMenuId] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const [archivePreview, setArchivePreview] = useState<Row | null>(null);
  const [archiveConfirmed, setArchiveConfirmed] = useState(false);
  const [confirmedPartitionScope, setConfirmedPartitionScope] = useState('');
  const [classesExpanded, setClassesExpanded] = useState(true);
  const [groupDraft, setGroupDraft] = useState<Row | null>(null);
  const [teacherDraft, setTeacherDraft] = useState<Row | null>(null);
  const [dragStudent, setDragStudent] = useState<Row | null>(null);
  const [pendingMove, setPendingMove] = useState<{ student: Row; target: Row; source: Row } | null>(null);
  const [pickedStudents, setPickedStudents] = useState<string[]>([]);
  const [assignment, setAssignment] = useState({ group_id: '', subject: '', grade_level: '' });
  const [sourceMode, setSourceMode] = useState('');
  const [schoolWritable, setSchoolWritable] = useState(false);
  const [connectionLost, setConnectionLost] = useState(false);
  const [studentDraft, setStudentDraft] = useState<Row | null>(null);
  const [schoolSession, setSchoolSession] = useState(!schoolMode);
  const [authChecking, setAuthChecking] = useState(schoolMode);
  const [schoolCode, setSchoolCode] = useState('');
  const [authError, setAuthError] = useState('');
  const initialGroupOpened = useRef(false);
  const teachersRef = useRef<Row[]>([]);
  useEffect(() => {
    if (!groupMenuId) return;
    const closeOutside = (event: PointerEvent) => { if (!(event.target instanceof Element) || !event.target.closest('[data-group-menu]')) setGroupMenuId(''); };
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') setGroupMenuId(''); };
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => { document.removeEventListener('pointerdown', closeOutside); document.removeEventListener('keydown', closeOnEscape); };
  }, [groupMenuId]);
  useEffect(() => { if (!notice) return; const timer = window.setTimeout(() => setNotice(''), 3600); return () => window.clearTimeout(timer); }, [notice]);
  useEffect(() => {
    if (!schoolMode) return;
    void api('/api/admin/school/auth/session').then(() => setSchoolSession(true)).catch(() => setSchoolSession(false)).finally(() => setAuthChecking(false));
  }, [api, schoolMode]);

  const editGroup = useCallback((group: Row) => {
    const matchingTeachers = teachersRef.current.filter((t) => (t.teacher_assignments || []).some((a: Row) => String(a.group_id) === String(group.id) && assignmentIsActive(a) && subjectLabel(a.subject) === subjectOf(group)));
    const defaultTeacher = String(group.default_teacher_identity_id || (matchingTeachers.length === 1 ? matchingTeachers[0].id : '') || '');
    setGroupMenuId('');
    setGroupDraft({ ...group, display_name: audienceLabel(group), _original_subject: group.subject || '', comment: group.comment ?? group.admin_note ?? '', default_teacher_identity_id: defaultTeacher, _original_default_teacher_identity_id: defaultTeacher });
    setPickedStudents([]);
    if (!schoolMode) void api<Row>(`/api/admin/groups/${encodeURIComponent(String(group.id))}`).then((detail) => setGroupDraft((current) => current && String(current.id) === String(group.id) ? { ...current, ...detail, comment: detail.comment ?? detail.admin_note ?? '', default_teacher_identity_id: current.default_teacher_identity_id } : current)).catch(() => undefined);
  }, [api, schoolMode]);

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      if (schoolMode) {
        const directory = await api<Directory>('/api/admin/school/directory');
        setConnectionLost(false);
        teachersRef.current = directory.teachers || [];
        setData(directory);
        if (initialGroupId && !initialGroupOpened.current) {
          const group = (directory.groups || []).find((item) => String(item.id) === String(initialGroupId));
          if (group) { initialGroupOpened.current = true; editGroup(group); }
        }
        setSchoolWritable(Boolean(directory.write_ready));
        setSourceMode(directory.read_only
          ? 'Школьная база Supabase открыта только для чтения в локальном режиме.'
          : directory.write_ready
          ? 'Источник: настоящий canonical school directory в Supabase. Изменения выполняются только после подтверждённого сохранения.'
          : 'Настоящий справочник Supabase открыт только для чтения. Для сохранения необходимы миграции 034/035; текущие данные не изменяются.');
        return;
      }
      const [groups, students, teachers, system] = await Promise.all([
        api<{ items: Row[] }>('/api/admin/groups?include_archived=true'),
        api<{ items: Row[] }>('/api/admin/people?kind=student'),
        api<{ items: Row[] }>('/api/admin/people?kind=teacher&include_inactive=true'),
        api<Row>('/api/admin/system').catch(() => ({} as Row)),
      ]);
      teachersRef.current = teachers.items || [];
      setData({ groups: groups.items || [], students: students.items || [], teachers: teachers.items || [] });
      if (initialGroupId && !initialGroupOpened.current) {
        const group = (groups.items || []).find((item) => String(item.id) === String(initialGroupId));
        if (group) { initialGroupOpened.current = true; editGroup(group); }
      }
      setSourceMode(system.health?.environment === 'local_sqlite' || system.settings?.database_configured === false ? 'Изолированная локальная база API. Сохранения доступны для ручной проверки, изменения не попадут в школьную базу.' : 'Источник: активная база, подключённая к административному API.');
    } catch (e) { if (schoolMode) setConnectionLost(true); setError(e instanceof Error ? e.message : 'Не удалось загрузить школьный справочник.'); }
    finally { setLoading(false); }
  }, [api, editGroup, initialGroupId, schoolMode]);
  useEffect(() => { if (schoolSession) void Promise.resolve().then(load); }, [load, schoolSession]);

  const classes = useMemo(() => data.groups.filter((g) => !g.archived_at && String(g.group_type || '').toLocaleLowerCase('ru') === 'class').sort((a, b) => classLabel(a).localeCompare(classLabel(b), 'ru', { numeric: true })), [data.groups]);
  const classNames = useMemo(() => classes.map(classLabel).filter(Boolean), [classes]);
  const parallels = useMemo(() => Array.from(new Set(classNames.map((name) => name.match(/^\d{1,2}/u)?.[0]).filter(Boolean) as string[])).sort((a, b) => Number(a) - Number(b)), [classNames]);
  const boardGroups = useMemo(() => {
    const q = boardQuery.trim().toLocaleLowerCase('ru');
    return data.groups.filter((item) => {
      if (audienceKind(item) === 'class') return false;
      if (Boolean(item.archived_at) !== showArchived) return false;
      if (grade) {
        const matchesGrade = grade.startsWith('parallel:')
          ? audienceGrades(item, data.students).includes(grade.slice(9))
          : audienceLabel(item) === grade || data.students.some((student) => (String(student.class_name || '') === grade || (student.base_classes || []).some((klass: Row) => audienceLabel(klass) === grade)) && membershipRows(student).some((m: Row) => groupIdOf(m) === String(item.id)));
        if (!matchesGrade) return false;
      }
      if (subject && subjectOf(item).toLocaleLowerCase('ru') !== subject.toLocaleLowerCase('ru')) return false;
      if (q && !`${audienceLabel(item)} ${title(item)} ${subjectOf(item)}`.toLocaleLowerCase('ru').includes(q) && !data.students.some((student) => `${title(student)} ${student.class_name || ''}`.toLocaleLowerCase('ru').includes(q) && membershipRows(student).some((m: Row) => groupIdOf(m) === String(item.id)))) return false;
      return true;
    }).sort((a, b) => audienceLabel(a).localeCompare(audienceLabel(b), 'ru', { numeric: true }));
  }, [boardQuery, data.groups, data.students, grade, subject, showArchived]);
  const subjects = useMemo(() => Array.from(new Set(data.groups.filter((g) => !g.archived_at && String(g.group_type || '').toLowerCase() !== 'class').map(subjectOf).filter(Boolean))).sort((a, b) => a.localeCompare(b, 'ru')), [data.groups]);
  const visibleStudents = useMemo(() => data.students.filter((p) => {
    const groupsForStudent = membershipRows(p).map(title).join(' ');
    const text = `${title(p)} ${p.class_name || ''} ${groupsForStudent}`.toLocaleLowerCase('ru');
    return (!studentQuery || text.includes(studentQuery.toLocaleLowerCase('ru'))) && (!studentClassFilter || p.class_name === studentClassFilter) && (!studentGroupFilter || membershipRows(p).some((m: Row) => groupIdOf(m) === studentGroupFilter));
  }).sort((a, b) => title(a).localeCompare(title(b), 'ru')), [data.students, studentClassFilter, studentGroupFilter, studentQuery]);
  const classStudents = (classItem: Row) => data.students.filter((p) => String(p.class_name || '') === classLabel(classItem) || (p.base_classes || []).some((b: Row) => typeof b === 'object' ? String(b.id) === String(classItem.id) : String(b) === classLabel(classItem)));
  const boardGroupBuckets = useMemo(() => {
    const buckets = new Map<string, Row[]>();
    for (const item of boardGroups) {
      const grades = audienceGrades(item, data.students);
      const key = grades.length === 1 ? `${grades[0]} класс` : grades.length ? 'Межвозрастные группы' : 'Другие группы';
      buckets.set(key, [...(buckets.get(key) || []), item]);
    }
    return [...buckets.entries()].sort(([left], [right]) => Number.parseInt(left, 10) - Number.parseInt(right, 10) || left.localeCompare(right, 'ru'));
  }, [boardGroups, data.students]);
  const boardSummaries = useMemo(() => {
    const memberIds = (group: Row) => new Set(data.students.filter((student) => membershipRows(student).some((membership: Row) => groupIdOf(membership) === String(group.id))).map((student) => String(student.id)));
    return new Map(boardGroups.map((group) => {
      const members = memberIds(group);
      const assignments = data.teachers.flatMap((teacher) => (teacher.teacher_assignments || []).filter((item: Row) => String(item.group_id) === String(group.id) && assignmentIsActive(item) && (!subjectOf(group) || subjectLabel(item.subject) === subjectOf(group))).map((item: Row) => ({ ...item, teacher_name: title(teacher) })));
      const teachers = [...new Set(assignments.map((item) => item.teacher_name))];
      const gradesForGroup = audienceGrades(group, data.students);
      const overlaps = boardGroups.filter((other) => String(other.id) !== String(group.id) && group.role === 'instructional_partition' && other.role === 'instructional_partition' && group.semantic_dimension && other.semantic_dimension === group.semantic_dimension && audienceGrades(other, data.students).some((grade) => gradesForGroup.includes(grade)) && [...memberIds(other)].some((id) => members.has(id))).map((other) => ({ name: audienceLabel(other), count: [...memberIds(other)].filter((id) => members.has(id)).length }));
      return [String(group.id), { members: members.size, teacher: teachers.length === 1 ? teachers[0] : '', assignmentCount: assignments.length, overlaps, sourceOverlaps: group.source_overlaps || [] }];
    }));
  }, [boardGroups, data.students, data.teachers]);
  const groupMembers = useMemo(() => {
    const members = new Map<string, Row[]>();
    for (const student of data.students) {
      for (const membership of membershipRows(student)) {
        const id = groupIdOf(membership);
        if (!id) continue;
        const roster = members.get(id);
        if (roster) roster.push(student);
        else members.set(id, [student]);
      }
    }
    return members;
  }, [data.students]);
  const selectedParallel = grade.startsWith('parallel:') ? grade.slice(9) : '';
  const explicitPartitionGroups = selectedParallel && subject ? boardGroups.filter((group) =>
    subjectOf(group).toLocaleLowerCase('ru') === subject.toLocaleLowerCase('ru') &&
    String(group.role || '').toLowerCase() === 'instructional_partition' &&
    Boolean(group.semantic_dimension)
  ) : [];
  const declaredCompletePartition = explicitPartitionGroups.length > 1 && new Set(explicitPartitionGroups.map((group) => String(group.semantic_dimension))).size === 1 && explicitPartitionGroups.every((group) => String(group.semantic_dimension) === 'grade7_shared_split');
  const partitionScope = `${selectedParallel}:${subject.toLocaleLowerCase('ru')}`;
  const hasConfirmedPartition = declaredCompletePartition || confirmedPartitionScope === partitionScope;
  const partitionGroups = declaredCompletePartition ? explicitPartitionGroups : hasConfirmedPartition ? boardGroups.filter((group) => !['class', 'exam_track'].includes(String(group.group_type || '').toLowerCase()) && subjectOf(group).toLocaleLowerCase('ru') === subject.toLocaleLowerCase('ru')) : [];
  const partitionIds = new Set(partitionGroups.map((group) => String(group.id)));
  const unassignedStudents = hasConfirmedPartition ? data.students.filter((student) => {
    const inParallel = String(student.class_name || '').match(/^\d{1,2}/u)?.[0] === selectedParallel || (student.base_classes || []).some((klass: Row) => {
      const name = typeof klass === 'object' ? classLabel(klass) : String(klass);
      return name.match(/^\d{1,2}/u)?.[0] === selectedParallel;
    });
    if (!inParallel) return false;
    if (boardQuery && !`${title(student)} ${student.class_name || ''}`.toLocaleLowerCase('ru').includes(boardQuery.toLocaleLowerCase('ru'))) return false;
    return !membershipRows(student).some((membership: Row) => partitionIds.has(groupIdOf(membership)));
  }) : [];
  const saveStudentClass = async (student: Row, nextClass: string) => {
    if (schoolMode && !schoolWritable) { setError('Школьный справочник открыт только для чтения.'); return; }
    if (!nextClass || nextClass === String(data.students.find((item) => String(item.id) === String(student.id))?.class_name || '')) return;
    const klass = classes.find((item) => classLabel(item) === nextClass);
    if (!klass) { setError('Выберите существующий административный класс из справочника.'); return; }
    setBusy(true); setError(''); setNotice('');
    try {
      await api(`/api/admin/people/${student.id}`, { method: 'PATCH', body: JSON.stringify({ class_name: nextClass }) });
      const update = (person: Row) => String(person.id) === String(student.id) ? { ...person, class_name: nextClass, base_classes: [{ ...klass, name: classLabel(klass) }] } : person;
      setData((current) => ({ ...current, students: current.students.map(update) }));
      setStudentDraft((current) => current && String(current.id) === String(student.id) ? update(current) : current);
      setClassDraft((current) => current ? { ...current, students: (current.students || []).map(update) } : current);
      setNotice(`Класс ученика обновлён: ${nextClass}.`); window.dispatchEvent(new Event('school-directory-updated'));
    } catch (e) { setError(e instanceof Error ? e.message : 'Не удалось изменить класс ученика.'); }
    finally { setBusy(false); }
  };
  const createStudent = async () => {
    if (!newStudentDraft?.display_name?.trim() || !newStudentDraft.class_name) return;
    if (schoolMode && !schoolWritable) { setError('Справочник сейчас доступен только для чтения.'); return; }
    setBusy(true); setError(''); setNotice('');
    try {
      await api('/api/admin/identities', { method: 'POST', body: JSON.stringify({ kind: 'student', display_name: newStudentDraft.display_name.trim(), class_name: newStudentDraft.class_name, status: 'active' }) });
      setNewStudentDraft(null); setNotice('Ученик создан и добавлен в выбранный класс.'); window.dispatchEvent(new Event('school-directory-updated')); await load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Не удалось создать ученика.'); }
    finally { setBusy(false); }
  };
  const openStudent = (student: Row) => { setStudentDraft(student); setGroupStudentQuery(''); };

  const save = async (path: string, method: string, body: Row) => {
    if (schoolMode && !schoolWritable) { setError('Сохранение станет доступно после миграций 034/035.'); return; }
    setBusy(true); setError(''); setNotice('');
    try { await api(path, { method, body: JSON.stringify(body) }); setNotice('Изменения сохранены.'); setGroupDraft(null); setTeacherDraft(null); window.dispatchEvent(new Event('school-directory-updated')); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Не удалось сохранить изменения.'); }
    finally { setBusy(false); }
  };
  const commitMembership = async (student: Row, target: Row, source?: Row) => {
    if (schoolMode && !schoolWritable) return;
    setBusy(true); setError(''); setNotice('');
    try {
      await api('/api/admin/memberships/transfer', { method: 'POST', body: JSON.stringify({ identity_id: String(student.id), ...(source ? { from_group_id: groupIdOf(source) } : {}), to_group_id: String(target.id), reason: source ? 'Перевод подтверждён администратором в редакторе' : 'Добавлено администратором в редакторе школьной структуры' }) });
      const update = (person: Row) => {
        if (String(person.id) !== String(student.id)) return person;
        const list = membershipRows(person).filter((m: Row) => !source || groupIdOf(m) !== groupIdOf(source));
        const next = source ? list : [...list];
        if (!next.some((m: Row) => groupIdOf(m) === String(target.id))) next.push({ id: String(target.id), group_id: String(target.id), group_name: title(target), name: title(target), subject: subjectOf(target), group_type: target.group_type, source: 'admin_override' });
        return { ...person, memberships: next, instructional_memberships: next.filter((m: Row) => String(m.group_type || '') !== 'exam_track'), exam_profile_memberships: next.filter((m: Row) => String(m.group_type || '') === 'exam_track') };
      };
      setData((current) => ({ ...current, students: current.students.map(update) }));
      setStudentDraft((current) => current ? update(current) : current);
      setNotice(source ? 'Ученик переведён в группу.' : 'Ученик добавлен в группу.');
      setPendingMove(null);
      window.dispatchEvent(new Event('school-directory-updated'));
    } catch (e) { setError(e instanceof Error ? e.message : 'Не удалось изменить состав группы.'); }
    finally { setBusy(false); }
  };
  const addTeacherAssignment = async () => {
    if (schoolMode && !schoolWritable) { setError('Сохранение станет доступно после миграций 034/035.'); return; }
    setBusy(true); setError(''); setNotice('');
    try { await api('/api/admin/teacher-assignments', { method: 'POST', body: JSON.stringify({ teacher_identity_id: String(teacherDraft?.id), ...(assignment.grade_level ? { grade_level: assignment.grade_level } : { group_id: assignment.group_id }), subject: assignment.subject.trim() }) }); setNotice('Назначение сохранено.'); setTeacherDraft(null); window.dispatchEvent(new Event('school-directory-updated')); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Не удалось сохранить назначение.'); }
    finally { setBusy(false); }
  };
  const removeMembership = async (student: Row, group: Row) => {
    if (schoolMode && !schoolWritable) { setError('Школьный справочник открыт только для чтения.'); return; }
    if (window.confirm(`Исключить ${title(student)} из группы «${title(group)}»?`)) {
      setBusy(true); setError(''); setNotice('');
      try {
        await api('/api/admin/memberships/transfer', { method: 'POST', body: JSON.stringify({ identity_id: String(student.id), from_group_id: String(group.id), reason: 'Исключение подтверждено администратором в редакторе' }) });
        const update = (person: Row) => String(person.id) !== String(student.id) ? person : (() => { const next = membershipRows(person).filter((m: Row) => groupIdOf(m) !== String(group.id)); return { ...person, memberships: next, instructional_memberships: next.filter((m: Row) => String(m.group_type || '') !== 'exam_track'), exam_profile_memberships: next.filter((m: Row) => String(m.group_type || '') === 'exam_track') }; })();
        setData((current) => ({ ...current, students: current.students.map(update) })); setStudentDraft((current) => current ? update(current) : current);
        setNotice('Ученик исключён из группы.'); window.dispatchEvent(new Event('school-directory-updated'));
      } catch (e) { setError(e instanceof Error ? e.message : 'Не удалось исключить ученика из группы.'); }
      finally { setBusy(false); }
    }
  };
  const addMembership = async (identityId: string, groupId: string) => {
    const student = data.students.find((item) => String(item.id) === identityId) || studentDraft;
    const target = data.groups.find((item) => String(item.id) === groupId);
    if (student && target) await commitMembership(student, target);
  };
  const moveMembership = async (student: Row, target: Row) => {
    if (schoolMode && !schoolWritable) return;
    if (membershipRows(student).some((m: Row) => groupIdOf(m) === String(target.id))) return;
    const targetIsExam = String(target.group_type || '').toLowerCase() === 'exam_track';
    const targetSubject = targetIsExam ? '' : subjectOf(target);
    const sourceMembership = targetSubject ? membershipRows(student).find((m: Row) => String(m.group_type || '').toLowerCase() !== 'exam_track' && subjectOf(m) === targetSubject && groupIdOf(m) !== String(target.id)) : null;
    if (!sourceMembership) { await commitMembership(student, target); return; }
    const source = data.groups.find((group) => String(group.id) === groupIdOf(sourceMembership));
    if (source) setPendingMove({ student, target, source }); else await commitMembership(student, target);
  };
  const prepareArchive = async (group: Row) => {
    setGroupMenuId(''); setBusy(true); setError(''); setArchiveConfirmed(false);
    try {
      const preview = await api<Row>(`/api/admin/groups/${encodeURIComponent(String(group.id))}/archive-preview`);
      setArchivePreview({ ...preview, group });
    } catch (e) { setError(e instanceof Error ? e.message : 'Не удалось проверить связи группы.'); }
    finally { setBusy(false); }
  };
  const changeArchive = async (group: Row, restore = false) => {
    if (schoolMode && !schoolWritable) return;
    setBusy(true); setError('');
    try {
      await api(`/api/admin/groups/${encodeURIComponent(String(group.id))}/${restore ? 'restore' : 'archive'}`, { method: 'POST', body: JSON.stringify({ confirm_used: archiveConfirmed }) });
      setArchivePreview(null); setGroupDraft(null); setGroupMenuId('');
      setNotice(restore ? 'Группа восстановлена.' : 'Группа архивирована. Её состав и история сохранены.');
      window.dispatchEvent(new Event('school-directory-updated')); await load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Не удалось изменить архивный статус.'); }
    finally { setBusy(false); }
  };
  if (schoolMode && authChecking) return <div className="admin-panel">Проверяем доступ к школьному справочнику…</div>;
  if (schoolMode && !schoolSession) return <section className="admin-panel school-editor-auth"><h2>Вход в школьный справочник</h2><p>Тестовый параметр role=admin не даёт доступа к настоящей базе. Получите одноразовый код командой /admincode в школьном Telegram-боте.</p><form onSubmit={async (event) => { event.preventDefault(); setAuthError(''); try { const result = await api<{ browser_session_token: string }>('/api/admin/school/auth/exchange', { method: 'POST', body: JSON.stringify({ code: schoolCode.trim() }) }); window.sessionStorage.setItem('my_island_school_admin_session', result.browser_session_token); setSchoolCode(''); setSchoolSession(true); } catch (e) { setAuthError(e instanceof Error ? e.message : 'Код не принят.'); } }}><label className="school-field">Одноразовый код<input value={schoolCode} onChange={(event) => setSchoolCode(event.target.value)} autoComplete="one-time-code" /></label><button className="admin-button admin-button--primary" disabled={!schoolCode.trim()}>Войти</button></form>{authError ? <p role="alert" className="admin-error-text">{authError}</p> : null}</section>;
  if (loading) return <div className="admin-panel">Загружаем школьный справочник…</div>;
  if (error && !data.groups.length && !data.students.length) return <section className="admin-panel"><p role="alert" className="admin-error-text">{error}</p><button className="admin-button" onClick={() => void load()}>Повторить</button></section>;

  if (section === 'teachers') return <div className="admin-stack school-editor">
    <div className="school-editor-heading"><div><p className="admin-eyebrow">Школьный справочник</p><h2>Преподаватели</h2><p>Предметы и назначения используются как рекомендации в конструкторе расписания.</p></div><button className="admin-button admin-button--primary" disabled={schoolMode && !schoolWritable} onClick={() => setTeacherDraft({ display_name: '', kind: 'teacher', status: 'active', subjects: [], comment: '', teacher_assignments: [] })}><Plus size={16} /> Создать преподавателя</button></div>
    {sourceMode ? <p className="school-source-note">{sourceMode}</p> : null}
    <div className="admin-toolbar"><label className="admin-search"><Search size={16} /><input value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Поиск преподавателя или предмета" placeholder="Поиск преподавателя или предмета…" /></label><select aria-label="Фильтр по предмету" value={teacherSubject} onChange={(e) => setTeacherSubject(e.target.value)}><option value="">Все предметы</option>{Array.from(new Set(data.teachers.flatMap((t) => (t.subjects || []).map(String)))).sort((a, b) => a.localeCompare(b, "ru")).map((s) => <option key={s} value={s}>{s}</option>)}</select><button className="admin-button admin-button--quiet" onClick={() => void load()}><RefreshCw size={15} /> Обновить</button></div>
    {error ? <p role="alert" className="admin-error-text">{error}</p> : null}{notice ? <output className="school-editor-notice">{notice}</output> : null}
    <div className="school-teacher-grid">{data.teachers.filter((t) => `${title(t)} ${(t.subjects || []).join(' ')} ${(t.teacher_assignments || []).map((a: Row) => a.subject).join(' ')}`.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru')) && (!teacherSubject || (t.subjects || []).includes(teacherSubject))).map((teacher) => <button className="school-teacher-card" key={teacher.id} onClick={() => { setTeacherDraft({ ...teacher, comment: teacher.comment ?? teacher.admin_note ?? '' }); setAssignment({ group_id: '', subject: '', grade_level: '' }); }}><span className="school-teacher-avatar">{title(teacher).slice(0, 1)}</span><span><strong>{title(teacher)}</strong><small>{(teacher.subjects || []).join(' · ') || Array.from(new Set((teacher.teacher_assignments || []).map((a: Row) => a.subject).filter(Boolean))).join(' · ') || 'Предметы не указаны'}</small><small>{(teacher.teacher_assignments || []).length} назначений · {teacher.status === 'inactive' ? 'неактивен' : 'активен'}</small></span><span className="school-teacher-arrow">→</span></button>)}</div>
    {teacherDraft ? <div role="presentation" className="school-editor-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setTeacherDraft(null); }}><dialog ref={(node) => { if (node && !node.open) node.showModal(); }} onCancel={(event) => { event.preventDefault(); setTeacherDraft(null); }} className="school-editor-dialog"><header><div><p className="admin-eyebrow">Карточка преподавателя</p><h2>{teacherDraft.id ? title(teacherDraft) : 'Новый преподаватель'}</h2></div><button className="admin-button admin-button--quiet" onClick={() => setTeacherDraft(null)}><X size={16} /></button></header>
      <label className="school-field">ФИО<input value={teacherDraft.display_name || ''} onChange={(e) => setTeacherDraft({ ...teacherDraft, display_name: e.target.value })} placeholder="Имя и фамилия" /></label>
      <label className="school-field">Предметы<input value={Array.isArray(teacherDraft.subjects) ? teacherDraft.subjects.join(', ') : ''} onChange={(e) => setTeacherDraft({ ...teacherDraft, subjects: e.target.value.split(',').map((s: string) => s.trim()).filter(Boolean) })} placeholder="Математика, Физика" /><small>Перечислите предметы через запятую.</small></label>
      <label className="school-field">Статус<select value={teacherDraft.status || 'active'} onChange={(e) => setTeacherDraft({ ...teacherDraft, status: e.target.value })}><option value="active">Активен</option><option value="inactive">Неактивен</option></select></label>
      <label className="school-field">Комментарий<textarea value={teacherDraft.comment || ''} onChange={(e) => setTeacherDraft({ ...teacherDraft, comment: e.target.value })} placeholder="Необязательно" /></label>
      <p className="admin-eyebrow">НАЗНАЧЕНИЯ</p><div className="admin-detail-list">{(teacherDraft.teacher_assignments || []).map((a: Row, i: number) => <div key={a.id || i}><strong>{a.subject || 'Предмет не указан'}</strong><small>{a.group_name || a.base_class_name || a.group_id} · {a.active === false ? 'неактивно' : 'активно'}</small>{a.active !== false ? <button className="admin-link" onClick={() => { if (window.confirm(`Снять назначение «${a.subject}»?`)) void save('/api/admin/teacher-assignments', 'POST', { teacher_identity_id: String(teacherDraft.id), group_id: String(a.group_id), subject: String(a.subject || ''), active: false }); }}>Снять</button> : null}</div>)}</div>
      <div className="school-assignment-form"><label className="school-field">Предмет<input value={assignment.subject} onChange={(e) => setAssignment({ ...assignment, subject: e.target.value })} placeholder="Например, Английский" /></label><label className="school-field">Группа или класс<select value={assignment.group_id} onChange={(e) => setAssignment({ ...assignment, group_id: e.target.value, grade_level: '' })}><option value="">Выбрать группу / класс</option>{data.groups.map((g) => <option key={g.id} value={g.id}>{audienceLabel(g)}{subjectOf(g) ? ` · ${subjectOf(g)}` : ''}</option>)}</select></label><label className="school-field">Параллель (назначение для предмета)<select value={assignment.grade_level} onChange={(e) => setAssignment({ ...assignment, grade_level: e.target.value, group_id: '' })}><option value="">Не выбрана</option>{Array.from({ length: 11 }, (_, i) => String(i + 1)).map((g) => <option key={g} value={g}>{g} класс</option>)}</select></label><button className="admin-button" disabled={!teacherDraft.id || !assignment.subject.trim() || (!assignment.group_id && !assignment.grade_level) || busy} onClick={() => void addTeacherAssignment()}><Plus size={15} /> Добавить назначение</button>{!teacherDraft.id ? <small>Сохраните преподавателя, чтобы добавлять назначения.</small> : null}</div>
      <footer><button className="admin-button admin-button--primary" disabled={!teacherDraft.display_name?.trim() || busy || (schoolMode && !schoolWritable)} onClick={() => void save(teacherDraft.id ? `/api/admin/people/${teacherDraft.id}` : '/api/admin/identities', teacherDraft.id ? 'PATCH' : 'POST', { display_name: teacherDraft.display_name.trim(), subjects: teacherDraft.subjects || [], status: teacherDraft.status || 'active', comment: teacherDraft.comment || '', ...(teacherDraft.id ? {} : { kind: 'teacher' }) })}>{busy ? 'Сохраняем…' : 'Сохранить преподавателя'}</button><button className="admin-button admin-button--quiet" onClick={() => setTeacherDraft(null)}>Закрыть</button></footer>
    </dialog></div> : null}
  </div>;

  return <div className="admin-stack school-editor">
    <div className="school-editor-heading"><div><p className="admin-eyebrow">Школьный справочник</p><p>Общий справочник классов, состава и учебных групп.</p></div><span className={schoolMode ? `school-connection ${connectionLost ? 'is-disconnected' : 'is-connected'}` : 'school-connection'}><i />{schoolMode ? connectionLost ? 'Supabase · Нет связи' : 'Supabase · Подключено' : connectionLabel}</span></div>
    <div className="school-editor-tabs" role="tablist" aria-label="Раздел справочника">{([['board', 'Доска'], ['students', 'Ученики']] as const).map(([key, label]) => <button role="tab" aria-selected={view === key} key={key} className={view === key ? 'is-active' : ''} onClick={() => setView(key)}>{label}</button>)}</div>
    {view === 'board' ? <>
      <div className="admin-toolbar school-editor-toolbar"><select aria-label="Класс или параллель" value={grade} onChange={(e) => setGrade(e.target.value)}><option value="">Все параллели</option>{parallels.map((p) => <option key={p} value={`parallel:${p}`}>{p}-я параллель</option>)}{classNames.map((c) => <option key={c} value={c}>{formatClassLabel(c)}</option>)}</select><select aria-label="Предмет или направление" value={subject} onChange={(e) => { setSubject(e.target.value); setClassesExpanded(!e.target.value); }}><option value="">Все предметы</option>{subjects.map((s) => <option key={s}>{s}</option>)}</select><label className="admin-search"><Search size={16} /><input value={boardQuery} onChange={(e) => setBoardQuery(e.target.value)} aria-label="Поиск групп и учеников" placeholder="Поиск групп и учеников…" /></label><button className="admin-button admin-button--quiet" onClick={() => void load()}><RefreshCw size={15} /> Обновить</button><button className="admin-button admin-button--primary" disabled={schoolMode && !schoolWritable} onClick={() => setGroupDraft({ name: '', display_name: '', group_type: 'instructional', subject: '', default_teacher_identity_id: '' })}><Plus size={16} /> Создать группу</button></div>
      <div className="school-board-controls"><label className="school-archive-toggle"><input type="checkbox" checked={showArchived} onChange={(e) => { setShowArchived(e.target.checked); setGroupMenuId(''); }} />Архивные группы</label><span>{subject ? `${boardGroups.length} групп по предмету «${subject}»` : `${boardGroups.length} учебных групп`}</span><span>Нажмите карточку, чтобы открыть состав. Перетаскивайте ученика на другую группу для перевода.</span></div>
      <div className="school-board-groups" aria-label="Учебные группы">
        {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/prefer-tag-over-role -- This named group accepts drops; keyboard users can add through its select. */}
        {boardGroupBuckets.map(([bucket, items]) => <section className="school-grade-section" key={bucket}><h3>{bucket}</h3><div className="school-group-grid">{items.map((g) => { const id = String(g.id); const summary = boardSummaries.get(id); const isExpanded = openGroupIds.includes(id); const isDropTarget = Boolean(!g.archived_at && dragStudent && !membershipRows(dragStudent).some((m: Row) => groupIdOf(m) === id)); const query = groupQueries[id] || ''; const members = groupMembers.get(id) || []; const candidates = data.students.filter((student) => !membershipRows(student).some((membership: Row) => groupIdOf(membership) === id) && `${title(student)} ${student.class_name || ''}`.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru'))).slice(0, 100); return <article key={id} data-group-id={id} role="group" aria-label={`Группа ${audienceLabel(g)}. Перетащите сюда ученика для добавления`} className={`school-group-card ${isExpanded ? 'is-expanded' : ''} ${isDropTarget ? 'is-drop-target' : ''} ${groupMenuId === id ? 'has-open-menu' : ''}`} onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }} onDrop={(e) => { e.preventDefault(); const studentId = e.dataTransfer.getData('text/plain'); const student = data.students.find((item) => String(item.id) === studentId) || dragStudent; if (student && !g.archived_at) void moveMembership(student, g); setDragStudent(null); }}><div className="school-group-card-heading"><button className="school-group-card-main" aria-expanded={isExpanded} onClick={() => setOpenGroupIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id])}><span className="school-group-card-title">{audienceLabel(g)}</span><span className="school-group-card-subject">{audienceLabel(g).startsWith(subjectOf(g)) ? 'Учебная группа' : subjectOf(g) || 'Учебная группа'}</span><span className="school-group-card-meta">{summary?.members || 0} учеников{summary?.teacher ? ` · ${summary.teacher}` : ''}</span>{summary?.overlaps?.length ? <span className="school-group-warning">Пересечение с {summary.overlaps[0].name}: {summary.overlaps[0].count}</span> : null}</button><div className="school-group-menu-anchor" data-group-menu><button className="school-icon-button" aria-label={`Действия группы ${audienceLabel(g)}`} aria-haspopup="menu" aria-expanded={groupMenuId === id} onClick={() => setGroupMenuId((current) => current === id ? '' : id)}>⋯</button>{groupMenuId === id ? <div className="school-group-menu" role="menu" aria-label={`Меню группы ${audienceLabel(g)}`}><button role="menuitem" onClick={() => editGroup(g)}>Редактировать</button><button role="menuitem" onClick={() => { setOpenGroupIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]); setGroupMenuId(''); }}>{isExpanded ? 'Свернуть состав' : 'Показать состав'}</button>{g.archived_at ? <button role="menuitem" disabled={busy || (schoolMode && !schoolWritable)} onClick={() => void changeArchive(g, true)}>Восстановить группу</button> : <button role="menuitem" disabled={busy} onClick={() => void prepareArchive(g)}>Архивировать группу…</button>}</div> : null}</div></div>{isExpanded ? <div className="school-group-roster" aria-label={`Состав группы ${audienceLabel(g)}`}><div className="school-group-roster-toolbar"><span>{members.length} учеников</span><button className="admin-button admin-button--quiet" onClick={() => editGroup(g)}>Настройки</button></div><label className="admin-search"><Search size={14} /><input aria-label={`Поиск в группе ${audienceLabel(g)}`} value={query} onChange={(e) => setGroupQueries((current) => ({ ...current, [id]: e.target.value }))} placeholder="Поиск ученика" /></label><select aria-label={`Добавить ученика в ${audienceLabel(g)}`} value="" disabled={busy || Boolean(g.archived_at) || (schoolMode && !schoolWritable)} onChange={(e) => { const student = data.students.find((item) => String(item.id) === e.target.value); if (student) void moveMembership(student, g); e.currentTarget.value = ''; }}><option value="">+ Добавить ученика…</option>{candidates.map((student) => <option key={student.id} value={student.id}>{title(student)} · {student.class_name || 'класс не указан'}</option>)}</select>{error ? <p className="admin-error-text" role="alert">{error}</p> : null}<div className="school-group-roster-list">{members.filter((student) => !query || title(student).toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru'))).map((student) => <article className="school-group-roster-row" key={student.id}><span className="school-drag-handle" draggable={!busy && !showArchived && (!schoolMode || schoolWritable)} onDragStart={(e) => { e.dataTransfer.setData('text/plain', String(student.id)); e.dataTransfer.effectAllowed = 'move'; setDragStudent(student); }} onDragEnd={() => setDragStudent(null)} title="Перетащить ученика"><GripVertical size={15} /></span><button className="school-group-student-open" onClick={() => openStudent(student)}><strong>{title(student)}</strong><small>{student.class_name ? formatClassLabel(student.class_name) : 'Класс не указан'}</small></button><button className="school-icon-button school-membership-remove" aria-label={`Исключить ${title(student)} из ${audienceLabel(g)}`} disabled={busy || (schoolMode && !schoolWritable)} onClick={() => void removeMembership(student, g)}>×</button></article>)}</div></div> : null}</article>; })}</div></section>)}
        {!boardGroups.length ? <div className="admin-panel">По выбранному предмету и параллели группы не найдены.</div> : null}
      </div>
      <details className="school-class-shelf" open={classesExpanded} onToggle={(event) => setClassesExpanded((event.currentTarget as HTMLDetailsElement).open)}><summary><span>Административные классы</span><small>{classes.length} классов{subject ? ' · можно развернуть' : ''}</small></summary><div className="school-class-shelf-tools"><label className="admin-search"><Search size={15} /><input value={classQuery} onChange={(e) => setClassQuery(e.target.value)} aria-label="Найти класс или ученика" placeholder="Найти класс или ученика…" /></label><button className="admin-button admin-button--primary" disabled={schoolMode && !schoolWritable} onClick={() => setNewStudentDraft({ display_name: '', class_name: classNames[0] || '' })}><Plus size={15} /> Новый ученик</button></div><div className="school-class-shelf-grid">{classes.filter((item) => !classQuery || `${classLabel(item)} ${classStudents(item).map(title).join(' ')}`.toLocaleLowerCase('ru').includes(classQuery.toLocaleLowerCase('ru'))).map((item) => <button key={item.id} className="school-class-compact" onClick={() => { setClassDraft({ ...item }); setClassQuery(''); }}><strong>{classLabel(item)}</strong><span>{classStudents(item).length} учеников</span><small>{classStudents(item).slice(0, 2).map(title).join(' · ') || 'Состав не указан'}</small></button>)}</div></details>
      {!showArchived && selectedParallel && subject ? <section className="school-unassigned" aria-label="Нераспределённые ученики"><strong>Нераспределённые ученики · {selectedParallel}-я параллель</strong>{hasConfirmedPartition ? <><small>{declaredCompletePartition ? 'Показаны ученики без эффективного членства в объявленном полном делении.' : 'Показаны ученики без эффективного членства в группах выбранного предмета.'}</small>{!declaredCompletePartition ? <button className="admin-button admin-button--quiet school-unassigned-revoke" onClick={() => setConfirmedPartitionScope('')}>Отменить подтверждение</button> : null}{unassignedStudents.length ? unassignedStudents.slice(0, 100).map((p) => <article className="school-student-chip" key={p.id}><span className="school-drag-handle" draggable={!busy && !showArchived && (!schoolMode || schoolWritable)} onDragStart={(e) => { e.dataTransfer.setData('text/plain', String(p.id)); e.dataTransfer.effectAllowed = 'move'; setDragStudent(p); }} onDragEnd={() => setDragStudent(null)} title="Перетащить ученика"><GripVertical size={14} /></span><button className="school-group-student-open" onClick={() => openStudent(p)}><strong>{title(p)}</strong><small>{p.class_name || 'Класс не указан'}</small></button></article>) : <p>Все ученики параллели распределены.</p>}</> : <><p>Для этой параллели не объявлено полное деление по предмету. Список нераспределённых скрыт.</p><label className="school-unassigned-confirm"><input type="checkbox" checked={confirmedPartitionScope === partitionScope} onChange={(e) => setConfirmedPartitionScope(e.target.checked ? partitionScope : '')} />Подтверждаю, что все ученики параллели должны быть распределены по показанным группам «{subject}».</label></>}</section> : null}
    </> : null}
    {view === 'students' ? <><div className="admin-toolbar school-editor-toolbar"><label className="admin-search"><Search size={16} /><input aria-label="Поиск ученика по ФИО" value={studentQuery} onChange={(e) => setStudentQuery(e.target.value)} placeholder="Поиск по ФИО" /></label><select aria-label="Фильтр по административному классу" value={studentClassFilter} onChange={(e) => setStudentClassFilter(e.target.value)}><option value="">Все классы</option>{classNames.map((c) => <option key={c} value={c}>{formatClassLabel(c)}</option>)}</select><select aria-label="Фильтр по учебной группе" value={studentGroupFilter} onChange={(e) => setStudentGroupFilter(e.target.value)}><option value="">Все учебные группы</option>{data.groups.filter((g) => !g.archived_at && String(g.group_type || '').toLowerCase() !== 'class').map((g) => <option key={g.id} value={g.id}>{audienceLabel(g)}</option>)}</select><button className="admin-button admin-button--quiet" onClick={() => void load()}><RefreshCw size={15} /> Обновить</button><button className="admin-button admin-button--primary" disabled={schoolMode && !schoolWritable} onClick={() => setNewStudentDraft({ display_name: '', class_name: classNames[0] || '' })}><Plus size={16} /> Добавить ученика</button></div><section className="admin-panel admin-table-panel"><div className="admin-table-wrap"><table className="admin-table school-students-table"><thead><tr><th>Ученик</th><th>Класс</th><th>Учебные группы</th></tr></thead><tbody>{visibleStudents.map((p) => { const memberships = membershipRows(p); const expanded = expandMemberships.includes(String(p.id)); const shown = expanded ? memberships : memberships.slice(0, 2); return <tr key={p.id}><td><button className="school-student-table-open" aria-label={`Открыть карточку ученика ${title(p)}`} onClick={() => openStudent(p)}><strong>{title(p)}</strong></button></td><td>{p.class_name ? formatClassLabel(p.class_name) : '—'}</td><td><span className="school-membership-chips">{shown.map((m: Row) => <span className="school-membership-chip" key={groupIdOf(m)}>{audienceLabel(data.groups.find((g) => String(g.id) === groupIdOf(m)) || { ...m, name: m.group_name || m.name })}</span>)}{memberships.length > 2 ? <button className="school-expand-memberships" onClick={(e) => { e.stopPropagation(); setExpandMemberships((current) => expanded ? current.filter((id) => id !== String(p.id)) : [...current, String(p.id)]); }}>{expanded ? 'Свернуть' : `+${memberships.length - 2}`}</button> : null}{!memberships.length ? <small>Нет учебных групп</small> : null}</span></td></tr>; })}</tbody></table></div></section></> : null}
    {classDraft ? <div role="presentation" className="school-editor-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) { setClassDraft(null); setClassQuery(''); } }}><dialog ref={(node) => { if (node && !node.open) node.showModal(); }} onCancel={(event) => { event.preventDefault(); setClassDraft(null); }} className="school-editor-dialog school-audience-drawer school-class-drawer" aria-modal="true" aria-label={`Состав класса ${classLabel(classDraft)}`}><header><div><p className="admin-eyebrow">АДМИНИСТРАТИВНЫЙ КЛАСС</p><h2>{formatClassLabel(classLabel(classDraft))}</h2><small>{classStudents(classDraft).length} учеников</small></div><button className="admin-button admin-button--quiet" aria-label="Закрыть класс" onClick={() => { setClassDraft(null); setClassQuery(''); }}><X size={16} /></button></header><div className="school-audience-actions"><button className="admin-button admin-button--primary" disabled={schoolMode && !schoolWritable} onClick={() => setNewStudentDraft({ display_name: '', class_name: classLabel(classDraft) })}><Plus size={15} /> Новый ученик</button><select aria-label="Перевести существующего ученика в класс" value="" disabled={schoolMode && !schoolWritable} onChange={(e) => { const p = data.students.find((student) => String(student.id) === e.target.value); if (p && window.confirm(`Перевести ${title(p)} из «${p.class_name || 'класса не указан'}» в «${classLabel(classDraft)}»?`)) void saveStudentClass(p, classLabel(classDraft)); e.currentTarget.value = ''; }}><option value="">Добавить существующего ученика…</option>{data.students.filter((p) => p.class_name !== classLabel(classDraft)).map((p) => <option key={p.id} value={p.id}>{title(p)} · {p.class_name || 'класс не указан'}</option>)}</select></div><label className="admin-search"><Search size={15} /><input value={classQuery} onChange={(e) => setClassQuery(e.target.value)} placeholder="Поиск по составу класса" /></label>{error ? <p className="admin-error-text" role="alert">{error}</p> : null}<div className="school-class-roster">{classStudents(classDraft).filter((p) => !classQuery || title(p).toLocaleLowerCase('ru').includes(classQuery.toLocaleLowerCase('ru'))).map((p) => <button className="school-class-roster-row" key={p.id} onClick={() => openStudent(p)}><span><strong>{title(p)}</strong><small>{p.class_name}</small></span><span>{membershipRows(p).map((m: Row) => <i key={groupIdOf(m)}>{audienceLabel(data.groups.find((g) => String(g.id) === groupIdOf(m)) || { ...m, name: m.group_name || m.name })}</i>)}</span><ChevronRight size={16} /></button>)}</div><footer><button className="admin-button admin-button--quiet" onClick={() => { setClassDraft(null); setClassQuery(''); }}>Закрыть класс</button></footer></dialog></div> : null}
    {studentDraft ? <div role="presentation" className="school-editor-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setStudentDraft(null); }}><dialog ref={(node) => { if (node && !node.open) node.showModal(); }} onCancel={(event) => { event.preventDefault(); setStudentDraft(null); }} className="school-editor-dialog school-student-dialog" aria-modal="true" aria-label={`Карточка ученика ${title(studentDraft)}`}><header><div><p className="admin-eyebrow">КАРТОЧКА УЧЕНИКА</p><h2>{title(studentDraft)}</h2></div><button className="admin-button admin-button--quiet" aria-label="Закрыть карточку" onClick={() => setStudentDraft(null)}><X size={16} /></button></header><div className="school-student-class-row"><label className="school-field">Административный класс<select value={studentDraft.class_name || ''} onChange={(e) => setStudentDraft({ ...studentDraft, class_name: e.target.value })}><option value="">Класс не указан</option>{classNames.map((c) => <option key={c} value={c}>{formatClassLabel(c)}</option>)}</select></label><button className="admin-button" disabled={busy || (schoolMode && !schoolWritable) || !studentDraft.class_name || studentDraft.class_name === data.students.find((p) => String(p.id) === String(studentDraft.id))?.class_name} onClick={() => void saveStudentClass(studentDraft, studentDraft.class_name)}>Сохранить класс</button></div><div className="school-detail-section"><h3>Учебные группы <span>{membershipRows(studentDraft).length}</span></h3><div className="admin-detail-list">{membershipRows(studentDraft).map((m: Row) => { const matched = data.groups.find((g) => String(g.id) === groupIdOf(m)); return <div key={groupIdOf(m)}><strong>{audienceLabel(data.groups.find((g) => String(g.id) === groupIdOf(m)) || { ...m, name: m.group_name || m.name })}</strong><small>{subjectOf(m) || 'Учебная группа'}</small>{matched ? <button className="admin-link" disabled={busy || (schoolMode && !schoolWritable)} onClick={() => void removeMembership(studentDraft, matched)}>Исключить</button> : null}</div>; })}{!membershipRows(studentDraft).some((m: Row) => String(m.group_type || '').toLowerCase() !== 'exam_track') ? <p className="school-empty-note">Ученик пока не входит в учебные группы.</p> : null}</div></div><div className="school-inline-add"><label className="admin-search"><Search size={14} /><input value={groupStudentQuery} onChange={(e) => setGroupStudentQuery(e.target.value)} placeholder="Найти учебную группу" /></label><div className="school-inline-results">{data.groups.filter((g) => !g.archived_at && String(g.group_type || '').toLowerCase() !== 'class' && !membershipRows(studentDraft).some((m: Row) => groupIdOf(m) === String(g.id)) && `${audienceLabel(g)} ${subjectOf(g)}`.toLocaleLowerCase('ru').includes(groupStudentQuery.toLocaleLowerCase('ru'))).slice(0, 8).map((g) => <button key={g.id} disabled={busy || (schoolMode && !schoolWritable)} onClick={() => void addMembership(String(studentDraft.id), String(g.id))}>{audienceLabel(g)} <small>{subjectOf(g) || 'Учебная группа'}</small></button>)}</div></div>{(studentDraft.exam_profile_memberships || []).length ? <div className="school-detail-section"><h3>Экзаменационные группы</h3><div className="admin-detail-list">{studentDraft.exam_profile_memberships.map((m: Row) => <div key={groupIdOf(m)}><strong>{audienceLabel(data.groups.find((g) => String(g.id) === groupIdOf(m)) || { ...m, name: m.group_name || m.name })}</strong><small>{m.subject || m.exam_track || 'Экзаменационная группа'}</small></div>)}</div></div> : null}</dialog></div> : null}
    {newStudentDraft ? <div role="presentation" className="school-editor-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setNewStudentDraft(null); }}><dialog ref={(node) => { if (node && !node.open) node.showModal(); }} onCancel={(event) => { event.preventDefault(); setNewStudentDraft(null); }} className="school-editor-dialog school-create-student-dialog" aria-modal="true"><header><div><p className="admin-eyebrow">Школьный справочник</p><h2>Новый ученик</h2></div><button className="admin-button admin-button--quiet" onClick={() => setNewStudentDraft(null)}><X size={16} /></button></header><label className="school-field">ФИО<input autoFocus value={newStudentDraft.display_name} onChange={(e) => setNewStudentDraft({ ...newStudentDraft, display_name: e.target.value })} /></label><label className="school-field">Административный класс<select value={newStudentDraft.class_name} onChange={(e) => setNewStudentDraft({ ...newStudentDraft, class_name: e.target.value })}><option value="">Выберите класс</option>{classNames.map((c) => <option key={c} value={c}>{formatClassLabel(c)}</option>)}</select></label><footer><button className="admin-button admin-button--primary" disabled={busy || !newStudentDraft.display_name.trim() || !newStudentDraft.class_name || (schoolMode && !schoolWritable)} onClick={() => void createStudent()}>{busy ? 'Создаём…' : 'Создать ученика'}</button><button className="admin-button admin-button--quiet" onClick={() => setNewStudentDraft(null)}>Отмена</button></footer></dialog></div> : null}
    {pendingMove ? <div role="presentation" className="school-editor-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setPendingMove(null); }}><dialog ref={(node) => { if (node && !node.open) node.showModal(); }} onCancel={(event) => { event.preventDefault(); setPendingMove(null); }} className="school-editor-dialog school-move-dialog" aria-modal="true"><header><div><p className="admin-eyebrow">ПЕРЕМЕЩЕНИЕ ВНУТРИ ПРЕДМЕТА</p><h2>{title(pendingMove.student)}</h2></div><button className="admin-button admin-button--quiet" onClick={() => setPendingMove(null)}><X size={16} /></button></header><p>Ученик уже состоит в группе «{audienceLabel(pendingMove.source)}».</p><footer><button className="admin-button admin-button--primary" disabled={busy} onClick={() => void commitMembership(pendingMove.student, pendingMove.target, pendingMove.source)}>Перевести в «{audienceLabel(pendingMove.target)}»</button><button className="admin-button" disabled={busy} onClick={() => void commitMembership(pendingMove.student, pendingMove.target)}>Оставить в обеих</button><button className="admin-button admin-button--quiet" onClick={() => setPendingMove(null)}>Отмена</button></footer></dialog></div> : null}
    {archivePreview ? <div className="school-editor-backdrop school-archive-backdrop"><dialog ref={(node) => { if (node && !node.open) node.showModal(); }} onCancel={(event) => { event.preventDefault(); setArchivePreview(null); }} className="school-editor-dialog school-archive-dialog" aria-modal="true" aria-label="Архивирование группы"><header><div><p className="admin-eyebrow">АРХИВ ГРУПП</p><h2>{audienceLabel(archivePreview.group)}</h2></div><button className="admin-button admin-button--quiet" aria-label="Закрыть проверку связей" onClick={() => setArchivePreview(null)}><X size={16} /></button></header><p>Архивная группа сохраняет ID, состав и историю. Она исчезнет из активной доски и выбора участников новых занятий.</p><dl className="school-archive-counts">{Object.entries({ active_memberships: 'Действующие ученики', historical_memberships: 'Исторические включения', assignments: 'Действующие назначения', historical_assignments: 'Исторические назначения', canonical_template: 'Ссылки в шаблонах', dated_weeks: 'Датированные недели', journals: 'Журналы', classroom_refs: 'Google Classroom', source_refs: 'Другие источники', json_refs: 'Сохранённые ссылки' }).map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{archivePreview.counts?.[key] ?? 'Не проверено'}</dd></div>)}</dl>{archivePreview.unknown_references?.length ? <p className="school-group-warning">Часть исторических связей невозможно проверить полностью. Физическое удаление недоступно; все связи будут сохранены.</p> : null}{archivePreview.archive_ready === false ? <p className="school-source-note">Для архивирования подготовлена миграция 037. Её применение к школьной базе требует отдельного согласования.</p> : null}{schoolMode && !schoolWritable ? <p className="school-source-note">Школьная база открыта только для чтения.</p> : null}{!archivePreview.archived ? <label className="school-archive-confirm"><input type="checkbox" checked={archiveConfirmed} onChange={(e) => setArchiveConfirmed(e.target.checked)} />Подтверждаю архивирование с сохранением состава, назначений и исторических занятий.</label> : null}{error ? <p role="alert" className="admin-error-text">{error}</p> : null}<footer><button className="admin-button admin-button--primary" disabled={busy || (!archivePreview.archived && !archiveConfirmed) || archivePreview.archive_ready === false || (schoolMode && !schoolWritable)} onClick={() => void changeArchive(archivePreview.group, Boolean(archivePreview.archived))}>{archivePreview.archived ? 'Восстановить группу' : 'Подтвердить архивирование'}</button><button className="admin-button admin-button--quiet" onClick={() => setArchivePreview(null)}>Отмена</button></footer></dialog></div> : null}
    {error ? <p role="alert" className="admin-error-text">{error}</p> : null}{notice ? <output className="school-editor-notice">{notice}</output> : null}{sourceMode && !schoolMode ? <small className="school-local-source">{sourceMode}</small> : null}
    {groupDraft && !groupDraft._addOnly ? <div role="presentation" className="school-editor-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setGroupDraft(null); }}><dialog ref={(node) => { if (node && !node.open) node.showModal(); }} onCancel={(event) => { event.preventDefault(); setGroupDraft(null); }} className="school-editor-dialog school-group-settings" aria-modal="true" aria-label="Настройки группы"><header><div><p className="admin-eyebrow">Настройки группы</p><h2>{groupDraft.id ? audienceLabel(groupDraft) : 'Создать группу'}</h2></div><button className="admin-button admin-button--quiet" onClick={() => setGroupDraft(null)}><X size={16} /></button></header>
      {schoolMode && !schoolWritable ? <p className="school-source-note">Школьный справочник открыт только для чтения. Настройки можно просмотреть.</p> : null}
      <label className="school-field">Название<input value={groupDraft.display_name ?? groupDraft.name ?? ''} onChange={(e) => setGroupDraft({ ...groupDraft, display_name: e.target.value, name: groupDraft.id ? groupDraft.name : e.target.value })} placeholder="Например, Английский B" /></label><label className="school-field">Предмет или направление<input value={groupDraft.subject || ''} onChange={(e) => setGroupDraft({ ...groupDraft, subject: e.target.value })} placeholder="Математика, Английский…" /></label>{groupDraft.id && subjectLabel(groupDraft.subject) !== subjectLabel(groupDraft._original_subject) ? <p className="school-group-warning" role="alert">Изменение основного предмета влияет на классификацию группы. Предметы существующих занятий и назначений останутся прежними.</p> : null}{!groupDraft._addOnly ? <><label className="school-field">Преподаватель по умолчанию<select value={groupDraft.default_teacher_identity_id || ''} onChange={(e) => setGroupDraft({ ...groupDraft, default_teacher_identity_id: e.target.value })}><option value="">Не назначен</option>{data.teachers.filter((t) => t.status !== "inactive").map((t) => <option key={t.id} value={t.id}>{title(t)}</option>)}</select></label><label className="school-field">Комментарий<textarea value={groupDraft.comment || ''} onChange={(e) => setGroupDraft({ ...groupDraft, comment: e.target.value })} placeholder="Необязательно" /></label></> : null}
      {!groupDraft.id ? <label className="school-field">Тип группы<select value={groupDraft.group_type} onChange={(e) => setGroupDraft({ ...groupDraft, group_type: e.target.value })}><option value="instructional">Учебная группа</option><option value="class">Административный класс</option><option value="exam_track">Экзаменационная группа</option></select></label> : null}
      {!groupDraft._addOnly && !groupDraft.id ? <label className="school-field">Добавить учеников (необязательно)<select multiple value={pickedStudents} onChange={(e) => setPickedStudents(Array.from(e.target.selectedOptions).map((o) => o.value))}>{data.students.map((p) => <option key={p.id} value={p.id}>{title(p)} · {p.class_name || 'класс не указан'}</option>)}</select></label> : null}
      {groupDraft._addOnly ? <label className="school-field">Ученик<select value={pickedStudents[0] || ''} onChange={(e) => setPickedStudents(e.target.value ? [e.target.value] : [])}><option value="">Выберите ученика любой параллели</option>{data.students.map((p) => <option key={p.id} value={p.id}>{title(p)} · {p.class_name || 'класс не указан'}</option>)}</select></label> : null}
      {groupDraft.id && !groupDraft._addOnly ? <button className="admin-button admin-button--quiet" disabled={busy} onClick={() => void prepareArchive(groupDraft)}>{groupDraft.archived_at ? 'Связи архивной группы' : 'Архивировать…'}</button> : null}
      <footer>{groupDraft._addOnly ? <button className="admin-button admin-button--primary" disabled={!pickedStudents.length || busy} onClick={() => void addMembership(pickedStudents[0], String(groupDraft.id))}>Добавить ученика</button> : <button className="admin-button admin-button--primary" disabled={!String(groupDraft.display_name || groupDraft.name).trim() || busy || (schoolMode && !schoolWritable)} onClick={async () => { if (groupDraft.id && subjectLabel(groupDraft.subject) !== subjectLabel(groupDraft._original_subject) && !window.confirm('Изменить основной предмет группы? Существующие назначения и занятия не будут изменены.')) return; const body = { name: String(groupDraft.name || groupDraft.display_name).trim(), display_name: String(groupDraft.display_name || groupDraft.name).trim(), subject: groupDraft.subject || '', group_type: groupDraft.group_type || 'instructional', comment: groupDraft.comment || '', ...(groupDraft.id && String(groupDraft.default_teacher_identity_id || "") === String(groupDraft._original_default_teacher_identity_id || "") ? {} : { default_teacher_identity_id: groupDraft.default_teacher_identity_id || null }) }; const path = groupDraft.id ? `/api/admin/groups/${groupDraft.id}` : '/api/admin/groups'; setBusy(true); setError(''); try { const saved = await api<Row>(path, { method: groupDraft.id ? 'PATCH' : 'POST', body: JSON.stringify(body) }); const groupId = String(groupDraft.id || saved.id || ''); if (pickedStudents.length && !groupId) throw new Error('Группа сохранена, но сервер не вернул её ID. Ученики не добавлены. Обновите список и добавьте их отдельно.'); let added = 0; let partial = false; for (const id of pickedStudents) { try { await api('/api/admin/memberships/transfer', { method: 'POST', body: JSON.stringify({ identity_id: id, to_group_id: groupId, reason: 'Добавлено администратором' }) }); added += 1; } catch { partial = true; break; } } setNotice(partial ? `Группа сохранена. Добавлено учеников: ${added} из ${pickedStudents.length}; остальные не сохранены. Проверьте список и повторите добавление.` : 'Изменения сохранены.'); setGroupDraft(null); setPickedStudents([]); window.dispatchEvent(new Event('school-directory-updated')); await load(); } catch (e) { setError(e instanceof Error ? e.message : 'Не удалось сохранить группу.'); } finally { setBusy(false); } }}>{busy ? 'Сохраняем…' : groupDraft.id ? 'Сохранить группу' : 'Создать группу'}</button>}{!groupDraft._addOnly && groupDraft.id && pickedStudents.length ? <button className="admin-button" disabled={busy} onClick={async () => { let added = 0; for (const id of pickedStudents) { try { await api('/api/admin/memberships/transfer', { method: 'POST', body: JSON.stringify({ identity_id: id, to_group_id: String(groupDraft.id), reason: 'Добавлено администратором' }) }); added += 1; } catch { setError(`Добавлено ${added} из ${pickedStudents.length}; оставшиеся записи не изменены.`); break; } } if (added === pickedStudents.length) setNotice(`Добавлено учеников: ${added}.`); setPickedStudents([]); setGroupDraft(null); window.dispatchEvent(new Event('school-directory-updated')); await load(); }}>Добавить выбранных ({pickedStudents.length})</button> : null}<button className="admin-button admin-button--quiet" onClick={() => setGroupDraft(null)}>Отмена</button></footer>
    </dialog></div> : null}
  </div>;
}


