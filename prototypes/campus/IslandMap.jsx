import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { buildingGeometry, worlds } from './worlds.js'
import { sharpWorlds } from './sharp-worlds.js'
import { placeLabels } from './label-layout.js'
import './island-map.css'

export const maxTravelScale = 1.46
const pendingImages = new Map()

function decodeImage(src) {
  if (pendingImages.has(src)) return pendingImages.get(src)
  const image = new Image(1254, 1254)
  image.alt = ''
  image.draggable = false
  image.className = 'scene-image'
  image.src = src
  const pending = image.decode().then(() => {
    image.width = image.naturalWidth
    image.height = image.naturalHeight
    image.dataset.loaded = 'true'
    return image
  }).finally(() => pendingImages.delete(src))
  pendingImages.set(src, pending)
  return pending
}

export function useIslandScene(worldId, requestedTheme, cameraPixels, reduceMotion) {
  const [scene,setScene] = useState(null)
  const [failed,setFailed] = useState(null)
  const currentScene = useRef(null)
  const standard = worlds[worldId][requestedTheme]
  const expanded = sharpWorlds[worldId]?.[requestedTheme]
  const requestedPixels = cameraPixels * (reduceMotion ? 1 : maxTravelScale)
  const upgraded = expanded && (requestedPixels > worlds[worldId].width || scene?.src === expanded)
  const src = cameraPixels ? (upgraded ? expanded : standard) : null

  useEffect(() => {
    if (!src || currentScene.current?.requestSrc === src) return
    let current = true
    function commit(image, theme, imageSrc, requestSrc = src) {
      if (!current) return
      const ready = { image, worldId, theme, src:imageSrc, requestSrc }
      currentScene.current = ready
      setScene(ready)
    }

    async function prepare() {
      const candidates = src === standard ? [src] : [src, standard]
      for (const imageSrc of candidates) {
        const image = await decodeImage(imageSrc).catch(() => null)
        if (!current) return
        if (image) {
          setFailed(null)
          commit(image, requestedTheme, imageSrc)
          return
        }
      }

      setFailed(src)
      if (currentScene.current || requestedTheme === 'day') return
      const daySrc = worlds[worldId].day
      const fallback = await decodeImage(daySrc).catch(() => null)
      if (fallback) commit(fallback, 'day', daySrc, daySrc)
    }
    void prepare()
    return () => { current = false }
  }, [src, standard, worldId, requestedTheme])

  return { scene, pending:!src || (scene?.requestSrc !== src && failed !== src), failed:!!src && failed === src }
}

export function IslandMap({ scene, camera, cameraStyle, world, worldId, activeId, disabled, sites, role, onOpen }) {
  const imageMount = useRef(null)
  const labels = useRef({})
  const [layout,setLayout] = useState(null)
  const labelIds = sites.map(site => site.id).join(" ")
  const geometry = buildingGeometry[worldId]
  const detail = geometry[activeId]

  useLayoutEffect(() => {
    if (scene) imageMount.current.replaceChildren(scene.image)
  }, [scene])

  useLayoutEffect(() => {
    const map = camera.current
    const stage = map.parentElement
    const ids = labelIds.split(' ')
    const chips = ids.map(id => labels.current[id].firstElementChild)
    const measure = () => {
      const locations = ids.map((id,index) => ({
        id, point:world.points[id],
        label:{ width:chips[index].offsetWidth, height:chips[index].offsetHeight },
      }))
      setLayout(placeLabels({ geometry, locations, side:map.offsetWidth, width:stage.clientWidth, height:stage.clientHeight }))
    }
    const observer = new ResizeObserver(measure)
    observer.observe(map)
    observer.observe(stage)
    chips.forEach(chip => observer.observe(chip))
    measure()
    return () => observer.disconnect()
  }, [camera, geometry, labelIds, world])

  return <div ref={camera} className="camera" style={cameraStyle}>
    <div className="scene-frame" data-scene-world={scene?.worldId} ref={imageMount} aria-hidden="true"/>
    <div className="harbour-shimmer" aria-hidden="true"/>
    {detail && scene?.image.naturalWidth === world.width && <svg className="architecture-detail" viewBox="0 0 100 100" aria-hidden="true">
      <path className="architecture-shadow" d={detail[1]}/>
      <path className="architecture-line" d={detail[1]}/>
    </svg>}
    <div className="markers" inert={disabled || undefined}>
      <svg className="building-hits" viewBox="0 0 100 100" aria-hidden="true">
        {sites.map(site => <polygon key={site.id} className="building-hit" data-location={site.id} points={geometry[site.id][0]} onClick={() => {
          labels.current[site.id]?.focus({ preventScroll:true })
          onOpen(site.id)
        }}/>)}
      </svg>
      {layout && <svg className="marker-leaders" viewBox="0 0 100 100" aria-hidden="true">
        {sites.map(({ id }) => {
          const [x,y] = world.points[id]
          const leader = layout[id]?.leader
          return leader && <g key={id}>
            <path d={`M${x} ${y}L${leader[0]} ${leader[1]}`}/>
            <circle cx={x} cy={y} r="1"/>
          </g>
        })}
      </svg>}
      {sites.map(site => {
        const [x,y] = world.points[site.id]
        const position = layout?.[site.id] ?? { x,y:y+6 }
        return <button key={site.id} ref={node => { labels.current[site.id] = node }} className={'marker marker-'+site.id}
          data-location={site.id} aria-label={site[role]+' — '+world.sites[site.id]}
          style={{left:position.x+'%',top:position.y+'%'}} onClick={event => {
            event.currentTarget.focus({ preventScroll:true })
            onOpen(site.id)
          }}>
          <span className="marker-tag"><site.icon size={14}/><span>{role === 'teacher' && site.short ? site.short : site[role]}</span></span>
        </button>
      })}
    </div>
  </div>
}
