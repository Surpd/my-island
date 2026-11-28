import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ArrowLeft, ArrowUpRight, Bell, BookOpen, CalendarDays, ChevronDown, ChevronRight, Compass, Flag, GraduationCap, Layers, List, MapPin, MoonStar, NotebookPen, Radio, Sailboat, Settings2, UserRound, Users, Waves, X } from 'lucide-react'
import Interior from './Interior'
import './campus.css'

const concepts = {
  tide: { name: 'Северная гавань', icon: Waves, sites: ['Часовая башня','Мастерская','Обсерватория','Маяк','Причал','Оранжерея'],
    student: [[52,22],[23,33],[79,32],[16,58],[78,65]], teacher: [[30,18],[25,39],[75,40],[14,63],[79,61],[77,19]] },
  press: { name: 'Бумажный атлас', icon: BookOpen, sites: ['Школьная звонница','Печатный цех','Читальный зал','Сигнальная башня','Лодочная станция','Полевой кабинет'],
    student: [[26,15],[32,40],[75,40],[18,68],[78,75]], teacher: [[31,18],[29,44],[73,43],[17,67],[76,68],[76,18]] },
  orbit: { name: 'Ночная станция', icon: MoonStar, sites: ['Башня времени','Корпус мастерских','Звёздный зал','Сигнальный маяк','Станция у воды','Оранжерея'],
    student: [[44,15],[25,63],[78,16],[8,27],[81,66]], teacher: [[27,18],[29,42],[74,43],[17,65],[78,66],[77,21]] },
}
const locations = [
  { id:'schedule', student:'Расписание', teacher:'Расписание', icon:CalendarDays },
  { id:'work', student:'Задания', teacher:'Группы и журналы', icon:NotebookPen },
  { id:'results', student:'Оценки', teacher:'Курсы Classroom', icon:BookOpen },
  { id:'news', student:'Объявления', teacher:'Объявления', icon:Radio },
  { id:'profile', student:'Профиль', teacher:'Профиль', icon:UserRound },
  { id:'homeroom', student:'', teacher:'Мой класс', icon:Users },
]
const art = import.meta.glob('./art/*.webp', { eager:true, query:'?url', import:'default' })
const home = { kind:'island' }
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches
const getRoute = () => history.state?.campus || home

function App() {
  const [theme,setTheme] = useState(() => Object.hasOwn(concepts,history.state?.theme) ? history.state.theme : 'tide')
  const [role,setRole] = useState(() => history.state?.role === 'teacher' ? 'teacher' : 'student')
  const [homeroom,setHomeroom] = useState(() => history.state?.homeroom !== false)
  const [dataState,setDataState] = useState('ready')
  const [route,setRoute] = useState(getRoute)
  const [arrived,setArrived] = useState('')
  const camera = useRef(null)
  const stage = useRef(null)
  const room = useRef(null)
  const preferences = useRef(null)
  const directory = useRef(null)
  const detailBack = useRef(null)
  const registerBack = useCallback(fn => { detailBack.current = fn }, [])
  const focusFrom = useRef(null)
  const scrollFrom = useRef(0)
  const latest = useRef(route)
  const backing = useRef(false)
  const pendingRole = useRef(null)
  const concept = concepts[theme]
  const sites = locations.slice(0,role === 'teacher' && homeroom ? 6 : 5)
  const active = route.kind === 'room' ? sites.find(site=>site.id===route.id) : null
  const index = active ? sites.indexOf(active) : -1
  const coords = concept[role]
  const image = art['./art/'+theme+'-'+role+'.webp']
  const isTeacher = role === 'teacher'
  const Icon = concept.icon
  useEffect(()=>{
    if (!backing.current && latest.current === route) history.replaceState({ campus:route, theme, role, homeroom }, '')
  },[route,theme,role,homeroom])

  function go(next) {
    if (backing.current) return
    if (latest.current.kind === 'island') {
      focusFrom.current = document.activeElement
      scrollFrom.current = window.scrollY
      history.pushState({ campus:next, theme, role, homeroom }, '')
    } else {
      history.replaceState({ campus:next, theme, role, homeroom }, '')
    }
    latest.current = next
    setArrived('')
    setRoute(next)
    if (next.kind === 'room' && stage.current) {
      const bounds = stage.current.getBoundingClientRect()
      if (matchMedia('(max-width:780px)').matches || bounds.top < -30 || bounds.top > innerHeight * .5) {
        requestAnimationFrame(()=>{
          if (latest.current === next) stage.current.scrollIntoView({block:'start', behavior:reduced() ? 'instant' : 'smooth'})
        })
      }
    }
  }
  function leave() {
    if (latest.current.kind === 'island' || backing.current) return
    backing.current = true
    history.back()
  }
  function back() {
    if (latest.current.kind === 'room' && detailBack.current) detailBack.current()
    else leave()
  }

  useEffect(()=>{
    const pop = () => {
      const next = getRoute()
      const was = latest.current
      if (!backing.current && next.kind === 'island' && was.kind === 'room' && detailBack.current) {
        history.pushState({ ...history.state, campus:was }, '')
        detailBack.current()
        return
      }
      backing.current = false
      latest.current = next
      setArrived('')
      setRoute(next)
      if (next.kind !== 'island') {
        const saved = history.state
        if (Object.hasOwn(concepts,saved?.theme)) setTheme(saved.theme)
        if (saved?.role) setRole(saved.role)
        if (typeof saved?.homeroom === 'boolean') setHomeroom(saved.homeroom)
      }
      if (pendingRole.current) { setRole(pendingRole.current); pendingRole.current=null }
      if (next.kind === 'island') requestAnimationFrame(()=>{
        focusFrom.current?.focus({preventScroll:true})
        window.scrollTo({top:scrollFrom.current,behavior:reduced() ? 'instant' : 'smooth'})
      })
    }
    addEventListener('popstate',pop)
    return ()=>removeEventListener('popstate',pop)
  },[])
  useEffect(()=>{
    if (route.kind === 'island') return
    const host = window.Telegram?.WebApp?.BackButton
    host?.show(); host?.onClick(back)
    const key = e => {
      if (e.key === 'Escape' && !document.querySelector('dialog[open]')) { e.preventDefault(); back() }
    }
    addEventListener('keydown',key)
    return ()=>{host?.offClick(back);host?.hide();removeEventListener('keydown',key)}
  },[route])
  useLayoutEffect(()=>{
    if (!active) return
    let current = true
    const node = camera.current
    // Reading the computed transform creates the CSS transition before querying its lifetime.
    const skipTravel = getComputedStyle(node).transform === 'none' || reduced()
    const moving = node.getAnimations()
    if (skipTravel || !moving.length) setArrived(active.id)
    else void Promise.allSettled(moving.map(a=>a.finished)).then(()=>{ if(current) setArrived(active.id) })
    return ()=>{current=false}
  },[active,theme,role])
  useLayoutEffect(()=>{
    for (const [ref,open] of [[room,!!active && arrived === active.id],[directory,route.kind==='directory'],[preferences,route.kind==='settings']]) {
      if (open && !ref.current.open) ref.current.showModal()
      if (!open && ref.current.open) ref.current.close()
    }
  },[active,arrived,route])
  function switchRole(next) {
    if (route.kind==='island') setRole(next)
    else {pendingRole.current=next; leave()}
  }

  const scale = active ? ({tide:2.12,press:1.9,orbit:2.22}[theme]) : 1
  const clampOffset = (point,target) => Math.max(100-100*scale,Math.min(0,target-point*scale))
  const transform = active ? 'translate('+clampOffset(coords[index][0],50)+'%, '+clampOffset(coords[index][1],36)+'%) scale('+scale+')' : 'translate(0%, 0%) scale(1)'
  const openSite = id => go({kind:'room',id})
  const cancel = e=>{e.preventDefault();back()}
  const currentLesson = <button className="lesson" onClick={()=>openSite('schedule')}>
    <span className="lesson-time"><CalendarDays size={16}/>Сейчас по расписанию</span>
    <span className="lesson-name">Алгебра <ArrowUpRight size={22}/></span>
    <span className="lesson-meta">09:55–10:40 <i/> {isTeacher?'Группа 8Б':'Кабинет 305'}</span>
  </button>

  return <div className="campus" data-theme={theme} data-role={role} data-travel={active ? 'true' : undefined}>
    <header className="mast"><div className="wordmark"><Sailboat size={30} strokeWidth={1.65}/>My Island</div>
      <div className="switches">
        <label className="picker"><Layers size={16}/><select id="concept" aria-label="Художественное направление" value={theme} onChange={e=>setTheme(e.target.value)}>{Object.entries(concepts).map(([id,c])=><option key={id} value={id}>{c.name}</option>)}</select><ChevronDown size={13}/></label>
        <label className="picker"><GraduationCap size={17}/><select id="role" aria-label="Роль кампуса" value={role} onChange={e=>switchRole(e.target.value)}><option value="student">Ученик</option><option value="teacher">Учитель</option></select><ChevronDown size={13}/></label>
        <button id="settings" className="icon" aria-label="Настройки демо" onClick={()=>go({kind:'settings'})}><Settings2 size={19}/></button>
      </div>
    </header>

    <main className="layout">
      <div className="intro"><div className="dateline"><span>Вторник, 24 ноября</span><span>10:10</span></div>
        <h1>{concept.name}</h1>
        <p className="persona">{isTeacher?'Преподавательский кампус':'Ученический кампус'}</p>
      </div>

      <section className="map-block" aria-label="Интерактивный остров">
        <div className="stage" ref={stage}>
          <div className="camera" ref={camera} style={{transform}}>
            {/* The Vite demo also embeds this artwork in its single-file offline export. */}
            {/* oxlint-disable-next-line next/no-img-element */}
            <img src={image} width="1254" height="1254" alt={'Остров «'+concept.name+'», '+(isTeacher?'кампус преподавателя':'кампус ученика')} fetchPriority="high" draggable="false"/>
          <div className={'markers'+(active?' departing':'')} inert={active ? true : undefined}>
            {sites.map((s,i)=><button key={s.id} className={'marker marker-'+s.id} data-location={s.id} aria-label={[concept.sites[i],s[role],s.id==='work'&&!isTeacher?'срок сдачи сегодня':'',s.id==='news'&&isTeacher?'важное объявление':''].filter(Boolean).join(' — ')} style={{left:coords[i][0]+'%',top:coords[i][1]+'%'}} onClick={()=>openSite(s.id)}>
              <span className="building-area" aria-hidden="true"/>
              <span className="marker-tag"><s.icon size={13}/><span>{s[role]}</span>{s.id === (isTeacher?'news':'work') && <span className="signal" title={isTeacher?'Важное объявление':'Срок сегодня'}/>}</span>
            </button>)}
          </div>
          </div>
          {active && !arrived && <button className="travel-back" onClick={back}><ArrowLeft size={16}/> К острову</button>}
        </div>
        <div className="map-key"><span><Icon size={17}/> {sites.length} мест на карте</span>
          <button id="places" onClick={()=>go({kind:'directory'})}><List size={17}/> Все места</button>
        </div>
      </section>
      <aside className="daybook" aria-label="События дня">{currentLesson}
        <h2>{isTeacher?'В рабочем плане':'Важное сегодня'}</h2>
        <button className="notice" onClick={()=>openSite(isTeacher?'schedule':'work')}><span className="notice-icon"><Flag size={18}/></span><span><strong>{isTeacher?'Окно между занятиями':'Срок сдачи сегодня'}</strong><small>{isTeacher?'10:40–11:50':'Алгебра, до 18:00'}</small></span><ChevronRight size={16}/></button>
        <button className="notice" onClick={()=>openSite(isTeacher?'news':'results')}><span className="notice-icon">{isTeacher?<Bell size={18}/>:<NotebookPen size={18}/>}</span><span><strong>{isTeacher?'Закрытие работ за ноябрь':'Последняя оценённая работа'}</strong><small>{isTeacher?'Объявление для преподавателей':'Алгебра · 16 из 20 баллов'}</small></span><ChevronRight size={16}/></button>
        {!isTeacher && <button className="notice" onClick={()=>openSite('news')}><span className="notice-icon"><Bell size={18}/></span><span><strong>Лабораторная в кабинете 207</strong><small>Физика, начало в 10:55</small></span><ChevronRight size={16}/></button>}
      </aside>
    </main>
    <footer><span><Compass size={16}/> My Island</span><span>Демо на вымышленных данных</span></footer>

    <dialog id="room" className="room" ref={room} aria-labelledby="room-name" onCancel={cancel}>
      {active && <><header className="room-head"><button id="back" onClick={back} aria-label="Назад"><ArrowLeft size={20}/></button>
        <span><MapPin size={14}/>{concept.sites[index]}</span><button className="icon room-close" onClick={leave} aria-label="Вернуться на остров"><X size={19}/></button></header>
        <div className="room-scroll"><h2 id="room-name">{active.id==='work'&&!isTeacher?'Домашние задания':active[role]}</h2>
          <Interior role={role} location={active.id} homeroom={homeroom} onRoleChange={switchRole} dataState={dataState} onRetry={()=>setDataState('ready')} registerBack={registerBack}/>
        </div></>}
    </dialog>
    <dialog id="directory" className="utility" ref={directory} aria-labelledby="dir-name" onCancel={cancel}>
      <div className="util-head"><h2 id="dir-name">Места острова</h2><button className="icon" aria-label="Закрыть" onClick={leave}><X size={19}/></button></div>
      {sites.map((site,i)=><button className="dir-link" key={site.id} onClick={()=>openSite(site.id)}><site.icon size={19}/><span><strong>{site[role]}</strong><small>{concept.sites[i]}</small></span><ChevronRight size={16}/></button>)}
    </dialog>
    <dialog id="preferences" className="utility" ref={preferences} aria-labelledby="settings-name" onCancel={cancel}>
      <div className="util-head"><h2 id="settings-name">Настройки демо</h2><button className="icon" aria-label="Закрыть" onClick={leave}><X size={19}/></button></div>
      <label className="field">Данные в локациях<select id="data-state" value={dataState} onChange={e=>setDataState(e.target.value)}>{[['ready','Загружены'],['loading','Загрузка'],['empty','Пустой ответ'],['unpublished','Расписание не опубликовано'],['error','Ошибка']].map(([v,n])=><option key={v} value={v}>{n}</option>)}</select></label>
      {isTeacher && <label className="check"><input type="checkbox" id="homeroom" checked={homeroom} onChange={e=>setHomeroom(e.target.checked)}/> Назначено классное руководство</label>}
    </dialog>
  </div>
}

createRoot(document.getElementById('root')).render(<App/>)
