const clearance = 4
const gridStep = 5
const minimumReach = 44

function bounds(points) {
  const coordinates = axis => points.map(point => point[axis])
  return {
    left:Math.min(...coordinates(0)), right:Math.max(...coordinates(0)),
    top:Math.min(...coordinates(1)), bottom:Math.max(...coordinates(1)),
  }
}

function overlaps(a,b) {
  return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top
}

function contains(points,x,y) {
  let inside = false
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const [ax,ay] = points[i], [bx,by] = points[j]
    if ((ay > y) !== (by > y) && x < (bx - ax) * (y - ay) / (by - ay) + ax) inside = !inside
  }
  return inside
}

function touchesBuilding(rect,shape) {
  if (!overlaps(rect,shape.bounds)) return false
  const corners = [[rect.left,rect.top],[rect.left,rect.bottom],[rect.right,rect.top],[rect.right,rect.bottom]]
  if (corners.some(([x,y]) => contains(shape.points,x,y))) return true
  return shape.points.some(([ax,ay],index) => {
    const [bx,by] = shape.points[(index + 1) % shape.points.length]
    if (ax > rect.left && ax < rect.right && ay > rect.top && ay < rect.bottom) return true
    return [rect.left,rect.right].some(x => {
      if ((ax > x) === (bx > x)) return false
      const y = ay + (by - ay) * (x - ax) / (bx - ax)
      return y > rect.top && y < rect.bottom
    }) || [rect.top,rect.bottom].some(y => {
      if ((ay > y) === (by > y)) return false
      const x = ax + (bx - ax) * (y - ay) / (by - ay)
      return x > rect.left && x < rect.right
    })
  })
}

export function placeLabels({ geometry, locations, side, width, height }) {
  if (side <= 0 || !locations.length) return null
  const gutterX = (width - side) / 2
  const gutterY = (height - side) / 2
  const shapes = locations.map(({ id, point, label }) => {
    const points = geometry[id][0].split(' ').map(pair => pair.split(',').map(value => Number(value) * side / 100))
    return { id, point:point.map(value => value * side / 100), label, points, bounds:bounds(points) }
  })
  // Place labels in stage coordinates so narrow map squares can use the water alongside them.
  const candidateSets = shapes.map(shape => {
    const [pinX,pinY] = shape.point
    const halfWidth = shape.label.width / 2, halfHeight = shape.label.height / 2
    const preferredY = pinY + halfHeight + 10
    const candidates = []
    const minX = 9 - gutterX + halfWidth, maxX = side + gutterX - 9 - halfWidth
    const minY = 8 - gutterY + halfHeight, maxY = side + gutterY - 8 - halfHeight
    const west = Math.max(minX,pinX - 80), east = Math.min(maxX,pinX + 80)
    const north = Math.max(minY,pinY - 75), south = Math.min(maxY,pinY + 85)
    for (let y = north; y <= south; y += gridStep) {
      for (let x = west; x <= east; x += gridStep) {
        const top = y - halfHeight, bottom = y + halfHeight
        const left = x - halfWidth, right = x + halfWidth
        if (Math.max(bottom,shape.bounds.bottom) - Math.min(top,shape.bounds.top) < minimumReach
          || Math.max(right,shape.bounds.right) - Math.min(left,shape.bounds.left) < minimumReach) continue
        const zone = { left:left-clearance, right:right+clearance, top:top-clearance, bottom:bottom+clearance }
        if (shapes.some(other => other !== shape && touchesBuilding(zone,other))) continue
        const cost = (x - pinX) ** 2 + (y - preferredY) ** 2
        candidates.push({ x,y,zone,cost,leader:[
          Math.max(left,Math.min(right,pinX)),
          Math.max(top,Math.min(bottom,pinY)),
        ] })
      }
    }
    return { id:shape.id, candidates:candidates.sort((a,b) => a.cost-b.cost) }
  }).sort((a,b) => a.candidates.length-b.candidates.length)

  // Start with the most constrained label; backtrack only when earlier choices block another label.
  const chosen = []
  let remaining = 1800
  function place(index) {
    if (index === candidateSets.length) return true
    if (--remaining < 0) return false
    const { id,candidates } = candidateSets[index]
    for (const candidate of candidates) {
      if (chosen.some(other => overlaps(other.zone,candidate.zone))) continue
      chosen.push({ ...candidate,id })
      if (place(index + 1)) return true
      chosen.pop()
    }
    return false
  }
  if (!place(0)) return null
  return Object.fromEntries(chosen.map(({ id,x,y,leader }) => [id,{
    x:x/side*100, y:y/side*100, leader:leader.map(value => value/side*100),
  }]))
}
