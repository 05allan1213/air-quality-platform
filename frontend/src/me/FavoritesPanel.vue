<script setup lang="ts">
import { computed, ref } from 'vue'
import type { RouteLocationRaw } from 'vue-router'
import { RefreshRight } from '@element-plus/icons-vue'
import { api, usePrivateRead } from '../api/client'
import { useSessionStore } from '../stores/session'
import OwnedState from './OwnedState.vue'
import { timeLabel, useOwnedPage } from './owned'

type Reading = {
  revision: number; indicator: string; observed_at: string; value: string | number | null
  unit: string; quality: string; window_definition: string; standard_series: string
  censor_direction?: string | null; censor_limit?: string | null
}
type Epoch = {
  site_epoch: number; name: string | null; site_name: string | null; status: 'current' | 'historical' | 'unknown'
  period_start: string | null; period_end: string | null; link: string
}
type Intent = { id: number; kind: 'city' | 'station' }
type Available = Intent & {
  state: 'ok' | 'no_data' | 'select_epoch'; product: number; target_id: number; source: string
  name: string | null; snapshot_at?: string | null; site_epoch?: number | null
  epochs?: Epoch[]; results: Reading[]; link: string | null
}
type Favorite = Available | (Intent & { state: 'unavailable' })
type Target = { route: RouteLocationRaw } | { path: string; params: Record<string, string> }
type ExactResult = { results?: Reading[]; points?: { results: Reading[] }[] }

const session = useSessionStore()
const detail = usePrivateRead<ExactResult>()
const previewId = ref<number>()
const { data, busy, error, notice, actionError, actionBusy, cursor, list, item, act } = useOwnedPage<Favorite>('favorites', clearPreview)
const previewRows = computed(() => detail.data.value?.results ?? detail.data.value?.points?.flatMap(point => point.results) ?? [])
const labels: Record<string, string> = { valid: '有效', missing: '缺测', censored: '低于或高于报告限', invalid: '无效', suspect: '可疑', disputed: '争议', unknown: '未知' }

function target(favorite: Available, link: string | null, epoch?: number): Target | null {
  if (!link?.startsWith('/api/v1/')) return null
  const url = new URL(link, window.location.origin)
  const params = Object.fromEntries(url.searchParams)
  if (url.origin !== window.location.origin || params.product !== String(favorite.product) || (params.scope ?? '') !== session.scope) return null
  if (favorite.kind === 'station' && url.pathname === `/api/v1/stations/${favorite.target_id}/`) {
    if (!epoch || params.site_epoch !== String(epoch)) return null
    return { route: { name: 'station', params: { id: favorite.target_id }, query: params } }
  }
  if (favorite.kind === 'city' && url.pathname === `/api/v1/cities/${favorite.target_id}/`) {
    return { route: { name: 'city', params: { id: favorite.target_id }, query: params } }
  }
  if (favorite.kind === 'city' && (url.pathname === `/api/v1/cities/${favorite.target_id}/trend/`
    || (url.pathname === '/api/v1/observations/query/' && params.cities === String(favorite.target_id)))) {
    return { path: url.pathname.slice('/api/v1/'.length), params }
  }
  return null
}

function revisit(favorite: Available) {
  return target(favorite, favorite.link, favorite.site_epoch ?? undefined)
}

function epochRoute(favorite: Available, epoch: Epoch) {
  const destination = target(favorite, epoch.link, epoch.site_epoch)
  return destination && 'route' in destination ? destination.route : undefined
}

function chooseDefault(favorite: Available) {
  const destination = revisit(favorite)
  return destination && 'route' in destination ? destination.route : undefined
}

function openExact(favorite: Available) {
  const destination = revisit(favorite)
  if (!destination || !('path' in destination)) return
  previewId.value = favorite.id
  void detail.run(signal => api.get<ExactResult>(destination.path, destination.params, signal))
}

function hasExactRecord(favorite: Available) {
  const destination = revisit(favorite)
  return Boolean(destination && 'path' in destination)
}

function clearPreview() {
  previewId.value = undefined
  detail.clear()
}

function reload(next?: string) {
  clearPreview()
  void list(next)
}

function remove(id: number) {
  void act(() => api.delete(item(id)), () => {
    notice.value = '已取消关注。'
    reload()
  }, () => reload())
}

function readingValue(reading: Reading) {
  if (reading.value !== null && reading.value !== undefined) return `${reading.value} ${reading.unit}`
  if (reading.censor_direction && reading.censor_limit != null) return `${reading.censor_direction}${reading.censor_limit} ${reading.unit}`
  return '—'
}
</script>

<template>
  <section class="me-panel" aria-labelledby="my-favorites-title">
    <header class="me-panel-head">
      <div><h2 id="my-favorites-title">我的关注</h2><p>按你关注时选择的产品读取当前获准摘要。</p></div>
      <button class="btn" type="button" :disabled="busy || actionBusy" @click="reload()"><RefreshRight aria-hidden="true" />刷新关注</button>
    </header>
    <p v-if="notice" class="status status-success" role="status">{{ notice }}</p>
    <p v-if="actionError" class="status status-error" role="alert">{{ actionError }}</p>
    <OwnedState :busy="busy" :error="error?.message" :empty="data?.results.length === 0" empty-text="还没有关注对象。可在城市或站点详情中添加关注。" @retry="reload()" />
    <ul v-if="data?.results.length" class="me-list">
      <li v-for="favorite in data.results" :key="favorite.id" class="me-row">
        <article v-if="favorite.state === 'unavailable'" class="me-entry">
          <div class="me-entry-head">
            <div><h3>{{ favorite.kind === 'city' ? '城市关注' : '站点关注' }}</h3><p>当前不可查看。你仍可取消关注。</p></div>
            <button class="btn" type="button" :disabled="actionBusy" @click="remove(favorite.id)">取消关注</button>
          </div>
        </article>
        <article v-else class="me-entry">
          <div class="me-entry-head">
            <div>
              <h3>{{ favorite.name || (favorite.kind === 'city' ? '城市关注' : '站点关注') }}</h3>
              <p>{{ favorite.source }} · 产品 {{ favorite.product }}<template v-if="favorite.site_epoch"> · 站址期 {{ favorite.site_epoch }}</template></p>
              <p v-if="favorite.snapshot_at">快照 {{ timeLabel(favorite.snapshot_at) }}</p>
            </div>
            <div class="me-actions">
              <RouterLink v-if="chooseDefault(favorite) && favorite.state !== 'select_epoch'" class="btn primary" :to="chooseDefault(favorite)!">查看{{ favorite.kind === 'city' ? '城市' : '站点' }}详情</RouterLink>
              <button v-else-if="hasExactRecord(favorite)" class="btn primary" type="button" @click="openExact(favorite)">查看原产品记录</button>
              <button class="btn" type="button" :disabled="actionBusy" @click="remove(favorite.id)">取消关注</button>
            </div>
          </div>
          <p v-if="favorite.state === 'no_data'" class="me-muted">当前没有获准的有效摘要。</p>
          <p v-if="favorite.state === 'select_epoch'" class="me-muted">没有可作为当前默认的站址期，请从获准站址期中选择。</p>
          <details v-if="favorite.epochs?.length" :open="favorite.state === 'select_epoch'" class="me-details">
            <summary>获准站址期</summary>
            <ul class="me-epochs">
              <li v-for="epoch in favorite.epochs" :key="epoch.site_epoch">
                <div>
                  <h4>{{ epoch.site_name || epoch.name || `站址期 ${epoch.site_epoch}` }}</h4>
                  <p>{{ epoch.status === 'current' ? '当前' : epoch.status === 'historical' ? '历史' : '状态未披露' }}<template v-if="epoch.period_start"> · {{ timeLabel(epoch.period_start) }} → {{ epoch.period_end ? timeLabel(epoch.period_end) : '至今' }}</template></p>
                </div>
                <RouterLink v-if="epochRoute(favorite, epoch)" class="btn" :to="epochRoute(favorite, epoch)!">查看此站址期</RouterLink>
              </li>
            </ul>
          </details>
          <div v-if="favorite.results.length" class="me-readings" aria-label="最近获准摘要">
            <div v-for="reading in favorite.results" :key="reading.revision" class="me-reading">
              <b>{{ reading.indicator }} · {{ readingValue(reading) }}</b>
              <span><time :datetime="reading.observed_at">{{ timeLabel(reading.observed_at) }}</time> · {{ labels[reading.quality] ?? reading.quality }}</span>
              <span>{{ reading.window_definition }} · {{ reading.standard_series }}</span>
            </div>
          </div>
          <div v-if="previewId === favorite.id" class="me-details">
            <div class="me-entry-head"><h4>原产品记录</h4><button class="btn" type="button" @click="clearPreview">收起记录</button></div>
            <OwnedState :busy="detail.busy.value" :error="detail.error.value?.message" :empty="detail.data.value !== null && previewRows.length === 0" empty-text="当前没有可查看的记录。" @retry="openExact(favorite)" />
            <ul v-if="previewRows.length" class="me-epochs">
              <li v-for="reading in previewRows" :key="`${reading.observed_at}/${reading.revision}`">
                <span>{{ timeLabel(reading.observed_at) }} · {{ reading.indicator || 'AQI' }}</span><b>{{ readingValue(reading) }}</b>
              </li>
            </ul>
          </div>
        </article>
      </li>
    </ul>
    <nav v-if="cursor || data?.page.next_cursor" class="me-actions me-pagination" aria-label="关注分页">
      <button v-if="cursor" class="btn" type="button" :disabled="busy || actionBusy" @click="reload()">返回第一页</button>
      <button v-if="data?.page.next_cursor" class="btn" type="button" :disabled="busy || actionBusy" @click="reload(data.page.next_cursor)">下一页</button>
    </nav>
  </section>
</template>
