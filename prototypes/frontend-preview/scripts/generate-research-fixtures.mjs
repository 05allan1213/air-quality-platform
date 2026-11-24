/** Prepare F-only research responses offline. The browser presents these results. */
import { createHash } from 'node:crypto'
import { writeFileSync } from 'node:fs'

const hour = 3_600_000
const base = Date.parse('2024-05-14T00:00:00+08:00')
const objects = { C: [101, 102], S: [201, 202, 203] }
const indicators = ['PM2.5', 'PM10', 'SO2', 'NO2', 'CO', 'O3']
const parameters = [[32, 25], [49, 35], [7, 8], [28, 20], [0.57, 0.57], [66, 70]]
const nonCoEdges = [0, 10, 25, 50, 100, 200, 500]
const coEdges = [0, 0.5, 1, 2, 4, 8, 16]
const windows = {
  normal: { start: 48, hours: 24 },
  extended: { start: 24, hours: 48 },
  insufficient: { start: 0, hours: 24 },
  empty: { start: 72, hours: 24 },
}
const mean = (items) => items.length ? items.reduce((sum, n) => sum + n, 0) / items.length : null
const round = (n) => Number(n.toFixed(6))
function versionSeed(key, group, columns) {
  return createHash('sha256')
    .update(JSON.stringify(['preview-f-research-v1', key, group.map((id) => [id, columns[id].values])]))
    .digest('hex')
    .slice(2)
}
const bounds = (start, hours) => ({
  start: new Date(base + start * hour).toISOString(),
  end: new Date(base + (start + hours) * hour).toISOString(),
})

function observation(entity, indicator, slot) {
  const offset = entity % 100 - 1
  const i = indicators.indexOf(indicator)
  const [baseline, amplitude] = parameters[i]
  const localHour = slot % 24
  let gap = slot >= 72
  if (slot < 24) gap ||= entity === 201 && slot >= 18 || entity === 202 && slot < 6
  if (slot >= 24 && slot < 72) {
    gap ||= entity === 201 && localHour === 5
      || entity === 202 && [12, 19].includes(localHour)
      || entity === 203 && localHour === 3
      || entity === 102 && localHour === 15
  }
  if (gap) return null
  if (slot === 61 && entity === 201 && indicator === 'PM2.5') return 'missing'
  if (slot === 62 && entity === 201 && indicator === 'SO2') return 'censored'
  if (slot === 65 && entity === 201 && indicator === 'SO2') return 0
  // Synthetic staggered peaks and deterministic noise. No source data is used.
  const morning = Math.exp(-((localHour - 8.5 + offset * 0.35) ** 2) / 13)
  const evening = Math.exp(-((localHour - 18.5 - offset * 0.45) ** 2) / 12)
  const signal = i === 5 ? 0.9 * Math.exp(-((localHour - 14) ** 2) / 25) : 0.7 * morning + evening
  const noise = Math.sin(slot * 1.88 + i * 0.95 + offset) + 0.5 * Math.cos(slot + i * 2)
  const disparity = (offset - 0.3) * 0.16 + (entity < 200 ? 0.12 : 0) + (slot < 48 ? 0.09 : 0)
  const value = baseline + amplitude * (signal + disparity + noise * (0.09 + i * 0.022))
  return i === 4 ? Number(value.toFixed(2)) : Math.round(value)
}

function coverage(values) {
  const E = values.length
  const missing = values.filter((item) => item === 'missing').length
  const censored = values.filter((item) => item === 'censored').length
  const gap = values.filter((item) => item === null).length
  const O = E - gap
  const V = O - missing - censored
  return {
    E, O, V, valid_ratio: V / E, received_ratio: O / E,
    outside_schedule: 0, denominator_state: 'known',
    exclusions: { gap, missing, censored, invalid: 0, suspect: 0, disputed: 0, unknown: 0 },
  }
}

function statistics(values, indicator, start) {
  const edges = [null, ...(indicator === 'CO' ? coEdges : nonCoEdges), null]
  const samples = values.filter((item) => typeof item === 'number')
  const distribution = edges.slice(0, -1).map((lower, index) => {
    const upper = edges[index + 1]
    return { lower, upper, n: samples.filter((value) => (lower === null || value >= lower) && (upper === null || value < upper)).length }
  })
  const daily = []
  for (let index = 0; index < values.length; index += 24) {
    const slice = values.slice(index, index + 24)
    daily.push({ ...bounds(start + index, slice.length), ...coverage(slice), mean: mean(slice.filter((value) => typeof value === 'number')) })
  }
  const total = coverage(values)
  return { ...total, conclusion: { state: total.valid_ratio >= 0.75 ? 'sufficient' : 'insufficient_coverage' }, mean: mean(samples), distribution, daily }
}

function coefficient(valuesA, valuesB) {
  const centerA = mean(valuesA)
  const centerB = mean(valuesB)
  const dx = valuesA.map((x) => x - centerA)
  const dy = valuesB.map((y) => y - centerB)
  const scale = Math.sqrt(dx.reduce((sum, d) => sum + d ** 2, 0) * dy.reduce((sum, d) => sum + d ** 2, 0))
  return scale ? round(dx.reduce((sum, d, i) => sum + d * dy[i], 0) / scale) : null
}

function pairing(keys, columns, hours, isPearson) {
  const slots = Array.from({ length: hours }, (_, i) => i)
    .filter((i) => keys.every((key) => typeof columns[key].values[i] === 'number'))
  const P = slots.length
  const enough = P / hours >= 0.75
  const result = { slots, P, E_common: hours, actual_pairs: P, paired_ratio: P / hours, conclusion: { state: enough ? 'sufficient' : 'insufficient_coverage' } }
  if (isPearson) {
    const pearson = enough && P >= 30
      ? coefficient(slots.map((i) => columns[keys[0]].values[i]), slots.map((i) => columns[keys[1]].values[i]))
      : null
    if (enough && P < 30) result.conclusion.state = 'insufficient_pairs'
    else if (enough && pearson === null) result.conclusion.state = 'zero_variance'
    return { ...result, pearson, interpretation: 'exploratory_pearson_seasonality_autocorrelation_missingness' }
  }
  const means = enough ? keys.map((key, pos) => ({ pos, entity: Number(key.split(':')[0]), mean: mean(slots.map((i) => columns[key].values[i])) })) : null
  const ranking = means?.toSorted((a, b) => b.mean - a.mean || a.pos - b.pos)
    .map((row, _i, ordered) => ({ ...row, rank: ordered.findIndex((candidate) => candidate.mean === row.mean) + 1 })) ?? null
  return { ...result, ranking, partial_summary: null, mean_difference: means ? means[0].mean - means[1].mean : null }
}

const data = Object.fromEntries(Object.entries(windows).map(([key, { start, hours }]) => {
  const columns = Object.fromEntries(Object.values(objects).flat().flatMap((entity) => indicators.map((indicator) => {
    const values = Array.from({ length: hours }, (_, i) => observation(entity, indicator, start + i))
    return [`${entity}:${indicator}`, { values, ...statistics(values, indicator, start) }]
  })))
  const combinations = Object.values(objects).flatMap((entities) => {
    const subsets = Array.from({ length: (1 << entities.length) - 1 }, (_, i) => entities.filter((_, pos) => ((i + 1) & (1 << pos)) !== 0))
    return subsets.filter((subset) => subset.length > 1).flatMap((subset) => indicators.map((indicator) => subset.map((entity) => `${entity}:${indicator}`)))
  })
  const correlations = Object.values(objects).flat().flatMap((entity) => indicators.flatMap((a) => indicators.filter((b) => a !== b).map((b) => [`${entity}:${a}`, `${entity}:${b}`])))
  const groups = [...Object.keys(columns).map((column) => [column]), ...combinations, ...correlations]
  const version_seeds = Object.fromEntries(groups.map((group) => [group.join('|'), versionSeed(key, group, columns)]))
  const pairs = Object.fromEntries([...combinations, ...correlations].map((group, i) => [group.join('|'), pairing(group, columns, hours, i >= combinations.length)]))
  return [key, { ...bounds(start, hours), hours, slot_offset: start, columns, pairs, version_seeds }]
}))

writeFileSync(new URL('../src/analysis/research-fixtures.json', import.meta.url), `${JSON.stringify(data)}\n`)

// Thin city projection shares the exact same F/C source values without loading the research fixture.
const city = Object.fromEntries(indicators.map((indicator) => [indicator, data.normal.columns[`101:${indicator}`].values]))
writeFileSync(new URL('../src/analysis/city-series.json', import.meta.url), `${JSON.stringify(city)}\n`)
