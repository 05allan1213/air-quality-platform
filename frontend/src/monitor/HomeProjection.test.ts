// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { homeCityLink, rememberHomeReturn, takeHomeReturn, utcLabel, type HomeCity } from './HomeProjection'

const anchor = '2024-02-02T00:00:00Z'
const href = '/api/v1/cities/17/trend/?product=8&scope=synthetic&anchor=2024-02-02T08%3A00%3A00%2B08%3A00'
const city = (url: string): HomeCity => ({ city: 17, name: null, geography: null, series: [], levels: [], links: { trend: [{ product: 8, href: url }] } })
const back = { path: '/?scope=synthetic', generation: 2, position: 1, cursors: ['test-signature'], city: 17, entrance: 'detail' as const, scroll: 12 }

afterEach(() => rememberHomeReturn())

describe('home projection boundaries', () => {
  it('accepts only the selected city, correct product, scope and exact anchor', () => {
    expect(homeCityLink(city(href), 'trend', 'synthetic', anchor)?.path).toBe('cities/17/trend/')
    expect(homeCityLink(city(href), 'trend', undefined, anchor)).toBeNull()
    expect(homeCityLink(city(href), 'trend', 'synthetic', '2024-02-03T00:00:00Z')).toBeNull()
    expect(homeCityLink(city(href.replace('/17/', '/18/')), 'trend', 'synthetic', anchor)).toBeNull()
    expect(homeCityLink(city(href.replace('product=8', 'product=9')), 'trend', 'synthetic', anchor)).toBeNull()
    expect(homeCityLink(city(`https://example.invalid${href}`), 'trend', 'synthetic', anchor)).toBeNull()
  })

  it('does not format an invalid URL anchor as an observed timestamp', () => {
    expect(utcLabel('invalid')).toBe('时刻未知')
    expect(utcLabel(anchor)).toMatch(/ UTC$/)
  })

  it('only consumes an in-memory return context once and on the same session/history entry', () => {
    rememberHomeReturn(back)
    expect(takeHomeReturn(back.path, 2, 1)).toEqual(back)
    expect(takeHomeReturn(back.path, 2, 1)).toBeUndefined()
    rememberHomeReturn(back)
    expect(takeHomeReturn(back.path, 3, 1)).toBeUndefined()
    rememberHomeReturn(back)
    expect(takeHomeReturn(back.path, 2, 4)).toBeUndefined()
    rememberHomeReturn(back)
    window.dispatchEvent(new Event('air-quality:clear-reads'))
    expect(takeHomeReturn(back.path, 2, 1)).toBeUndefined()
  })
})
