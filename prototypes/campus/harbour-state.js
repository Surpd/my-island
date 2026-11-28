import { useEffect, useState } from 'react'

const preferencesKey = 'my-island.northern-harbour.preferences'
const defaults = { lighting:'auto', reduceMotion:false, largeText:false }

export function usePreferences() {
  const [preferences,setPreferences] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(preferencesKey))
      if (!saved) return defaults
      return {
        lighting:['auto','day','night'].includes(saved.lighting) ? saved.lighting : defaults.lighting,
        reduceMotion:saved.reduceMotion === true,
        largeText:saved.largeText === true,
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

const getHost = () => window.Telegram?.WebApp

export function useHarbourHost() {
  const [host,setHost] = useState(getHost)
  useEffect(() => {
    if (host || location.protocol === 'file:') return
    const launch = new URLSearchParams(location.hash.slice(1))
    if (!launch.has('tgWebAppPlatform') || !launch.has('tgWebAppVersion')) return
    const script = document.createElement('script')
    script.src = 'https://telegram.org/js/telegram-web-app.js'
    script.async = true
    script.onload = () => setHost(getHost())
    document.head.append(script)
    return () => { script.onload = null; script.remove() }
  },[host])
  const [isNight,setIsNight] = useState(hourIsNight)
  const [visible,setVisible] = useState(() => !document.hidden && getHost()?.isActive !== false)
  const [motionQuery] = useState(() => matchMedia('(prefers-reduced-motion: reduce)'))
  const [systemReduce,setSystemReduce] = useState(() => motionQuery.matches)
  const [viewport,setViewport] = useState({})
  const [compact,setCompact] = useState(() => innerHeight < 510)
  useEffect(() => {
    const modernHost = host && host.isVersionAtLeast?.('8.0') !== false
    const viewportEvents = modernHost ? ['viewportChanged','safeAreaChanged','contentSafeAreaChanged'] : ['viewportChanged']
    const visualViewport = window.visualViewport
    let timer
    const motionChanged = () => setSystemReduce(motionQuery.matches)
    const syncViewport = (event) => {
      if (event?.isStateStable === false) return
      const css = {}
      const visibleHeight = visualViewport && visualViewport.scale === 1 ? visualViewport.height : innerHeight
      css['--browser-height'] = visibleHeight + 'px'
      const height = host?.viewportStableHeight || host?.viewportHeight
      if (Number.isFinite(height) && height > 0) css['--host-height'] = height + 'px'
      setCompact(Math.min(height || visibleHeight,visibleHeight) < 510)
      for (const edge of ['top','right','bottom','left']) {
        const physical = modernHost && host.safeAreaInset?.[edge]
        const content = modernHost && host.contentSafeAreaInset?.[edge]
        if (Number.isFinite(physical)) css['--host-safe-' + edge] = Math.max(0,physical) + 'px'
        if (Number.isFinite(content)) css['--host-content-' + edge] = Math.max(0,content) + 'px'
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
    const deactivate = () => {
      setVisible(false)
      clearTimeout(timer)
    }
    const visibilityChanged = () => document.hidden || host?.isActive === false ? deactivate() : activate()
    motionQuery.addEventListener?.('change',motionChanged)
    document.addEventListener('visibilitychange',visibilityChanged)
    window.addEventListener('pageshow',visibilityChanged)
    window.addEventListener('focus',visibilityChanged)
    window.addEventListener('resize',syncViewport)
    visualViewport?.addEventListener('resize',syncViewport)
    host?.ready?.()
    viewportEvents.forEach(name => host?.onEvent?.(name,syncViewport))
    if (modernHost) {
      host.onEvent?.('activated',activate)
      host.onEvent?.('deactivated',deactivate)
    }
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
      visualViewport?.removeEventListener('resize',syncViewport)
      if (modernHost) {
        host.offEvent?.('activated',activate)
        host.offEvent?.('deactivated',deactivate)
      }
    }
  },[motionQuery,host])
  return { isNight,visible,systemReduce,viewport,compact,host }
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
