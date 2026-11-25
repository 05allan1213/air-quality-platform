<script setup lang="ts">
import { computed, defineAsyncComponent } from 'vue'
import {
  columnLabel, conclusionNames, grains, methods, metric, qualityNames, ratio, stamp, windowNames,
  type Bounds, type Result, type Support,
} from './analysisContract'

const AnalysisCharts = defineAsyncComponent(() => import('./AnalysisCharts.vue'))
const props = defineProps<{ result: Result }>()
const emit = defineEmits<{ drill: [request: Support, label: string, event: MouseEvent] }>()
const resultZone = computed(() => props.result.source_timezone)
const buckets = computed(() => props.result.buckets ?? [])
const monthBuckets = computed(() => props.result.grain === 'season' ? (props.result.months ?? []) : [])
const hasStatistics = computed(() => buckets.value.some(bucket => bucket.mean !== undefined))
const hasPair = computed(() => props.result.method !== 'statistics')
const hasVisuals = computed(() => Boolean(props.result.chart || props.result.distribution?.length || props.result.heatmap?.length || buckets.value.some(bucket => bucket.box)))
const seasonNames: Record<string, string> = { spring: '春季', summer: '夏季', autumn: '秋季', winter: '冬季' }
const label = (pos: number) => columnLabel(props.result, pos)
const interval = (bounds: Bounds) => `${stamp(bounds.start, resultZone.value)} 至 ${stamp(bounds.end, resultZone.value)}`
const conclusion = (state: string) => conclusionNames[state] ?? '结论不可用'
const pairedUnit = computed(() => props.result.columns[0]?.unit)
const totalRank = computed(() => props.result.ranking ?? props.result.partial_summary?.ranking)
const anomalyNames: Record<string, string> = {
  ok: '按四分位距识别，仅作核查线索。原记录保留。',
  insufficient_samples: '覆盖率或有效样本数不足，未识别候选异常。',
  zero_iqr: '四分位距为零，未识别候选异常。',
}
</script>

<template>
  <section class="panel analysis-section analysis-result" aria-labelledby="analysis-result-title">
    <div class="analysis-section-heading">
      <div><h2 id="analysis-result-title">分析结果</h2><p>{{ methods[result.method].label }} · {{ grains[result.grain] }} · {{ result.selection.source }} · 产品 #{{ result.selection.product }}</p></div>
      <span :class="['analysis-conclusion', result.conclusion.state === 'sufficient' ? 'status-success' : 'status-warning']" role="status">{{ conclusion(result.conclusion.state) }}</span>
    </div>
    <p class="analysis-context">{{ interval(result.requested_window) }}（结束时刻不含） · 源时区 {{ result.source_timezone }} · {{ windowNames[result.selection.window_definition] ?? result.selection.window_definition }} · {{ result.method_version }}</p>
    <p v-if="result.attribution?.length" class="analysis-context">来源归属 {{ result.attribution.join('；') }}</p>
    <p v-if="result.state === 'no_data'" class="status" role="status">该范围没有获准披露的观测记录。</p>
    <p v-if="result.whole_period_mean != null || result.partial_mean != null" class="analysis-highlight"><strong>{{ result.whole_period_mean != null ? '整期均值' : '获准区间均值' }}</strong> {{ metric(result.whole_period_mean ?? result.partial_mean) }} {{ result.columns[0]?.unit }}</p>

    <section class="analysis-result-section" aria-labelledby="analysis-coverage-title">
      <h3 id="analysis-coverage-title">样本与覆盖</h3>
      <p>E 为预期时槽，O 为到报时槽，V 为兼容有效时槽。— 表示服务未给出该项。</p>
      <div class="analysis-table-scroll" tabindex="0" role="region" aria-label="样本与覆盖表">
        <table class="analysis-table">
          <thead><tr><th scope="col">系列</th><th scope="col">E</th><th scope="col">O</th><th scope="col">V</th><th scope="col">到报 O/E</th><th scope="col">有效 V/E</th><th scope="col">分母</th><th scope="col">排除与计划外</th></tr></thead>
          <tbody>
            <tr v-for="column in result.columns" :key="column.pos">
              <th scope="row">{{ label(column.pos) }}<small>{{ column.unit }} · {{ column.standard_series }}</small></th>
              <td>{{ metric(column.E) }}</td><td>{{ column.O }}</td><td>{{ column.V }}</td><td>{{ ratio(column.received_ratio) }}</td><td>{{ ratio(column.valid_ratio) }}</td>
              <td>{{ column.denominator_state === 'known' ? '已知' : conclusion(column.denominator_state) }}</td>
              <td><span v-for="(count, reason) in column.exclusions" v-show="count" :key="reason" class="analysis-exclusion">{{ qualityNames[reason] ?? reason }} {{ count }}</span><span v-if="column.outside_schedule" class="analysis-exclusion">计划外 {{ column.outside_schedule }}</span><span v-if="!column.outside_schedule && !Object.values(column.exclusions).some(Boolean)">—</span></td>
            </tr>
          </tbody>
        </table>
      </div>
      <details class="analysis-disclosure"><summary>查看时槽依据</summary>
        <dl class="analysis-windows">
          <div v-for="column in result.columns" :key="column.pos">
            <dt>{{ label(column.pos) }}</dt>
            <dd v-for="window in column.expected_windows" :key="window.start">预期 {{ interval(window) }}</dd>
            <dd v-for="window in column.unknown_windows" :key="window.start">预期未知 {{ interval(window) }}</dd>
            <dd v-if="!column.expected_windows.length && !column.unknown_windows.length">无预期窗口</dd>
          </div>
          <div v-if="result.expected_common_windows?.length"><dt>共同预期窗口</dt><dd v-for="window in result.expected_common_windows" :key="window.start">{{ interval(window) }}</dd></div>
        </dl>
      </details>
    </section>

    <section v-if="hasPair" class="analysis-result-section" aria-labelledby="analysis-pair-title">
      <div class="analysis-section-heading">
        <div><h3 id="analysis-pair-title">共同样本</h3><p>配对以相同观测时刻为准。<template v-if="result.method === 'compare'">差值方向为前两列 A − B。</template></p></div>
        <button v-if="result.chart && result.paired_drilldown" type="button" class="btn" @click="emit('drill', result.paired_drilldown, '共同样本', $event)">查看配对明细</button>
      </div>
      <dl class="analysis-metrics">
        <div><dt>共同预期 E共</dt><dd>{{ metric(result.E_common) }}</dd></div>
        <div><dt>共同有效 P</dt><dd>{{ metric(result.P) }}</dd></div>
        <div><dt>配对覆盖 P/E共</dt><dd>{{ ratio(result.paired_ratio) }}</dd></div>
        <div><dt>实际配对数</dt><dd>{{ metric(result.actual_pairs) }}</dd></div>
        <div v-if="result.method === 'pearson'"><dt>Pearson r</dt><dd>{{ metric(result.pearson) }}</dd></div>
        <div v-if="result.method === 'compare' && result.mean_difference != null"><dt>整期共同样本均值差</dt><dd>{{ metric(result.mean_difference) }} {{ pairedUnit }}</dd></div>
        <div v-if="result.partial_summary?.mean_difference != null"><dt>获准共同区间均值差</dt><dd>{{ metric(result.partial_summary.mean_difference) }} {{ pairedUnit }}</dd></div>
      </dl>
      <p v-if="result.method === 'pearson'">Pearson 为探索性关联。季节性、自相关和缺测可能影响结果。配对数至少 30，且两列方差须非零。</p>
      <p v-if="result.method === 'compare' && !result.ranking">当前条件无整期排名。有效配对与各列覆盖可独立核查。</p>
      <div v-if="totalRank?.length" class="analysis-table-scroll" tabindex="0" role="region" :aria-label="result.ranking ? '整期共同样本排名' : '获准共同区间排名'">
        <table class="analysis-table"><caption>{{ result.ranking ? '整期共同样本排名' : '获准共同区间排名' }}</caption><thead><tr><th scope="col">名次</th><th scope="col">系列</th><th scope="col">有效均值</th></tr></thead><tbody><tr v-for="row in totalRank" :key="row.pos"><td>{{ row.rank }}</td><th scope="row">{{ label(row.pos) }}</th><td>{{ metric(row.mean) }} {{ pairedUnit }}</td></tr></tbody></table>
      </div>
      <details v-if="result.paired_points?.length" class="analysis-disclosure">
        <summary>查看逐时差值和变幅</summary>
        <p>变幅基准为 B，基准为零时留空。</p>
        <div class="analysis-table-scroll" tabindex="0" role="region" aria-label="逐时差值和变幅表"><table class="analysis-table"><thead><tr><th scope="col">观测时间</th><th scope="col">差值 {{ pairedUnit }}</th><th scope="col">变幅（%）</th></tr></thead><tbody><tr v-for="point in result.paired_points" :key="point.at"><th scope="row">{{ stamp(point.at, resultZone) }}</th><td>{{ metric(point.difference) }}</td><td>{{ metric(point.percent_change) }}</td></tr></tbody></table></div>
      </details>
    </section>

    <AnalysisCharts v-if="hasVisuals" :key="result.input_version" :result="result" @drill="(support, name, event) => emit('drill', support, name, event)" />
    <p v-if="!result.chart" class="status">当前结果未提供原始序列和支撑记录。可查看获准的派生统计。</p>

    <section v-if="buckets.length" class="analysis-result-section" aria-labelledby="analysis-buckets-title">
      <h3 id="analysis-buckets-title">{{ grains[result.grain] }}统计与支撑记录</h3>
      <p>统计按有效样本计算。<template v-if="result.chart">到报明细可包含缺测、删失等记录。</template></p>
      <div class="analysis-table-scroll" tabindex="0" role="region" aria-label="统计分桶表">
        <table class="analysis-table">
          <thead><tr><th scope="col">来源地方时段</th><th scope="col">系列</th><th scope="col">E / O / V</th><th scope="col">解释状态</th><template v-if="hasStatistics"><th scope="col">均值 / 中位数</th><th scope="col">最小 / 最大</th></template><th v-if="result.chart" scope="col">查看样本</th></tr></thead>
          <tbody>
            <tr v-for="bucket in buckets" :key="`${bucket.pos}:${bucket.start}`">
              <th scope="row"><span v-if="bucket.season">{{ bucket.season_year }} 年{{ seasonNames[bucket.season] }}<br /></span>{{ interval(bucket) }}</th>
              <td>{{ label(bucket.pos) }}</td><td>{{ metric(bucket.E) }} / {{ bucket.O }} / {{ bucket.V }}</td>
              <td>{{ conclusion(bucket.conclusion.state) }}<small v-if="bucket.complete_month !== undefined">{{ bucket.complete_month ? '完整月窗口' : '非完整月窗口' }} · {{ bucket.all_received ? '全部到报' : '未证实全部到报' }} · {{ bucket.all_effective ? '全部有效' : '未证实全部有效' }}</small><small v-if="bucket.complete_season !== undefined">{{ bucket.complete_season ? '完整季窗口' : '非完整季窗口' }}</small></td>
              <template v-if="hasStatistics"><td>{{ metric(bucket.mean) }} / {{ metric(bucket.median) }}</td><td>{{ metric(bucket.minimum) }} / {{ metric(bucket.maximum) }}<small v-if="bucket.minimum_at != null">最小值 {{ stamp(bucket.minimum_at, resultZone) }}<template v-if="bucket.minimum_ties">（{{ bucket.minimum_ties }} 次）</template></small><small v-if="bucket.maximum_at != null">最大值 {{ stamp(bucket.maximum_at, resultZone) }}<template v-if="bucket.maximum_ties">（{{ bucket.maximum_ties }} 次）</template></small></td></template>
              <td v-if="result.chart"><div class="analysis-buttons"><button type="button" class="btn" @click="emit('drill', bucket.drilldown, `${label(bucket.pos)} · ${interval(bucket)}`, $event)">有效样本</button><button type="button" class="btn" @click="emit('drill', bucket.observed_drilldown, `${label(bucket.pos)} · ${interval(bucket)}`, $event)">到报记录</button></div></td>
            </tr>
          </tbody>
        </table>
      </div>
      <details v-if="monthBuckets.length" class="analysis-disclosure">
        <summary>查看分月统计</summary>
        <div class="analysis-table-scroll" tabindex="0" role="region" aria-label="分月统计表"><table class="analysis-table"><thead><tr><th scope="col">来源地方时段</th><th scope="col">E / O / V</th><th scope="col">均值</th><th scope="col">解释状态</th><th v-if="result.chart" scope="col">支撑记录</th></tr></thead><tbody><tr v-for="bucket in monthBuckets" :key="bucket.start"><th scope="row">{{ interval(bucket) }}</th><td>{{ metric(bucket.E) }} / {{ bucket.O }} / {{ bucket.V }}</td><td>{{ metric(bucket.mean) }}</td><td>{{ conclusion(bucket.conclusion.state) }}</td><td v-if="result.chart"><button type="button" class="btn" @click="emit('drill', bucket.drilldown, interval(bucket), $event)">有效样本</button></td></tr></tbody></table></div>
      </details>
    </section>

    <section v-if="result.anomalies" class="analysis-result-section" aria-labelledby="analysis-anomaly-title">
      <h3 id="analysis-anomaly-title">候选异常<template v-if="result.anomalies.count != null">（{{ result.anomalies.count }}）</template></h3>
      <p>{{ anomalyNames[result.anomalies.state] ?? '当前结果未提供候选异常。' }}</p>
      <details v-if="result.anomalies.candidates?.length" class="analysis-disclosure">
        <summary>查看获准的候选记录</summary>
        <p v-if="result.anomalies.truncated">候选详情已达响应上限。</p>
        <div class="analysis-table-scroll" tabindex="0" role="region" aria-label="候选异常表"><table class="analysis-table"><thead><tr><th scope="col">观测时间</th><th scope="col">数值</th><th scope="col">前后时段</th></tr></thead><tbody><tr v-for="candidate in result.anomalies.candidates" :key="candidate.key_id"><th scope="row">{{ stamp(candidate.at, resultZone) }}</th><td>{{ metric(candidate.value) }} {{ result.columns[0]?.unit }}</td><td><button type="button" class="btn" @click="emit('drill', candidate.drilldown, `异常候选 · ${stamp(candidate.at, resultZone)}`, $event)">查看支撑样本</button></td></tr></tbody></table></div>
      </details>
    </section>
  </section>
</template>

<style>
.analysis-result-section { min-width: 0; margin-top: 18px; padding-top: 15px; border-top: 1px solid var(--aq-border); }
.analysis-conclusion { padding: 6px 10px; border-radius: 6px; font-weight: 600; font-size: .875rem; }
.analysis-context { overflow-wrap: anywhere; font-size: .875rem; }
.analysis-highlight { padding: 12px; border-radius: 8px; background: var(--aq-content); }
.analysis-result small { display: block; margin-top: 4px; color: var(--aq-secondary); }
.analysis-coverage { min-width: 0; }
.analysis-exclusion { display: inline-block; margin-inline-end: 10px; }
.analysis-disclosure { margin-top: 12px; }
.analysis-disclosure summary { cursor: pointer; color: var(--aq-link); padding: 7px 0; text-decoration: underline; text-underline-offset: 3px; }
.analysis-windows { display: grid; gap: 12px; padding: 10px; margin: 0; border-radius: 8px; background: var(--aq-content); }
.analysis-windows dt { font-weight: 600; }
.analysis-windows dd { margin: 3px 0; }
.analysis-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 130px), 1fr)); gap: 0; border: 1px solid var(--aq-border); border-radius: 8px; background: var(--aq-content); }
.analysis-metrics > div { padding: 10px 12px; min-width: 0; border-inline-end: 1px solid var(--aq-border); }
.analysis-metrics > div:last-child { border-inline-end: 0; }
.analysis-metrics dt { color: var(--aq-secondary); font-size: .85rem; }
.analysis-metrics dd { margin: 3px 0 0; font-size: 1.28rem; font-weight: 630; font-variant-numeric: tabular-nums; }
.analysis-result caption { text-align: left; padding: 8px 11px; font-weight: 630; }
</style>
