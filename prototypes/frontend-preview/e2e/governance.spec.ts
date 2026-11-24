import { test, expect, ready } from './fixtures'

test('领取、保留、关闭均留存隔离；理由必选，取消恢复焦点',async({page})=>{
  await page.goto('/quality');await ready(page,'无序异值争议')
  const start=page.getByRole('button',{name:'领取事件',exact:true});await start.click();await expect(page.getByRole('dialog')).toContainText('预计工单状态');await page.getByRole('button',{name:'取消'}).click();await expect(start).toBeFocused()
  await start.click();await page.getByRole('button',{name:'确认提交'}).click();await expect(page.getByRole('alert')).toContainText('请选择处置理由');await expect(page.locator('#quality-reason')).toBeFocused()
  await page.getByLabel(/处置理由/).selectOption('claim_review');await page.getByRole('button',{name:'确认提交'}).click();await expect(page.getByTestId('quality-status')).toHaveText('审阅中');await expect(page.locator('.history-list')).toContainText('领取事件')
  await page.getByRole('button',{name:'保留隔离',exact:true}).click();await page.getByLabel(/处置理由/).selectOption('retain_isolation');await page.getByRole('button',{name:'确认提交'}).click();await expect(page.locator('.history-list')).toContainText('保留隔离');
  await page.getByRole('button',{name:'关闭事件',exact:true}).click();await expect(page.getByRole('dialog')).toContainText('数据隔离');await page.getByLabel(/处置理由/).selectOption('review_complete');await page.getByRole('button',{name:'确认提交'}).click();await expect(page.getByTestId('quality-status')).toHaveText('已处置');await expect(page.locator('.revision-panel')).toContainText('观测隔离中');await expect(page.locator('.revision-value strong')).toHaveText(['42','71'])
})

test('有来源证据的纠正、审阅权限、过期控制版本',async({page})=>{
  await page.goto('/quality');await ready(page,'无序异值争议')
  await page.getByTestId('quality-scenario').selectOption('readonly');await expect(page.getByRole('button',{name:'领取事件'})).toBeDisabled();await expect(page.locator('.action-panel')).toContainText('仅可审阅')
  await page.getByTestId('quality-scenario').selectOption('stale');await page.getByRole('button',{name:'领取事件'}).click();await page.getByLabel(/处置理由/).selectOption('claim_review');await page.getByRole('button',{name:'确认提交'}).click();await expect(page.getByRole('alert')).toContainText('版本');await page.getByRole('button',{name:'取消'}).click();await expect(page.getByTestId('quality-status')).toHaveText('待处理')
  await page.getByTestId('quality-scenario').selectOption('verified');const correction=page.getByRole('button',{name:'按来源证据解除隔离'});await correction.click();await page.getByLabel(/处置理由/).selectOption('verified_correction');await page.getByRole('button',{name:'确认提交'}).click();await expect(page.getByTestId('quality-status')).toHaveText('已处置');await expect(page.locator('.revision-panel')).toContainText('已解除隔离');await expect(page.locator('.revision-value strong').first()).toHaveText('39');await expect(page.locator('.version-bar')).toContainText('v176')
  await page.getByTestId('quality-scenario').selectOption('resolved');await expect(page.getByTestId('quality-status')).toHaveText('已处置');await expect(page.locator('.history-list')).toContainText('可信新版');await expect(page.getByRole('button',{name:'领取事件'})).toHaveCount(0)
})
