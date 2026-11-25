import type { Page, Request, TestInfo } from '@playwright/test'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'

export function browserDiagnostics(testInfo: TestInfo, evidenceName: string, baseURL: string | undefined, classify: (url: URL) => string | undefined) {
  const directory = process.env.S08_EVIDENCE_DIR
  const origin = baseURL && new URL(baseURL).origin
  type Status = number | 'aborted' | 'timeout' | 'failed'
  type Entry = { phase: string; path: string; status?: Status; elapsedMs?: number }
  const requests = new Map<Request, { entry: Entry; startedAt: number }>()
  const reads: Entry[] = []
  let phase = 'start'

  function pathCategory(raw: string) {
    const url = new URL(raw)
    return url.origin === origin ? classify(url) : undefined
  }

  function persist(failureLine?: number, width?: { viewport: number; document: number }) {
    if (!directory) return
    const observed = Array.from(requests.values(), ({ entry }) => entry)
    writeFileSync(join(directory, evidenceName), JSON.stringify({ phase, failureLine, width, requests: [...observed, ...reads] }, null, 2))
  }

  function finish(request: Request, status: Status) {
    const observed = requests.get(request)
    if (!observed || observed.entry.status !== undefined) return
    observed.entry.status = status
    observed.entry.elapsedMs = Math.round(performance.now() - observed.startedAt)
    persist()
  }

  function watch(page: Page) {
    if (!directory || !origin) return
    page.on('request', request => {
      if (requests.size >= 100) return
      const path = pathCategory(request.url())
      if (!path) return
      requests.set(request, { entry: { phase, path }, startedAt: performance.now() })
      persist()
    })
    page.on('response', response => finish(response.request(), response.status()))
    page.on('requestfailed', request => {
      const code = request.failure()?.errorText
      finish(request, code === 'net::ERR_ABORTED' ? 'aborted' : code === 'net::ERR_TIMED_OUT' ? 'timeout' : 'failed')
    })
  }

  function note(raw: string, status: number) {
    if (!directory || reads.length >= 20) return
    const path = pathCategory(raw)
    if (path) {
      reads.push({ phase, path, status })
      persist()
    }
  }

  async function save(page: Page, error: unknown) {
    if (!directory) return
    const result = error instanceof Error ? error.stack : testInfo.errors[0]?.stack
    const relative = testInfo.file.split('/').at(-1)
    const frame = result?.split('\n').find(line => relative && line.includes(`${relative}:`))
    const failureLine = Number(/:(\d+):\d+\)?$/.exec(frame ?? '')?.[1]) || undefined
    persist(failureLine)
    const width = await page.locator('html').evaluate(element => ({ viewport: innerWidth, document: element.scrollWidth }), undefined, { timeout: 500 }).catch(() => undefined)
    persist(failureLine, width)
  }

  return { watch, note, save, at: (value: string) => { phase = value; persist() } }
}
