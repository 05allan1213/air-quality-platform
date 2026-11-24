import rows from './assets/sample-cities.json'

// This module is the local F data provider. Views never query an observation service.
export const snapshot = {
  source: 'preview.f', product: 'L', synthetic: true, standard: 'HJ633-2012',
  observedAt: '2024-05-16T23:00:00+08:00', date: '2024.05.16', hour: '23:00',
} as const
export const grades = ['优', '良', '轻度污染', '中度污染', '重度污染', '严重污染'] as const
export const ranges = ['0–50', '51–100', '101–150', '151–200', '201–300', '>300'] as const
export const gradeColors = ['#00E400', '#FFFF00', '#FF7E00', '#FF0000', '#99004C', '#7E0023'] as const
export const metricLabels = { AQI: 'AQI', PM25: 'PM₂.₅', PM10: 'PM₁₀', O3: 'O₃' } as const
export type Metric = keyof typeof metricLabels
const pollutantNames: Record<string, string> = { PM25: 'PM₂.₅', PM10: 'PM₁₀', O3: 'O₃', NO2: 'NO₂', '-': '无' }
export interface CitySample {
  name: string
  province: string
  aqi: number
  grade: number
  primary: string
  levels: Record<Exclude<Metric, 'AQI'>, number | null>
  point: { x: number; y: number } | null
  rank: number
}
const gradeOf = (aqi: number) => 1 + [50, 100, 150, 200, 300].filter(limit => aqi > limit).length
const syntheticCity = { name: '云汀市', province: '合成地域', aqi: 46, primary: '-', x: null, y: null }
const rosterRows = [...rows, syntheticCity]
const uniqueValues = [...new Set(rosterRows.map(row => row.aqi))].sort((a, b) => b - a)
export const cities: CitySample[] = rosterRows.map((row, i) => {
  const grade = gradeOf(row.aqi)
  const levels = (key: string, offset: number) => row.primary === key ? grade : Math.max(1, grade - (i + offset) % 3)
  return {
    name: row.name, province: row.province, aqi: row.aqi, grade, primary: pollutantNames[row.primary],
    levels: { PM25: levels('PM25', 1), PM10: levels('PM10', 2), O3: i === 9 ? null : levels('O3', 0) },
    point: row.x !== null && row.y !== null ? { x: row.x, y: row.y } : null,
    rank: uniqueValues.indexOf(row.aqi) + 1,
  }
})
export const ranked = [...cities].sort((a, b) => a.rank - b.rank || a.name.localeCompare(b.name, 'zh-CN'))
export const lowFirst = [...ranked].sort((a, b) => a.aqi - b.aqi || a.name.localeCompare(b.name, 'zh-CN'))
export const missing = ['绵阳', '唐山', '大同'] as const
// Frozen, exact fixture roster and hourly ledger counts. The current hour reconciles with the L rows.
export const expected = cities.length + missing.length
export const coverage = (100 * cities.length / expected).toFixed(0)
export const hourly = [35, 38, 37, 40, 43, 41, 42, 45, 44, 46, 45, cities.length].map((count, i) => ({ time: `${12 + i}:00`, count }))
export const distribution = grades.map((name, index) => ({ name, color: gradeColors[index], count: cities.filter(city => city.grade === index + 1).length }))
export const goodCount = distribution[0].count + distribution[1].count
export const pollutants = ['PM₂.₅', 'PM₁₀', 'O₃', 'NO₂'].map(name => ({ name, count: cities.filter(city => city.primary === name).length })).sort((a, b) => b.count - a.count)
export const provinces = [...new Set(cities.map(city => city.province))].sort((a, b) => a.localeCompare(b, 'zh-CN'))
export function filteredCities(query: string, province: string, grade: string, order: string) {
  return (order === 'asc' ? lowFirst : ranked).filter(city => city.name.includes(query.trim()) && (!province || province === city.province) && (!grade || Number(grade) === city.grade))
}
export const metricGrade = (city: CitySample, metric: Metric) => metric === 'AQI' ? city.grade : city.levels[metric]
export function metricText(city: CitySample, metric: Metric) {
  if (metric === 'AQI') return `AQI ${city.aqi} · ${grades[city.grade - 1]}`
  const level = city.levels[metric]
  return `${metricLabels[metric]} ${level === null ? '缺测' : `${level} 级`}`
}
