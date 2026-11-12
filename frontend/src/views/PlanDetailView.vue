<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { plansApi } from '@/api'
import {
  PLAN_ACTIONS,
  PLAN_STATUS_TEXT,
  type PlanDay,
  type PlanVersionVO,
  type TravelPlan,
} from '@/api/types'
import FactBadge from '@/components/FactBadge.vue'
import PlanEditDialog from '@/components/PlanEditDialog.vue'

/**
 * 计划详情。
 *
 * 原则：这一页只展示契约里已有的内容，缺什么就明说「待核实」。
 * 尤其是时间、门票、开放时间这类字段，L0 阶段一律没有实时数据，
 * 不能为了让页面好看就编一个值出来（AC-08）。
 */
const route = useRoute()
const router = useRouter()

const plan = ref<TravelPlan | null>(null)
const versions = ref<PlanVersionVO[]>([])
const loading = ref(false)
const acting = ref(false)
const editing = ref(false)

const planId = computed(() => String(route.params.id ?? ''))
const status = computed(() => plan.value?.status ?? '')
const actions = computed(() => PLAN_ACTIONS[status.value] ?? [])
const statusText = computed(() => PLAN_STATUS_TEXT[status.value] ?? status.value)

/** 缺失字段翻译成人话，直接显示字段名对用户没意义 */
const MISSING_TEXT: Record<string, string> = {
  originCity: '出发地',
  startDate: '出发日期',
  endDate: '返程日期',
  travelers: '出行人数',
  budget: '预算',
  interests: '兴趣偏好',
  transport: '交通方式',
  accommodation: '住宿偏好',
}

const missingItems = computed(() => {
  const fields = plan.value?.basic?.missingFields ?? []
  return fields.map((f) => MISSING_TEXT[f] ?? f)
})

async function load(): Promise<void> {
  if (!planId.value) return
  loading.value = true
  try {
    plan.value = await plansApi.detail(planId.value)
    // 版本列表失败不该拖垮整页：它只是补充信息
    versions.value = await plansApi.versions(planId.value).catch(() => [])
  } catch {
    plan.value = null
  } finally {
    loading.value = false
  }
}

const ACTION_TEXT: Record<string, string> = {
  confirm: '确认行程',
  start: '开始行程',
  complete: '标记完成',
  cancel: '取消行程',
}

async function runAction(action: 'confirm' | 'start' | 'complete' | 'cancel'): Promise<void> {
  if (!plan.value || acting.value) return
  if (action === 'confirm' || action === 'cancel') {
    const tip =
      action === 'confirm'
        ? '确认后状态变为「已确认」，并保存一个版本快照。待核实项不会自动消失，请出发前确认。'
        : '取消后行程不再可用，但记录会保留。确定取消吗？'
    try {
      await ElMessageBox.confirm(tip, ACTION_TEXT[action], {
        confirmButtonText: '确定',
        cancelButtonText: '再想想',
      })
    } catch {
      return
    }
  }

  acting.value = true
  try {
    await plansApi[action](plan.value.planId)
    ElMessage.success(`${ACTION_TEXT[action]}成功`)
    await load()
  } catch {
    // 状态机的拒绝原因（如「不允许从已确认变更为已确认」）由拦截器展示
  } finally {
    acting.value = false
  }
}

function basicMeta(): Array<{ label: string; value: string }> {
  const basic = plan.value?.basic
  if (!basic) return []
  const items: Array<{ label: string; value: string }> = []
  items.push({ label: '出发地', value: basic.originCity || '待补充' })
  items.push({
    label: '目的地',
    value: (basic.destinations ?? []).filter(Boolean).join(' · ') || basic.destination || '待补充',
  })
  items.push({ label: '天数', value: basic.days ? `${basic.days} 天` : '待补充' })
  items.push({ label: '人数', value: basic.travelers ? `${basic.travelers} 人` : '待补充' })
  items.push({
    label: '日期',
    value:
      basic.startDate && basic.endDate
        ? `${basic.startDate} → ${basic.endDate}`
        : basic.startDate || '待补充',
  })
  return items
}

function dayTitle(day: PlanDay): string {
  return day.date ? `${day.date}` : `第 ${day.dayIndex} 天`
}

function formatTime(value?: string): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function goBack(): void {
  if (window.history.length > 1) router.back()
  else router.push('/plans')
}

onMounted(load)
</script>

<template>
  <div class="detail-page" v-loading="loading">
    <div class="inner">
      <button type="button" class="back" @click="goBack">← 返回</button>

      <template v-if="plan">
        <header class="head">
          <div class="head-main">
            <div class="title-row">
              <h1>{{ plan.title }}</h1>
              <span class="status" :class="status.toLowerCase()">{{ statusText }}</span>
              <span class="version">v{{ plan.version }}</span>
            </div>
            <div class="meta">
              <span v-for="item in basicMeta()" :key="item.label" class="meta-item">
                <span class="k">{{ item.label }}</span>
                <span class="v">{{ item.value }}</span>
              </span>
            </div>
          </div>
          <div class="head-ops">
            <!-- 只有草稿能改：已确认的行程改动会让「确认」失去意义（与后端状态机一致） -->
            <el-button
              v-if="status === 'DRAFT'"
              size="small"
              :disabled="acting"
              @click="editing = true"
            >
              编辑
            </el-button>
            <el-button
              v-for="action in actions"
              :key="action"
              :type="action === 'cancel' ? 'default' : 'primary'"
              :loading="acting"
              size="small"
              @click="runAction(action)"
            >
              {{ ACTION_TEXT[action] }}
            </el-button>
          </div>
        </header>

        <div v-if="plan.unverifiedFieldCount > 0" class="notice">
          <strong>{{ plan.unverifiedFieldCount }} 个字段待核实</strong>
          <span>
            当前未接入实时天气、票价与路况，页面里标注「待核实」的内容请以景区公告与实际报价为准。
          </span>
        </div>

        <div v-if="missingItems.length" class="missing">
          <span class="missing-label">还可以补充：</span>
          <span class="missing-item" v-for="item in missingItems" :key="item">{{ item }}</span>
          <span class="missing-hint">补充后行程会更贴合实际情况（在对话里说一句就行）</span>
        </div>

        <section class="block">
          <h2>每日安排</h2>
          <div v-for="day in plan.days ?? []" :key="day.dayIndex" class="day">
            <div class="day-head">
              <span class="day-index">{{ dayTitle(day) }}</span>
              <span v-if="day.city" class="day-city">{{ day.city }}</span>
              <span v-if="day.theme" class="day-theme">{{ day.theme }}</span>
            </div>

            <ul class="items">
              <li v-for="(item, i) in day.items ?? []" :key="`${day.dayIndex}-${i}`" class="item">
                <div class="item-time">
                  <span>{{ item.startTime || '—' }}</span>
                  <span v-if="item.endTime" class="end">{{ item.endTime }}</span>
                </div>
                <div class="item-main">
                  <div class="item-title">{{ item.title }}</div>
                  <div class="item-facts">
                    <FactBadge label="时长" :field="item.duration" />
                    <FactBadge label="开放" :field="item.openHours" />
                    <FactBadge label="门票" :field="item.ticketPrice" />
                  </div>
                  <p v-if="item.note" class="item-note">{{ item.note }}</p>
                </div>
              </li>
            </ul>
          </div>
        </section>

        <section v-if="plan.budget?.items?.length" class="block">
          <h2>预算</h2>
          <p v-if="plan.budget.note" class="block-note">{{ plan.budget.note }}</p>
          <table class="budget">
            <thead>
              <tr>
                <th>类别</th>
                <th>金额</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(item, i) in plan.budget.items" :key="i">
                <td>{{ item.label }}</td>
                <td><FactBadge :field="item.amount" /></td>
                <td class="budget-note">{{ item.note || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <section v-if="plan.risks?.length || plan.aiNote" class="block">
          <h2>提醒</h2>
          <ul v-if="plan.risks?.length" class="risks">
            <li v-for="(risk, i) in plan.risks" :key="i">{{ risk }}</li>
          </ul>
          <p v-if="plan.aiNote" class="block-note">{{ plan.aiNote }}</p>
        </section>

        <section v-if="versions.length" class="block">
          <h2>版本历史</h2>
          <ul class="versions">
            <li v-for="v in versions" :key="v.id">
              <span class="v-no">v{{ v.versionNo }}</span>
              <span class="v-note">{{ v.changeSummary || '快照' }}</span>
              <span class="v-time">{{ formatTime(v.createTime) }}</span>
            </li>
          </ul>
        </section>
      </template>

      <el-empty v-else-if="!loading" description="找不到这个行程">
        <el-button type="primary" @click="router.push('/plans')">回到我的旅行</el-button>
      </el-empty>
    </div>

    <!-- saved 与 refresh 都走 load()：保存后版本列表也变了，重新拉一遍最省事 -->
    <PlanEditDialog v-model="editing" :plan="plan" @saved="load" @refresh="load" />
  </div>
</template>

<style scoped>
.detail-page {
  height: 100%;
  overflow-y: auto;
  padding: 24px 32px 48px;
}

.inner {
  max-width: 880px;
  margin: 0 auto;
}

.back {
  margin-bottom: 14px;
  padding: 0;
  border: none;
  background: transparent;
  font-size: 13px;
  color: var(--at-text-secondary);
  cursor: pointer;
}

.back:hover {
  color: var(--at-primary);
}

.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--at-border);
}

.title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.title-row h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--at-text);
}

.status {
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  line-height: 1.7;
  background: #f2f4f7;
  color: var(--at-text-secondary);
}

.status.confirmed,
.status.in_progress {
  background: var(--at-primary-light);
  color: var(--at-primary);
}

.status.completed {
  background: #eef6ee;
  color: #3f7a48;
}

.version {
  font-size: 11px;
  color: #a8b1bd;
}

.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 18px;
  margin-top: 10px;
}

.meta-item {
  font-size: 12px;
}

.meta-item .k {
  margin-right: 5px;
  color: #9aa4b0;
}

.meta-item .v {
  color: var(--at-text);
}

.head-ops {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
}

.notice {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin-top: 16px;
  padding: 10px 14px;
  border: 1px solid #f3ddb4;
  border-radius: 8px;
  background: #fdf8ee;
  font-size: 12px;
  line-height: 1.6;
  color: #8a6316;
}

.notice span {
  color: #a9761b;
}

.missing {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  font-size: 12px;
  color: var(--at-text-secondary);
}

.missing-label {
  color: #9aa4b0;
}

.missing-item {
  padding: 2px 8px;
  border: 1px dashed var(--at-border);
  border-radius: 10px;
}

.missing-hint {
  margin-left: 4px;
  color: #a8b1bd;
}

.block {
  margin-top: 26px;
}

.block h2 {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
  color: var(--at-text);
}

.block-note {
  margin: 0 0 10px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--at-text-secondary);
}

.day {
  padding: 14px 16px;
  border: 1px solid var(--at-border);
  border-radius: var(--at-radius);
  background: var(--at-surface);
}

.day + .day {
  margin-top: 10px;
}

.day-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding-bottom: 10px;
  border-bottom: 1px dashed var(--at-border);
}

.day-index {
  font-size: 13px;
  font-weight: 600;
  color: var(--at-text);
}

.day-city {
  font-size: 12px;
  color: var(--at-primary);
}

.day-theme {
  font-size: 12px;
  color: var(--at-text-secondary);
}

.items {
  margin: 0;
  padding: 0;
  list-style: none;
}

.item {
  display: flex;
  gap: 14px;
  padding: 10px 0;
}

.item + .item {
  border-top: 1px solid #f2f4f7;
}

.item-time {
  flex-shrink: 0;
  width: 52px;
  font-size: 12px;
  color: var(--at-text-secondary);
  text-align: right;
}

.item-time .end {
  display: block;
  color: #b6bec8;
}

.item-main {
  flex: 1;
  min-width: 0;
}

.item-title {
  font-size: 13px;
  color: var(--at-text);
}

.item-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin-top: 5px;
}

.item-note {
  margin: 5px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: #a8b1bd;
}

.budget {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.budget th {
  padding: 8px 10px;
  border-bottom: 1px solid var(--at-border);
  font-weight: 500;
  color: #9aa4b0;
  text-align: left;
}

.budget td {
  padding: 9px 10px;
  border-bottom: 1px solid #f2f4f7;
  color: var(--at-text);
}

.budget-note {
  color: var(--at-text-secondary) !important;
}

.risks {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.8;
  color: var(--at-text-secondary);
}

.versions {
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 12px;
}

.versions li {
  display: flex;
  gap: 12px;
  padding: 7px 0;
  border-bottom: 1px solid #f2f4f7;
}

.v-no {
  width: 36px;
  color: var(--at-primary);
}

.v-note {
  flex: 1;
  color: var(--at-text-secondary);
}

.v-time {
  color: #a8b1bd;
}
</style>
