'use client';
import {useState} from 'react';
import {Search, X} from 'lucide-react';
import type {TeacherColorMigrationPreview} from './use-school-teacher-colors';
import type {TeacherColorInput} from './teacher-color-registry';

type Registry = {
  color: (id:string) => string; accent: (id:string) => string; code: (id:string) => string;
  overrides: Record<string,string>; setColor: (id:string, color:string) => void; resetColor: (id:string) => void;
  storageError: string; ready: boolean; mode?: 'local'|'school'; error?: string; notice?: string;
  pending?: Set<string>; initializing?: boolean; seeding?: boolean; unassignedCount?: number;
  initialize?: () => Promise<void>; seedApproved?: () => Promise<void>;
  previewLocalMigration?: () => Promise<TeacherColorMigrationPreview>;
  confirmLocalMigration?: (preview:TeacherColorMigrationPreview) => Promise<void>;
};

export function TeacherColorPanel({teachers, registry, onFilter, onClose}: {teachers: TeacherColorInput[]; registry: Registry; onFilter: (id:string) => void; onClose: () => void}) {
  const [query,setQuery] = useState('');
  const [migration,setMigration] = useState<TeacherColorMigrationPreview | null>(null);
  const [panelError,setPanelError] = useState('');
  const [migrationBusy,setMigrationBusy] = useState(false);
  const filtered = teachers.filter(t=>`${t.name} ${registry.code(t.id)}`.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru')));
  const duplicate = teachers.some((t,i)=>teachers.slice(i+1).some(other=>registry.color(t.id)===registry.color(other.id)));
  return <div className="constructor-conflict-backdrop"><dialog className="constructor-conflict-dialog constructor-color-panel" aria-labelledby="teacher-colors-title" ref={node=>{if(node&&!node.open)node.showModal();}} onCancel={event=>{event.preventDefault();onClose();}}>
    <header><div><small>{registry.mode === 'school' ? 'Общие цвета школы · уроки и расписание не меняются' : 'Локальные настройки · расписание не меняется'}</small><h2 id="teacher-colors-title">Преподаватели и цвета</h2></div><button aria-label="Закрыть цвета преподавателей" onClick={onClose}><X size={20}/></button></header>
    <label className="constructor-color-search"><Search size={17}/><input autoFocus type="search" aria-label="Найти преподавателя по имени или коду" placeholder="Имя или код преподавателя" value={query} onChange={e=>setQuery(e.target.value)}/></label>
    <p>{registry.mode === 'school' ? 'Изменения видны всем администраторам школы после сохранения. Цвет можно сбросить к исходному назначению.' : 'Цвета сохраняются только в этом браузере. Чтобы перенести их в настройки школы, сначала просмотрите список и подтвердите импорт.'} Один цвет во всех днях и видах.</p>
    {registry.mode === 'school' ? <div className="constructor-color-actions">
      <button disabled={!registry.ready || registry.initializing || registry.unassignedCount === 0} onClick={()=>void registry.initialize?.()}>{registry.initializing ? 'Закрепляем…' : `Закрепить новые цвета (${registry.unassignedCount})`}</button>
      <button disabled={!registry.ready || registry.seeding} onClick={()=>void registry.seedApproved?.()}>{registry.seeding ? 'Применяем…' : 'Добавить утверждённые исходные цвета'}</button>
      <button disabled={!registry.ready || migrationBusy} onClick={async()=>{setPanelError('');setMigrationBusy(true);try{if(registry.previewLocalMigration)setMigration(await registry.previewLocalMigration());}catch(error){setPanelError((error as Error).message || 'Не удалось прочитать локальные настройки.');}finally{setMigrationBusy(false);}}}>{migrationBusy ? 'Проверяем…' : 'Перенести локальные настройки'}</button>
    </div> : null}
    {!registry.ready ? <output aria-live="polite">Загружаем цвета…</output> : null}
    {registry.error ? <p role="alert">{registry.error}</p> : null}
    {registry.notice ? <output aria-live="polite">{registry.notice}</output> : null}
    {panelError ? <p role="alert">{panelError}</p> : null}
    {migration ? <section className="constructor-color-migration" aria-label="Предпросмотр переноса цветов">
      <strong>Предпросмотр: {migration.plan.length} записей, проблем: {migration.issues.length}</strong>
      {migration.issues.length ? <ul>{migration.issues.map((issue,index)=><li key={`${issue.teacher_id}-${index}`}>{issue.teacher_id}: {issue.error}</li>)}</ul> : migration.plan.length ? <>
        <p>Будут перенесены только ручные цвета. Автоматические назначения и исходные настройки браузера останутся на месте.</p>
        <table><thead><tr><th>Преподаватель</th><th>Сейчас</th><th>Локальный цвет</th><th>Действие</th></tr></thead><tbody>
          {migration.plan.map(item=>{const teacher=teachers.find(t=>t.id===item.teacher_id);return <tr key={item.teacher_id}>
            <td>{teacher?.name || item.teacher_id}</td>
            <td>{item.current_color || 'нет цвета'}</td><td>{item.color}</td>
            <td>{item.status==='will_add'?'добавить':item.status==='replace_manual'?'заменить ручной цвет':'заменить общий цвет'}</td>
          </tr>;})}
        </tbody></table>
      </> : <p>Нет ручных настроек для переноса.</p>}
      <button disabled={!migration.ready || migration.plan.length===0 || migrationBusy || !registry.confirmLocalMigration} onClick={async()=>{setPanelError('');setMigrationBusy(true);try{await registry.confirmLocalMigration?.(migration);setMigration(null);}catch(error){setPanelError((error as Error).message || 'Не удалось перенести настройки. Обновите предпросмотр.');}finally{setMigrationBusy(false);}}}>{migrationBusy ? 'Переносим…' : 'Подтвердить перенос'}</button>
      <button disabled={migrationBusy} onClick={()=>setMigration(null)}>Отмена</button>
    </section> : null}
    {registry.storageError ? <p role="alert">Настройки доступны в этой вкладке, но браузер не подтвердил сохранение: {registry.storageError}</p> : null}
    {duplicate ? <p className="constructor-color-note">Некоторые оттенки совпадают. Ориентируйтесь также на имя и постоянный код.</p> : null}
    <div className="constructor-color-list">{filtered.map(t=><div key={t.id} className="constructor-color-row">
      <label title={`Изменить цвет: ${t.name}`}><input type="color" aria-label={`Цвет: ${t.name}`} value={registry.color(t.id)} disabled={!registry.ready || registry.pending?.has(t.id)} onChange={e=>registry.setColor(t.id,e.target.value)}/></label>
      <button className="constructor-color-person" onClick={()=>{onFilter(t.id);onClose();}}><strong>{t.name}</strong><small><i style={{background:registry.accent(t.id)}}/>{registry.code(t.id)}{registry.overrides[t.id] ? ' · ручной цвет' : ''}</small></button>
      <button aria-label={`Сбросить цвет: ${t.name}`} disabled={!registry.overrides[t.id] || registry.pending?.has(t.id)} onClick={()=>registry.resetColor(t.id)}>Сбросить</button>
    </div>)}{!filtered.length ? <p>Преподаватели не найдены</p> : null}</div>
    <footer><span>{filtered.length} из {teachers.length} преподавателей</span><button onClick={()=>{onFilter('');onClose();}}>Показать всех</button><button onClick={onClose}>Готово</button></footer>
  </dialog></div>;
}
