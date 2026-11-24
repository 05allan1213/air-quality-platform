import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

export const cityStates = {
  normal: '数据完整', partial: '部分缺测', stale: '时效未知', empty: '窗口无数据', error: '读取失败',
} as const
export type CityState = keyof typeof cityStates
export function isCityState(value: unknown): value is CityState {
  return typeof value === 'string' && Object.hasOwn(cityStates, value)
}
export const usePreviewStore = defineStore('preview', () => {
  const role = ref<'operator' | 'reader'>('operator')
  const canGovern = computed(() => role.value === 'operator')
  const cityState = ref<CityState>('normal')
  const favorites = ref({ operator: false, reader: false })
  const favorite = computed({ get: () => favorites.value[role.value], set: value => { favorites.value[role.value] = value } })
  const toast = ref('')
  let timer: ReturnType<typeof setTimeout> | undefined
  function notify(text: string) {
    clearTimeout(timer)
    toast.value = text
    timer = setTimeout(() => { toast.value = '' }, 3600)
  }
  return { role, canGovern, cityState, favorite, toast, notify }
})
