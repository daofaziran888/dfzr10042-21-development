import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  AttributionVersion,
  ChannelReview,
  ContributionRow,
  EntryRow,
  LedgerOrder,
  LedgerTouchpoint,
  OverviewResult,
  PageResult,
  VersionList,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ============================================================
// 归因逐笔明细
// ============================================================

async function readJson<T>(path: string, init?: RequestInit, message = '归因数据读取失败'): Promise<T> {
  const response = await request(path, init)
  if (!response.ok) {
    let detail = message
    try {
      const payload = await response.json()
      if (typeof payload.detail === 'string') detail = payload.detail
    } catch {
      /* 保留默认提示 */
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

export async function loadContributions(params: Record<string, string> = {}): Promise<{
  items: ContributionRow[]
  total: number
  conversions: number
  total_weight: number
}> {
  const query = new URLSearchParams(params).toString()
  return await readJson(`/api/attribution_model/contributions?${query}`, undefined, '归因贡献读取失败')
}

export async function loadVersions(): Promise<VersionList> {
  return await readJson('/api/attribution_model/versions', undefined, '归因版本读取失败')
}

export async function loadOrders(): Promise<{ items: LedgerOrder[]; total: number; conversions: number }> {
  return await readJson('/api/attribution_model/orders', undefined, '订单明细读取失败')
}

export async function loadTouchpoints(): Promise<{ items: LedgerTouchpoint[]; total: number }> {
  return await readJson('/api/attribution_model/touchpoints', undefined, '触点明细读取失败')
}

export async function submitOrder(values: Record<string, string>): Promise<ActionResult> {
  return await readJson('/api/attribution_model/orders', {
    method: 'POST',
    body: JSON.stringify(values),
  }, '订单登记失败')
}

export async function submitTouchpoint(values: Record<string, string>): Promise<ActionResult> {
  return await readJson('/api/attribution_model/touchpoints', {
    method: 'POST',
    body: JSON.stringify(values),
  }, '触点登记失败')
}

export async function recomputeAttribution(values: Record<string, string>): Promise<AttributionVersion> {
  return await readJson('/api/attribution_model/recompute', {
    method: 'POST',
    body: JSON.stringify({
      window_minutes: Number(values.window_minutes || 120),
      algorithm: values.algorithm || 'linear',
      confirm: values.confirm === 'true',
      note: values.note || '',
    }),
  }, '归因重算失败')
}

export async function confirmVersion(versionId: number): Promise<AttributionVersion> {
  return await readJson('/api/attribution_model/confirm', {
    method: 'POST',
    body: JSON.stringify({ version_id: versionId }),
  }, '归因版本确认失败')
}

export async function loadReviews(): Promise<{ items: ChannelReview[]; total: number }> {
  return await readJson('/api/funnel_def/reviews', undefined, '渠道复盘读取失败')
}

export async function createChannelReview(values: Record<string, string>): Promise<ChannelReview> {
  return await readJson('/api/funnel_def/reviews', {
    method: 'POST',
    body: JSON.stringify({
      channel: values.channel,
      version_id: values.version_id,
      batch_id: values.batch_id || undefined,
      steps: values.steps ? values.steps.split(/[,，、>→|]/).map((item) => item.trim()).filter(Boolean) : undefined,
      strict: values.strict === 'true',
    }),
  }, '渠道复盘失败')
}

export async function loadClaims(batchId?: string): Promise<{
  batch_id?: string
  claims?: { order_id: string; version_id: number; version_label: string; channel: string; channels: string[] }[]
  batches: Record<string, { order_id: string; version_id: number; version_label: string; channel: string; channels: string[] }[]>
  total: number
}> {
  const query = batchId ? `?batch_id=${encodeURIComponent(batchId)}` : ''
  return await readJson(`/api/funnel_def/claims${query}`, undefined, '批次领取读取失败')
}

export async function loadDemoLedger(): Promise<{ ok: boolean; version: AttributionVersion }> {
  return await readJson('/api/attribution_model/ledger/demo', undefined, '演示数据载入失败')
}
