import { readFileSync } from 'node:fs'

export interface BrowserAccount {
  id: number
  username: string
  password: string
}

export interface QualityFixture {
  product: number
  issue: number
  window: number
  run: string
  batch: number
  receipt: number
  city: number
  observed_at: string
}

interface ResearchFixture {
  product: number
  city: number
  start: string
  end: string
}

export interface BrowserSeed {
  origin: 'F'
  fixture_version: 's08-browser-fixture-v1'
  scope: 'synthetic'
  source: string
  products: Record<'L' | 'C' | 'S' | 'T' | 'D', number>
  cities: { id: number; name: string; code: string }[]
  city: number
  station: number
  site_epoch: number
  previous_site_epoch: number
  paired_site_epochs: [number, number]
  quality_issue: number
  snapshot_at: string
  start: string
  end: string
  accounts: Record<
    'reader' | 'peer' | 'operator' | 'schedule_editor' | 'reviewer' | 'adjudicator' | 'station_manager' | 'diagnostics' | 'ops_reviewer' | 'grant_manager' | 'admin' | 'account_admin' | 'schedule_manager' | 'disabled' | 'downgraded',
    BrowserAccount
  >
  collection: {
    product: number
    job: number
    schedule_template: number
    schedule_template_version: number
    city: number
    target: string | null
  }
  quality: {
    source: string
    start: string
    end: string
    close: QualityFixture
    correct_source: QualityFixture & { sibling_at: string; sibling_window: number }
    correct_mapping: QualityFixture & {
      target: string
      target_city: number
      target_window: number
      target_run: string
      target_receipt: number
      target_review_grant: number
    }
    station: { product: number; station: number; site_epoch: number; identifier: number; at: string; target: string }
  }
  favorite_rights: {
    product: number
    grant: number
    export_grant: number
    city: number
    name: string
    source: string
    start: string
    end: string
  }
  research: {
    calendar: ResearchFixture & { discovery_start: string }
    pearson: ResearchFixture
    range: ResearchFixture
  }
  operations: {
    product: number
    window: number
    start: string
    end: string
    observed_at: string
    fetched_at: string
    first_valid_write_at: string
    audit: { kind: 'grant'; id: number; start: string; end: string }
    grant: { id: number; product: number; version: number }
  }
}

export function browserSeed(): BrowserSeed {
  const path = process.env.S08_BROWSER_SEED_FILE
  if (!path) throw new Error('S08_BROWSER_SEED_FILE is required for isolated browser verification.')
  return JSON.parse(readFileSync(path, 'utf8')) as BrowserSeed
}
