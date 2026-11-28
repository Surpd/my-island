import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ArrowLeft, ArrowUpRight, BookOpen, CalendarDays, ChevronRight, Compass, List, MapPin, MoonStar, NotebookPen, Radio, Sailboat, Settings2, Sun, UserRound, Users, X } from 'lucide-react'
import Interior from './Interior'
import { useCameraPixels, useHarbourHost, usePreferences } from './harbour-state'
import { detailArt } from './details.js'
import { worlds } from './worlds.js'
import './campus.css'

const locations = [
  { id:'schedule', student:'Расписание', teacher:'Расписание', icon:CalendarDays },
  { id:'work', student:'Задания', teacher:'Группы и журналы', short:'Группы', icon:NotebookPen },
  { id:'results', student:'Оценки', teacher:'Курсы Classroom', short:'Курсы', icon:BookOpen },
  { id:'news', student:'Объявления', teacher:'Объявления', icon:Radio },
  { id:'profile', student:'Профиль', teacher:'Профиль', icon:UserRound },
  { id:'homeroom', student:'', teacher:'Мой класс', icon:Users },
]

const cohorts = {
  junior:{ grade:'5–6 классы', classroom:'5Б', subject:'Математика' },
  middle:{ grade:'7–9 классы', classroom:'8Б', subject:'Алгебра' },
  senior:{ grade:'10 класс', classroom:'10А', subject:'Алгебра и начала анализа' },
  graduate:{ grade:'11 класс', classroom:'11А', subject:'Алгебра и начала анализа' },
}
const dataOptions = [
  ['ready','Загружены'],
  ['loading','Загрузка'],
  ['empty','Пустой ответ'],
  ['unpublished','Расписание не опубликовано'],
  ['error','Ошибка'],
]
const home = { kind:'island' }
const getRoute = (role,homeroom) => {
  const route = history.state?.campus
  if (route?.kind === 'room') {
    if (route.id === 'homeroom' && (role !== 'teacher' || !homeroom)) return home
    return locations.some(site => site.id === route.id) ? route : home
  }
  return route?.kind === 'directory' || route?.kind === 'settings' ? route : home
}
const saveRoute = (mode,route) => history[mode]({ ...history.state, campus:route },'')
const edgeTranslation = (point) => point < 20 ? 20 : point > 80 ? 80 : 50

function SceneImage({ world, theme }) {
  const [dayReady,setDayReady] = useState(false)
  const [nightReady,setNightReady] = useState(false)
  return <>
    {/* Both lights use the same coordinates. Keep the day layer behind the night crossfade. */}
    {/* oxlint-disable-next-line next/no-img-element */}
    <img className="scene-image scene-day" data-loaded={dayReady} onLoad={()=>setDayReady(true)} src={world.day} width={world.width} height={world.height || world.width} alt="" fetchPriority="high" draggable="false"/>
    {(theme === 'night' || nightReady) &&
      // The night frame uses the same self-contained asset path.
      // oxlint-disable-next-line next/no-img-element
      <img className="scene-image scene-night" src={world.night} width={world.width} height={world.height || world.width} alt="" data-loaded={nightReady && theme === 'night'} onLoad={()=>setNightReady(true)} draggable="false"/>}
  </>
}

function Preferences({ selected, change, dataState, setDataState, onClose, theme, systemReduce }) {
  const teacher = selected.role === 'teacher'
  const LightingIcon = theme === 'night' ? MoonStar : Sun
  return <>
    <div className="util-head"><h2 id="settings-name">Настройки</h2><button className="icon" aria-label="Закрыть настройки" onClick={onClose}><X size={20}/></button></div>
    <section className="preference-group" aria-labelledby="appearance-name">
      <h3 id="appearance-name"><LightingIcon size={17}/> Вид гавани</h3>
      <label className="field"><span>Освещение</span><select id="lighting" value={selected.lighting} onChange={e=>change('lighting',e.target.value)}>
        <option value="auto">Автоматически</option><option value="day">День</option><option value="night">Ночь</option>
      </select></label>
      <p className="field-note">{selected.lighting === 'auto' ? 'Ночь с 20:00 до 07:00 по местному времени.' : 'Выбранное освещение действует постоянно.'}</p>
      <label className="check">Меньше движения<input type="checkbox" id="reduce-motion" checked={systemReduce || selected.reduceMotion} disabled={systemReduce} onChange={e=>change('reduceMotion',e.target.checked)}/></label>
      {systemReduce && <p className="field-note">Действует настройка движения устройства.</p>}
      <label className="check">Крупнее текст панелей<input type="checkbox" id="large-text" checked={selected.largeText} onChange={e=>change('largeText',e.target.checked)}/></label>
    </section>
    <section className="preference-group" aria-labelledby="account-name">
      <h3 id="account-name"><UserRound size={17}/> Доступ к кампусу</h3>
      <div className="account-record"><strong>{teacher ? 'Мария Лебедева' : 'Александра Волкова'}</strong><span>Демо-аккаунт Telegram связан</span><span>Доступ активен</span></div>
      <label className="field"><span>Роль в демо</span><select id="role" value={selected.role} onChange={e=>change('role',e.target.value)}>
        <option value="student">Ученик</option><option value="teacher">Учитель</option>
      </select></label>
      {!teacher && <label className="field"><span>Возрастной кампус</span><select id="age-group" value={selected.stage} onChange={e=>change('stage',e.target.value)}>
        {Object.entries(cohorts).map(([id,cohort])=><option key={id} value={id}>{cohort.grade}</option>)}
      </select></label>}
      {teacher && <label className="check">Классное руководство<input type="checkbox" id="homeroom" checked={selected.homeroom} onChange={e=>change('homeroom',e.target.checked)}/></label>}
    </section>
    <details className="preference-group demo-options" open>
      <summary>Состояние демо</summary>
      <label className="field" htmlFor="data-state">Данные в локациях<select id="data-state" value={dataState} onChange={e=>setDataState(e.target.value)}>{dataOptions.map(([value,name])=><option key={value} value={value}>{name}</option>)}</select></label>
    </details>
  </>
}

function App() {
  const [selected,change] = usePreferences()
  const { isNight,visible,systemReduce,viewport,compact } = useHarbourHost()
  const { role, stage, homeroom } = selected
  const reduceMotion = systemReduce || selected.reduceMotion
  const theme = selected.lighting === 'auto' ? (isNight ? 'night' : 'day') : selected.lighting
  const [dataState,setDataState] = useState('ready')
  const [route,setRoute] = useState(() => getRoute(role,homeroom))
  const [arrived,setArrived] = useState('')
  const [returning,setReturning] = useState(false)
  const camera = useRef(null)
  const arrival = useRef(null)
  const lens = useRef(null)
  const { pixels:cameraPixels,pixelRatio } = useCameraPixels(camera)
  const room = useRef(null)
  const preferences = useRef(null)
  const directory = useRef(null)
  const detailBack = useRef(null)
  const focusFrom = useRef(null)
  const returnFocus = useRef(false)
  const savedScroll = useRef(0)
  const latest = useRef(route)
  const backing = useRef(false)
  const registerBack = useCallback(fn => { detailBack.current = fn },[])
  const isTeacher = role === 'teacher'
  const worldId = isTeacher ? 'faculty' : stage
  const world = worlds[worldId]
  const cohort = cohorts[stage]
  const sites = locations.filter(site => site.id !== 'homeroom' || (isTeacher && homeroom))
  const active = route.kind === 'room' ? sites.find(site=>site.id === route.id) : null

  useEffect(() => {
    if (history.state?.campus !== latest.current) saveRoute('replaceState',latest.current)
  },[])

  function go(next) {
    if (backing.current) return
    const from = latest.current
    if (from.kind === 'island') {
      focusFrom.current = document.activeElement
      savedScroll.current = window.scrollY
    }
    saveRoute(from.kind === 'island' ? 'pushState' : 'replaceState',next)
    latest.current = next
    setReturning(from.kind === 'room' && next.kind !== 'room')
    setArrived('')
    setRoute(next)
  }

  const leave = useCallback(() => {
    const currentRoute = latest.current
    if (currentRoute.kind === 'island' || backing.current) return
    backing.current = true
    history.back()
  },[])

  const back = useCallback(() => {
    const currentRoute = latest.current
    if (currentRoute.kind === 'room' && detailBack.current) detailBack.current()
    else leave()
  },[leave])

  useEffect(() => {
    const pop = () => {
      const next = getRoute(role,homeroom)
      const was = latest.current
      if (!backing.current && next.kind === 'island' && was.kind === 'room' && detailBack.current) {
        saveRoute('pushState',was)
        detailBack.current()
        return
      }
      backing.current = false
      latest.current = next
      setReturning(was.kind === 'room' && next.kind !== 'room')
      setArrived('')
      setRoute(next)
      if (next.kind === 'island') {
        returnFocus.current = true
        window.scrollTo({top:savedScroll.current,behavior:'instant'})
      }
    }
    window.addEventListener('popstate',pop)
    return () => window.removeEventListener('popstate',pop)
  },[role,homeroom])

  useEffect(() => {
    const host = window.Telegram?.WebApp
    const button = host?.BackButton
    if (route.kind === 'island') {
      button?.hide?.()
      return
    }
    button?.show?.()
    if (button?.onClick) button.onClick(back)
    else host?.onEvent?.('backButtonClicked',back)
    const keydown = e => {
      if (e.key === 'Escape' && !document.querySelector('dialog[open]')) { e.preventDefault(); back() }
    }
    window.addEventListener('keydown',keydown)
    return () => {
      if (button?.offClick) button.offClick(back)
      else host?.offEvent?.('backButtonClicked',back)
      button?.hide?.()
      window.removeEventListener('keydown',keydown)
    }
  },[route.kind,back,visible])

  useEffect(() => {
    if (route.kind !== 'island' || returning || !returnFocus.current) return
    returnFocus.current = false
    const origin = focusFrom.current
    const focusTarget = origin?.isConnected ? origin : document.getElementById('places')
    focusTarget?.focus({preventScroll:true})
  },[route.kind,returning])

  const activeId = active?.id
  const point = world.points[activeId]
  const nativeScale = cameraPixels ? world.width / cameraPixels : 1
  const scale = point && !reduceMotion ? Math.max(1,Math.min(2.05,nativeScale)) : 1
  useLayoutEffect(() => {
    if (!activeId && !returning) return
    const node = camera.current
    let current = true
    const transform = getComputedStyle(node).transform
    const cameraMotion = transform === 'none' ? [] : node.getAnimations?.() || []
    const focusMotion = arrival.current?.getAnimations?.() || []
    const lensMotion = lens.current?.getAnimations?.() || []
    const travel = reduceMotion ? [] : [...cameraMotion,...focusMotion,...lensMotion]
    const complete = () => {
      if (activeId) setArrived(activeId)
      else setReturning(false)
    }
    if (!travel.length) complete()
    else void Promise.allSettled(travel.map(animation=>animation.finished)).then(() => { if (current) complete() })
    return () => { current = false }
  },[activeId,returning,reduceMotion,worldId,scale])

  useLayoutEffect(() => {
    const panels = [[room,!!activeId && arrived === activeId],[directory,route.kind === 'directory'],[preferences,route.kind === 'settings']]
    // Close the outgoing panel before opening its replacement.
    for (const [ref,open] of panels) if (!open && ref.current.open) ref.current.close()
    for (const [ref,open] of panels) {
      if (open && !ref.current.open) {
        ref.current.scrollTop = 0
        ref.current.showModal()
      }
    }
  },[activeId,arrived,route.kind])

  const switchRole = next => {
    change('role',next)
    if (route.kind === 'room') leave()
  }
  const openSite = id => {
    if (latest.current.kind === 'island' || latest.current.kind === 'directory') go({kind:'room',id})
  }
  const openSettings = () => go({kind:'settings'})
  const cancel = e => { e.preventDefault(); back() }
  const offset = (source,target) => Math.max(100 - 100 * scale,Math.min(0,target - source * scale))
  const inspectionPosition = (source,target) =>
    (offset(source,target) + (source * scale) - 50) * cameraPixels / pixelRatio / 100 + 'px'
  const cameraStyle = {
    '--camera-x':point ? offset(point[0],50)+'%' : '0%',
    '--camera-y':point ? offset(point[1],30)+'%' : '0%',
    '--camera-scale':scale,
    '--native-size': world.width / pixelRatio + 'px',
    '--arrival-x':point ? point[0]+'%' : '50%',
    '--arrival-y':point ? point[1]+'%' : '50%',
  }
  const LightingIcon = theme === 'night' ? MoonStar : Sun

  return <div className="campus" data-theme={theme} data-role={role} data-world={worldId} data-motion={reduceMotion ? 'reduce' : 'full'} data-active={visible} data-compact={compact || undefined} data-text={selected.largeText ? 'large' : undefined} data-travel={activeId || undefined} style={viewport}>
    <header className="mast">
      <div className="wordmark"><Sailboat size={21} strokeWidth={1.7}/>My Island</div>
      <button id="settings" className="icon settings-button" aria-label="Настройки гавани" onClick={openSettings}><Settings2 size={20}/></button>
    </header>

    <main className="layout">
      <div className="intro">
        <h1>Северная гавань</h1>
        <p className="persona">{isTeacher ? 'Кампус преподавателей' : cohort.grade}<span aria-hidden="true"/><LightingIcon size={14}/>{theme === 'night' ? 'Ночь' : 'День'}</p>
      </div>

      <section className="map-block" aria-label="Интерактивный остров">
        <div className="stage">
          <div ref={camera} className="camera" style={cameraStyle}>
            <SceneImage key={worldId} world={world} theme={theme}/>
            <div className="harbour-shimmer" aria-hidden="true"/>
            {activeId && <div ref={arrival} className="camera-focus" aria-hidden="true"/>}
            <div className="markers" inert={(!!activeId || returning) || undefined}>
              {sites.map(site => {
                const [x,y] = world.points[site.id]
                return <button key={site.id} className={'marker marker-'+site.id} data-location={site.id} aria-label={site[role]+' — '+world.sites[site.id]} style={{left:x+'%',top:y+'%','--tag-anchor':edgeTranslation(x)+'%'}} onClick={()=>openSite(site.id)}>
                  <span className="marker-tag"><site.icon size={14}/><span>{isTeacher && site.short ? site.short : site[role]}</span></span>
                </button>
              })}
            </div>
          </div>
          {activeId && !arrived && !reduceMotion && detailArt[activeId] &&
            <figure ref={lens} className="arrival-lens" aria-hidden="true" style={{'--lens-x':inspectionPosition(point[0],50),'--lens-y':inspectionPosition(point[1],30)}}>
              {/* The inspection lens uses a separate native-resolution architecture plate. */}
              {/* oxlint-disable-next-line next/no-img-element */}
              <img src={detailArt[activeId]} alt="" width="126" height="126" draggable="false"/>
            </figure>}
          <div className="chart-compass" aria-hidden="true"><Compass size={28} strokeWidth={1.15}/></div>
          {activeId && !arrived && <button className="travel-back" onClick={back}><ArrowLeft size={17}/> К острову</button>}
        </div>
        <div className="map-key">
          <span><MapPin size={16}/> {sites.length} мест на острове</span>
          <button id="places" onClick={()=>go({kind:'directory'})}><List size={17}/> Все места</button>
        </div>
      </section>

      <aside className="daybook" aria-label="Текущий урок">
        <button className="lesson" onClick={()=>openSite('schedule')}>
          <span className="lesson-time"><CalendarDays size={16}/> По расписанию сейчас</span>
          <strong className="lesson-name">{isTeacher ? 'Алгебра' : cohort.subject}<ArrowUpRight size={21}/></strong>
          <span className="lesson-meta"><time>09:55–10:40</time><span>{isTeacher ? 'Группа 8Б' : 'Кабинет 305'}</span></span>
        </button>
      </aside>
    </main>
    <footer>Демо на вымышленных данных</footer>

    <dialog id="room" className="room" ref={room} aria-labelledby="room-name" onCancel={cancel}>
      {active && <><header className="room-head"><button id="back" onClick={back} aria-label="Назад"><ArrowLeft size={20}/></button>
        <span><MapPin size={15}/>{world.sites[active.id]}</span><button className="icon room-close" onClick={leave} aria-label="Вернуться на остров"><X size={19}/></button></header>
        <div className="room-scroll">
          <div className="room-heading">
            <h2 id="room-name">{active.id === 'work' && !isTeacher ? 'Домашние задания' : active[role]}</h2>
            {detailArt[active.id] && <figure className="room-detail" aria-hidden="true">
              {/* The offline prototype bundles each architecture plate as its own local asset. */}
              {/* oxlint-disable-next-line next/no-img-element */}
              <img src={detailArt[active.id]} alt="" width="100" height="64" draggable="false"/>
            </figure>}
          </div>
          <Interior key={active.id} role={role} stage={stage} location={active.id} homeroom={homeroom} onRoleChange={switchRole} dataState={dataState} onRetry={()=>setDataState('ready')} registerBack={registerBack} onSettings={openSettings}/>
        </div></>}
    </dialog>
    <dialog id="directory" className="utility" ref={directory} aria-labelledby="dir-name" onCancel={cancel}>
      <div className="util-head"><h2 id="dir-name">Места острова</h2><button className="icon" aria-label="Закрыть" onClick={leave}><X size={20}/></button></div>
      {sites.map(site=><button className="dir-link" key={site.id} onClick={()=>openSite(site.id)}><site.icon size={19}/><span><strong>{site[role]}</strong><small>{world.sites[site.id]}</small></span><ChevronRight size={16}/></button>)}
    </dialog>
    <dialog id="preferences" className="utility preferences" ref={preferences} aria-labelledby="settings-name" onCancel={cancel}>
      <Preferences selected={selected} change={change} theme={theme} systemReduce={systemReduce} onClose={leave} dataState={dataState} setDataState={setDataState}/>
    </dialog>
  </div>
}

createRoot(document.getElementById('root')).render(<App/>)
