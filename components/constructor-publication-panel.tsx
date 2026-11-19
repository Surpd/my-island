'use client';

import {useEffect, useEffectEvent, useRef, useState} from 'react';
import {X} from 'lucide-react';
import Link from 'next/link';
import { normalizeRoomDisplay } from './schedule-rooms';
import {
  ConstructorLegacyReview, ConstructorRosterReview,
  type NormalizationCopy, type NormalizationPreview, type RosterPreview, type RosterScope,
} from './constructor-review-fixes';

type Api = <T>(path:string, init?:RequestInit)=>Promise<T>;
type ApiError = Error & {status?:number};
type SemanticBlock = {weekday:number;start:string;end:string;grade:string;assignments:{activity:string;teachers:string[];groups:string[];metadata?:{room?:string}}[]};
type LessonContext = {day?:string|number;start?:string;end?:string;subject?:string;teachers?:string[];groups?:string[]};
type Diagnostic = {severity:'info'|'conflict'|'critical';code:string;message:string;lesson_id?:string;related_lesson_ids?:string[];block_key?:string;context?:LessonContext;action?:string};
type Deferred = {lesson_id:string;reason:string;block_key?:string;exclusion_scope?:'direct'|'cascade';cause_codes?:string[];cause_lesson_ids?:string[];action:'excluded'|'preserved_base'|'unknown'|'retained_unresolved';context?:LessonContext};
type Summary = {source_lessons?:number;resolved_lessons?:number;unresolved_lessons?:number;acknowledged_exceptions?:number;campus_lessons?:number;main_campus_cards?:number;elective_blocks?:number;elective_lanes?:number;clubs_hidden?:number;unresolved_personal_assignments?:number;non_blocking_warnings?:number;admin_only_lessons?:number;with_remarks_lessons?:number;diagnostic_events?:number;conflict_lessons?:number;affected_students?:number;day_mode_issues?:number;direct_exclusions?:number;cascade_exclusions?:number;ready_lessons:number;deferred_lessons:number;deferred_blocks:number;unchanged_lessons:number;replaced_lessons:number;info_count:number;conflict_count:number;critical_count:number};
type Preview = {published_source?:unknown;preview_token:string;can_publish:boolean;publication_available?:boolean;read_only?:boolean;base_version_id?:string|null;head_revision:number;blockers:{code:string;message:string;lesson_id?:string}[];deferred:Deferred[];diagnostics?:Diagnostic[];summary?:Summary;source_kind?:'template_week'|'authored_week'|'template';diff:{added:number;changed:number;removed:number;before_blocks:number;after_blocks:number};change_items?:{block_key:string;operation:string;before:SemanticBlock|null;after:SemanticBlock|null}[]};
type Publication = {id:string;scope_kind:string;scope_key:string;created_at:string;published_id:string;version_id?:string;runtime_readback:{verified:boolean;block_count:number};restored_from?:string;summary?:Partial<Summary>;diagnostics?:Diagnostic[];deferred?:Deferred[];preview?:{diagnostics?:Diagnostic[];summary?:Partial<Summary>;deferred?:Deferred[]};metadata?:{diagnostics?:Diagnostic[];summary?:Partial<Summary>;deferred?:Deferred[]};preview_metadata?:{diagnostics?:Diagnostic[];summary?:Partial<Summary>;deferred?:Deferred[]}};
type History = {items:Publication[];heads?:Record<string,{revision:number}>};
type Filter = 'all'|'remarks'|'conflict'|'critical'|'deferred';
const dayNames=['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
const overlapCodes = new Set(['teacher_overlap', 'room_overlap', 'student_overlap']);
const isScheduleConflict = (issue: Diagnostic) => issue.severity === 'conflict' && overlapCodes.has(issue.code);
const emptySummary:Summary={ready_lessons:0,deferred_lessons:0,deferred_blocks:0,unchanged_lessons:0,replaced_lessons:0,info_count:0,conflict_count:0,critical_count:0};

function readableContext(context?:LessonContext,teacherNames:Record<string,string>={},groupNames:Record<string,string>={}) {
  if(!context)return '';
  const day=typeof context.day==='number'?dayNames[context.day]:context.day;
  const labels=[day,context.start&&context.end?`${context.start}–${context.end}`:context.start,context.subject,
    context.teachers?.length?`Преподаватели: ${context.teachers.map(name=>teacherNames[name]||name).join(', ')}`:'',
    context.groups?.length?`Группы: ${context.groups.map(name=>groupNames[name]||name).join(', ')}`:''].filter(Boolean);
  return labels.join(' · ');
}

function sourceLabel(kind?:Preview['source_kind']) {
  if(kind==='template_week')return 'Неделя рассчитана из школьного шаблона';
  if(kind==='authored_week')return 'Черновик отдельной недели';
  if(kind==='template')return 'Школьный шаблон';
  return 'Текущая база публикации';
}

function deferredOutcome(action:string, scope?:string) { return action==='preserved_base' ? 'Сохранится прежнее занятие из опубликованной версии.' : action==='retained_unresolved' ? 'Запись останется в административном источнике; непроверенное назначение не попадёт в персональное расписание.' : action==='excluded' && scope==='cascade' ? 'Исключено из-за атомарной связи с другой затронутой записью.' : action==='excluded' ? 'Исключено непосредственно по указанной причине.' : 'Решение сохранено в исходной записи публикации.'; }

const reasonTitles:Record<string,string>={
  review_required:'Требуется проверка записи', audience_unresolved:'Не удалось подтвердить аудиторию', stale_group_subject_mismatch:'Устаревшая группа другого предмета',
  student_overlap:'Ученик записан на два занятия одновременно', teacher_overlap:'Преподаватель назначен одновременно на разные занятия', room_overlap:'Кабинет занят одновременно на разные очные занятия',
  joint_activity_unconfirmed:'Возможно совместное занятие', missing_teacher:'Не указан преподаватель', missing_room:'Не указан кабинет', unknown_teacher:'Преподаватель отсутствует или неактивен',
  elective_block_unlinked:'Курс по выбору не сопоставлен с общим блоком',
  elective_block_name_conflict:'В названиях общего блока есть расхождение',
  unsupported_partial_cohort:'Нельзя определить остаток параллели', mixed_day_modes_unsupported:'В занятии смешаны режимы дня', projection_mismatch:'Не удалось подтвердить персональное отображение',
  day_mode_conflict:'Режимы классов одной параллели различаются', explicit_deferred:'Запись отложена администратором', unknown_class:'Класс не найден в справочнике', missing_special_scope:'Не определена аудитория отметки',
};

function storedDiagnostics(item:Publication) { return item.diagnostics||item.preview?.diagnostics||item.preview_metadata?.diagnostics||item.metadata?.diagnostics||[]; }
function storedDeferred(item:Publication) { return item.deferred||item.preview?.deferred||item.preview_metadata?.deferred||item.metadata?.deferred||[]; }

export function ConstructorPublicationPanel({
  api, draftId, revision, draftName, weekStart, dirty, readOnly, publicationEnabled, mode, day, classId, classLabel,
  onClose, onSave, onRostersUpdated, onNormalizedCopy, onPublished, onOpenLesson,
  teacherNames, groupNames, lessonLabels,
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
  onOpenLesson: (id: string) => void;
  teacherNames: Record<string, string>;
  groupNames: Record<string, string>;
  lessonLabels: Record<string, string>;
}) {
  const historyOnly = mode === 'history';
  const [scope,setScope] = useState(weekStart ? 'week' : 'template');
  const [week,setWeek] = useState(weekStart || '');
  const [preview,setPreview] = useState<Preview|null>(null);
  const [previewTarget,setPreviewTarget] = useState<{draftId:string;revision:number;scope:string;week:string}|null>(null);
  const [history,setHistory] = useState<History>({items:[]});
  const [historyPending, setHistoryPending] = useState(historyOnly);
  const [confirmed,setConfirmed] = useState(false);
  const [busy,setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [rosterScope, setRosterScope] = useState<RosterScope>('stale');
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
  const [filter,setFilter] = useState<Filter>('all');
  const [query, setQuery] = useState('');
  const [restore,setRestore] = useState<{publication:Publication; expected_head_revision:number; preview:{preview_token:string;can_restore:boolean;blockers:{message:string}[];diff:Preview['diff']}}|null>(null);
  const [restoreConfirmed,setRestoreConfirmed] = useState(false);
  const prefix = `/api/admin/schedule/constructor/${encodeURIComponent(draftId)}`;
  const legacyPrefix = `/api/admin/schedule/experiments/school/${encodeURIComponent(draftId)}`;
  const canCheckRosters = publicationEnabled && !readOnly;
  const canCheckLegacy = !readOnly && !weekStart;
  const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'Не удалось выполнить запрос.';
  const showError = (error: unknown) => {
    setMessage(errorMessage(error));
    setSessionExpired((error as ApiError).status === 401);
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
  const rawDiagnostics = activePreview?.diagnostics||[];
  const diagnostics:Diagnostic[] = [...rawDiagnostics,...(activePreview?.blockers||[]).filter(blocker=>!rawDiagnostics.some(item=>item.severity==='critical'&&item.message===blocker.message)).map(blocker=>({severity:'critical' as const,code:blocker.code,message:blocker.message,lesson_id:blocker.lesson_id,context:undefined}))];
  const conflicts = diagnostics.filter(isScheduleConflict);
  const conflictLessons = new Set(conflicts.flatMap(issue => [issue.lesson_id, ...(issue.related_lesson_ids || [])]).filter(Boolean));
  const counts = {
    info: diagnostics.filter(issue => issue.severity !== 'critical' && !isScheduleConflict(issue)).length,
    conflict: conflicts.length,
    critical: diagnostics.filter(issue => issue.severity === 'critical').length,
    deferred: activePreview?.deferred.length || 0,
  };
  const normalizedQuery = query.trim().toLocaleLowerCase('ru');
  const matchesQuery = (ids: (string | undefined)[], context: LessonContext | undefined, text: string) =>
    !normalizedQuery || [text, ...ids.map(id => id ? lessonLabels[id] : ''), readableContext(context, teacherNames, groupNames)]
      .join(' ').toLocaleLowerCase('ru').includes(normalizedQuery);
  const visibleDiagnostics = diagnostics.filter(item =>
    (filter === 'all' || filter === 'remarks' && item.severity !== 'critical' && !isScheduleConflict(item) ||
      filter === 'conflict' && isScheduleConflict(item) || filter === 'critical' && item.severity === 'critical') &&
    matchesQuery([item.lesson_id, ...(item.related_lesson_ids || [])], item.context, item.message));
  const groupedDiagnostics = [...visibleDiagnostics.reduce((groups, item) => {
    const key = `${item.severity}:${item.code}`;
    const group = groups.get(key) || { severity: item.severity, code: item.code, items: [] as Diagnostic[] };
    group.items.push(item);
    groups.set(key, group);
    return groups;
  }, new Map<string, { severity: Diagnostic['severity']; code: string; items: Diagnostic[] }>()).values()];
  const copyReport = async () => {
    const byCode = new Map<string, { count: number; ids: Set<string> }>();
    for (const issue of diagnostics) {
      const group = byCode.get(issue.code) || { count: 0, ids: new Set<string>() };
      group.count++;
      for (const id of [issue.lesson_id, ...(issue.related_lesson_ids || [])]) {
        if (id && lessonLabels[id]) group.ids.add(id);
      }
      byCode.set(issue.code, group);
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
  const review = async (keepMessage = false) => {
    if (dirty || scope === 'week' && !week) return;
    reviewRequest.current?.abort();
    const controller = new AbortController();
    reviewRequest.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 120_000);
    setChecking(true);
    if (!keepMessage) setMessage('');
    setSessionExpired(false);
    setPreview(null);
    setPreviewTarget(null);
    setConfirmed(false);
    setFilter('all');
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
              setMessage(`Не удалось проверить публикацию. ${failureMessage(error)}`);
              setSessionExpired((error as ApiError).status === 401);
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
    try { if (!await onSave()) onClose(); }
    finally { setBusy(false); }
  };
  const applyRosters = async () => {
    if (dirty || readOnly || busy || !rosters?.summary.safe_count || rosters.revision !== revision) return;
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
    if(!activePreview || !confirmed || dirty || checking || busy || readOnly || activePreview.publication_available===false)return;
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
  const filterButtons: [Filter,string,number?][]=[['all','Все'],['remarks','Замечания',counts.info],['conflict','Конфликты',counts.conflict],['critical','Блокируют публикацию',counts.critical],['deferred','Отложенные',counts.deferred]];
  const filteredDeferred=filter==='all'||filter==='deferred'?(activePreview?.deferred||[]).filter(item=>matchesQuery([item.lesson_id, ...(item.cause_lesson_ids || [])], item.context, item.reason)):[];
  const deferredGroups=[...filteredDeferred.reduce((groups,item)=>{const key=(item.cause_codes||[]).join(',')||item.reason||'unresolved';const group=groups.get(key)||{code:key,items:[] as Deferred[]};group.items.push(item);groups.set(key,group);return groups;},new Map<string,{code:string;items:Deferred[]}>()).values()];
  const controlsBusy = busy || checking || checkingRosters;
  const currentRosters = rosters?.revision === revision ? rosters : null;
  const currentLegacy = legacy?.source_revision === revision ? legacy : null;
  const showFixes = !historyOnly && !dirty && !published && (canCheckRosters || canCheckLegacy) &&
    (Boolean(currentRosters || currentLegacy || rosterError || legacyError) || reviewedRevision === revision);
  const weekValid = scope !== 'week' || Boolean(week) && new Date(`${week}T12:00:00`).getDay() === 1;
  const resetPublicationPreview = () => { setPreview(null); setPreviewTarget(null); setConfirmed(false); setFilter('all'); setMessage(''); };
  const openLessonButton = (id?: string, related = false) => id && lessonLabels[id] ?
    <button type="button" className="constructor-review-text-button" disabled={busy} onClick={() => onOpenLesson(id)}>
      {related ? 'Открыть связанное занятие' : 'Открыть занятие'}
    </button> : null;

  return <div className="constructor-conflict-backdrop"><dialog className="constructor-conflict-dialog constructor-publication-panel" aria-labelledby="publication-title" ref={node=>{if(node&&!node.open)node.showModal();}} onCancel={event=>{event.preventDefault();if(!busy)onClose();}}>
    <header>
      <div><h2 id="publication-title">{historyOnly ? 'История публикаций' : 'Проверка расписания'}</h2><small>{draftName}</small></div>
      <button type="button" disabled={busy} aria-label={historyOnly ? 'Закрыть историю публикаций' : 'Закрыть проверку'} onClick={onClose}><X size={20}/></button>
    </header>
    <div className="constructor-review-scroll">
    {!historyOnly && dirty ? <output>У вас есть несохранённые правки. Сохраните черновик для проверки.</output> : null}
    {!historyOnly && !dirty && !published ? <>
      {checking ? <output className="constructor-review-progress">Проверяем занятия, составы групп и отличия от опубликованной версии…</output> : null}
      {showFixes ? <details className="constructor-review-fixes">
        <summary>Безопасные исправления{currentRosters || currentLegacy ? ` · ${(currentRosters?.summary.safe_count ?? 0) + (currentLegacy?.automatic_count ?? 0)}` : ''}</summary>
        {canCheckRosters ? <ConstructorRosterReview preview={currentRosters} error={rosterError} busy={controlsBusy} readOnly={readOnly} scope={rosterScope} classLabel={classLabel} onScopeChange={target => void checkRosterScope(target)} onApply={() => void applyRosters()} onOpenLesson={onOpenLesson} /> : null}
        {canCheckLegacy ? <ConstructorLegacyReview preview={currentLegacy} error={legacyError} busy={controlsBusy} readOnly={readOnly} lessonLabels={lessonLabels} onCopy={() => void copyNormalized()} onOpenLesson={onOpenLesson} /> : null}
      </details> : null}
      {publicationEnabled ? <div className="constructor-publication-fields">
        {weekStart ? <p>Неделя с <strong>{new Date(`${weekStart}T12:00:00`).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })}</strong></p> : <>
          <label>Что проверить<select aria-label="Вид публикации" value={scope} disabled={controlsBusy} onChange={event => { setScope(event.target.value); resetPublicationPreview(); }}><option value="template">Школьный шаблон</option><option value="week">Неделя из шаблона</option></select></label>
          {scope === 'week' ? <label>Понедельник<input type="date" aria-label="Неделя публикации" value={week} disabled={controlsBusy} onChange={event => { setWeek(event.target.value); resetPublicationPreview(); }} /></label> : null}
        </>}
      </div> : <p>Публикация в этой среде недоступна.</p>}
      {publicationEnabled && !weekValid && week ? <p role="alert">Выберите понедельник.</p> : null}
      {(!activePreview || legacyError || rosterError) && !checking && reviewedRevision === revision && (publicationEnabled || legacyError || rosterError) ? <button type="button" disabled={controlsBusy || !weekValid} onClick={() => void review()}>Проверить изменения</button> : null}
    </> : null}
    {activePreview && !historyOnly ? <section aria-label="Проверка публикации" className="constructor-review-results">
      <h3>Проверка перед публикацией</h3>
      <p className="constructor-publication-source">{sourceLabel(activePreview.source_kind)}</p>
      {activePreview.read_only ? <p className="constructor-publication-readonly">Эта версия доступна для просмотра.</p> : null}
      <div className="constructor-publication-summary" aria-label="Итоги проверки">
        <span><b>{summary.main_campus_cards ?? summary.campus_lessons ?? summary.ready_lessons}</b> в Campus</span>
        <span><b>{conflictLessons.size}</b> с пересечениями</span>
        <span><b>{summary.deferred_lessons}</b> ждут уточнения</span>
        <span><b>{summary.critical_count ?? counts.critical}</b> блокирующих ошибок</span>
      </div>
      <details className="constructor-review-detail">
        <summary>Что изменится для школы</summary>
        <p>{summary.source_lessons ?? summary.ready_lessons + summary.deferred_lessons} записей в расписании. {summary.elective_blocks ?? 0} общих блоков курсов по выбору и {summary.elective_lanes ?? 0} направлений. Кружки ({summary.clubs_hidden ?? 0}) доступны в конструкторе. Затронуто учеников: {summary.affected_students ?? 0}.</p>
        <div className="constructor-publication-diff"><span>Добавится {activePreview.diff.added}</span><span>Изменится {activePreview.diff.changed}</span><span>Будет убрано {activePreview.diff.removed}</span></div>
        {activePreview.change_items?.length ? <details><summary>Сравнить с опубликованной версией</summary><div className="constructor-publication-changes">{activePreview.change_items?.map(item=><article key={item.block_key}><div><small>Опубликовано</small>{describe(item.before)}</div><div><small>После публикации</small>{describe(item.after)}</div></article>)}</div></details> : null}
      </details>
      <h4>Что требует внимания</h4>
      <div className="constructor-review-filters">
        <div className="constructor-publication-filters" aria-label="Фильтр замечаний">{filterButtons.map(([value,label,count])=><button type="button" key={value} aria-pressed={filter===value} onClick={()=>setFilter(value)}>{label}{count === undefined ? null : <span>{count}</span>}</button>)}</div>
        {diagnostics.length || counts.deferred ? <input type="search" placeholder="Предмет, класс или преподаватель" aria-label="Найти в замечаниях" value={query} onChange={event => setQuery(event.target.value)} /> : null}
      </div>
      {visibleDiagnostics.length ? <div className="constructor-publication-diagnostics">{groupedDiagnostics.map(group => <details key={`${group.severity}:${group.code}`} open={normalizedQuery ? true : undefined}>
        <summary>{group.severity === 'critical' ? 'Критично' : overlapCodes.has(group.code) ? 'Конфликт' : group.severity === 'conflict' ? 'Требует уточнения' : 'Замечание'} · {reasonTitles[group.code] || group.items[0]?.message || 'Проверка записи'} · {group.items.length}</summary>
        <ul>{group.items.map((item, index) => <li className={`is-${item.severity}`} key={`${item.lesson_id || 'schedule'}:${index}`}>
          <p>{item.message}</p>
          <small>{readableContext(item.context, teacherNames, groupNames) || (item.lesson_id ? lessonLabels[item.lesson_id] : '')}</small>
          {openLessonButton(item.lesson_id)}
          {item.related_lesson_ids?.filter(id => id !== item.lesson_id && lessonLabels[id]).map(id => <div className="constructor-review-related" key={id}>
            <small>Связано с занятием «{lessonLabels[id]}»</small>
            {openLessonButton(id, true)}
          </div>)}
        </li>)}</ul>
      </details>)}</div> : null}
      {deferredGroups.length ? <div className="constructor-publication-deferred"><h4>Отложенные занятия</h4>{deferredGroups.map(group => <details key={group.code} open={normalizedQuery ? true : undefined}>
        <summary>{reasonTitles[group.code] || 'Требуется уточнение'} · {group.items.length}</summary>
        <ul>{group.items.map((item, index) => <li key={`${item.lesson_id}:${index}`}>
          <p>{readableContext(item.context, teacherNames, groupNames) || lessonLabels[item.lesson_id] || 'Занятие из исходного расписания'}</p>
          {item.reason ? <small>{item.reason}</small> : null}
          <small>{deferredOutcome(item.action, item.exclusion_scope)}</small>
          {openLessonButton(item.lesson_id)}
        </li>)}</ul>
      </details>)}</div> : null}
      {!visibleDiagnostics.length && !filteredDeferred.length ? <p>{normalizedQuery ? 'По этому запросу ничего не найдено.' : 'В этой категории замечаний нет.'}</p> : null}
      {diagnostics.length ? <details className="constructor-review-detail"><summary>Отчёт проверки</summary><button type="button" onClick={() => void copyReport()}>Скопировать отчёт проверки</button></details> : null}
      {activePreview.publication_available === false || readOnly ? <p role="note">Публикация для этой версии недоступна.</p> : activePreview.can_publish ? <label className="constructor-publication-confirm">
        <input type="checkbox" checked={confirmed && previewMatches} disabled={controlsBusy} onChange={event => setConfirmed(event.target.checked)} />
        <span>Подтверждаю публикацию {scope === 'week' ? 'этой недели' : 'шаблона'}{summary.deferred_lessons ? `. ${summary.deferred_lessons} записей останутся в конструкторе до уточнения` : ''}.</span>
      </label> : <p role="alert">Исправьте критические проблемы выше и повторите проверку.</p>}
    </section> : null}
    {message ? <output className="constructor-publication-message">{message}</output> : null}
    {sessionExpired ? <section role="alert" className="constructor-publication-session-help"><p>Перепроверьте текущий вход. Если admin-сессия ещё действует, страница откроется без нового кода; форма входа появится только если срок сессии действительно истёк.</p><button type="button" className="admin-button" disabled={busy} onClick={()=>window.location.reload()}>Перепроверить вход</button></section> : null}
    {published ? <nav className="constructor-publication-links" aria-label="После публикации"><Link href={publishedHref} onClick={onClose}>Открыть опубликованное расписание</Link><button type="button" onClick={onClose}>К оставшимся записям черновика</button></nav> : null}
    {restore ? <section aria-label="Проверка восстановления"><h3>Восстановление отдельной новой версией</h3><p>Версия от {new Date(restore.publication.created_at).toLocaleString('ru-RU')}. Добавить: {restore.preview.diff.added}; изменить: {restore.preview.diff.changed}; удалить: {restore.preview.diff.removed}.</p>{restore.preview.blockers.map((item,index)=><p key={index} role="alert">{item.message}</p>)}<label className="constructor-publication-confirm"><input type="checkbox" checked={restoreConfirmed} onChange={event=>setRestoreConfirmed(event.target.checked)}/>Подтверждаю замену текущей публикации новой восстановленной версией.</label><button disabled={busy||!restore.preview.can_restore||!restoreConfirmed} onClick={()=>void confirmRestore()}>Подтвердить восстановление</button><button disabled={busy} onClick={()=>setRestore(null)}>Отмена</button></section> : null}
    {historyOnly ? <details open><summary>Журнал публикаций этого черновика ({history.items.length})</summary>{historyPending ? <output>Загружаем публикации…</output> : !history.items.length ? <p>У этого черновика ещё нет публикаций.</p> : history.items.map(item=>{const itemDiagnostics=storedDiagnostics(item);const itemDeferred=storedDeferred(item);const recordedSummary=item.summary||item.preview?.summary||item.preview_metadata?.summary||item.metadata?.summary;const itemSummary={...emptySummary,...recordedSummary};return <article key={item.id}><strong>{item.scope_kind==='template'?'Школьный шаблон':item.scope_key.startsWith('week:')?`Неделя с ${item.scope_key.slice(5)}`:'Отдельная неделя'}</strong><p>{new Date(item.created_at).toLocaleString('ru-RU')} · {item.runtime_readback.block_count} блоков · {item.runtime_readback.verified?'чтение подтверждено':'не подтверждено'}{item.restored_from?' · восстановленная версия':''}</p><p className="constructor-publication-history-summary">{recordedSummary ? `${itemSummary.ready_lessons} готово · ${itemSummary.deferred_lessons} отложено · ${itemDiagnostics.length} замечаний` : `Опубликовано ${item.runtime_readback.block_count} блоков; сводные количества этой версии не записаны`}</p><details><summary>Сведения о проверке</summary>{itemDiagnostics.length?<ul>{itemDiagnostics.map((diagnostic,index)=><li key={`${diagnostic.code}-${index}`}><strong>{diagnostic.severity==='critical'?'Критично':diagnostic.severity==='conflict'?'Конфликт':'Подсказка'}</strong> · {diagnostic.message}{readableContext(diagnostic.context,teacherNames,groupNames)?<small>{readableContext(diagnostic.context,teacherNames,groupNames)}</small>:null}</li>)}</ul>:<p>Для этой публикации замечаний не сохранено.</p>}{itemDeferred.length?<ul>{itemDeferred.map((deferred,index)=><li key={`${deferred.lesson_id}-${index}`}>{readableContext(deferred.context,teacherNames,groupNames)||'Занятие из черновика'} · {deferredOutcome(deferred.action)}</li>)}</ul>:null}</details><p className="constructor-publication-version">{item.scope_kind==='template'?'Версия шаблона':'Версия недели'}: <code>{item.published_id}</code></p>{item.scope_kind==='week' ? <small>Шаблон: {item.version_id}</small> : null}<small>Публикация расписания</small>{!readOnly && !dirty && history.heads?.[item.scope_key] ? <button disabled={busy} onClick={()=>void reviewRestore(item)}>Проверить восстановление</button> : null}</article>})}</details> : null}
    </div>
    <footer>
      <button type="button" disabled={busy} onClick={onClose}>К конструктору</button>
      {!historyOnly && dirty ? <button type="button" className="is-primary" disabled={busy} onClick={() => void saveForReview()}>{busy ? 'Сохраняем…' : 'Сохранить и проверить'}</button> : null}
      {!historyOnly && activePreview && !published && activePreview.publication_available !== false && !readOnly ?
        <button type="button" className="is-primary" disabled={controlsBusy || !activePreview.can_publish || !confirmed} onClick={() => void publish()}>{activePreview.can_publish
          ? `${counts.info || counts.conflict || counts.deferred ? 'Опубликовать с замечаниями' : 'Опубликовать'} ${scope === 'week' ? 'неделю' : 'шаблон'}`
          : 'Опубликовать после исправления'}</button> : null}
    </footer>
  </dialog></div>;
}
