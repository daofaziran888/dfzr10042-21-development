<template>
  <section class="channel-review" data-testid="channel-review">
    <header class="panel-head">
      <div>
        <h3>渠道转化复盘</h3>
        <p class="page-desc">
          按一份已确认的归因明细取数：先用所选渠道的转化圈定复盘人群和转化事件范围，再计算各步骤人数。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="review-claims" @click="showClaims">核对批次领取版本</button>
        <button class="btn" type="button" data-testid="review-refresh" @click="reload">刷新复盘记录</button>
      </div>
    </header>

    <div class="analysis-form">
      <label>
        <span>渠道</span>
        <select v-model="reviewForm.channel" data-testid="review-channel">
          <option value="">请选择渠道</option>
          <option v-for="channel in channels" :key="channel" :value="channel">{{ channel }}</option>
        </select>
      </label>
      <label>
        <span>归因明细版本（仅已确认）</span>
        <select v-model="reviewForm.version_id" data-testid="review-version">
          <option value="">当前生效版本</option>
          <option v-for="version in confirmedVersions" :key="version.version_id" :value="String(version.version_id)">
            {{ version.version_label }} · {{ version.window_minutes }} 分钟 · {{ version.conversion_count }} 笔
          </option>
        </select>
      </label>
      <label>
        <span>复盘批次（同批次同订单只能绑定一个版本）</span>
        <input v-model="reviewForm.batch_id" data-testid="review-batch" placeholder="留空自动分配" />
      </label>
      <label>
        <span>漏斗步骤（逗号分隔）</span>
        <input v-model="reviewForm.steps" data-testid="review-steps" placeholder="访问,加购,下单,支付" />
      </label>
      <button class="btn primary" type="button" data-testid="start-review" @click="startReview">开始复盘</button>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-if="claimsHint" class="hint-text" data-testid="review-claims-hint">{{ claimsHint }}</span>
    </div>

    <div v-if="current" class="review-result" data-testid="review-result">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">领取归因版本</span>
          <strong class="stat-value" data-testid="review-version-label">
            {{ current.attribution_version.version_label }}
            <span :class="current.version_verified ? 'badge ok' : 'badge warn'">
              {{ current.version_verified ? '校验通过' : '校验不一致' }}
            </span>
          </strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">归因窗口</span>
          <strong class="stat-value">{{ current.attribution_version.window_minutes }} 分钟（只看回看）</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">复盘人群</span>
          <strong class="stat-value" data-testid="review-population">{{ current.population.user_count }} 人</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">转化订单</span>
          <strong class="stat-value" data-testid="review-conversions">{{ current.conversion_count }} 笔</strong>
        </article>
      </div>

      <dl class="review-meta">
        <div>
          <dt>转化事件范围</dt>
          <dd data-testid="review-event-range">
            {{ current.conversion_event_range.start }} ~ {{ current.conversion_event_range.end }}
          </dd>
        </div>
        <div>
          <dt>复盘批次</dt>
          <dd>{{ current.batch_id }}</dd>
        </div>
        <div>
          <dt>领取校验</dt>
          <dd>{{ current.attribution_version.checksum.slice(0, 12) }}…（{{ current.status }}）</dd>
        </div>
      </dl>

      <table class="data-table" data-testid="review-funnel">
        <thead>
          <tr>
            <th>步骤</th>
            <th>步骤人数（渠道内触达）</th>
            <th>严格漏斗人数</th>
            <th>步骤转化率</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(step, index) in current.steps" :key="step" :data-testid="`review-step-${step}`">
            <td>{{ step }}</td>
            <td>{{ current.step_counts[index] }}</td>
            <td>{{ current.cumulative_counts[index] }}</td>
            <td>{{ percent(current.step_rates[index]) }}</td>
          </tr>
        </tbody>
      </table>
      <p class="muted-text">
        总转化率：{{ percent(current.total_conversion_rate) }}；复盘人群：
        {{ current.population.user_ids.join('、') || '无' }}
      </p>

      <div v-if="current.conflicts.length" class="conflict-box" data-testid="review-conflicts">
        <strong>领取冲突（{{ current.conflicts.length }} 笔订单未纳入本次复盘）</strong>
        <ul>
          <li v-for="conflict in current.conflicts" :key="conflict.order_id">
            订单 {{ conflict.order_id }} 已由 {{ conflict.claimed_by_channel }} 按
            {{ conflict.claimed_version_label }} 领取，本次请求 {{ conflict.requested_version_label }}。
          </li>
        </ul>
      </div>

      <details class="snapshot-box">
        <summary>本次领取的窗口与触点快照</summary>
        <table class="data-table">
          <thead>
            <tr>
              <th>订单</th>
              <th>归因窗口</th>
              <th>触点</th>
              <th>渠道权重</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="order in current.orders" :key="order.order_id">
              <td>{{ order.order_id }}</td>
              <td class="cell-window">{{ order.window.start }} ~ {{ order.window.end }}</td>
              <td>
                <span v-for="touch in order.touchpoints" :key="touch.touchpoint_id" class="touch-chip">
                  {{ touch.channel }} · {{ touch.timestamp }} · {{ percent(touch.weight) }}
                </span>
              </td>
              <td>
                <span v-for="(weight, channel) in order.channel_weights" :key="channel" class="weight-chip">
                  {{ channel }} {{ percent(weight) }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </details>
    </div>

    <table class="data-table history-table" data-testid="review-history">
      <thead>
        <tr>
          <th>复盘</th>
          <th>渠道</th>
          <th>批次</th>
          <th>领取版本</th>
          <th>窗口</th>
          <th>转化订单</th>
          <th>人群</th>
          <th>状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="review in reviews" :key="review.review_id" :data-testid="`review-${review.review_id}`">
          <td>{{ review.review_label }}</td>
          <td>{{ review.channel }}</td>
          <td>{{ review.batch_id }}</td>
          <td>{{ review.attribution_version.version_label }}</td>
          <td>{{ review.attribution_version.window_minutes }} 分钟</td>
          <td>{{ review.conversion_order_ids.join('、') || '—' }}</td>
          <td>{{ review.population.user_count }}</td>
          <td>
            {{ review.status }}
            <span v-if="review.conflicts.length" class="badge warn">冲突 {{ review.conflicts.length }}</span>
          </td>
        </tr>
        <tr v-if="!reviews.length">
          <td colspan="8" class="empty-state">暂无渠道复盘记录</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import {
  createChannelReview,
  loadClaims,
  loadContributions,
  loadReviews,
  loadVersions,
} from '@/api/backend-service'
import type { AttributionVersion, ChannelReview, ContributionRow } from '@/data/types'

const reviews = ref<ChannelReview[]>([])
const versions = ref<AttributionVersion[]>([])
const contributions = ref<ContributionRow[]>([])
const current = ref<ChannelReview | null>(null)
const errorMessage = ref('')
const claimsHint = ref('')

const reviewForm = reactive({
  channel: '',
  version_id: '',
  batch_id: '',
  steps: '访问,加购,下单,支付',
})

const confirmedVersions = computed(() => versions.value.filter((version) => version.confirmed))
const channels = computed(() => {
  const seen = new Set<string>()
  for (const row of contributions.value) {
    for (const [channel, weight] of Object.entries(row.channel_weights)) {
      if (Number(weight) > 0) seen.add(channel)
    }
  }
  return Array.from(seen).sort()
})

function percent(value: number): string {
  return `${(Number(value || 0) * 100).toFixed(2).replace(/\.?0+$/, '')}%`
}

async function reload() {
  errorMessage.value = ''
  try {
    const [reviewPayload, versionPayload, contributionPayload] = await Promise.all([
      loadReviews(),
      loadVersions(),
      loadContributions({ confirmed_only: 'true' }),
    ])
    reviews.value = reviewPayload.items
    versions.value = versionPayload.items
    contributions.value = contributionPayload.items
    if (!current.value && reviewPayload.items.length) {
      current.value = reviewPayload.items[reviewPayload.items.length - 1]
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '渠道复盘读取失败'
  }
}

async function startReview() {
  errorMessage.value = ''
  try {
    const review = await createChannelReview({ ...reviewForm })
    current.value = review
    if (reviewForm.channel === '') reviewForm.channel = review.channel
    await reload()
    current.value = review
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '渠道复盘失败'
  }
}

async function showClaims() {
  claimsHint.value = ''
  try {
    const report = await loadClaims()
    const parts = Object.entries(report.batches).map(([batch, claims]) => {
      const versions = Array.from(new Set(claims.map((claim) => claim.version_label))).sort()
      return `${batch}: ${claims.length} 笔订单，版本 ${versions.join('/') || '—'}`
    })
    claimsHint.value = parts.length ? `批次领取：${parts.join('；')}` : '当前没有批次领取记录'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批次领取读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.channel-review { margin-top: 24px; }
.panel-head { display: flex; justify-content: space-between; align-items: flex-start; }
.panel-head h3 { margin: 0 0 4px; }
.review-result { margin: 12px 0; }
.review-meta { display: flex; flex-wrap: wrap; gap: 24px; background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin: 0 0 12px; }
.review-meta dt { font-size: 12px; color: var(--muted); }
.review-meta dd { margin: 2px 0 0; font-size: 13px; }
.conflict-box { border: 1px solid #fecaca; background: #fef2f2; color: #991b1b; border-radius: 8px; padding: 10px 12px; margin: 12px 0; font-size: 13px; }
.conflict-box ul { margin: 6px 0 0; padding-left: 18px; }
.snapshot-box { margin: 12px 0; font-size: 13px; }
.snapshot-box summary { cursor: pointer; color: var(--brand); margin-bottom: 8px; }
.touch-chip, .weight-chip { display: inline-block; margin: 2px 4px 2px 0; padding: 2px 6px; border-radius: 4px; font-size: 12px; }
.touch-chip { background: #eef4ff; color: #1f4ea1; }
.weight-chip { background: #eefaf0; color: #17663a; }
.badge { margin-left: 4px; padding: 1px 6px; border-radius: 10px; background: #f1f5f9; color: #475569; font-size: 11px; }
.badge.ok { background: #dcfce7; color: #166534; }
.badge.warn { background: #fee2e2; color: #991b1b; }
.muted-text { color: var(--muted); font-size: 12px; }
.hint-text { color: #17663a; font-size: 12px; }
.cell-window { white-space: nowrap; font-size: 12px; }
.history-table { margin-top: 12px; }
</style>
