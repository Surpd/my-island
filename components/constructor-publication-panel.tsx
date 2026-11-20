'use client';

import {useEffect, useEffectEvent, useRef, useState} from 'react';
import { ArrowRight, ChevronDown, X } from 'lucide-react';
import Link from 'next/link';
import { normalizeRoomDisplay } from './schedule-rooms';
import { formatLessonTimeRange } from './lesson-time';
import './constructor-publication-panel.css';
import {
  ConstructorLegacyReview, ConstructorRosterReview,
  type NormalizationCopy, type NormalizationPreview, type RosterPreview, type RosterScope,
} from './constructor-review-fixes';

type Api = <T>(path:string, init?:RequestInit)=>Promise<T>;
type ApiError = Error & {status?:number};
type SemanticBlock = {weekday:number;start:string;end:string;grade:string;assignments:{activity:string;teachers:string[];groups:string[];metadata?:{room?:string}}[]};
type LessonContext = {
  day?: string | number;
  start?: string;
  end?: string;
  subject?: string;
  teachers?: string[];
  groups?: string[];
  class_ids?: string[];
  room?: string;
};
type Diagnostic = {
  severity: 'info' | 'conflict' | 'critical' | 'warning';
  code: string;
  message: string;
  lesson_id?: string;
  related_lesson_ids?: string[];
  block_key?: string;
  context?: LessonContext;
  action?: string;
  affected_student_ids?: string[];
  affected_student_count?: number;
  class_ids?: string[];
  weekday?: number;
};
type Deferred = {lesson_id:string;reason:string;block_key?:string;exclusion_scope?:'direct'|'cascade';cause_codes?:string[];cause_lesson_ids?:string[];action:'excluded'|'preserved_base'|'unknown'|'retained_unresolved';context?:LessonContext};
type Summary = {source_lessons?:number;resolved_lessons?:number;unresolved_lessons?:number;acknowledged_exceptions?:number;campus_lessons?:number;main_campus_cards?:number;elective_blocks?:number;elective_lanes?:number;clubs_hidden?:number;unresolved_personal_assignments?:number;non_blocking_warnings?:number;admin_only_lessons?:number;with_remarks_lessons?:number;diagnostic_events?:number;conflict_lessons?:number;affected_students?:number;day_mode_issues?:number;direct_exclusions?:number;cascade_exclusions?:number;ready_lessons:number;deferred_lessons:number;deferred_blocks:number;unchanged_lessons:number;replaced_lessons:number;info_count:number;conflict_count:number;critical_count:number};
type Preview = {published_source?:unknown;preview_token:string;can_publish:boolean;publication_available?:boolean;read_only?:boolean;base_version_id?:string|null;head_revision:number;blockers:Omit<Diagnostic,'severity'>[];deferred:Deferred[];diagnostics?:Diagnostic[];summary?:Summary;source_kind?:'template_week'|'authored_week'|'template';diff:{added:number;changed:number;removed:number;before_blocks:number;after_blocks:number};change_items?:{block_key:string;operation:string;before:SemanticBlock|null;after:SemanticBlock|null}[]};
type Publication = {id:string;scope_kind:string;scope_key:string;created_at:string;published_id:string;version_id?:string;runtime_readback:{verified:boolean;block_count:number};restored_from?:string;summary?:Partial<Summary>;diagnostics?:Diagnostic[];deferred?:Deferred[];preview?:{diagnostics?:Diagnostic[];summary?:Partial<Summary>;deferred?:Deferred[]};metadata?:{diagnostics?:Diagnostic[];summary?:Partial<Summary>;deferred?:Deferred[]};preview_metadata?:{diagnostics?:Diagnostic[];summary?:Partial<Summary>;deferred?:Deferred[]}};
type History = {items:Publication[];heads?:Record<string,{revision:number}>};

export type ConstructorReviewLesson = {
  day: number;
  start?: string;
  end?: string;
  subject: string;
  classes: string[];
  teachers: string[];
  room?: string;
  groups?: string[];
};
export type ConstructorReviewNavigation = { title: string; relatedIds: string[] };
export type ConstructorLocalIssue = { lessonId: string; related?: string; severity: 'error' | 'warning'; type: 'teacher' | 'room' | 'audience' | 'data'; category?: string; code?: string; message: string };
export type ConstructorReviewFilter = 'all' | 'critical' | 'conflict' | 'warning' | 'info' | 'deferred';
type Severity = Exclude<ConstructorReviewFilter, 'all'>;
type ReviewSection = 'issues' | 'fixes' | 'publication';
type IssueType = 'time' | 'teacher' | 'room' | 'audience' | 'record' | 'publication';
type IssueText = { type: IssueType; title: string; guidance?: string };
type ReviewIssue = { key: string; severity: Severity; ids: string[]; diagnostics: Diagnostic[]; deferred: Deferred[] };
type ReviewView = {
  section: ReviewSection;
  filter: ConstructorReviewFilter;
  dayFilter: string;
  classFilter: string;
  typeFilter: string;
  query: string;
  scrollTop: number;
  selectedKey: string | null;
  openFixes: string[];
  focusedFixLesson?: string;
  scope: string;
  week: string;
  rosterScope: RosterScope;
};

const savedReviewViews = new Map<string, ReviewView>();
const dayNames = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const fullDayNames = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];
const overlapCodes = new Set(['teacher_overlap', 'room_overlap', 'student_overlap', 'joint_activity_unconfirmed']);
const audienceCodes = new Set(['audience_unresolved', 'roster_review', 'stale_group_subject_mismatch', 'missing_runtime_grade']);
const preferredIssueCodes = ['joint_activity_unconfirmed', 'student_overlap', 'roster_review', 'stale_group_subject_mismatch'];
const severityLabels: Record<Severity, string> = {
  critical: 'Блокирует публикацию',
  conflict: 'Конфликт',
  warning: 'Требует внимания',
  info: 'К сведению',
  deferred: 'Отложено',
};
const issueTypes: Record<IssueType, string> = {
  time: 'День и время', teacher: 'Преподаватели', room: 'Кабинеты',
  audience: 'Группы и ученики', record: 'Данные занятий', publication: 'Публикация',
};
const emptySummary:Summary={ready_lessons:0,deferred_lessons:0,deferred_blocks:0,unchanged_lessons:0,replaced_lessons:0,info_count:0,conflict_count:0,critical_count:0};

const issueText: Record<string, IssueText> = {
  student_overlap: { type: 'audience', title: 'У учеников два занятия одновременно', guidance: 'В личном расписании этих учеников появится отметка, что занятие ждёт уточнения. Сверьте составы групп или перенесите одно занятие.' },
  teacher_overlap: { type: 'teacher', title: 'У преподавателя два занятия одновременно', guidance: 'Преподаватель не сможет провести оба занятия. Назначьте другого преподавателя или измените время.' },
  room_overlap: { type: 'room', title: 'Два очных занятия в одном кабинете', guidance: 'Оба занятия занимают кабинет в одно время. Выберите свободный кабинет или перенесите одно занятие.' },
  joint_activity_unconfirmed: { type: 'teacher', title: 'Нужно подтвердить совместное занятие', guidance: 'Совпадают преподаватель, предмет и время. Подтвердите, что группы занимаются вместе, или разведите занятия по времени.' },
  joint_activity: { type: 'teacher', title: 'Совместное занятие подтверждено', guidance: 'Составы групп сохранены.' },
  review_required: { type: 'record', title: 'Уточните данные занятия', guidance: 'Сверьте предмет, преподавателя и состав участников, затем подтвердите нужное назначение.' },
  audience_unresolved: { type: 'audience', title: 'Не подтверждён состав участников', guidance: 'Пока состав не подтверждён, ученики могут не увидеть занятие в личном расписании. Выберите класс или группу и проверьте состав.' },
  stale_group_subject_mismatch: { type: 'audience', title: 'Группа относится к другому предмету', guidance: 'Ученики могут получить неверное назначение. Выберите группу для этого предмета и сверьте её состав.' },
  roster_review: { type: 'audience', title: 'Состав группы изменился', guidance: 'Сохранённый список учеников отличается от текущего. Сверьте изменения, включая ручные исключения, и подтвердите подходящий состав.' },
  missing_teacher: { type: 'teacher', title: 'Преподаватель не назначен', guidance: 'Занятие может отсутствовать в личном расписании преподавателя. Укажите преподавателя или подтвердите самостоятельную работу, если она предусмотрена.' },
  unknown_teacher: { type: 'teacher', title: 'Преподавателя нет среди действующих', guidance: 'Проверьте, кто ведёт занятие, и выберите преподавателя из справочника.' },
  missing_room: { type: 'room', title: 'Не указан кабинет', guidance: 'Участники не узнают, куда прийти. Укажите кабинет очного занятия.' },
  elective_block_unlinked: { type: 'record', title: 'Курс по выбору не связан с общим блоком', guidance: 'Направление может отображаться отдельно от других вариантов. Укажите его общий блок.' },
  elective_block_name_conflict: { type: 'record', title: 'У направлений одного блока разные названия', guidance: 'Чтобы направления отображались вместе, задайте одно название общего блока.' },
  unsupported_partial_cohort: { type: 'audience', title: 'Не определены участники из параллели', guidance: 'Укажите подтверждённый список учеников для части параллели.' },
  mixed_day_modes_unsupported: { type: 'time', title: 'У участников разные режимы дня', guidance: 'Уточните режимы классов и состав занятия.' },
  day_mode_conflict: { type: 'time', title: 'У классов одной параллели разные режимы дня', guidance: 'Откройте настройки дня и устраните различия в режимах этих классов.' },
  explicit_deferred: { type: 'record', title: 'Занятие отложено администратором', guidance: 'Учтите ранее принятое решение. Верните занятие в расписание, если причина откладывания устранена.' },
  unknown_class: { type: 'audience', title: 'Класс отсутствует в справочнике', guidance: 'Выберите действующий класс занятия.' },
  missing_special_scope: { type: 'audience', title: 'Для отметки не определён класс', guidance: 'Укажите, к каким классам относится отметка.' },
  missing_runtime_grade: { type: 'audience', title: 'Не определена параллель', guidance: 'Проверьте классы и состав участников.' },
  projection_mismatch: { type: 'audience', title: 'Нельзя подтвердить личные расписания участников', guidance: 'Сверьте классы, группы и списки участников перед публикацией.' },
  empty_activity: { type: 'record', title: 'Не указано название занятия', guidance: 'Введите название предмета или события.' },
  invalid_slot: { type: 'time', title: 'День или время выходят за рамки расписания', guidance: 'Выберите действующий день и проверьте начало и конец занятия.' },
  duplicate_lesson_id: { type: 'record', title: 'В черновике повторяется запись занятия', guidance: 'Проверьте, какое занятие должно остаться, и обратитесь к ответственному за расписание, если дубль нельзя убрать.' },
  missing_lesson_id: { type: 'record', title: 'Система не может опознать запись занятия', guidance: 'Откройте свежую копию черновика. Если проблема осталась, обратитесь к ответственному за расписание.' },
  missing_origin_block: { type: 'publication', title: 'Не найдено исходное опубликованное занятие', guidance: 'Сверьте черновик с действующей версией перед заменой занятия.' },
  protected: { type: 'publication', title: 'Изменение защищённого занятия требует решения', guidance: 'Сверьте изменение с действующей версией и подтвердите подходящий вариант.' },
  protected_base: { type: 'publication', title: 'Черновик меняет защищённое занятие', guidance: 'Сверьте черновик с действующей версией перед публикацией.' },
  stale_base: { type: 'publication', title: 'Опубликованная версия изменилась', guidance: 'Откройте свежий черновик и повторите проверку.' },
  missing_base: { type: 'publication', title: 'Школьный шаблон ещё не опубликован', guidance: 'Выберите опубликованный школьный шаблон перед публикацией недели.' },
};

function diagnosticText(issue: Diagnostic): IssueText {
  return issueText[issue.code] || (issue.severity === 'info'
    ? { type: 'record', title: 'Сведения о занятии' }
    : { type: 'record', title: 'Запись требует проверки', guidance: 'Сверьте данные записи с текущим расписанием.' });
}

function readableNote(message?: string) {
  const text = message?.trim() || '';
  if (!/[а-яё]/iu.test(text) || /[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}|\b(?:canonical|campus-?projection|blockId|teacherId|lesson_id|[a-z]+_[a-z_]+)\b/i.test(text)) return '';
  return text;
}

function safeNames(values: string[] | undefined, directory: Record<string, string> = {}) {
  return [...new Set((values || []).map(value => directory[value] || value).filter(value =>
    value.trim() && !/^(?:[a-z]+[a-z\d]*[-_][a-z\d_-]+|[a-z\d_-]+:\S+|[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12})$/i.test(value),
  ))];
}

function readableContext(context?:LessonContext,teacherNames:Record<string,string>={},groupNames:Record<string,string>={}) {
  if(!context)return '';
  const day=typeof context.day==='number'?dayNames[context.day]:context.day;
  const teachers = safeNames(context.teachers, teacherNames);
  const groups = safeNames(context.groups, groupNames);
  return [day, formatLessonTimeRange(context.start, context.end), context.subject,
    teachers.length ? teachers.join(', ') : '', groups.length ? groups.join(', ') : ''].filter(Boolean).join(' · ');
}

function sourceLabel(kind?:Preview['source_kind']) {
  if(kind==='template_week')return 'Неделя рассчитана из школьного шаблона';
  if(kind==='authored_week')return 'Черновик отдельной недели';
  if(kind==='template')return 'Школьный шаблон';
  return 'Текущая база публикации';
}

function deferredOutcome(action: Deferred['action'], scope?: Deferred['exclusion_scope']) {
  if (action === 'preserved_base') return 'Сохранится прежнее занятие из опубликованной версии.';
  if (action === 'retained_unresolved') return 'Занятие останется в конструкторе. Непроверенное назначение не появится в личном расписании.';
  if (action === 'excluded' && scope === 'cascade') return 'Занятие связано с другой записью, которую нужно проверить перед публикацией.';
  if (action === 'excluded') return 'Занятие не войдёт в эту публикацию.';
  return 'Решение сохранено в истории публикации.';
}

function localDiagnostic(issue: ConstructorLocalIssue): Diagnostic {
  const code = issue.code || (issue.related && issue.type !== 'data' ?
    { teacher: 'teacher_overlap', room: 'room_overlap', audience: 'student_overlap' }[issue.type] :
    issue.category === 'critical' ? 'invalid_slot' :
    issue.category === 'review' ? 'roster_review' :
    issue.type === 'data' && /не указан[оа]? (название|предмет)/i.test(issue.message) ? 'empty_activity' :
    { teacher: 'missing_teacher', room: 'missing_room', audience: 'audience_unresolved', data: 'review_required' }[issue.type]);
  const overlapCount = code === 'student_overlap' ? Number(/одновременно:\s*(\d+)/iu.exec(issue.message)?.[1] || 0) : undefined;
  return {
    code, message: overlapCount ? '' : issue.message,
    lesson_id: issue.lessonId, related_lesson_ids: issue.related ? [issue.related] : [],
    affected_student_count: overlapCount,
    severity: issue.category === 'critical' ? 'critical' : issue.related ? 'conflict' : 'warning',
  };
}

function issueSeverity(issue: Diagnostic): Severity {
  if (issue.severity === 'critical') return 'critical';
  if (overlapCodes.has(issue.code) && issue.severity === 'conflict') return 'conflict';
  return issue.severity === 'info' && !['missing_room', 'missing_teacher', 'unknown_teacher'].includes(issue.code) ? 'info' : 'warning';
}

const severityOrder: Severity[] = ['critical', 'conflict', 'warning', 'deferred', 'info'];

function buildReviewIssues(diagnostics: Diagnostic[], deferred: Deferred[]) {
  const issues = new Map<string, ReviewIssue>();
  const issuesByLesson = new Map<string, ReviewIssue[]>();
  const deferredIssues = new Map<string, ReviewIssue>();
  for (const diagnostic of diagnostics) {
    const ids = [...new Set([...(diagnostic.lesson_id ? [diagnostic.lesson_id] : []), ...diagnostic.related_lesson_ids || []])].sort();
    const severity = issueSeverity(diagnostic);
    const contextKey = diagnostic.block_key || [diagnostic.weekday, ...(diagnostic.class_ids || [])].join('/');
    const family = ids.length > 1 && overlapCodes.has(diagnostic.code) ? 'pair' :
      ids.length === 1 && audienceCodes.has(diagnostic.code) ? 'audience' : diagnostic.code;
    const key = JSON.stringify([family, ...ids, ids.length ? '' : contextKey]);
    let issue = issues.get(key);
    if (!issue) {
      issue = { key, severity, ids, diagnostics: [], deferred: [] };
      issues.set(key, issue);
      for (const id of ids) {
        const related = issuesByLesson.get(id) || [];
        related.push(issue);
        issuesByLesson.set(id, related);
      }
    }
    if (severityOrder.indexOf(severity) < severityOrder.indexOf(issue.severity)) issue.severity = severity;
    if (!issue.diagnostics.some(item => item.code === diagnostic.code)) issue.diagnostics.push(diagnostic);
  }
  const priority = (item: Diagnostic) => {
    const rank = preferredIssueCodes.indexOf(item.code);
    return rank < 0 ? preferredIssueCodes.length : rank;
  };
  for (const issue of issues.values()) issue.diagnostics.sort((left, right) => priority(left) - priority(right));
  for (const entry of deferred) {
    const ids = [...new Set([entry.lesson_id, ...entry.cause_lesson_ids || []])];
    const codes = entry.cause_codes || [];
    const linked = (issuesByLesson.get(entry.lesson_id) || []).find(candidate =>
      codes.some(code => candidate.diagnostics.some(item => item.code === code)));
    if (linked) linked.deferred.push(entry);
    if (!deferredIssues.has(entry.lesson_id)) {
      deferredIssues.set(entry.lesson_id, {
        key: JSON.stringify(['deferred', entry.lesson_id]),
        severity: 'deferred', ids, diagnostics: [], deferred: [entry],
      });
    }
  }
  return {
    issues: [...issues.values(), ...[...deferredIssues.values()].filter(issue =>
      !issuesByLesson.get(issue.ids[0])?.some(candidate => candidate.deferred.includes(issue.deferred[0])))]
      .sort((left, right) => severityOrder.indexOf(left.severity) - severityOrder.indexOf(right.severity)),
    deferredIssues: [...deferredIssues.values()],
  };
}

function storedDiagnostics(item:Publication) { return item.diagnostics||item.preview?.diagnostics||item.preview_metadata?.diagnostics||item.metadata?.diagnostics||[]; }
function storedDeferred(item:Publication) { return item.deferred||item.preview?.deferred||item.preview_metadata?.deferred||item.metadata?.deferred||[]; }

export function ConstructorPublicationPanel({
  api, draftId, revision, draftName, weekStart, dirty, readOnly, publicationEnabled, mode, day, classId, classLabel,
  onClose, onSave, onRostersUpdated, onNormalizedCopy, onPublished, onOpenLesson,
  teacherNames, groupNames, lessonLabels, lessonDetails = {}, localIssues = [],
  onOpenComparison, onOpenGridDecisions, onResolveIssue, initialFilter,
}: {
  api: Api;
  draftId: string;
  revision: number;
  draftName: string;
  weekStart?: string;
  dirty: boolean;
  readOnly: boolean;
  publicationEnabled: boolean;
  mode: 'review' | 'history';
  day: number;
  classId?: string;
  classLabel?: string;
  onClose: () => void;
  onSave: () => Promise<boolean>;
  onRostersUpdated: (value: unknown) => void;
  onNormalizedCopy: (copy: NormalizationCopy) => Promise<void>;
  onPublished: () => Promise<void>;
  onOpenLesson: (id: string, context?: ConstructorReviewNavigation) => void;
  onOpenComparison?: () => void;
  onOpenGridDecisions?: () => void;
  onResolveIssue?: (ids: string[], code: string) => boolean;
  initialFilter?: ConstructorReviewFilter;
  teacherNames: Record<string, string>;
  groupNames: Record<string, string>;
  lessonLabels: Record<string, string>;
  lessonDetails?: Record<string, ConstructorReviewLesson>;
  localIssues?: ConstructorLocalIssue[];
}) {
  const historyOnly = mode === 'history';
  const [initialView] = useState(() => !historyOnly ? savedReviewViews.get(draftId) : undefined);
  const [section, setSection] = useState<ReviewSection>(initialFilter ? 'issues' : initialView?.section || 'issues');
  const [scope,setScope] = useState(weekStart ? 'week' : !initialFilter && initialView?.scope || 'template');
  const [week,setWeek] = useState(weekStart || (!initialFilter && initialView?.week) || '');
  const [preview,setPreview] = useState<Preview|null>(null);
  const [previewTarget,setPreviewTarget] = useState<{draftId:string;revision:number;scope:string;week:string}|null>(null);
  const [history,setHistory] = useState<History>({items:[]});
  const [historyPending, setHistoryPending] = useState(historyOnly);
  const [confirmed,setConfirmed] = useState(false);
  const [busy,setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [rosterScope, setRosterScope] = useState<RosterScope>(() =>
    initialView?.rosterScope === 'class' && !classId ? 'stale' : initialView?.rosterScope || 'stale',
  );
  const [rosters, setRosters] = useState<RosterPreview | null>(null);
  const [rosterError, setRosterError] = useState('');
  const [checkingRosters, setCheckingRosters] = useState(false);
  const [legacy, setLegacy] = useState<NormalizationPreview | null>(null);
  const [legacyError, setLegacyError] = useState('');
  const [reviewedRevision, setReviewedRevision] = useState<number | null>(null);
  const reviewRequest = useRef<AbortController | null>(null);
  const rosterRequest = useRef<AbortController | null>(null);
  const automaticRevision = useRef<number | null>(null);
  const [message,setMessage] = useState('');
  const [sessionExpired,setSessionExpired] = useState(false);
  const [published,setPublished] = useState(false);
  const [filter,setFilter] = useState<ConstructorReviewFilter>(initialFilter || initialView?.filter || 'all');
  const [query, setQuery] = useState(initialFilter ? '' : initialView?.query || '');
  const [dayFilter, setDayFilter] = useState(initialFilter ? '' : initialView?.dayFilter || '');
  const [classFilter, setClassFilter] = useState(initialFilter ? '' : initialView?.classFilter || '');
  const [typeFilter, setTypeFilter] = useState(initialFilter ? '' : initialView?.typeFilter || '');
  const [selectedKey, setSelectedKey] = useState<string | null>(initialFilter ? null : initialView?.selectedKey || null);
  const scrollBody = useRef<HTMLDivElement | null>(null);
  const restoreView = useRef(!initialFilter ? initialView : undefined);
  const selectedControl = useRef<HTMLButtonElement | null>(null);
  const [restore,setRestore] = useState<{publication:Publication; expected_head_revision:number; preview:{preview_token:string;can_restore:boolean;blockers:{message:string}[];diff:Preview['diff']}}|null>(null);
  const [restoreConfirmed,setRestoreConfirmed] = useState(false);
  const prefix = `/api/admin/schedule/constructor/${encodeURIComponent(draftId)}`;
  const legacyPrefix = `/api/admin/schedule/experiments/school/${encodeURIComponent(draftId)}`;
  const canCheckRosters = publicationEnabled && !readOnly;
  const canCheckLegacy = !readOnly && !weekStart;
  const errorMessage = (error: unknown) => {
    const status = (error as ApiError | null)?.status;
    if (status === 401) return 'Перепроверьте вход и повторите действие.';
    if (status === 403) return 'Для этого действия нужен доступ администратора расписания.';
    if (status === 409 || status === 412) return 'Расписание изменилось. Откройте свежую версию и повторите проверку.';
    if (status === 422) return 'Проверьте выбранный черновик и даты.';
    if (status === 429) return 'Слишком много запросов. Попробуйте ещё раз чуть позже.';
    return 'Проверьте соединение и повторите действие.';
  };
  const showError = (error: unknown) => {
    setMessage(errorMessage(error));
    setSessionExpired((error as ApiError | null)?.status === 401);
  };
  const loadHistory = () => api<History>(`${prefix}/publications`).then(setHistory);
  useEffect(() => {
    if (!historyOnly) return;
    const controller = new AbortController();
    void api<History>(`${prefix}/publications`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setHistory(value); })
      .catch(error => { if (!controller.signal.aborted) showError(error); })
      .finally(() => { if (!controller.signal.aborted) setHistoryPending(false); });
    return () => controller.abort();
  }, [api, historyOnly, prefix]);
  useEffect(() => () => {
    const reviewing = reviewRequest.current;
    const checkingScope = rosterRequest.current;
    reviewRequest.current = null;
    rosterRequest.current = null;
    reviewing?.abort();
    checkingScope?.abort();
  }, []);
  const publishedQuery = new URLSearchParams(scope==='week'&&week ? {week_start:week} : {});
  if (typeof window!=='undefined' && new URLSearchParams(window.location.search).get('role')==='admin') publishedQuery.set('role','admin');
  const publishedHref = `/admin/schedule/published${publishedQuery.size ? `?${publishedQuery}` : ''}`;
  const body = {scope_kind:scope, expected_revision:revision, ...(scope==='week' ? {week_start:week} : {})};
  const describe = (block:SemanticBlock|null) => block ? <><strong>{dayNames[block.weekday]} {block.start}–{block.end} · {block.grade} параллель</strong>{block.assignments.map((item,index)=><p key={index}>{item.activity} · {item.teachers.map(id=>teacherNames[id]||'Преподаватель не подтверждён').join(', ')||'без преподавателя'} · {item.groups.map(id=>groupNames[id]||'Группа не подтверждена').join(', ')}{item.metadata?.room ? ` · ${normalizeRoomDisplay(item.metadata.room)}` : ''}</p>)}</> : <span>Нет занятия</span>;
  const previewMatches=previewTarget?.draftId===draftId&&previewTarget.revision===revision&&previewTarget.scope===scope&&(scope!=='week'||previewTarget.week===week);
  const activePreview=previewMatches&&!dirty?preview:null;
  const summary = {...emptySummary,...activePreview?.summary};
  const rawDiagnostics = activePreview?.diagnostics || [];
  const observedDiagnostics = activePreview ? [
    ...rawDiagnostics,
    ...(activePreview.blockers || []).filter(blocker => !rawDiagnostics.some(item =>
      item.severity === 'critical' && item.code === blocker.code && item.lesson_id === blocker.lesson_id &&
      (!blocker.block_key || item.block_key === blocker.block_key) &&
      (blocker.weekday == null || item.weekday === blocker.weekday),
    )).map(blocker => ({ ...blocker, severity: 'critical' as const })),
  ] : localIssues.map(localDiagnostic);
  const currentRosters = rosters?.revision === revision ? rosters : null;
  const currentLegacy = legacy?.source_revision === revision ? legacy : null;
  const rosterIssues: Diagnostic[] = (currentRosters?.lessons || [])
    .filter(lesson => lesson.changed || !lesson.safe)
    .map(lesson => ({
      severity: 'warning', code: 'roster_review', lesson_id: lesson.id,
      message: lesson.reason || '', context: lesson,
    }));
  const diagnostics = [...observedDiagnostics, ...rosterIssues];
  const { issues, deferredIssues } = buildReviewIssues(diagnostics, activePreview?.deferred || []);
  const conflicts = issues.filter(issue => issue.severity === 'conflict');
  const conflictLessons = new Set(conflicts.flatMap(issue => issue.ids));
  const counts = {
    info: issues.filter(issue => issue.severity === 'info').length,
    warning: issues.filter(issue => issue.severity === 'warning').length,
    conflict: conflicts.length,
    critical: issues.filter(issue => issue.severity === 'critical').length,
    deferred: deferredIssues.length,
  };
  const getTargets = (issue: ReviewIssue) => issue.ids.map(id => {
    const saved = lessonDetails[id];
    const context = issue.diagnostics.find(item => item.lesson_id === id && item.context)?.context
      || issue.deferred.find(item => item.lesson_id === id && item.context)?.context;
    const rawDay = context?.day;
    const groupLabels = safeNames(context?.groups, groupNames);
    const contextClassNames = safeNames(context?.class_ids?.map(id => groupNames[id]).filter(Boolean));
    return {
      id,
      available: Boolean(saved || lessonLabels[id]),
      day: saved?.day ?? (typeof rawDay === 'number' ? rawDay : undefined),
      start: saved?.start ?? context?.start,
      end: saved?.end ?? context?.end,
      subject: saved?.subject || context?.subject || lessonLabels[id]?.split(' · ')[0] || 'Занятие',
      classes: safeNames([...(saved?.classes || []), ...contextClassNames,
        ...groupLabels.filter(name => /^\d{1,2}(?:\s+класс|\s*-?\s*[А-ЯA-Z])$/iu.test(name))]),
      teachers: safeNames(saved?.teachers || context?.teachers, teacherNames),
      groups: safeNames(saved?.groups || groupLabels, groupNames),
      room: normalizeRoomDisplay(saved?.room || context?.room),
    };
  });
  const toDisplayIssue = (issue: ReviewIssue) => {
    const targets = getTargets(issue);
    const descriptions = issue.diagnostics.length ? issue.diagnostics.map(diagnosticText) :
      [{ title: 'Занятие отложено', type: 'publication' as const, guidance: 'Откройте занятие и проверьте, какие данные нужно уточнить.' }];
    const classNames = safeNames([...targets.flatMap(target => target.classes),
      ...issue.diagnostics.flatMap(item => (item.class_ids || item.context?.class_ids || []).map(id => groupNames[id]).filter(Boolean))]);
    const days = [...new Set([...targets.map(target => target.day),
      ...issue.diagnostics.map(item => item.weekday ?? item.context?.day)].filter((day): day is number => typeof day === 'number'))];
    return { ...issue, targets, descriptions, classNames, days };
  };
  const allIssues = issues.map(toDisplayIssue);
  const allDeferred = deferredIssues.map(toDisplayIssue);
  const dayOptions = [...new Set([...allIssues, ...allDeferred].flatMap(issue => issue.days))].filter(day => fullDayNames[day]).sort((a, b) => a - b);
  const classOptions = [...new Set([...allIssues, ...allDeferred].flatMap(issue => issue.classNames))].sort((a, b) => a.localeCompare(b, 'ru', { numeric: true }));
  const typeOptions = [...new Set([...allIssues, ...allDeferred].flatMap(issue => issue.descriptions.map(item => item.type)))];
  const normalizedQuery = query.trim().toLocaleLowerCase('ru').replace(/ё/g, 'е');
  const normalizeText = (value: string) => value.toLocaleLowerCase('ru').replace(/ё/g, 'е');
  const visibleIssues = (filter === 'deferred' ? allDeferred : allIssues).filter(issue => (
    (filter === 'all' || filter === issue.severity) &&
    (dayFilter === '' || issue.days.includes(Number(dayFilter))) &&
    (classFilter === '' || issue.classNames.includes(classFilter)) &&
    (typeFilter === '' || issue.descriptions.some(item => item.type === typeFilter)) &&
    (normalizedQuery === '' || normalizeText([
      ...issue.descriptions.map(item => item.title), ...issue.classNames,
      ...issue.days.map(item => fullDayNames[item]),
      ...issue.targets.flatMap(target => [target.subject, target.start, target.end, target.room, ...target.teachers, ...target.groups]),
    ].filter(Boolean).join(' ')).includes(normalizedQuery))
  ));
  const clearFilters = () => {
    setFilter('all');
    setDayFilter('');
    setClassFilter('');
    setTypeFilter('');
    setQuery('');
  };
  const copyReport = async () => {
    const byCode = new Map<string, { count: number; ids: Set<string> }>();
    for (const issue of issues) {
      for (const diagnostic of issue.diagnostics) {
        const group = byCode.get(diagnostic.code) || { count: 0, ids: new Set<string>() };
        group.count++;
        for (const id of issue.ids) if (lessonLabels[id]) group.ids.add(id);
        byCode.set(diagnostic.code, group);
      }
    }
    const rows = [...byCode].map(([code, group]) => {
      const links = [...group.ids].map(id => {
        const search = new URLSearchParams({ draft: draftId, lesson: id });
        return `${window.location.origin}/admin/schedule/constructor?${search}`;
      });
      return [code, group.count, links.join(', ')].join('\t');
    });
    try {
      await navigator.clipboard.writeText(['Отчёт проверки расписания', ...rows].join('\n'));
      setMessage('Отчёт проверки скопирован. Он содержит коды, количества и ссылки на записи.');
    } catch {
      setMessage('Браузер не смог скопировать отчёт.');
    }
  };
  const rosterBody = (target: RosterScope) => ({
    expected_revision: revision, scope: target,
    ...(target === 'day' ? { day } : {}),
    ...(target === 'class' ? { class_id: classId } : {}),
  });
  const requestRosters = (target: RosterScope, signal: AbortSignal) => api<RosterPreview>(`${prefix}/audiences/preview`, {
    method: 'POST', body: JSON.stringify(rosterBody(target)), signal,
  });
  const checkRosterScope = async (target: RosterScope) => {
    rosterRequest.current?.abort();
    const controller = new AbortController();
    rosterRequest.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 120_000);
    setRosterScope(target);
    setCheckingRosters(true);
    setRosters(null);
    setRosterError('');
    try {
      const result = await requestRosters(target, controller.signal);
      if (!controller.signal.aborted) setRosters(result);
    } catch (error) {
      if (rosterRequest.current === controller) setRosterError(controller.signal.aborted ? 'Истекло время ожидания. Повторите проверку.' : errorMessage(error));
    } finally {
      window.clearTimeout(timeout);
      if (rosterRequest.current === controller) setCheckingRosters(false);
    }
  };
  const controlsBusy = busy || checking || checkingRosters;
  const canShowFixes = canCheckRosters || canCheckLegacy;
  const weekValid = scope !== 'week' || Boolean(week) && new Date(`${week}T12:00:00`).getDay() === 1;
  const resetPublicationPreview = () => { setPreview(null); setPreviewTarget(null); setConfirmed(false); setMessage(''); };
  const saveView = (key = selectedKey) => {
    if (historyOnly) return;
    if (savedReviewViews.size >= 20 && !savedReviewViews.has(draftId)) {
      const oldest = savedReviewViews.keys().next().value;
      if (oldest) savedReviewViews.delete(oldest);
    }
    const scroller = scrollBody.current;
    const focused = scroller?.contains(document.activeElement) ? document.activeElement as HTMLElement : undefined;
    const view = {
      section, filter, query, dayFilter, classFilter, typeFilter, scope, week, rosterScope,
      selectedKey: key, scrollTop: scroller?.scrollTop || 0,
      openFixes: [...(scroller?.querySelectorAll<HTMLDetailsElement>('details[open][data-review-fix]') || [])]
        .flatMap(item => item.dataset.reviewFix || []),
      focusedFixLesson: focused?.dataset.reviewFixLessonId,
    };
    savedReviewViews.set(draftId, view);
    return view;
  };
  const close = () => { saveView(); onClose(); };
  const review = async (keepMessage = false) => {
    if (dirty || !weekValid) return;
    if (scrollBody.current?.scrollTop && !restoreView.current) {
      restoreView.current = saveView();
    }
    reviewRequest.current?.abort();
    const controller = new AbortController();
    reviewRequest.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 120_000);
    setChecking(true);
    if (!keepMessage) setMessage('');
    setSessionExpired(false);
    setConfirmed(false);
    const checkRosters = canCheckRosters && rosters?.revision !== revision;
    const checkLegacy = canCheckLegacy && legacy?.source_revision !== revision;
    if (checkRosters) {
      setRosters(null);
      setRosterError('');
    }
    if (checkLegacy) {
      setLegacy(null);
      setLegacyError('');
    }
    const failureMessage = (error: unknown) => controller.signal.aborted ? 'Истекло время ожидания. Повторите проверку.' : errorMessage(error);
    const current = () => reviewRequest.current === controller;
    try {
      await Promise.all([
        publicationEnabled ? api<Preview>(`${prefix}/publication/preview`, { method: 'POST', body: JSON.stringify(body), signal: controller.signal })
          .then(result => {
            if (current() && !controller.signal.aborted) {
              setPreview(result);
              setPreviewTarget({ draftId, revision, scope, week });
            }
          }).catch(error => {
            if (current()) {
              setPreview(null);
              setPreviewTarget(null);
              setMessage(`Не удалось проверить публикацию. ${failureMessage(error)}`);
              setSessionExpired((error as ApiError | null)?.status === 401);
            }
          }) : null,
        checkRosters ? requestRosters(rosterScope, controller.signal)
          .then(result => { if (current() && !controller.signal.aborted) setRosters(result); })
          .catch(error => { if (current()) setRosterError(failureMessage(error)); }) : null,
        checkLegacy ? api<NormalizationPreview>(`${legacyPrefix}/normalization-preview`, { method: 'POST', signal: controller.signal })
          .then(result => {
            if (!current() || controller.signal.aborted) return;
            if (result.source_revision === revision && result.source_id === draftId) setLegacy(result);
            else setLegacyError('Версия черновика изменилась. Откройте свежую версию.');
          }).catch(error => { if (current()) setLegacyError(failureMessage(error)); }) : null,
      ]);
    } finally {
      window.clearTimeout(timeout);
      if (current()) {
        setChecking(false);
        setReviewedRevision(revision);
      }
    }
  };
  const autoReview = useEffectEvent(() => {
    if (dirty || historyOnly || published || automaticRevision.current === revision) return;
    automaticRevision.current = revision;
    void review(true);
  });
  useEffect(() => {
    const timer = window.setTimeout(() => autoReview(), 0);
    return () => window.clearTimeout(timer);
  }, [dirty, historyOnly, revision]);
  const saveForReview = async () => {
    setBusy(true);
    try { if (!await onSave()) close(); }
    finally { setBusy(false); }
  };
  const applyRosters = async () => {
    if (dirty || readOnly || busy || !rosters?.summary.safe_count || rosters.revision !== revision) return;
    restoreView.current = saveView();
    setBusy(true);
    setMessage('');
    try {
      const result = await api<{ draft?: unknown }>(`${prefix}/audiences/apply`, {
        method: 'POST',
        body: JSON.stringify({ ...rosterBody(rosterScope), preview_token: rosters.preview_token, reason: 'Подтверждение проверенных составов в конструкторе' }),
      });
      setMessage(`Составы обновлены для ${rosters.summary.safe_count} занятий.`);
      setPreview(null);
      setConfirmed(false);
      onRostersUpdated(result.draft || result);
    } catch (error) {
      setRosterError(errorMessage(error));
      setRosters(null);
      setPreview(null);
      setConfirmed(false);
    } finally {
      setBusy(false);
    }
  };
  const copyNormalized = async () => {
    if (dirty || readOnly || busy || !legacy?.automatic_count || legacy.source_revision !== revision) return;
    setBusy(true);
    try {
      const copy = await api<NormalizationCopy>(`${legacyPrefix}/normalization-copy`, {
        method: 'POST',
        body: JSON.stringify({ expected_revision: revision, expected_source_hash: legacy.source_hash, expected_plan_hash: legacy.plan_hash }),
      });
      await onNormalizedCopy(copy);
    } catch (error) {
      setLegacyError(errorMessage(error));
      setLegacy(null);
    } finally {
      setBusy(false);
    }
  };
  const publish = async()=>{
    if(!activePreview?.can_publish || !confirmed || dirty || checking || busy || readOnly || activePreview.publication_available===false)return;
    setBusy(true);setMessage('');setSessionExpired(false);
    try {
      const result = await api<{published_id:string;scope_key?:string;version_id?:string;week_start?:string;created_at?:string;summary?:Partial<Summary>;runtime_readback:{verified:boolean;block_count:number;assignment_count:number}}>(`${prefix}/publication/publish`,{method:'POST',body:JSON.stringify({...body,preview_token:activePreview.preview_token,confirmed:true})});
      const publishedWhen=result.created_at?new Date(result.created_at).toLocaleString('ru-RU'):'сейчас';
      const where=result.week_start?` · неделя с ${result.week_start}`:scope==='week'?` · неделя с ${week}`:' · школьный шаблон';
      const resultSummary={...emptySummary,...(result.summary||activePreview.summary)};
      const totals=`В Campus доступны ${resultSummary.campus_lessons??resultSummary.ready_lessons} занятий. Требуют уточнения ${resultSummary.admin_only_lessons??resultSummary.deferred_lessons} записей.`;
      setMessage(`Опубликовано${where} (${publishedWhen}). ${totals} ${result.runtime_readback.verified ? 'Проверка после публикации пройдена.' : 'Проверка после публикации ещё не пройдена.'}`);
      setPublished(true);setPreview(null);setConfirmed(false);await onPublished();
    } catch(error){showError(error);setPreview(null);setConfirmed(false);}finally{setBusy(false);}
  };
  const reviewRestore = async(publication:Publication)=>{
    const head = history.heads?.[publication.scope_key];
    if(!head)return;
    setBusy(true);setMessage('');setSessionExpired(false);setRestore(null);setRestoreConfirmed(false);setPreview(null);
    try {
      const result = await api<{preview_token:string;can_restore:boolean;blockers:{message:string}[];diff:Preview['diff']}>('/api/admin/schedule/constructor/publication/restore/preview',{method:'POST',body:JSON.stringify({publication_id:publication.id,expected_head_revision:head.revision})});
      setRestore({publication,expected_head_revision:head.revision,preview:result});
    }catch(error){showError(error);}finally{setBusy(false);}
  };
  const confirmRestore = async()=>{
    if(!restore || !restoreConfirmed)return;
    setBusy(true);setMessage('');setSessionExpired(false);
    try {
      const result = await api<{published_id:string;runtime_readback:{verified:boolean}}>('/api/admin/schedule/constructor/publication/restore',{method:'POST',body:JSON.stringify({publication_id:restore.publication.id,expected_head_revision:restore.expected_head_revision,preview_token:restore.preview.preview_token,confirmed:true})});
      setMessage(`Создана новая восстановленная версия расписания. Чтение: ${result.runtime_readback.verified?'подтверждено':'не подтверждено'}. Для дальнейшей работы откройте конструктор.`);setRestore(null);setRestoreConfirmed(false);await loadHistory();await onPublished();
    }catch(error){showError(error);setRestore(null);}finally{setBusy(false);}
  };
  const openLesson = (id: string, issue?: ReturnType<typeof toDisplayIssue>) => {
    const relatedIds = issue?.targets.filter(target => target.available).map(target => target.id) || [];
    saveView(issue?.key);
    onOpenLesson(id, issue ? { title: issue.descriptions[0].title, relatedIds } : undefined);
  };
  const changeSection = (next: ReviewSection) => {
    if (section === next) return;
    setSection(next);
    setConfirmed(false);
    scrollBody.current?.scrollTo({ top: 0 });
  };

  useEffect(() => {
    const pending = restoreView.current;
    const scroller = scrollBody.current;
    if (!pending || !scroller || checking || !dirty && !activePreview && reviewedRevision !== revision) return;
    const frame = window.requestAnimationFrame(() => {
      if (dirty && !selectedControl.current) return;
      for (const details of scroller.querySelectorAll<HTMLDetailsElement>('details[data-review-fix]')) {
        if (details.dataset.reviewFix && pending.openFixes.includes(details.dataset.reviewFix)) details.open = true;
      }
      scroller.scrollTop = pending.scrollTop;
      const fromFixes = pending.focusedFixLesson ?
        [...scroller.querySelectorAll<HTMLButtonElement>('button[data-review-fix-lesson-id]')]
          .find(button => button.dataset.reviewFixLessonId === pending.focusedFixLesson) : undefined;
      const focus = selectedControl.current || fromFixes;
      focus?.scrollIntoView({ block: 'nearest' });
      focus?.focus({ preventScroll: true });
      // A save can temporarily leave the server preview unavailable while the local list is visible.
      if (!dirty) restoreView.current = undefined;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activePreview, checking, dirty, reviewedRevision, revision]);

  return <div className="constructor-conflict-backdrop">
    <dialog className="constructor-conflict-dialog constructor-publication-panel constructor-review-dialog"
      aria-labelledby="publication-title" ref={node => { if (node && !node.open) node.showModal(); }}
      onCancel={event => { event.preventDefault(); if (!busy) close(); }}>
      <header>
        <div><h2 id="publication-title">{historyOnly ? 'История публикаций' : 'Проверка расписания'}</h2><small>{draftName}</small></div>
        <button type="button" disabled={busy} aria-label={historyOnly ? 'Закрыть историю публикаций' : 'Закрыть проверку'} onClick={close}><X size={20}/></button>
      </header>
      {!historyOnly ? <nav className="constructor-review-nav" aria-label="Разделы проверки">
        <button type="button" aria-pressed={section === 'issues'} disabled={busy} onClick={() => changeSection('issues')}>Проблемы</button>
        {canShowFixes || onOpenGridDecisions ? <button type="button" aria-pressed={section === 'fixes'} disabled={busy} onClick={() => changeSection('fixes')}>Составы и старые записи</button> : null}
        {onOpenComparison ? <button type="button" disabled={busy} onClick={() => { saveView(); onOpenComparison(); }}>Сверка с Google Sheets<ArrowRight size={14} aria-hidden="true" /></button> : null}
        {publicationEnabled ? <button type="button" aria-pressed={section === 'publication'} disabled={busy} onClick={() => changeSection('publication')}>Публикация</button> : null}
      </nav> : null}
      <div className="constructor-review-scroll" ref={scrollBody}>
        {!historyOnly && dirty ? <output className="constructor-review-status">Сохраните правки, чтобы обновить результаты полной проверки.</output> : null}
        {!historyOnly && checking ? <output className="constructor-review-status">Проверяем расписание и составы групп…</output> : null}
        {message ? <output className="constructor-publication-message">{message}</output> : null}
        {sessionExpired ? <section role="alert" className="constructor-publication-session-help">
          <p>Срок входа мог истечь. Перепроверьте вход, чтобы продолжить.</p>
          <button type="button" className="admin-button" disabled={busy} onClick={() => window.location.reload()}>Перепроверить вход</button>
        </section> : null}
        {!historyOnly && section === 'issues' ? <section className="constructor-review-issues" aria-labelledby="constructor-review-issues-title">
          <div className="constructor-review-list-heading">
            <h3 id="constructor-review-issues-title">Что требует внимания</h3>
            {!dirty && !checking && reviewedRevision === revision && !published && weekValid ?
              <button type="button" disabled={controlsBusy} onClick={() => void review()}>Проверить снова</button> : null}
          </div>
          {scope === 'week' && weekValid ? <p className="constructor-publication-source">
            Неделя с {new Date(`${week}T12:00:00`).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })}
          </p> : null}
          {allIssues.length || filter !== 'all' || dayFilter || classFilter || typeFilter || query ? <>
            <div className="constructor-review-tools">
              <label>Серьёзность<select aria-label="Серьёзность" value={filter} onChange={event => setFilter(event.target.value as ConstructorReviewFilter)}>
                <option value="all">Все</option>
                <option value="critical">Блокируют публикацию ({counts.critical})</option>
                <option value="conflict">Конфликты ({counts.conflict})</option>
                <option value="warning">Предупреждения ({counts.warning})</option>
                <option value="info">К сведению ({counts.info})</option>
                <option value="deferred">Отложенные ({counts.deferred})</option>
              </select></label>
              <label>День<select aria-label="День" value={dayFilter} onChange={event => setDayFilter(event.target.value)}>
                <option value="">Все дни</option>
                {dayFilter && !dayOptions.includes(Number(dayFilter)) ? <option value={dayFilter}>{fullDayNames[Number(dayFilter)]}</option> : null}
                {dayOptions.map(value => <option key={value} value={value}>{fullDayNames[value]}</option>)}
              </select></label>
              <label>Класс<select aria-label="Класс" value={classFilter} onChange={event => setClassFilter(event.target.value)}>
                <option value="">Все классы</option>
                {classFilter && !classOptions.includes(classFilter) ? <option value={classFilter}>{classFilter}</option> : null}
                {classOptions.map(value => <option key={value} value={value}>{value}</option>)}
              </select></label>
              <label>Тип проблемы<select aria-label="Тип проблемы" value={typeFilter} onChange={event => setTypeFilter(event.target.value)}>
                <option value="">Все типы</option>
                {Object.entries(issueTypes).filter(([value]) => value === typeFilter || typeOptions.includes(value as IssueType)).map(([value, label]) =>
                  <option key={value} value={value}>{label}</option>)}
              </select></label>
              <label className="constructor-review-search">Поиск по вопросам
                <input type="search" placeholder="Предмет, кабинет или преподаватель" value={query} onChange={event => setQuery(event.target.value)} />
              </label>
            </div>
            <div className="constructor-review-result-line">
              <output aria-live="polite">Показано {visibleIssues.length} из {filter === 'deferred' ? allDeferred.length : allIssues.length}</output>
              {filter !== 'all' || dayFilter || classFilter || typeFilter || query ? <button type="button" onClick={clearFilters}>Сбросить фильтры</button> : null}
            </div>
          </> : null}
          {visibleIssues.length ? <ul className="constructor-review-cards">
            {visibleIssues.map(issue => {
              const expanded = selectedKey === issue.key;
              const first = issue.targets[0];
              const related = issue.targets.length > 1;
              const primaryDiagnostic = issue.diagnostics.find(item => diagnosticText(item).type === typeFilter) || issue.diagnostics[0];
              const primaryCode = primaryDiagnostic?.code;
              const affectedStudents = Math.max(0, ...issue.diagnostics.map(item => item.affected_student_ids?.length || item.affected_student_count || 0));
              const statusText = [...new Set(issue.deferred.map(item => deferredOutcome(item.action, item.exclusion_scope)))];
              const note = primaryCode === 'review_required' || primaryDiagnostic && !issueText[primaryDiagnostic.code]
                ? readableNote(primaryDiagnostic?.message) : issue.diagnostics.length ? '' : readableNote(issue.deferred[0]?.cause_codes?.length ? '' : issue.deferred[0]?.reason);
              const roster = issue.diagnostics.some(item => item.code === 'roster_review')
                ? currentRosters?.lessons.find(lesson => lesson.id === first?.id) : undefined;
              const contextText = issue.diagnostics[0]?.context;
              const issueTitle = issue.descriptions[0].title;
              return <li key={issue.key} className="constructor-review-card" data-severity={issue.severity}
                data-review-issue={issue.diagnostics.map(item => item.code).join(' ') || 'deferred'} data-review-lesson-ids={issue.ids.join(' ')}>
                <div className="constructor-review-card-head">
                  <button type="button" className="constructor-review-card-toggle" aria-expanded={expanded}
                    ref={expanded ? selectedControl : null} onClick={() => setSelectedKey(expanded ? null : issue.key)}>
                    <span><span className="constructor-review-severity">{severityLabels[issue.severity]}</span>
                      <strong>{issueTitle}</strong></span>
                    <ChevronDown size={18} aria-hidden="true" />
                  </button>
                  <div className="constructor-review-card-lede">
                    <p>{related
                      ? [issue.days.map(day => dayNames[day]).filter(Boolean).join(', '),
                        first?.start ? formatLessonTimeRange(first.start, first.end) : '',
                        [...new Set(issue.targets.map(target => target.subject))].join(' / '),
                        issue.classNames.join(', ')].filter(Boolean).join(' · ')
                      : first ? [first.day == null ? '' : dayNames[first.day], formatLessonTimeRange(first.start, first.end),
                        first.subject, first.classes.join(', ')].filter(Boolean).join(' · ')
                      : [issue.days.map(day => dayNames[day]).filter(Boolean).join(', '),
                        contextText && [formatLessonTimeRange(contextText.start, contextText.end, ''), contextText.subject].filter(Boolean).join(' · '),
                        issue.classNames.join(', ')].filter(Boolean).join(' · ') || 'Проверка черновика'}</p>
                    {!expanded && first?.available && !related ?
                      <button type="button" className="constructor-review-jump" disabled={busy}
                        aria-label={`Показать в расписании — ${first.subject}. ${issueTitle}`}
                        onClick={() => openLesson(first.id, issue)}>Показать в расписании<ArrowRight size={15} aria-hidden="true" /></button> : null}
                    {related ? <button type="button" className="constructor-review-jump" aria-expanded={expanded}
                      onClick={() => setSelectedKey(expanded ? null : issue.key)}>
                      {expanded ? 'Скрыть занятия' : issue.targets.length === 2 ? 'Показать оба занятия' : `Показать занятия (${issue.targets.length})`}
                      <ArrowRight size={15} aria-hidden="true" />
                    </button> : null}
                  </div>
                </div>
                {expanded ? <div className="constructor-review-card-detail">
                  {issue.descriptions.map((text, index) => <div className="constructor-review-explanation" key={`${text.title}-${index}`}>
                    {index ? <strong>{text.title}</strong> : null}
                    {text.guidance ? <p>{text.guidance}</p> : null}
                  </div>)}
                  {note && !issue.descriptions.some(item => item.title === note || item.guidance === note) ? <p>{note}</p> : null}
                  {affectedStudents > 0 ? <p>Учеников с пересечением — <b>{affectedStudents}</b>.</p> : null}
                  {roster?.changed && roster.proposed_count !== null ? <p>В черновике — <b>{roster.old_count}</b>, в текущем составе — <b>{roster.proposed_count}</b>.</p> : null}
                  {statusText.map(text => <p className="constructor-review-outcome" key={text}>{text}</p>)}
                  {issue.targets.length ? <ul className="constructor-review-targets" aria-label="Связанные занятия">
                    {issue.targets.map(target => <li key={target.id}>
                      <div>
                        <strong>{target.subject}</strong>
                        <p>{[target.day == null ? '' : fullDayNames[target.day], formatLessonTimeRange(target.start, target.end), target.classes.join(', ')].filter(Boolean).join(' · ')}</p>
                        <p className="constructor-review-target-meta">{[target.teachers.join(', '), target.room, target.groups.join(', ')].filter(Boolean).join(' · ')}</p>
                        {!target.available ? <p>Этого занятия нет в открытом черновике.</p> : null}
                      </div>
                      {target.available ? <button type="button" className="constructor-review-jump" disabled={busy}
                        aria-label={`Показать в расписании — ${target.subject}`} onClick={() => openLesson(target.id, issue)}>
                        Показать в расписании<ArrowRight size={15} aria-hidden="true" />
                      </button> : null}
                    </li>)}
                  </ul> : null}
                  {first?.available && !readOnly && primaryCode && issue.severity !== 'info' && issue.severity !== 'deferred' ?
                    <button type="button" disabled={busy} onClick={() => {
                      saveView(issue.key);
                      if (!onResolveIssue?.(issue.targets.map(target => target.id), primaryCode)) openLesson(first.id, issue);
                    }}>{primaryCode === 'joint_activity_unconfirmed' ? 'Подтвердить решение' :
                      primaryDiagnostic && diagnosticText(primaryDiagnostic).type === 'audience' && primaryCode !== 'student_overlap' ? 'Проверить состав' :
                      related ? 'Разобрать конфликт' : 'Исправить'}</button> : null}
                  {!issue.targets.length && issue.descriptions.some(item => item.type === 'publication') && publicationEnabled ?
                    <button type="button" onClick={() => changeSection('publication')}>Перейти к публикации</button> : null}
                  {issue.diagnostics.length || issue.deferred.some(item => item.reason) ? <details className="constructor-review-technical">
                    <summary>Технические подробности</summary>
                    {issue.diagnostics.map(item => <p key={item.code}><code>{item.code}</code> {item.message}</p>)}
                    {issue.deferred.map(item => item.reason ? <p key={item.lesson_id}>{item.reason}</p> : null)}
                  </details> : null}
                </div> : null}
              </li>;
            })}
          </ul> : !checking && (dirty || reviewedRevision === revision) ? <p className="constructor-review-empty">
            {allIssues.length || filter !== 'all' || dayFilter || classFilter || typeFilter || query ?
              'По выбранным фильтрам вопросов нет.' : !activePreview && publicationEnabled && !dirty ?
              'Результаты полной проверки недоступны. Проверьте ещё раз.' :
              dirty ? 'В несохранённых правках пока нет обнаруженных проблем.' : 'По результатам проверки вопросов нет.'}
          </p> : null}
          {diagnostics.length ? <details className="constructor-review-detail"><summary>Отчёт проверки</summary><button type="button" onClick={() => void copyReport()}>Скопировать отчёт проверки</button></details> : null}
        </section> : null}
        {!historyOnly && section === 'fixes' ? <div className="constructor-review-fixes">
          {onOpenGridDecisions ? <button type="button" disabled={busy} onClick={() => { saveView(); onOpenGridDecisions(); }}>История решений в сетке</button> : null}
          {dirty ? <p>После сохранения можно заново проверить составы и старые записи.</p> : <>
            {canCheckRosters ? <ConstructorRosterReview preview={currentRosters} error={rosterError} busy={controlsBusy}
              readOnly={readOnly} scope={rosterScope} classLabel={classLabel} lessonLabels={lessonLabels}
              onScopeChange={target => void checkRosterScope(target)} onApply={() => void applyRosters()} onOpenLesson={openLesson} /> : null}
            {canCheckLegacy ? <ConstructorLegacyReview preview={currentLegacy} error={legacyError} busy={controlsBusy}
              readOnly={readOnly} lessonLabels={lessonLabels} onCopy={() => void copyNormalized()} onOpenLesson={openLesson} /> : null}
          </>}
        </div> : null}
        {!historyOnly && section === 'publication' && !dirty && !published ? <section aria-label="Проверка публикации" className="constructor-review-results">
          <h3>Проверка перед публикацией</h3>
          {weekStart ? <p>Неделя с <strong>{new Date(`${weekStart}T12:00:00`).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })}</strong></p> :
            <div className="constructor-publication-fields">
              <label>Вид публикации<select aria-label="Вид публикации" value={scope} disabled={controlsBusy} onChange={event => { setScope(event.target.value); resetPublicationPreview(); }}>
                <option value="template">Школьный шаблон</option><option value="week">Неделя из шаблона</option>
              </select></label>
              {scope === 'week' ? <label>Неделя публикации<input type="date" value={week} disabled={controlsBusy}
                onChange={event => { setWeek(event.target.value); resetPublicationPreview(); }} /></label> : null}
            </div>}
          {!weekValid ? <output>Выберите понедельник нужной недели.</output> : null}
          {!activePreview && !checking ? <button type="button" disabled={controlsBusy || !weekValid} onClick={() => void review()}>Проверить изменения</button> : null}
          {activePreview ? <>
            <p className="constructor-publication-source">{sourceLabel(activePreview.source_kind)}</p>
            {activePreview.read_only ? <p className="constructor-publication-readonly">Эта версия доступна для просмотра.</p> : null}
            <div className="constructor-publication-summary" aria-label="Итоги проверки">
              <span><b>{summary.main_campus_cards ?? summary.campus_lessons ?? summary.ready_lessons}</b> в Campus</span>
              <span><b>{conflictLessons.size}</b> с пересечениями</span>
              <span><b>{summary.deferred_lessons}</b> ждут уточнения</span>
              <span><b>{counts.critical}</b> блокирующих ошибок</span>
            </div>
            <details className="constructor-review-detail">
              <summary>Что изменится для школы</summary>
              <p>{summary.source_lessons ?? summary.ready_lessons + summary.deferred_lessons} занятий в расписании. Затронуто учеников — {summary.affected_students ?? 0}.</p>
              <div className="constructor-publication-diff"><span>Добавится {activePreview.diff.added}</span><span>Изменится {activePreview.diff.changed}</span><span>Будет убрано {activePreview.diff.removed}</span></div>
              {activePreview.change_items?.length ? <details><summary>Сравнить с опубликованной версией</summary><div className="constructor-publication-changes">{activePreview.change_items.map(item =>
                <article key={item.block_key}><div><small>Опубликовано</small>{describe(item.before)}</div><div><small>После публикации</small>{describe(item.after)}</div></article>)}</div></details> : null}
            </details>
            {activePreview.publication_available === false || readOnly ? <p role="note">Публикация для этой версии недоступна.</p> :
              activePreview.can_publish ? <label className="constructor-publication-confirm">
                <input type="checkbox" checked={confirmed && previewMatches} disabled={controlsBusy} onChange={event => setConfirmed(event.target.checked)} />
                <span>Подтверждаю публикацию {scope === 'week' ? 'этой недели' : 'шаблона'}{summary.deferred_lessons ? `. Записей до уточнения — ${summary.deferred_lessons}` : ''}.</span>
              </label> : <div className="constructor-review-publication-blocked">
                <p>Перед публикацией нужно исправить блокирующие проблемы.</p>
                <button type="button" onClick={() => { clearFilters(); setFilter('critical'); changeSection('issues'); }}>Показать блокирующие проблемы</button>
              </div>}
          </> : null}
        </section> : null}
        {published ? <nav className="constructor-publication-links" aria-label="После публикации">
          <Link href={publishedHref} onClick={close}>Открыть опубликованное расписание</Link>
          <button type="button" onClick={close}>К оставшимся записям черновика</button>
        </nav> : null}
    {restore ? <section aria-label="Проверка восстановления"><h3>Восстановление отдельной новой версией</h3><p>Версия от {new Date(restore.publication.created_at).toLocaleString('ru-RU')}. Добавить: {restore.preview.diff.added}; изменить: {restore.preview.diff.changed}; удалить: {restore.preview.diff.removed}.</p>{restore.preview.blockers.map((item,index)=><p key={index} role="alert">{item.message}</p>)}<label className="constructor-publication-confirm"><input type="checkbox" checked={restoreConfirmed} onChange={event=>setRestoreConfirmed(event.target.checked)}/>Подтверждаю замену текущей публикации новой восстановленной версией.</label><button disabled={busy||!restore.preview.can_restore||!restoreConfirmed} onClick={()=>void confirmRestore()}>Подтвердить восстановление</button><button disabled={busy} onClick={()=>setRestore(null)}>Отмена</button></section> : null}
    {historyOnly ? <details open><summary>Журнал публикаций этого черновика ({history.items.length})</summary>{historyPending ? <output>Загружаем публикации…</output> : !history.items.length ? <p>У этого черновика ещё нет публикаций.</p> : history.items.map(item=>{const itemDiagnostics=storedDiagnostics(item);const itemDeferred=storedDeferred(item);const recordedSummary=item.summary||item.preview?.summary||item.preview_metadata?.summary||item.metadata?.summary;const itemSummary={...emptySummary,...recordedSummary};return <article key={item.id}><strong>{item.scope_kind==='template'?'Школьный шаблон':item.scope_key.startsWith('week:')?`Неделя с ${item.scope_key.slice(5)}`:'Отдельная неделя'}</strong><p>{new Date(item.created_at).toLocaleString('ru-RU')} · {item.runtime_readback.block_count} блоков · {item.runtime_readback.verified?'чтение подтверждено':'не подтверждено'}{item.restored_from?' · восстановленная версия':''}</p><p className="constructor-publication-history-summary">{recordedSummary ? `${itemSummary.ready_lessons} готово · ${itemSummary.deferred_lessons} отложено · ${itemDiagnostics.length} замечаний` : `Опубликовано ${item.runtime_readback.block_count} блоков; сводные количества этой версии не записаны`}</p><details><summary>Сведения о проверке</summary>{itemDiagnostics.length?<ul>{itemDiagnostics.map((diagnostic,index)=><li key={`${diagnostic.code}-${index}`}><strong>{diagnostic.severity==='critical'?'Критично':diagnostic.severity==='conflict'?'Конфликт':'Подсказка'}</strong> · {diagnostic.message}{readableContext(diagnostic.context,teacherNames,groupNames)?<small>{readableContext(diagnostic.context,teacherNames,groupNames)}</small>:null}</li>)}</ul>:<p>Для этой публикации замечаний не сохранено.</p>}{itemDeferred.length?<ul>{itemDeferred.map((deferred,index)=><li key={`${deferred.lesson_id}-${index}`}>{readableContext(deferred.context,teacherNames,groupNames)||'Занятие из черновика'} · {deferredOutcome(deferred.action)}</li>)}</ul>:null}</details><p className="constructor-publication-version">{item.scope_kind==='template'?'Версия шаблона':'Версия недели'}: <code>{item.published_id}</code></p>{item.scope_kind==='week' ? <small>Шаблон: {item.version_id}</small> : null}<small>Публикация расписания</small>{!readOnly && !dirty && history.heads?.[item.scope_key] ? <button disabled={busy} onClick={()=>void reviewRestore(item)}>Проверить восстановление</button> : null}</article>})}</details> : null}
    </div>
    <footer>
      <button type="button" disabled={busy} onClick={close}>К конструктору</button>
      {!historyOnly && dirty ? <button type="button" className="is-primary" disabled={busy} onClick={() => void saveForReview()}>{busy ? 'Сохраняем…' : 'Сохранить и проверить'}</button> : null}
      {!historyOnly && section === 'publication' && activePreview && !published && activePreview.publication_available !== false && !readOnly ?
        <button type="button" className="is-primary" disabled={controlsBusy || !activePreview.can_publish || !confirmed} onClick={() => void publish()}>{activePreview.can_publish
          ? `${counts.info || counts.warning || counts.conflict || counts.deferred ? 'Опубликовать с замечаниями' : 'Опубликовать'} ${scope === 'week' ? 'неделю' : 'шаблон'}`
          : 'Опубликовать после исправления'}</button> : null}
    </footer>
  </dialog></div>;
}
