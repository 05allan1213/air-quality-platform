// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import CityLedger from './CityLedger.vue'
import { usePreviewStore } from '../shared/preview'

describe('历史窗口表单', () => {
  beforeEach(() => setActivePinia(createPinia()))
  it('查询返回前切换演示情境会丢弃旧请求', async () => {
    const wrapper = mount(CityLedger, { global: { stubs: { ElDialog:true } } })
    await wrapper.get('form').trigger('submit')
    expect(wrapper.get('form button').text()).toContain('查询中')
    usePreviewStore().cityState = 'empty'
    await nextTick()
    await new Promise(r => setTimeout(r, 220)); await flushPromises()
    expect(wrapper.find('tbody').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('共 24 条记录')
    wrapper.unmount()
  })
})
