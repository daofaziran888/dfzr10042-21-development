/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

/** 归因逐笔明细：一笔订单在窗口内命中哪些渠道触点、各占多少权重。 */
export type ContributionTouchpoint = {
  touchpoint_id: string
  user_id: string
  channel: string
  timestamp: string
  source?: string
  weight: number
}

export type ContributionRow = {
  order_id: string
  user_id: string
  order_time: string
  amount: number | string
  status: string
  valid: boolean
  window: { start: string; end: string; minutes: number }
  touchpoints: ContributionTouchpoint[]
  channel_weights: Record<string, number>
  total_weight: number
  version_id: number
  version_label: string
  version_status: string
  version_status_label?: string
  confirmed: boolean
  window_minutes: number
}

export type AttributionVersion = {
  version_id: number
  version_label: string
  status: string
  active: boolean
  algorithm: string
  window_minutes: number
  window: { minutes: number; lookback_only: boolean }
  status_label?: string
  created_at: string
  confirmed_at: string | null
  confirmed_by: string | null
  confirmed: boolean
  note: string
  conversion_count: number
  changed_order_ids: string[]
  checksum: string
  contributions?: ContributionRow[]
}

export type VersionList = { items: AttributionVersion[]; total: number }

export type LedgerOrder = {
  order_id: string
  user_id: string
  order_time: string
  amount: number | string
  status: string
  valid: boolean
}

export type LedgerTouchpoint = ContributionTouchpoint

export type ReviewClaim = {
  order_id: string
  channel: string
  version_id: number
  version_label: string
  status: string
}

export type ReviewConflictRow = {
  order_id: string
  channel: string
  reason: string
  claimed_by_channel: string
  claimed_version_id: number
  claimed_version_label: string
  requested_version_id: number
  requested_version_label: string
}

export type ChannelReview = {
  review_id: number
  review_label: string
  batch_id: string
  channel: string
  status: string
  claimed_at: string
  actor: string
  attribution_version: {
    version_id: number
    version_label: string
    status: string
    confirmed_at: string | null
    window_minutes: number
    window: { minutes: number; lookback_only: boolean }
    algorithm: string
    checksum: string
  }
  version_verified: boolean
  version_ids_used: number[]
  conversion_order_ids: string[]
  requested_order_ids: string[]
  conversion_count: number
  population: { user_ids: string[]; user_count: number }
  conversion_event_range: { start: string | null; end: string | null }
  orders: {
    order_id: string
    user_id: string
    order_time: string
    window: { start: string; end: string; minutes: number }
    channel_weights: Record<string, number>
    touchpoints: ContributionTouchpoint[]
  }[]
  touchpoint_snapshot: ContributionTouchpoint[]
  steps: string[]
  step_counts: number[]
  cumulative_counts: number[]
  step_rates: number[]
  total_conversion_rate: number
  claims: ReviewClaim[]
  conflicts: ReviewConflictRow[]
  partial: boolean
}
