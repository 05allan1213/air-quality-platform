import { cities, type CitySample } from './snapshot'

type Point = { x: number; y: number }
const located = cities.filter((city): city is CitySample & { point: Point } => city.point !== null)

// Start with a generous target. Only trim the side facing a close neighbour,
// at the perpendicular bisector, so neither target can cover the other's dot.
function targetFor(center: Point) {
  const r = 13
  let polygon = [{ x: -r, y: -r }, { x: r, y: -r }, { x: r, y: r }, { x: -r, y: r }]
  let radius = 4
  for (const { point } of located) {
    if (point === center) continue
    const dx = point.x - center.x, dy = point.y - center.y
    const distance = Math.hypot(dx, dy)
    if (distance > 2 * Math.SQRT2 * r) continue
    radius = Math.min(radius, distance * .38)
    const side = (p: Point) => p.x * dx + p.y * dy - distance ** 2 / 2
    const clipped: Point[] = []
    polygon.forEach((a, i) => {
      const b = polygon[(i + 1) % polygon.length]
      const from = side(a), to = side(b)
      if (from <= 0) clipped.push(a)
      if ((from <= 0) !== (to <= 0)) {
        const t = from / (from - to)
        clipped.push({ x: a.x + t * (b.x - a.x), y: a.y + t * (b.y - a.y) })
      }
    })
    polygon = clipped
  }
  return { radius, target: polygon.map(p => `${p.x},${p.y}`).join(' ') }
}

export const points = located.map(city => ({ ...city, ...targetFor(city.point) }))
