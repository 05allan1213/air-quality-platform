import { describe, expect, it } from 'vitest'
import { cities, coverage, distribution, expected, filteredCities, goodCount, hourly, lowFirst, metricText, missing, pollutants, ranked, snapshot } from './snapshot'

describe('全国总览 F 合成时刻', () => {
  it('同刻 AQI、到报名录、分布和污染物行数可相互核对', () => {
    expect(snapshot).toMatchObject({ synthetic: true, product: 'L', standard: 'HJ633-2012' })
    expect(new Set(cities.map(city => city.name)).size).toBe(cities.length)
    expect(missing.every(name => !cities.some(city => city.name === name))).toBe(true)
    expect(expected).toBe(cities.length + missing.length)
    expect(hourly.at(-1)).toEqual({ time: snapshot.hour, count: cities.length })
    expect(Number(coverage)).toBe(100 * cities.length / expected)
    expect(distribution.reduce((sum, band) => sum + band.count, 0)).toBe(cities.length)
    expect(goodCount).toBe(cities.filter(city => city.aqi <= 100).length)
    expect(pollutants.reduce((sum, item) => sum + item.count, 0)).toBe(cities.filter(city => city.primary !== '无').length)
  })
  it('保留并列名次、缺测等级与无位置样本', () => {
    expect(ranked.map(city => city.aqi)).toEqual(lowFirst.map(city => city.aqi).reverse())
    const tied = cities.filter(city => city.aqi === 82)
    expect(tied).toHaveLength(2)
    expect(tied[0].rank).toBe(tied[1].rank)
    expect(cities.filter(city => !city.point).map(city => city.name)).toEqual(['云汀市'])
    const missingLevel = cities.find(city => city.levels.O3 === null)!
    expect(metricText(missingLevel, 'O3')).toBe('O₃ 缺测')
  })
  it('组合筛选保持原窗口与原排名，零结果不回填', () => {
    expect(filteredCities(' 石家 ', '河北', '4', 'desc')).toEqual([ranked[0]])
    expect(filteredCities('石家', '河南', '', 'desc')).toEqual([])
    expect(filteredCities('', '', '6', 'asc')).toEqual([])
    expect(filteredCities('', '', '', 'asc')[0].name).toBe('海口')
  })
})
