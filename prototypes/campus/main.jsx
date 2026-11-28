import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ArrowLeft, ArrowUpRight, BookOpen, CalendarDays, ChevronRight, Maximize2, Minimize2, List, MoonStar, NotebookPen, Radio, Sun, UserRound, Users, X } from 'lucide-react'
import Interior, { getFollowingLessons, getInteriorTitle } from './Interior'
import { useCameraPixels, useHarbourHost, usePreferences } from './harbour-state'
import { IslandMap, maxTravelScale, useIslandScene } from './IslandMap.jsx'
import { buildingGeometry, worlds } from './worlds.js'
import './campus.css'
import './room.css'
import Preview, { cohorts, getPreview } from './Preview.jsx'

const locations = [
  { id:'schedule', student:'Расписание', teacher:'Расписание', icon:CalendarDays },
  { id:'work', student:'Задания', teacher:'Группы и журналы', short:'Группы', icon:NotebookPen },
  { id:'results', student:'Оценки', teacher:'Курсы Classroom', short:'Курсы', icon:BookOpen },
  { id:'news', student:'Объявления', teacher:'Объявления', icon:Radio },
  { id:'profile', student:'Профиль', teacher:'Профиль', icon:UserRound },
  { id:'homeroom', student:'', teacher:'Мой класс', icon:Users },
]

const home = { kind:'island', depth:0 }
const getRoomRoute = route => route.kind === 'room' ? route : route.from?.kind === 'room' ? route.from : null
const roomScope = (id,role,stage,homeroom) => role === 'teacher' ? role + ':' + (id !== 'homeroom' || homeroom) : role + ':' + stage
const getRoute = (role,stage,homeroom) => {
  const route = history.state?.campus
  if (!route?.depth || !['room','directory','settings'].includes(route.kind)) return home
  const section = getRoomRoute(route)
  if (section && section.scope !== roomScope(section.id,role,stage,homeroom)) {
    return route.kind === 'settings' ? { ...route, from:null } : home
  }
  return route
}
const saveRoute = (mode,route) => history[mode]({ ...history.state, campus:route },'')
const longRooms = new Set(['schedule','work','results','homeroom'])
function Preferences({ selected, change, onClose, theme, systemReduce, lightingPending, lightingFailed }) {
  const LightingIcon = theme === 'night' ? MoonStar : Sun
  return <>
    <div className="util-head"><h2 id="settings-name">Настройки</h2><button className="icon" aria-label="Закрыть настройки" onClick={onClose}><X size={20}/></button></div>
    <section className="preference-group" aria-labelledby="appearance-name">
      <h3 id="appearance-name"><LightingIcon size={17} aria-hidden="true"/> Оформление</h3>
      <label className="field"><span>Освещение</span><select id="lighting" value={selected.lighting} onChange={e=>change('lighting',e.target.value)}>
        <option value="auto">Авто</option><option value="day">День</option><option value="night">Ночь</option>
      </select></label>
      <p className="field-note">{selected.lighting === 'auto' ? 'Ночь с 20:00 до 07:00 по местному времени.' : 'Выбранное освещение действует постоянно.'}</p>
      <output className="scene-status" htmlFor="lighting">{lightingFailed ? 'Не удалось загрузить выбранный вид гавани.' : lightingPending ? 'Загружаем карту…' : ''}</output>
      <label className="check">Меньше движения<input type="checkbox" id="reduce-motion" checked={systemReduce || selected.reduceMotion} disabled={systemReduce} onChange={e=>change('reduceMotion',e.target.checked)}/></label>
      {systemReduce && <p className="field-note">Действует настройка движения устройства.</p>}
      <label className="check">Крупный текст<input type="checkbox" id="large-text" checked={selected.largeText} onChange={e=>change('largeText',e.target.checked)}/></label>
    </section>
  </>
}

function App() {
  const [selected,change] = usePreferences()
  const harbour = useHarbourHost()
  const [preview] = useState(getPreview)
  const { isNight,viewport } = harbour
  const requestedTheme = selected.lighting === 'auto' ? (isNight ? 'night' : 'day') : selected.lighting
  if (preview.showForm) return <Preview scenario={preview.scenario} theme={requestedTheme} largeText={selected.largeText} viewport={viewport}/>
  return <Campus preview={preview} selected={selected} change={change} harbour={harbour} requestedTheme={requestedTheme}/>
}

function Campus({ preview, selected, change, harbour, requestedTheme }) {
  const { visible,systemReduce,viewport,compact,host } = harbour
  const { role,stage,homeroom,state } = preview.scenario
  const reduceMotion = systemReduce || selected.reduceMotion
  const [dataState,setDataState] = useState(state)
  const [roomChoices,setRoomChoices] = useState({})
  const [route,setRoute] = useState(() => getRoute(role,stage,homeroom))
  const [arrived,setArrived] = useState('')
  const [returning,setReturning] = useState(false)
  const camera = useRef(null)
  const { pixels:cameraPixels,pixelRatio } = useCameraPixels(camera)
  const room = useRef(null)
  const preferences = useRef(null)
  const directory = useRef(null)
  const focusFrom = useRef(null)
  const returnFocus = useRef(false)
  const savedScroll = useRef(0)
  const latest = useRef(route)
  const backing = useRef(false)
  const roomViews = useRef(new Map())
  const [sheetSizes,setSheetSizes] = useState({})
  const scrollArea = useRef(null)
  const roomHeading = useRef(null)
  const viewSequence = useRef(0)
  const [session] = useState(Date.now)
  const resizeView = useRef(null)
  const isTeacher = role === 'teacher'
  const worldId = isTeacher ? 'faculty' : stage
  const world = worlds[worldId]
  const { scene, pending:lightingPending, failed:lightingFailed } = useIslandScene(worldId,requestedTheme,cameraPixels,reduceMotion)
  const theme = scene?.theme ?? requestedTheme
  const sceneId = scene?.worldId ?? worldId
  const sceneWorld = worlds[sceneId]
  const cohort = cohorts[stage]
  const sites = locations.filter(site => site.id !== 'homeroom' || (isTeacher && homeroom))
  const currentRoom = getRoomRoute(route)
  const active = sites.find(site => site.id === currentRoom?.id && currentRoom.scope === roomScope(site.id,role,stage,homeroom))
  const expanded = active && (sheetSizes[currentRoom.rootKey] ?? longRooms.has(active.id))
  const choiceKey = active && worldId + ':' + active.id
  const setChoice = (name,value) => setRoomChoices(previous => ({
    ...previous, [choiceKey]:{ ...previous[choiceKey], [name]:value },
  }))
  const roomVisible = route.kind === 'room' && !!active && arrived === active.id

  const captureRoomView = useCallback((from) => {
    if (from.kind !== 'room' || !room.current?.open || !scrollArea.current) return
    const activeElement = document.activeElement
    const buttons = [...scrollArea.current.querySelectorAll('button, a[href], input, select')]
    roomViews.current.set(from.key, {
      scrollTop:scrollArea.current.scrollTop,
      focused:room.current.contains(activeElement) ? activeElement : null,
      index:buttons.indexOf(activeElement),
    })
  },[])

  const showRoute = useCallback((next,from) => {
    const oldRoom = getRoomRoute(from)
    const nextRoom = getRoomRoute(next)
    if (oldRoom?.rootKey !== nextRoom?.rootKey) {
      setReturning(Boolean(oldRoom && !nextRoom))
      setArrived('')
    }
    latest.current = next
    setRoute(next)
  },[])

  useEffect(() => {
    if (history.state?.campus !== latest.current) saveRoute('replaceState',latest.current)
    const previous = history.scrollRestoration
    history.scrollRestoration = 'manual'
    return () => { history.scrollRestoration = previous }
  },[])

  function go(destination) {
    if (backing.current) return
    const from = latest.current
    if (from.kind === 'island') {
      focusFrom.current = document.activeElement
      savedScroll.current = window.scrollY
      roomViews.current.clear()
      setSheetSizes({})
    }
    captureRoomView(from)
    // Choosing a building from the directory consumes that entry. A room root returns to the island.
    const replace = from.kind === 'directory' && destination.kind === 'room'
    const depth = (from.depth ?? 0) + (replace ? 0 : 1)
    const key = session + ':' + ++viewSequence.current
    const next = { ...destination, key, depth }
    if (next.kind === 'room') {
      next.rootKey ??= key
      next.scope = roomScope(next.id,role,stage,homeroom)
    }
    saveRoute(replace ? 'replaceState' : 'pushState',next)
    showRoute(next,from)
  }

  const travelBack = useCallback((toIsland = false) => {
    const route = latest.current
    if (!route.depth || backing.current) return
    backing.current = true
    history.go(toIsland ? -route.depth : -1)
  },[])
  const back = useCallback(() => travelBack(),[travelBack])
  const leave = useCallback(() => travelBack(true),[travelBack])

  useEffect(() => {
    const pop = () => {
      const next = getRoute(role,stage,homeroom)
      const staleDepth = next === home && history.state?.campus?.depth
      if (staleDepth) {
        backing.current = true
        history.go(-staleDepth)
        return
      }
      const was = latest.current
      captureRoomView(was)
      backing.current = false
      showRoute(next,was)
      if (next.kind === 'island') {
        returnFocus.current = true
        window.scrollTo({top:savedScroll.current,behavior:'instant'})
      }
    }
    window.addEventListener('popstate',pop)
    return () => window.removeEventListener('popstate',pop)
  },[role,stage,homeroom,captureRoomView,showRoute])

  useEffect(() => {
    const button = host?.isVersionAtLeast?.('6.1') === false ? null : host?.BackButton
    if (route.kind === 'island') {
      button?.hide?.()
      return
    }
    button?.show?.()
    if (button?.onClick) button.onClick(back)
    else if (button) host?.onEvent?.('backButtonClicked',back)
    const keydown = e => {
      if (e.key === 'Escape' && !document.querySelector('dialog[open]')) { e.preventDefault(); back() }
    }
    window.addEventListener('keydown',keydown)
    return () => {
      if (button?.offClick) button.offClick(back)
      else if (button) host?.offEvent?.('backButtonClicked',back)
      button?.hide?.()
      window.removeEventListener('keydown',keydown)
    }
  },[route.kind,back,host,visible])

  useEffect(() => {
    if (route.kind !== 'island' || returning || !returnFocus.current) return
    returnFocus.current = false
    const origin = focusFrom.current
    const focusTarget = origin?.isConnected ? origin : document.getElementById('places')
    focusTarget?.focus({preventScroll:true})
  },[route.kind,returning])

  const activeId = active?.id
  const silhouette = scene?.worldId === worldId ? buildingGeometry[worldId][activeId]?.[0] : null
  const vertices = silhouette?.split(' ').map(vertex => vertex.split(',').map(Number))
  const point = vertices && [0,1].map(axis => {
    const values = vertices.map(vertex => vertex[axis])
    return (Math.min(...values) + Math.max(...values)) / 2
  })
  // Registered vector details keep the selected architecture legible at the upper limit.
  const detailWidth = Math.max(scene?.image.naturalWidth || 0, world.width * 1.3)
  const detailScale = cameraPixels ? detailWidth / cameraPixels : maxTravelScale
  const scale = point && !reduceMotion ? Math.min(maxTravelScale,Math.max(1.3,detailScale)) : 1
  useLayoutEffect(() => {
    if (!activeId && !returning) return
    const node = camera.current
    let current = true
    // Flush the new transform before inspecting the CSS transition.
    void getComputedStyle(node).transform
    const travel = reduceMotion ? [] : node.getAnimations?.() || []
    const complete = () => {
      if (activeId) setArrived(activeId)
      else setReturning(false)
    }
    if (!travel.length) complete()
    else void Promise.allSettled(travel.map(animation=>animation.finished)).then(() => { if (current) complete() })
    return () => { current = false }
  },[activeId,returning,reduceMotion,worldId,scale])

  useLayoutEffect(() => {
    const panels = [[room,roomVisible],[directory,route.kind === 'directory'],[preferences,route.kind === 'settings']]
    // Close the outgoing panel before opening its replacement.
    for (const [ref,open] of panels) if (!open && ref.current.open) ref.current.close()
    for (const [ref,open] of panels) {
      if (open && !ref.current.open) {
        if (ref !== room) ref.current.scrollTop = 0
        ref.current.showModal()
      }
    }
  },[roomVisible,route.kind])

  useLayoutEffect(() => {
    if (!roomVisible) return
    const view = roomViews.current.get(route.key)
    const scroll = scrollArea.current
    scroll.scrollTop = view?.scrollTop ?? 0
    const storedFocus = view?.focused
    const contentFocus = view?.index >= 0
      ? scroll.querySelectorAll('button, a[href], input, select')[view.index]
      : null
    const target = storedFocus?.isConnected && room.current.contains(storedFocus) ? storedFocus : contentFocus ?? roomHeading.current
    target?.focus({preventScroll:true})
  },[roomVisible,route.key])

  useLayoutEffect(() => {
    const resize = resizeView.current
    if (!roomVisible || resize?.key !== route.key || resize.appliedTop != null) return
    scrollArea.current.scrollTop = resize.desiredTop
    resize.appliedTop = scrollArea.current.scrollTop
  },[expanded,roomVisible,route.key])

  const toggleSheet = () => {
    const previous = resizeView.current
    const scrollTop = scrollArea.current.scrollTop
    const desiredTop = previous?.key === route.key && previous.appliedTop === scrollTop
      ? previous.desiredTop : scrollTop
    resizeView.current = { key:route.key, desiredTop, appliedTop:null }
    setSheetSizes(previous => ({ ...previous, [currentRoom.rootKey]:!expanded }))
  }
  const openSite = id => {
    if (latest.current.kind === 'island' || latest.current.kind === 'directory') go({kind:'room',id})
  }
  const openDetail = (detail) => {
    if (latest.current.key === currentRoom.key) go({kind:'room',id:active.id,rootKey:currentRoom.rootKey,detail})
  }
  const openSettings = event => {
    event.currentTarget?.focus({preventScroll:true})
    const from = latest.current
    if (from.kind === 'room' && from.id === 'profile') go({kind:'settings',from})
  }
  const cancel = e => { e.preventDefault(); back() }
  const offset = (source,target) => Math.max(100 - 100 * scale,Math.min(0,target - source * scale))
  const cameraStyle = {
    '--camera-x':point ? offset(point[0],50)+'%' : '0%',
    '--camera-y':point ? offset(point[1],37)+'%' : '0%',
    '--camera-scale':scale,
    '--native-size': sceneWorld.width / pixelRatio + 'px',
  }
  return <div className="campus" data-theme={theme} data-lighting-pending={lightingPending || undefined} data-lighting-error={lightingFailed || undefined} data-role={role} data-world={worldId} data-motion={reduceMotion ? 'reduce' : 'full'} data-active={visible} data-compact={compact || undefined} data-text={selected.largeText ? 'large' : undefined} data-travel={activeId || undefined} style={viewport}>
    <main className="layout">
      <section className="map-block" aria-label="Интерактивный остров">
        <div className="stage">
          {!scene && <output className="map-status">{lightingFailed ? 'Карта не загрузилась. Разделы доступны в списке «Все места».' : 'Загружаем остров…'}</output>}
          <IslandMap scene={scene} camera={camera} cameraStyle={cameraStyle} world={sceneWorld} worldId={sceneId}
            activeId={point && !reduceMotion ? activeId : undefined} disabled={!scene || sceneId !== worldId || !!activeId || returning}
            sites={sites.filter(site => sceneWorld.points[site.id])} role={role} onOpen={openSite}/>
          {activeId && !arrived && <button className="travel-back" onClick={back}><ArrowLeft size={17}/> К острову</button>}
        </div>
      </section>

      <aside className="daybook" aria-label="Учебный день">
        <div className="daybook-head"><h1>Вторник, <span>24 ноября</span></h1>
          <button id="places" onClick={event => { event.currentTarget.focus({preventScroll:true}); go({kind:'directory'}) }}><List size={17} aria-hidden="true"/> Все места</button>
        </div>
        <button className="lesson" onClick={event => { event.currentTarget.focus({preventScroll:true}); openSite('schedule') }}>
          <span className="lesson-time">Сейчас по расписанию</span>
          <strong className="lesson-name">{isTeacher ? 'Алгебра' : cohort.subject}</strong>
          <span className="lesson-meta"><time>09:55–10:40</time><span>{isTeacher ? 'Группа 8Б' : 'Кабинет 305'}</span></span>
          <ArrowUpRight className="lesson-arrow" size={24} aria-hidden="true"/>
        </button>
        <section className="following" aria-labelledby="later-heading">
          <h2 id="later-heading">Дальше сегодня</h2>
          <ol>{getFollowingLessons(role,stage).map(({ time, subject, place }) =>
            <li key={time}><time>{time}</time><strong>{subject}</strong><span>{place}</span></li>
          )}</ol>
        </section>
      </aside>
    </main>

    <dialog id="room" className="room" ref={room} aria-labelledby="room-name" data-expanded={Boolean(expanded)} onCancel={cancel}>
      {active && <><header className="room-head">
        <button id="back" onClick={back} aria-label="Назад" title="Назад"><ArrowLeft size={20}/></button>
        <h2 id="room-name" ref={roomHeading} tabIndex={-1}>{getInteriorTitle(currentRoom.detail) ?? (active.id === 'work' && !isTeacher ? 'Домашние задания' : active[role])}</h2>
        <button className="sheet-toggle" onClick={toggleSheet} aria-controls="room-content" aria-expanded={Boolean(expanded)} aria-label={expanded ? 'Свернуть раздел' : 'Развернуть раздел'} title={expanded ? 'Свернуть раздел' : 'Развернуть раздел'}>{expanded ? <Minimize2 size={16}/> : <Maximize2 size={16}/>}<span aria-hidden="true">{expanded ? 'Свернуть' : 'Развернуть'}</span></button>
        <button className="icon room-close" onClick={leave} aria-label="Вернуться на остров" title="Вернуться на остров"><X size={19}/></button>
      </header>
        <div className="room-scroll" id="room-content" ref={scrollArea} onClickCapture={event => {
          event.target.closest('button')?.focus({preventScroll:true})
        }}>
          <Interior key={active.id} role={role} stage={stage} location={active.id} homeroom={homeroom} onSettings={openSettings} previewHref={preview.toolsHref} dataState={dataState} onRetry={()=>setDataState('ready')} detail={currentRoom.detail} open={openDetail} choices={roomChoices[choiceKey] ?? {}} setChoice={setChoice}/>
        </div></>}
    </dialog>
    <dialog id="directory" className="utility" ref={directory} aria-labelledby="dir-name" onCancel={cancel}>
      <div className="util-head"><h2 id="dir-name">Места острова</h2><button className="icon" aria-label="Закрыть" onClick={leave}><X size={20}/></button></div>
      {sites.map(site=><button className="dir-link" key={site.id} onClick={()=>openSite(site.id)}><site.icon size={19}/><span><strong>{site[role]}</strong><small>{world.sites[site.id]}</small></span><ChevronRight size={16}/></button>)}
    </dialog>
    <dialog id="preferences" className="utility preferences" ref={preferences} aria-labelledby="settings-name" onCancel={cancel}>
      <Preferences lightingPending={lightingPending} lightingFailed={lightingFailed} selected={selected} change={change} theme={theme} systemReduce={systemReduce} onClose={back}/>
    </dialog>
  </div>
}

createRoot(document.getElementById('root')).render(<App/>)
