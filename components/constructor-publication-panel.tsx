'use client';

import {useEffect, useState} from 'react';
import {X} from 'lucide-react';

type Api = <T>(path:string, init?:RequestInit)=>Promise<T>;
type SemanticBlock = {weekday:number;start:string;end:string;grade:string;assignments:{activity:string;teachers:string[];groups:string[];metadata?:{room?:string}}[]};
type Preview = {preview_token:string; can_publish:boolean; base_version_id:string; head_revision:number; blockers:{code:string; message:string; lesson_id?:string}[]; deferred:{lesson_id:string; reason:string}[]; diff:{added:number; changed:number; removed:number; before_blocks:number; after_blocks:number};change_items?:{block_key:string;operation:string;before:SemanticBlock|null;after:SemanticBlock|null}[]};
type Publication = {id:string; scope_kind:string; scope_key:string; created_at:string; published_id:string; runtime_readback:{verified:boolean; block_count:number}; restored_from?:string};
type History = {items:Publication[]; heads?:Record<string,{revision:number}>};

export function ConstructorPublicationPanel({api,draftId,revision,weekStart,onClose,onPublished,teacherNames,groupNames}: {api:Api; draftId:string; revision:number; weekStart?:string; onClose:()=>void; onPublished:()=>Promise<void>;teacherNames:Record<string,string>;groupNames:Record<string,string>}) {
  const [scope,setScope] = useState(weekStart ? 'week' : 'template');
  const [week,setWeek] = useState(weekStart || '');
  const [preview,setPreview] = useState<Preview|null>(null);
  const [history,setHistory] = useState<History>({items:[]});
  const [confirmed,setConfirmed] = useState(false);
  const [busy,setBusy] = useState(false);
  const [message,setMessage] = useState('');
  const [published,setPublished] = useState(false);
  const [restore,setRestore] = useState<{publication:Publication; expected_head_revision:number; preview:{preview_token:string;can_restore:boolean;blockers:{message:string}[];diff:Preview['diff']}}|null>(null);
  const [restoreConfirmed,setRestoreConfirmed] = useState(false);
  const prefix = `/api/admin/schedule/constructor/${encodeURIComponent(draftId)}`;
  const loadHistory = () => api<History>(`${prefix}/publications`).then(setHistory);
  useEffect(()=>{let active=true; void api<History>(`${prefix}/publications`).then(value=>{if(active)setHistory(value);}).catch(error=>{if(active)setMessage(error.message);}); return()=>{active=false;};},[api,prefix]);
  const body = {scope_kind:scope, expected_revision:revision, ...(scope==='week' ? {week_start:week} : {})};
  const describe = (block:SemanticBlock|null) => block ? <><strong>{['Пн','Вт','Ср','Чт','Пт','Сб','Вс'][block.weekday]} {block.start}–{block.end} · {block.grade} параллель</strong>{block.assignments.map((item,index)=><p key={index}>{item.activity} · {item.teachers.map(id=>teacherNames[id]||id).join(', ')||'без преподавателя'} · {item.groups.map(id=>groupNames[id]||id).join(', ')}{item.metadata?.room ? ` · ${item.metadata.room}` : ''}</p>)}</> : <span>Нет занятия</span>;
  const review = async()=>{
    setBusy(true);setMessage('');setPreview(null);setConfirmed(false);
    try {setPreview(await api<Preview>(`${prefix}/publication/preview`,{method:'POST',body:JSON.stringify(body)}));}
    catch(error){setMessage((error as Error).message);}finally{setBusy(false);}
  };
  const publish = async()=>{
    if(!preview || !confirmed)return;
    setBusy(true);setMessage('');
    try {
      const result = await api<{published_id:string; runtime_readback:{verified:boolean;block_count:number;assignment_count:number}}>(`${prefix}/publication/publish`,{method:'POST',body:JSON.stringify({...body,preview_token:preview.preview_token,confirmed:true})});
      setMessage(`Опубликовано. ${result.runtime_readback.verified ? 'Чтение через canonical подтверждено' : 'Проверка чтения не подтверждена'}: ${result.runtime_readback.block_count} блоков, ${result.runtime_readback.assignment_count} назначений. ID: ${result.published_id}`);
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
      setMessage(`Создана новая восстановленная версия ${result.published_id}. Чтение: ${result.runtime_readback.verified?'подтверждено':'не подтверждено'}. Для дальнейшей работы создайте новый черновик опубликованного шаблона в списке черновиков.`);setRestore(null);setRestoreConfirmed(false);await loadHistory();await onPublished();
    }catch(error){setMessage((error as Error).message);setRestore(null);}finally{setBusy(false);}
  };
  return <div className="constructor-conflict-backdrop"><dialog className="constructor-conflict-dialog constructor-publication-panel" aria-labelledby="publication-title" ref={node=>{if(node&&!node.open)node.showModal();}} onCancel={event=>{event.preventDefault();if(!busy)onClose();}}>
    <header><div><small>Сохранённая ревизия {revision} · отдельное подтверждение</small><h2 id="publication-title">Публикация расписания</h2></div><button disabled={busy} aria-label="Закрыть публикацию" onClick={onClose}><X size={20}/></button></header>
    <p>Черновик остаётся редактируемым. Публикация создаёт отдельную неизменяемую версию, доступную ученикам и преподавателям.</p>
    <div className="constructor-publication-fields"><label>Назначение<select aria-label="Вид публикации" value={scope} disabled={busy || published} onChange={event=>{setScope(event.target.value);setPreview(null);setConfirmed(false);}}><option value="template">Школьный шаблон</option><option value="week">Отдельная неделя</option></select></label>{scope==='week' ? <label>Понедельник<input type="date" aria-label="Неделя публикации" value={week} disabled={busy || published} onChange={event=>{setWeek(event.target.value);setPreview(null);setConfirmed(false);}}/></label> : null}</div>
    <p>{scope==='template' ? 'Новый шаблон не изменит ранее опубликованные независимые недели.' : 'Неделя не изменит школьный шаблон и другие недели.'} Обновления источника не заменяют опубликованную вручную версию автоматически.</p>
    {!published ? <button disabled={busy || scope==='week'&&!week} onClick={()=>void review()}>{busy ? 'Проверяем…' : 'Проверить изменения'}</button> : null}
    {preview ? <section aria-label="Проверка публикации"><h3>Изменения относительно опубликованных данных</h3><div className="constructor-publication-diff"><span>+{preview.diff.added} новых</span><span>{preview.diff.changed} изменено</span><span>−{preview.diff.removed} удалено</span></div><p>Блоков: {preview.diff.before_blocks} → {preview.diff.after_blocks}. База: <code>{preview.base_version_id}</code></p>
      {preview.change_items?.length ? <details open><summary>Что изменится ({preview.change_items.length})</summary><div className="constructor-publication-changes">{preview.change_items.map(item=><article key={item.block_key}><div><small>Сейчас</small>{describe(item.before)}</div><div><small>После публикации</small>{describe(item.after)}</div></article>)}</div></details> : null}
      {preview.blockers.length ? <><h4>Требуют исправления</h4><ul>{preview.blockers.map((item,index)=><li key={index}>{item.message}{item.lesson_id ? ` · ${item.lesson_id}` : ''}</li>)}</ul></> : null}
      {preview.deferred.length ? <><h4>Отложенные изменения</h4><ul>{preview.deferred.map((item,index)=><li key={index}>{item.lesson_id} · {item.reason}</li>)}</ul><p>Исходные назначения для отложенных записей сохраняются.</p></> : null}
      {preview.can_publish ? <label className="constructor-publication-confirm"><input type="checkbox" checked={confirmed} onChange={event=>setConfirmed(event.target.checked)}/>Я проверил изменения и подтверждаю публикацию {scope==='week' ? 'этой недели' : 'шаблона'}.</label> : <p role="alert">Публикация заблокирована до разрешения перечисленных проблем.</p>}
    </section> : null}
    {message ? <output className="constructor-publication-message">{message}</output> : null}
    {restore ? <section aria-label="Проверка восстановления"><h3>Восстановление отдельной новой версией</h3><p>Версия от {new Date(restore.publication.created_at).toLocaleString('ru-RU')}. Добавить: {restore.preview.diff.added}; изменить: {restore.preview.diff.changed}; удалить: {restore.preview.diff.removed}.</p>{restore.preview.blockers.map((item,index)=><p key={index} role="alert">{item.message}</p>)}<label className="constructor-publication-confirm"><input type="checkbox" checked={restoreConfirmed} onChange={event=>setRestoreConfirmed(event.target.checked)}/>Подтверждаю замену текущей публикации новой восстановленной версией.</label><button disabled={busy||!restore.preview.can_restore||!restoreConfirmed} onClick={()=>void confirmRestore()}>Подтвердить восстановление</button><button disabled={busy} onClick={()=>setRestore(null)}>Отмена</button></section> : null}
    <details><summary>Журнал публикаций этого черновика ({history.items.length})</summary>{!history.items.length ? <p>У этого черновика ещё нет публикаций.</p> : history.items.map(item=><article key={item.id}><strong>{item.scope_kind==='template'?'Шаблон':item.scope_key.replace('week:','Неделя ')}</strong><p>{new Date(item.created_at).toLocaleString('ru-RU')} · {item.runtime_readback.block_count} блоков · {item.runtime_readback.verified?'чтение подтверждено':'не подтверждено'}{item.restored_from?' · восстановленная версия':''}</p><small>{item.published_id}</small>{history.heads?.[item.scope_key] ? <button disabled={busy} onClick={()=>void reviewRestore(item)}>Проверить восстановление</button> : null}</article>)}</details>
    <footer><button disabled={busy} onClick={onClose}>К конструктору</button><button className="is-primary" disabled={busy || !preview?.can_publish || !confirmed} onClick={()=>void publish()}>Опубликовать {scope==='week'?'неделю':'шаблон'}</button></footer>
  </dialog></div>;
}
