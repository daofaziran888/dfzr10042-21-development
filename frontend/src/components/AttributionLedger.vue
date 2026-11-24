<template>
  <section class="ledger-panel" data-testid="attribution-ledger">
    <header class="panel-head">
      <div>
        <h3>逐笔归因贡献</h3>
        <p class="page-desc">
          每笔有效订单只落在自己的归因窗口内，列出关联渠道触点与权重；每笔贡献合计恒为 1。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="ledger-demo" @click="loadDemo">载入两笔订单示例</button>
        <button class="btn" type="button" data-testid="ledger-refresh" @click="reload">刷新</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">有效转化</span>
        <strong class="stat-value" data-testid="ledger-conversions">{{ conversions }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">明细订单</span>
        <strong class="stat-value">{{ rows.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">领取版本</span>
        <strong class="stat-value" data-testid="ledger-version">{{ versionLabel }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">窗口</span>
        <strong class="stat-value">{{ windowMinutes }} 分钟</strong>
      </article>
    </div>

    <div class="analysis-form">
      <label>
        <span>订单编号</span>
        <input v-model="orderForm.order_id" data-testid="order-id" placeholder="ORDER-0001" />
      </label>
      <label>
        <span>用户编号</span>
        <input v-model="orderForm.user_id" data-testid="order-user" placeholder="u1" />
      </label>
      <label>
        <span>下单时间</span>
        <input v-model="orderForm.order_time" data-testid="order-time" placeholder="2026-11-24T12:00:00" />
      </label>
      <button class="btn" type="button" data-testid="submit-order" @click="submitOrderForm">登记订单</button>
      <label>
        <span>触点渠道</span>
        <input v-model="touchForm.channel" data-testid="touch-channel" placeholder="信息流广告" />
      </label>
      <label>
        <span>触点用户</span>
        <input v-model="touchForm.user_id" data-testid="touch-user" placeholder="u1" />
      </label>
      <label>
        <span>触点时间</span>
        <input v-model="touchForm.timestamp" data-testid="touch-time" placeholder="2026-11-24T11:45:00" />
      </label>
      <button class="btn" type="button" data-testid="submit-touch" @click="submitTouchForm">补入触点</button>
    </div>

    <div class="analysis-form">
      <label>
        <span>归因窗口（分钟）</span>
        <input v-model="recomputeForm.window_minutes" data-testid="window-minutes" placeholder="120" />
      </label>
      <label>
        <span>归因算法</span>
        <select v-model="recomputeForm.algorithm" data-testid="algorithm">
          <option value="linear">线性</option>
          <option value="first_touch">首次触点</option>
          <option value="last_touch">末次触点</option>
          <option value="time_decay">时间衰减</option>
          <option value="position_based">位置归因</option>
        </select>
      </label>
      <button class="btn primary" type="button" data-testid="recompute" @click="runRecompute">归因重算</button>
      <button class="btn" type="button" data-testid="confirm-version" @click="runConfirm">确认当前版本</button>
      <span v-if="message" class="hint-text" data-testid="ledger-message">{{ message }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </div>

    <table class="data-table" data-testid="contribution-table">
      <thead>
        <tr>
          <th>订单编号</th>
          <th>用户</th>
          <th>下单时间</th>
          <th>归因窗口</th>
          <th>关联触点</th>
          <th>渠道权重</th>
          <th>贡献合计</th>
          <th>明细版本</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="`${row.version_label}-${row.order_id}`"
          :data-testid="`contribution-${row.order_id}`"
        >
          <td>{{ row.order_id }}</td>
          <td>{{ row.user_id }}</td>
          <td>{{ row.order_time }}</td>
          <td class="cell-window">{{ row.window.start }} ~ {{ row.window.end }}（{{ row.window.minutes }} 分钟）</td>
          <td>
            <span
              v-for="touch in row.touchpoints"
              :key="touch.touchpoint_id"
              class="touch-chip"
              :data-testid="`touch-${row.order_id}-${touch.touchpoint_id}`"
            >
              {{ touch.channel }} · {{ touch.timestamp }} · {{ percent(touch.weight) }}
            </span>
            <span v-if="!row.touchpoints.length" class="muted-text">无触点</span>
          </td>
          <td>
            <span
              v-for="(weight, channel) in row.channel_weights"
              :key="channel"
              class="weight-chip"
              :data-testid="`weight-${row.order_id}-${channel}`"
            >
              {{ channel }} {{ percent(weight) }}
            </span>
          </td>
          <td>{{ row.total_weight.toFixed(2) }}</td>
          <td>
            {{ row.version_label }}
            <span :class="row.confirmed ? 'badge ok' : 'badge'">{{ row.version_status_label ?? row.version_status }}</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="8" class="empty-state">
            暂无逐笔归因明细，可先登记订单与触点后重算，或载入示例数据
          </td>
        </tr>
      </tbody>
    </table>

    <div class="version-strip">
      <strong>归因版本</strong>
      <button
        v-for="version in versions"
        :key="version.version_id"
        class="version-pill"
        :class="{ active: String(version.version_id) === selectedVersion }"
        type="button"
        :data-testid="`version-${version.version_id}`"
        @click="selectVersion(version)"
      >
        {{ version.version_label }} · {{ version.status_label ?? version.status }} · {{ version.window_minutes }} 分钟 ·
        {{ version.conversion_count }} 笔
      </button>
      <span v-if="!versions.length" class="muted-text">尚未生成版本</span>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import {
  confirmVersion,
  loadContributions,
  loadDemoLedger,
  loadVersions,
  recomputeAttribution,
  submitOrder,
  submitTouchpoint,
} from '@/api/backend-service'
import type { AttributionVersion, ContributionRow } from '@/data/types'

const rows = ref<ContributionRow[]>([])
const versions = ref<AttributionVersion[]>([])
const conversions = ref(0)
const selectedVersion = ref('')
const message = ref('')
const errorMessage = ref('')

const orderForm = reactive({ order_id: '', user_id: '', order_time: '' })
const touchForm = reactive({ channel: '', user_id: '', timestamp: '' })
const recomputeForm = reactive({ window_minutes: '120', algorithm: 'linear' })

const versionLabel = computed(() => {
  const version = versions.value.find((item) => String(item.version_id) === selectedVersion.value)
  return version ? version.version_label : '—'
})
const windowMinutes = computed(() => {
  const version = versions.value.find((item) => String(item.version_id) === selectedVersion.value)
  return version ? version.window_minutes : 120
})

function percent(value: number): string {
  return `${(Number(value || 0) * 100).toFixed(2).replace(/\.?0+$/, '')}%`
}

async function reload() {
  errorMessage.value = ''
  try {
    const params: Record<string, string> = {}
    if (selectedVersion.value) params.version_id = selectedVersion.value
    const [contributionPayload, versionPayload] = await Promise.all([
      loadContributions(params),
      loadVersions(),
    ])
    rows.value = contributionPayload.items
    conversions.value = contributionPayload.conversions
    versions.value = versionPayload.items
    if (!selectedVersion.value && versionPayload.items.length) {
      const active = versionPayload.items.find((item) => item.active) ?? versionPayload.items[versionPayload.items.length - 1]
      selectedVersion.value = String(active.version_id)
      await reload()
      return
    }
    if (!versionPayload.items.length) conversions.value = contributionPayload.conversions
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '归因明细读取失败'
  }
}

function selectVersion(version: AttributionVersion) {
  selectedVersion.value = String(version.version_id)
  void reload()
}

async function submitOrderForm() {
  errorMessage.value = ''
  message.value = ''
  try {
    const result = await submitOrder({ ...orderForm })
    message.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '订单登记失败'
  }
}

async function submitTouchForm() {
  errorMessage.value = ''
  message.value = ''
  try {
    const result = await submitTouchpoint({ ...touchForm })
    message.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '触点登记失败'
  }
}

async function runRecompute() {
  errorMessage.value = ''
  message.value = ''
  try {
    const version = await recomputePhase(false)
    message.value = `已生成 ${version.version_label}（${version.conversion_count} 笔有效转化）`
    selectedVersion.value = String(version.version_id)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '归因重算失败'
  }
}

async function recomputePhase(confirm: boolean): Promise<AttributionVersion> {
  return await recomputeAttribution({
    window_minutes: recomputeForm.window_minutes,
    algorithm: recomputeForm.algorithm,
    confirm: String(confirm),
  })
}

async function runConfirm() {
  errorMessage.value = ''
  message.value = ''
  try {
    const current = versions.value.find((item) => String(item.version_id) === selectedVersion.value)
    if (!current) throw new Error('请先重算生成归因明细')
    const version = await confirmVersion(current.version_id)
    message.value = `${version.version_label} 已确认，渠道复盘将按该明细取数`
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '版本确认失败'
  }
}

async function loadDemo() {
  errorMessage.value = ''
  message.value = ''
  try {
    const result = await loadDemoLedger()
    message.value = `示例数据已载入并确认 ${result.version.version_label}`
    selectedVersion.value = String(result.version.version_id)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '示例数据载入失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.ledger-panel { margin-top: 20px; }
.panel-head { display: flex; justify-content: space-between; align-items: flex-start; }
.panel-head h3 { margin: 0 0 4px; }
.touch-chip, .weight-chip { display: inline-block; margin: 2px 4px 2px 0; padding: 2px 6px; border-radius: 4px; font-size: 12px; }
.touch-chip { background: #eef4ff; color: #1f4ea1; }
.weight-chip { background: #eefaf0; color: #17663a; }
.badge { margin-left: 4px; padding: 1px 6px; border-radius: 10px; background: #f1f5f9; color: #475569; font-size: 11px; }
.badge.ok { background: #dcfce7; color: #166534; }
.version-strip { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 12px; font-size: 12px; }
.version-pill { border: 1px solid var(--border); background: #fff; border-radius: 999px; padding: 4px 10px; cursor: pointer; font-size: 12px; }
.version-pill.active { border-color: var(--brand); color: var(--brand); }
.hint-text { color: #17663a; font-size: 12px; }
.muted-text { color: var(--muted); }
.cell-window { white-space: nowrap; font-size: 12px; }
</style>
