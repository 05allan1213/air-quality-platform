<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  label: string
  numerator?: number | null
  denominator?: number | null
  note: string
}>()

// Draw a proportion from the two disclosed counts. The readout is the exact pair.
const arc = computed(() => {
  const { numerator, denominator } = props
  if (numerator == null || denominator == null || denominator <= 0
    || numerator < 0 || numerator > denominator) return null
  return `${100 * numerator / denominator} 100`
})
</script>

<template>
  <div class="home-ratio" role="group" :aria-label="label">
    <div class="ratio-caption">
      <span aria-hidden="true">{{ label }}</span>
      <small>{{ note }}</small>
    </div>
    <div class="ratio-ring">
      <svg width="88" height="88" viewBox="0 0 88 88" aria-hidden="true">
        <circle cx="44" cy="44" r="40" class="ratio-track" />
        <circle v-if="arc" cx="44" cy="44" r="40" class="ratio-arc" pathLength="100" :stroke-dasharray="arc" transform="rotate(-90 44 44)" />
      </svg>
      <strong class="ratio-value">{{ numerator ?? '—' }} / {{ denominator ?? '—' }}</strong>
    </div>
  </div>
</template>

<style scoped>
.summary-strip > .home-ratio {
  grid-template-columns: minmax(0, 1fr) auto;
  align-content: center;
  gap: 8px;
}
.ratio-caption { display: grid; gap: 4px; min-width: 0; }
.ratio-caption span { color: var(--aq-secondary); font-size: 12px; }
.ratio-caption small { color: var(--aq-muted); font-size: 11px; }
.ratio-ring {
  position: relative;
  display: grid;
  place-items: center;
  inline-size: min(100%, 5.5rem);
  aspect-ratio: 1;
}
.ratio-ring svg { position: absolute; inset: 0; inline-size: 100%; block-size: 100%; fill: none; stroke-width: 4; }
.ratio-track { stroke: var(--aq-control-border); }
.ratio-arc { stroke: var(--aq-brand); }
.ratio-ring .ratio-value {
  z-index: 0;
  grid-row: auto;
  max-inline-size: 80%;
  color: var(--aq-text);
  font-size: .875rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.15;
  text-align: center;
  overflow-wrap: anywhere;
}
@media (max-width: 1100px) {
  .summary-strip > .home-ratio { grid-template-columns: minmax(0, 1fr); justify-items: center; text-align: center; }
}
@media (forced-colors: active) {
  .ratio-track { stroke: GrayText; }
  .ratio-arc { stroke: Highlight; }
}
</style>
