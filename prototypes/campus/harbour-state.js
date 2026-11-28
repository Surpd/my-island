import { useEffect, useState } from 'react'

const preferencesKey = 'my-island.northern-harbour.preferences'
const defaults = { lighting:'auto', stage:'middle', role:'student', reduceMotion:false, largeText:false, homeroom:true }

export function usePreferences() {
  const [preferences,setPreferences] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(preferencesKey))
      if (!saved) return defaults
      return {
        lighting:['auto','day','night'].includes(saved.lighting) ? saved.lighting : defaults.lighting,
        stage:['junior','middle','senior','graduate'].includes(saved.stage) ? saved.stage : defaults.stage,
        role:saved.role === 'teacher' ? 'teacher' : defaults.role,
        reduceMotion:saved.reduceMotion === true,
        largeText:saved.largeText === true,
        homeroom:saved.homeroom !== false,
      }
    } catch { return defaults }
  })
  useEffect(() => {
    try { localStorage.setItem(preferencesKey,JSON.stringify(preferences)) } catch { /* The session preference still works. */ }
  },[preferences])
  return [preferences,(key,value) => setPreferences(current => ({ ...current, [key]:value }))]
}

const hourIsNight = (date = new Date()) => date.getHours() >= 20 || date.getHours() < 7

function nextChange(now) {
  const next = new Date(now)
  next.setHours(now.getHours() < 7 ? 7 : 20,0,0,0)
  if (now.getHours() >= 20) {
    next.setDate(next.getDate() + 1)
    next.setHours(7,0,0,0)
  }
  return next - now
}

const viewportEvents = ['viewportChanged','safeAreaChanged','contentSafeAreaChanged']
const getHost = () => window.Telegram?.WebApp

export function useHarbourHost() {
  const [isNight,setIsNight] = useState(hourIsNight)
  const [visible,setVisible] = useState(() => !document.hidden && getHost()?.isActive !== false)
  const [motionQuery] = useState(() => matchMedia('(prefers-reduced-motion: reduce)'))
  const [systemReduce,setSystemReduce] = useState(() => motionQuery.matches)
  const [viewport,setViewport] = useState({})
  const [compact,setCompact] = useState(() => innerHeight < 510)
  useEffect(() => {
    const host = getHost()
    let timer
    const motionChanged = () => setSystemReduce(motionQuery.matches)
    const syncViewport = (event) => {
      if (event?.isStateStable === false) return
      const css = {}
      const height = host?.viewportStableHeight || host?.viewportHeight
      if (Number.isFinite(height) && height > 0) css['--host-height'] = height + 'px'
      setCompact((height || innerHeight) < 510)
      for (const edge of ['top','right','bottom','left']) {
        const outer = host?.safeAreaInset?.[edge]
        const inner = host?.contentSafeAreaInset?.[edge]
        if (Number.isFinite(outer) || Number.isFinite(inner)) {
          css['--host-safe-' + edge] = (Math.max(0,outer || 0) + Math.max(0,inner || 0)) + 'px'
        }
      }
      setViewport(css)
    }
    const checkTime = () => {
      clearTimeout(timer)
      const now = new Date()
      setIsNight(hourIsNight(now))
      timer = setTimeout(checkTime,nextChange(now) + 40)
    }
    const activate = () => {
      setVisible(!document.hidden)
      checkTime()
      syncViewport()
    }
    const deactivate = () => setVisible(false)
    const visibilityChanged = () => document.hidden || host?.isActive === false ? deactivate() : activate()
    motionQuery.addEventListener?.('change',motionChanged)
    document.addEventListener('visibilitychange',visibilityChanged)
    window.addEventListener('pageshow',visibilityChanged)
    window.addEventListener('focus',visibilityChanged)
    window.addEventListener('resize',syncViewport)
    host?.ready?.()
    viewportEvents.forEach(name => host?.onEvent?.(name,syncViewport))
    host?.onEvent?.('activated',activate)
    host?.onEvent?.('deactivated',deactivate)
    visibilityChanged()
    syncViewport()
    return () => {
      clearTimeout(timer)
      motionQuery.removeEventListener?.('change',motionChanged)
      document.removeEventListener('visibilitychange',visibilityChanged)
      window.removeEventListener('pageshow',visibilityChanged)
      window.removeEventListener('focus',visibilityChanged)
      window.removeEventListener('resize',syncViewport)
      viewportEvents.forEach(name => host?.offEvent?.(name,syncViewport))
      host?.offEvent?.('activated',activate)
      host?.offEvent?.('deactivated',deactivate)
    }
  },[motionQuery])
  return { isNight,visible,systemReduce,viewport,compact }
}

export function useCameraPixels(camera) {
  const [pixelRatio,setPixelRatio] = useState(() => window.devicePixelRatio)
  const [pixels,setPixels] = useState(0)
  useEffect(() => {
    const node = camera.current
    const measure = () => {
      const density = window.devicePixelRatio
      const width = Number.parseFloat(getComputedStyle(node).width)
      setPixelRatio(density)
      setPixels(width * density)
    }
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    window.addEventListener('resize',measure)
    measure()
    return () => {
      observer.disconnect()
      window.removeEventListener('resize',measure)
    }
  },[camera])
  return { pixels,pixelRatio }
}
