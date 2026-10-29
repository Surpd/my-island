'use client';

import {useEffect, useState} from 'react';
import {X} from 'lucide-react';
import Link from 'next/link';

type Api = <T>(path:string, init?:RequestInit)=>Promise<T>;
type SemanticBlock = {weekday:number;start:string;end:string;grade:string;assignments:{activity:string;teachers:string[];groups:string[];metadata?:{room?:string}}[]};
type LessonContext = {day?:string|number;start?:string;end?:string;subject?:string;teachers?:string[];groups?:string[]};
type Diagnostic = {severity:'info'|'conflict'|'critical';code:string;message:string;lesson_id?:string;block_key?:string;context?:LessonContext;action?:string};
type Deferred = {lesson_id:string;reason:string;block_key?:string;action:'excluded'|'preserved_base'|'unknown'|'retained_unresolved';context?:LessonContext};
type Summary = {source_lessons?:number;resolved_lessons?:number;unresolved_lessons?:number;acknowledged_exceptions?:number;campus_lessons?:number;admin_only_lessons?:number;with_remarks_lessons?:number;ready_lessons:number;deferred_lessons:number;deferred_blocks:number;unchanged_lessons:number;replaced_lessons:number;info_count:number;conflict_count:number;critical_count:number};
type Preview = {published_source?:unknown;preview_token:string;can_publish:boolean;publication_available?:boolean;read_only?:boolean;base_version_id?:string|null;head_revision:number;blockers:{code:string;message:string;lesson_id?:string}[];deferred:Deferred[];diagnostics?:Diagnostic[];summary?:Summary;source_kind?:'template_week'|'authored_week'|'template';diff:{added:number;changed:number;removed:number;before_blocks:number;after_blocks:number};change_items?:{block_key:string;operation:string;before:SemanticBlock|null;after:SemanticBlock|null}[]};
type Publication = {id:string;scope_kind:string;scope_key:string;created_at:string;published_id:string;version_id?:string;runtime_readback:{verified:boolean;block_count:number};restored_from?:string;summary?:Partial<Summary>;diagnostics?:Diagnostic[];deferred?:Deferred[];preview?:{diagnostics?:Diagnostic[];summary?:Partial<Summary>;deferred?:Deferred[]};metadata?:{diagnostics?:Diagnostic[];summary?:Partial<Summary>;deferred?:Deferred[]};preview_metadata?:{diagnostics?:Diagnostic[];summary?:Partial<Summary>;deferred?:Deferred[]}};
type History = {items:Publication[];heads?:Record<string,{revision:number}>};
type Filter = 'all'|'remarks'|'conflict'|'critical'|'deferred';
const dayNames=['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
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
  if(kind==='authored_week')return 'Авторская версия недели';
  if(kind==='template')return 'Школьный шаблон';
  return 'Текущая база публикации';
}

function deferredOutcome(action:string) { return action==='preserved_base' ? 'Сохранится прежнее занятие из опубликованной версии.' : action==='retained_unresolved' ? 'Исходная запись сохранится в публикации для административного рассмотрения; неподтверждённое назначение не появится в Campus.' : action==='excluded' ? 'Назначение исключено из Campus этой исторической версии.' : 'Решение сохранено в исходной записи публикации.'; }

function storedDiagnostics(item:Publication) { return item.diagnostics||item.preview?.diagnostics||item.preview_metadata?.diagnostics||item.metadata?.diagnostics||[]; }
function storedDeferred(item:Publication) { return item.deferred||item.preview?.deferred||item.preview_metadata?.deferred||item.metadata?.deferred||[]; }

export function ConstructorPublicationPanel({api,draftId,revision,weekStart,onClose,onPublished,teacherNames,groupNames}: {api:Api; draftId:string; revision:number; weekStart?:string; onClose:()=>void; onPublished:()=>Promise<void>;teacherNames:Record<string,string>;groupNames:Record<string,string>}) {
  const [scope,setScope] = useState(weekStart ? 'week' : 'template');
  const [week,setWeek] = useState(weekStart || '');
  const [preview,setPreview] = useState<Preview|null>(null);
  const [previewTarget,setPreviewTarget] = useState<{draftId:string;revision:number;scope:string;week:string}|null>(null);
  const [history,setHistory] = useState<History>({items:[]});
  const [confirmed,setConfirmed] = useState(false);
  const [busy,setBusy] = useState(false);
  const [message,setMessage] = useState('');
  const [published,setPublished] = useState(false);
  const [filter,setFilter] = useState<Filter>('all');
  const [restore,setRestore] = useState<{publication:Publication; expected_head_revision:number; preview:{preview_token:string;can_restore:boolean;blockers:{message:string}[];diff:Preview['diff']}}|null>(null);
  const [restoreConfirmed,setRestoreConfirmed] = useState(false);
  const prefix = `/api/admin/schedule/constructor/${encodeURIComponent(draftId)}`;
  const loadHistory = () => api<History>(`${prefix}/publications`).then(setHistory);
  useEffect(()=>{let active=true; void api<History>(`${prefix}/publications`).then(value=>{if(active)setHistory(value);}).catch(error=>{if(active)setMessage((error as Error).message);}); return()=>{active=false;};},[api,prefix]);
  const publishedQuery = new URLSearchParams(scope==='week'&&week ? {week_start:week} : {});
  if (typeof window!=='undefined' && new URLSearchParams(window.location.search).get('role')==='admin') publishedQuery.set('role','admin');
  const publishedHref = `/admin/schedule/published${publishedQuery.size ? `?${publishedQuery}` : ''}`;
  const body = {scope_kind:scope, expected_revision:revision, ...(scope==='week' ? {week_start:week} : {})};
  const describe = (block:SemanticBlock|null) => block ? <><strong>{dayNames[block.weekday]} {block.start}–{block.end} · {block.grade} параллель</strong>{block.assignments.map((item,index)=><p key={index}>{item.activity} · {item.teachers.map(id=>teacherNames[id]||'Преподаватель не подтверждён').join(', ')||'без преподавателя'} · {item.groups.map(id=>groupNames[id]||'Группа не подтверждена').join(', ')}{item.metadata?.room ? ` · ${item.metadata.room}` : ''}</p>)}</> : <span>Нет занятия</span>;
  const previewMatches=previewTarget?.draftId===draftId&&previewTarget.revision===revision&&previewTarget.scope===scope&&(scope!=='week'||previewTarget.week===week);
  const activePreview=previewMatches?preview:null;
  const summary = {...emptySummary,...activePreview?.summary};
  const rawDiagnostics = activePreview?.diagnostics||[];
  const diagnostics:Diagnostic[] = [...rawDiagnostics,...(activePreview?.blockers||[]).filter(blocker=>!rawDiagnostics.some(item=>item.severity==='critical'&&item.message===blocker.message)).map(blocker=>({severity:'critical' as const,code:blocker.code,message:blocker.message,lesson_id:blocker.lesson_id,context:undefined}))];
  const counts={info:diagnostics.filter(item=>item.severity==='info').length,conflict:diagnostics.filter(item=>item.severity==='conflict').length,critical:diagnostics.filter(item=>item.severity==='critical').length,deferred:activePreview?.deferred.length||0};
  const visibleDiagnostics=diagnostics.filter(item=>filter==='all'||filter==='remarks'&&item.severity==='info'||filter==='conflict'&&item.severity==='conflict'||filter==='critical'&&item.severity==='critical');
  const review = async()=>{
    setBusy(true);setMessage('');setPreview(null);setPreviewTarget(null);setConfirmed(false);setFilter('all');
    try {const result=await api<Preview>(`${prefix}/publication/preview`,{method:'POST',body:JSON.stringify(body)});setPreview(result);setPreviewTarget({draftId,revision,scope,week});}
    catch(error){setMessage((error as Error).message);}finally{setBusy(false);}
  };
  const publish = async()=>{
    if(!activePreview || !confirmed || activePreview.publication_available===false)return;
    setBusy(true);setMessage('');
    try {
      const result = await api<{published_id:string;scope_key?:string;version_id?:string;week_start?:string;created_at?:string;summary?:Partial<Summary>;runtime_readback:{verified:boolean;block_count:number;assignment_count:number}}>(`${prefix}/publication/publish`,{method:'POST',body:JSON.stringify({...body,preview_token:activePreview.preview_token,confirmed:true})});
      const publishedWhen=result.created_at?new Date(result.created_at).toLocaleString('ru-RU'):'сейчас';
      const where=result.week_start?` · неделя с ${result.week_start}`:scope==='week'?` · неделя с ${week}`:' · школьный шаблон';
      const version=result.published_id;
      const resultSummary={...emptySummary,...(result.summary||activePreview.summary)};
      const totals=resultSummary.source_lessons!==undefined?` Все ${resultSummary.source_lessons} исходных записей сохранены. В Campus доступны ${resultSummary.campus_lessons??resultSummary.ready_lessons}; ${resultSummary.admin_only_lessons??resultSummary.deferred_lessons} ожидают административного рассмотрения.`:` ${resultSummary.ready_lessons} готово, ${resultSummary.deferred_lessons} отложено, ${resultSummary.deferred_blocks} блоков отложено.`;
      setMessage(`Опубликовано${where} (${publishedWhen}). Версия: ${version}.${scope==='week'&&result.version_id ? ` Шаблон: ${result.version_id}.` : ''}${totals} ${result.runtime_readback.verified ? 'Чтение расписания подтверждено' : 'Чтение расписания пока не подтверждено'}: ${result.runtime_readback.block_count} блоков, ${result.runtime_readback.assignment_count} назначений.`);
      setPublished(true);setPreview(null);setConfirmed(false);await loadHistory();await onPublished();
    } catch(error){setMessage((error as Error).message);setPreview(null);setConfirmed(false);}finally{setBusy(false);}
  };
  const reviewRestore = async(publication:Publication)=>{
    const head = history.heads?.[publication.scope_key];
    if(!head)return;
    setBusy(true);setMessage('');setRestore(null);setRestoreConfirmed(false);setPreview(null);
    try {
      const result = await api<{preview_token:string;can_restore:boolean;blockers:{message:string}[];diff:Preview['diff']}>('/api/admin/schedule/constructor/publication/restore/preview',{method:'POST',body:JSON.stringify({publication_id:publication.id,expected_head_revision:head.revision})});
      setRestore({publication,expected_head_revision:head.revision,preview:result});
    }catch(error){setMessage((error as Error).message);}finally{setBusy(false);}
  };
  const confirmRestore = async()=>{
    if(!restore || !restoreConfirmed)return;
    setBusy(true);
    try {
      const result = await api<{published_id:string;runtime_readback:{verified:boolean}}>('/api/admin/schedule/constructor/publication/restore',{method:'POST',body:JSON.stringify({publication_id:restore.publication.id,expected_head_revision:restore.expected_head_revision,preview_token:restore.preview.preview_token,confirmed:true})});
      setMessage(`Создана новая восстановленная версия расписания. Чтение: ${result.runtime_readback.verified?'подтверждено':'не подтверждено'}. Для дальнейшей работы откройте конструктор.`);setRestore(null);setRestoreConfirmed(false);await loadHistory();await onPublished();
    }catch(error){setMessage((error as Error).message);setRestore(null);}finally{setBusy(false);}
  };
  const filterButtons: [Filter,string,number][]=[['all','Все',diagnostics.length+counts.deferred],['remarks','Замечания',counts.info],['conflict','Конфликты',counts.conflict],['critical','Критичные',counts.critical],['deferred','Отложенные',counts.deferred]];
  const filteredDeferred=filter==='all'||filter==='deferred'?activePreview?.deferred||[]:[];
  return <div className="constructor-conflict-backdrop"><dialog className="constructor-conflict-dialog constructor-publication-panel" aria-labelledby="publication-title" ref={node=>{if(node&&!node.open)node.showModal();}} onCancel={event=>{event.preventDefault();if(!busy)onClose();}}>
    <header><div><small>Сохранённая ревизия {revision} · отдельное подтверждение</small><h2 id="publication-title">Публикация расписания</h2></div><button disabled={busy} aria-label="Закрыть публикацию" onClick={onClose}><X size={20}/></button></header>
    <p>Публикация сохраняет полный исходный черновик и все замечания в неизменяемой версии. В Student/Teacher Campus попадают только безопасные подтверждённые назначения; нерешённые записи остаются доступны администратору.</p>
    <div className="constructor-publication-fields"><label>Назначение<select aria-label="Вид публикации" value={scope} disabled={busy || published} onChange={event=>{setScope(event.target.value);setPreview(null);setPreviewTarget(null);setConfirmed(false);setFilter('all');}}><option value="template">Школьный шаблон</option><option value="week">Отдельная неделя</option></select></label>{scope==='week' ? <label>Понедельник<input type="date" aria-label="Неделя публикации" value={week} disabled={busy || published} onChange={event=>{setWeek(event.target.value);setPreview(null);setPreviewTarget(null);setConfirmed(false);setFilter('all');}}/></label> : null}</div>
    <p>{scope==='template' ? 'Новый шаблон не изменит ранее опубликованные независимые недели.' : 'Неделя не изменит школьный шаблон и другие недели.'} Обновления источника не заменяют опубликованную вручную версию автоматически.</p>
    {!published ? <button disabled={busy || scope==='week'&&!week} onClick={()=>void review()}>{busy ? 'Проверяем…' : 'Проверить изменения'}</button> : null}
    {activePreview ? <section aria-label="Проверка публикации"><h3>Проверка перед публикацией</h3><p className="constructor-publication-source">{sourceLabel(activePreview.source_kind)} · база: {activePreview.base_version_id||'последняя опубликованная версия расписания'}</p>
      {activePreview.source_kind==='template_week' ? <p>Это шаблонная неделя: проверьте оперативные изменения отдельно.</p> : null}
      {activePreview.read_only ? <p className="constructor-publication-readonly">Предварительный просмотр: эта версия открыта только для чтения.</p> : null}
      <div className="constructor-publication-summary" aria-label="Итоги проверки">
        <span><b>{summary.source_lessons??summary.ready_lessons+summary.deferred_lessons}</b> всего исходных</span><span><b>{summary.resolved_lessons??summary.ready_lessons}</b> подтверждены</span><span><b>{summary.with_remarks_lessons??counts.info}</b> с замечаниями</span><span><b>{summary.acknowledged_exceptions??0}</b> подтверждённых исключений</span><span><b>{summary.campus_lessons??summary.ready_lessons}</b> доступны в Campus</span><span><b>{summary.admin_only_lessons??summary.deferred_lessons}</b> только для рассмотрения</span><span><b>{counts.info}</b> замечаний</span><span><b>{counts.conflict}</b> конфликтов</span><span><b>{counts.critical}</b> критических ошибок</span><span><b>{summary.deferred_lessons}</b> отложено</span><span><b>{summary.deferred_blocks}</b> блоков отложено</span><span><b>{summary.unchanged_lessons}</b> без изменений</span><span><b>{summary.replaced_lessons}</b> заменено</span>
      </div>
      <div className="constructor-publication-diff"><span>+{activePreview.diff.added} новых</span><span>{activePreview.diff.changed} изменено</span><span>−{activePreview.diff.removed} удалено</span><span>{activePreview.diff.before_blocks} → {activePreview.diff.after_blocks} блоков</span></div>
      {activePreview.change_items?.length ? <details><summary>Изменения в расписании ({activePreview.change_items.length})</summary><div className="constructor-publication-changes">{activePreview.change_items.map(item=><article key={item.block_key}><div><small>Сейчас</small>{describe(item.before)}</div><div><small>После публикации</small>{describe(item.after)}</div></article>)}</div></details> : null}
      <h4>Замечания проверки</h4><div className="constructor-publication-filters" aria-label="Фильтр замечаний">{filterButtons.map(([value,label,count])=><button type="button" key={value} aria-pressed={filter===value} onClick={()=>setFilter(value)}>{label} <span>{count}</span></button>)}</div>
      {visibleDiagnostics.length ? <ul className="constructor-publication-diagnostics">{visibleDiagnostics.map((item,index)=><li className={`is-${item.severity}`} key={`${item.code}-${item.lesson_id||index}`}><strong>{item.severity==='critical'?'Требует исправления':item.severity==='conflict'?'Конфликт':'Подсказка'}</strong><p>{item.message}</p>{readableContext(item.context,teacherNames,groupNames)?<small>{readableContext(item.context,teacherNames,groupNames)}</small>:null}</li>)}</ul> : null}
      {filteredDeferred.length ? <div className="constructor-publication-deferred"><h4>Отложенные занятия</h4><ul>{filteredDeferred.map((item,index)=><li key={`${item.lesson_id}-${index}`}><p>{readableContext(item.context,teacherNames,groupNames)||'Занятие из черновика'}{item.reason?` · ${item.reason}`:''}</p><small>{deferredOutcome(item.action)}</small></li>)}</ul></div> : null}
      {!visibleDiagnostics.length&&!filteredDeferred.length ? <p>В этой категории замечаний нет.</p> : null}
      {activePreview.publication_available===false ? <p role="note">Публикация сейчас недоступна для этой версии.</p> : activePreview.can_publish ? <label className="constructor-publication-confirm"><input type="checkbox" checked={confirmed&&previewMatches} onChange={event=>setConfirmed(event.target.checked)}/>Я проверил изменения и подтверждаю публикацию {scope==='week' ? 'этой недели' : 'шаблона'}{counts.info||counts.conflict||counts.deferred?' с указанными замечаниями':''}{summary.deferred_lessons ? activePreview.published_source?` и сохранение ${summary.deferred_lessons} нерешённых записей в полном опубликованном источнике без неподтверждённых назначений в Campus`:` и исключение ${summary.deferred_lessons} назначений из Campus этой версии` : ''}.</label> : <p role="alert">Публикация заблокирована до разрешения критичных проблем.</p>}
      <nav className="constructor-publication-links" aria-label="Быстрые переходы"><button type="button" onClick={onClose}>Вернуться к черновику</button><button type="button" onClick={()=>setFilter(counts.critical?'critical':counts.conflict?'conflict':'deferred')}>Показать проблемы</button><Link href={publishedHref} onClick={onClose}>Открыть опубликованное расписание</Link></nav>
    </section> : null}
    {message ? <output className="constructor-publication-message">{message}</output> : null}
    {published ? <nav className="constructor-publication-links" aria-label="После публикации"><Link href={publishedHref} onClick={onClose}>Открыть опубликованное расписание</Link><button type="button" onClick={onClose}>К оставшимся записям черновика</button></nav> : null}
    {restore ? <section aria-label="Проверка восстановления"><h3>Восстановление отдельной новой версией</h3><p>Версия от {new Date(restore.publication.created_at).toLocaleString('ru-RU')}. Добавить: {restore.preview.diff.added}; изменить: {restore.preview.diff.changed}; удалить: {restore.preview.diff.removed}.</p>{restore.preview.blockers.map((item,index)=><p key={index} role="alert">{item.message}</p>)}<label className="constructor-publication-confirm"><input type="checkbox" checked={restoreConfirmed} onChange={event=>setRestoreConfirmed(event.target.checked)}/>Подтверждаю замену текущей публикации новой восстановленной версией.</label><button disabled={busy||!restore.preview.can_restore||!restoreConfirmed} onClick={()=>void confirmRestore()}>Подтвердить восстановление</button><button disabled={busy} onClick={()=>setRestore(null)}>Отмена</button></section> : null}
    <details><summary>Журнал публикаций этого черновика ({history.items.length})</summary>{!history.items.length ? <p>У этого черновика ещё нет публикаций.</p> : history.items.map(item=>{const itemDiagnostics=storedDiagnostics(item);const itemDeferred=storedDeferred(item);const recordedSummary=item.summary||item.preview?.summary||item.preview_metadata?.summary||item.metadata?.summary;const itemSummary={...emptySummary,...recordedSummary};return <article key={item.id}><strong>{item.scope_kind==='template'?'Школьный шаблон':item.scope_key.startsWith('week:')?`Неделя с ${item.scope_key.slice(5)}`:'Отдельная неделя'}</strong><p>{new Date(item.created_at).toLocaleString('ru-RU')} · {item.runtime_readback.block_count} блоков · {item.runtime_readback.verified?'чтение подтверждено':'не подтверждено'}{item.restored_from?' · восстановленная версия':''}</p><p className="constructor-publication-history-summary">{recordedSummary ? `${itemSummary.ready_lessons} готово · ${itemSummary.deferred_lessons} отложено · ${itemDiagnostics.length} замечаний` : `Опубликовано ${item.runtime_readback.block_count} блоков; сводные количества этой версии не записаны`}</p><details><summary>Сведения о проверке</summary>{itemDiagnostics.length?<ul>{itemDiagnostics.map((diagnostic,index)=><li key={`${diagnostic.code}-${index}`}><strong>{diagnostic.severity==='critical'?'Критично':diagnostic.severity==='conflict'?'Конфликт':'Подсказка'}</strong> · {diagnostic.message}{readableContext(diagnostic.context,teacherNames,groupNames)?<small>{readableContext(diagnostic.context,teacherNames,groupNames)}</small>:null}</li>)}</ul>:<p>Для этой публикации замечаний не сохранено.</p>}{itemDeferred.length?<ul>{itemDeferred.map((deferred,index)=><li key={`${deferred.lesson_id}-${index}`}>{readableContext(deferred.context,teacherNames,groupNames)||'Занятие из черновика'} · {deferredOutcome(deferred.action)}</li>)}</ul>:null}</details><p className="constructor-publication-version">{item.scope_kind==='template'?'Версия шаблона':'Версия недели'}: <code>{item.published_id}</code></p>{item.scope_kind==='week' ? <small>Шаблон: {item.version_id}</small> : null}<small>Публикация расписания</small>{history.heads?.[item.scope_key] ? <button disabled={busy} onClick={()=>void reviewRestore(item)}>Проверить восстановление</button> : null}</article>})}</details>
    <footer><button disabled={busy} onClick={onClose}>К конструктору</button><button className="is-primary" disabled={busy || !activePreview?.can_publish || activePreview.publication_available===false || !confirmed || !previewMatches} onClick={()=>void publish()}>{counts.info||counts.conflict||counts.deferred?'Опубликовать с замечаниями':'Опубликовать'} {scope==='week'?'неделю':'шаблон'}</button></footer>
  </dialog></div>;
}
