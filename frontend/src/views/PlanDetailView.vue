<script setup lang="ts">
import { computed, h, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { plansApi } from '@/api'
import {
  PLAN_ACTIONS,
  PLAN_STATUS_TEXT,
  type PlanDay,
  type PlanItem,
  type PlanProgressVO,
  type PlanTodayVO,
  type PlanVersionVO,
  type ProgressItem,
  type TravelPlan,
} from '@/api/types'
import FactBadge from '@/components/FactBadge.vue'
import PlanAdjustDialog from '@/components/PlanAdjustDialog.vue'
import PlanEditDialog from '@/components/PlanEditDialog.vue'
import PlanStartDialog from '@/components/PlanStartDialog.vue'
import PlanTodayPanel from '@/components/PlanTodayPanel.vue'
import { canRollback, canRollbackTo, rollbackConfirmText, rollbackSuccessText, versionLabel } from '@/utils/rollback'
import { groupUnverified } from '@/utils/planFacts'
import { clientDate, defaultScheduleView } from '@/utils/today'

/** 跨零点检查的定时器句柄（60s 一次的轻量比较） */
let rolloverTimer: number | undefined

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
/** 执行进度：只有「进行中」才会出现勾选框，其余状态仅展示汇总 */
const progress = ref<PlanProgressVO | null>(null)
/** 正在提交的行程项 key，避免连点重复请求（后端本身幂等，前端也别制造无用请求） */
const marking = ref<string | null>(null)
const loading = ref(false)
const acting = ref(false)
const editing = ref(false)
/** 正在回滚的版本号，避免连点；null 表示没有进行中的回滚 */
const rollingBack = ref<number | null>(null)
/** 当日执行视图；终态不请求（后端也会拒），保持 null 即可 */
const today = ref<PlanTodayVO | null>(null)
/** 正在提交的当日行程项，避免连点 */
const todayMarking = ref<string | null>(null)
/** 今日 / 全部日程：进行中且能算出今天时默认「今日」 */
const scheduleView = ref<'today' | 'all'>('all')
const startDialog = ref(false)
/** 调整建议框：只有已确认 / 进行中才有意义，草稿直接编辑更快 */
const adjusting = ref(false)

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

/**
 * 「N 处信息待核实」的明细（BUG-004）。
 *
 * 详情页可以报数字——用户在这里就是要看细节的；但数字必须能被解释，
 * 所以配一个可展开的分类清单：哪一类、多少处、举个例子。
 * `plan.value` 为空时给空分组，模板里自然不渲染。
 */
const factSummary = computed(() => groupUnverified(plan.value))
/** 明细默认收起：展开后一屏放不下，先让用户看到总数和原因 */
const showFactDetail = ref(false)

async function load(): Promise<void> {
  if (!planId.value) return
  loading.value = true
  try {
    plan.value = await plansApi.detail(planId.value)
    // 版本列表与进度都是补充信息，失败不该拖垮整页
    versions.value = await plansApi.versions(planId.value).catch(() => [])
    progress.value = await plansApi.progress(planId.value).catch(() => null)
    await loadToday()
    scheduleView.value = defaultScheduleView(status.value, today.value)
  } catch {
    plan.value = null
  } finally {
    loading.value = false
  }
}

/**
 * 读当日视图。
 *
 * 日期按本机时区传：服务端与用户所在时区可能差一天，用服务器时间算会让
 * 跨时区用户在深夜看到昨天的行程。终态不请求，避免每次进页面都撞一个 30103。
 */
async function loadToday(): Promise<void> {
  if (!planId.value || status.value === 'COMPLETED' || status.value === 'CANCELLED') {
    today.value = null
    return
  }
  today.value = await plansApi.today(planId.value, clientDate()).catch(() => null)
}

/** 只有进行中能打勾；已完成看汇总，草稿/已确认还没出发 */
const canTrack = computed(() => status.value === 'IN_PROGRESS')

/**
 * 调整确认后的收尾：先关窗再刷新。
 *
 * 关窗放这里而不是让子组件 emit update:modelValue —— 那条路走通过，但实测在
 * 「确认成功 + 父组件同时刷新」这个交叠时刻不生效，弹窗会留在原地并把自己算成过期。
 * 父组件本来就持有 adjusting，由它决定何时关闭是更短、更可控的一条链。
 */
async function onAdjustApplied(): Promise<void> {
  adjusting.value = false
  await load()
}

/** 当日卡片里勾选：与「全部日程」共用同一个接口，只刷新当日视图 */
async function toggleToday(itemId: string, completed: boolean): Promise<void> {
  if (!plan.value || todayMarking.value) return
  todayMarking.value = itemId
  try {
    progress.value = await plansApi.markProgress(plan.value.planId, itemId, completed)
    await loadToday()
  } catch {
    // 状态机拒绝（如行程被别处结束）由拦截器提示
  } finally {
    todayMarking.value = null
  }
}

const progressSummary = computed(() => {
  const p = progress.value
  if (!p || p.total === 0) return null
  return { done: p.completed, total: p.total, percent: p.percent }
})

/**
 * 行程项与进度项的对应关系。
 * 计划详情里不带 itemId（契约冻结的形态里没有这个字段），
 * 所以用「第几天 + 日内序号」对齐——这两个值在一天内唯一。
 */
const progressIndex = computed(() => {
  const map = new Map<string, ProgressItem>()
  for (const day of progress.value?.days ?? []) {
    for (const item of day.items ?? []) {
      map.set(`${day.dayIndex}#${item.itemOrder}`, item)
    }
  }
  return map
})

function progressOf(day: PlanDay, item: PlanItem): ProgressItem | undefined {
  return progressIndex.value.get(`${day.dayIndex}#${item.itemOrder}`)
}

async function toggleItem(day: PlanDay, item: PlanItem): Promise<void> {
  const row = progressOf(day, item)
  if (!row || !plan.value || marking.value) return
  const key = `${day.dayIndex}#${item.itemOrder}`
  marking.value = key
  try {
    progress.value = await plansApi.markProgress(plan.value.planId, row.itemId, !row.completed)
  } catch {
    // 状态机的拒绝（如行程已完成）由拦截器提示，这里不再弹第二个框
  } finally {
    marking.value = null
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
  // 开始行程要先确认出发日：这一天就是第 1 天，不能由系统替用户默认
  if (action === 'start') {
    startDialog.value = true
    return
  }
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

/**
 * 回滚到某个版本。
 *
 * 确认弹窗必须把「进度会清空」讲在前面：回滚会重建行程条目、itemId 全变，
 * 旧的勾选记录对不上新条目，不清掉就会显示「完成了 3 项」却一项都没打勾。
 */
async function rollbackTo(version: PlanVersionVO): Promise<void> {
  if (!plan.value || rollingBack.value !== null) return
  const current = plan.value.version
  const completed = progress.value?.completed ?? 0

  // 多行文案用 VNode 渲染：纯文本模式下 \n 会被折叠成空格，读起来像一句话
  const text = rollbackConfirmText(version.versionNo, current, completed)
  try {
    await ElMessageBox.confirm(
      h(
        'div',
        { style: 'line-height: 1.75; max-width: 430px' },
        text.split('\n').map((line) => h('p', { style: 'margin: 0 0 6px' }, line)),
      ),
      `回滚到 v${version.versionNo}`,
      { confirmButtonText: '确定回滚', cancelButtonText: '再想想' },
    )
  } catch {
    return
  }

  rollingBack.value = version.versionNo
  try {
    const result = await plansApi.rollback(plan.value.planId, version.versionNo)
    // 直接用返回的详情替换，避免「回滚成功再拉一次」中间闪一下旧内容
    plan.value = result.plan
    ElMessage.success(
      rollbackSuccessText(result.restoredFrom, result.version, result.clearedProgress),
    )
    // 版本列表与进度都变了；失败保留已更新的正文，不因为补充信息失败就报错
    void plansApi
      .versions(plan.value.planId)
      .then((list) => (versions.value = list))
      .catch(() => undefined)
    progress.value = await plansApi.progress(plan.value.planId).catch(() => null)
  } catch {
    // 版本不存在 / 终态拒绝 / 并发冲突由拦截器提示，这里不叠第二个弹窗
  } finally {
    rollingBack.value = null
  }
}

function basicMeta(): Array<{ label: string; value: string }> {  const basic = plan.value?.basic
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

/** 跨零点：页面不刷新也要从今天翻到明天，否则凌晨还停在昨天的行程 */
function checkRollover(): void {
  if (today.value && clientDate() !== today.value.date) void loadToday()
}

onMounted(() => {
  void load()
  rolloverTimer = window.setInterval(checkRollover, 60_000)
  window.addEventListener('focus', checkRollover)
})

onBeforeUnmount(() => {
  if (rolloverTimer) window.clearInterval(rolloverTimer)
  window.removeEventListener('focus', checkRollover)
})
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
            <!-- 已确认或进行中才给调整入口：草稿阶段直接编辑，不必绕一圈 AI -->
            <el-button
              v-if="status === 'CONFIRMED' || status === 'IN_PROGRESS'"
              size="small"
              :disabled="acting"
              @click="adjusting = true"
            >
              调整行程
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
          <strong class="notice-title">共 {{ plan.unverifiedFieldCount }} 处信息待核实</strong>
          <span>
            天气、票价与路况还没接入实时数据源，下面标注「待核实」的内容请以景区公告与实际报价为准。
          </span>
          <button
            v-if="factSummary.groups.length"
            type="button"
            class="notice-toggle"
            @click="showFactDetail = !showFactDetail"
          >
            {{ showFactDetail ? '收起分类明细' : '按类别看是哪几处' }}
          </button>
          <ul v-if="showFactDetail" class="fact-list">
            <li v-for="group in factSummary.groups" :key="group.key" class="fact-row">
              <span class="fact-name">{{ group.label }}</span>
              <span class="fact-count">{{ group.count }} 处</span>
              <span class="fact-eg">
                {{ group.subjects.join('、')
                }}{{ group.subjectCount > group.subjects.length ? ` 等 ${group.subjectCount} 项` : '' }}
              </span>
            </li>
          </ul>
        </div>

        <div v-if="missingItems.length" class="missing">
          <span class="missing-label">还可以补充：</span>
          <span class="missing-item" v-for="item in missingItems" :key="item">{{ item }}</span>
          <span class="missing-hint">补充后行程会更贴合实际情况（在对话里说一句就行）</span>
        </div>

        <!-- 执行进度：进行中才可勾选，已完成只读。草稿/已确认还没出发，不占版面 -->
        <div
          v-if="progressSummary && (status === 'IN_PROGRESS' || status === 'COMPLETED') && scheduleView === 'all'"
          class="track"
        >
          <div class="track-head">
            <span class="track-title">执行进度</span>
            <span class="track-count">
              {{ progressSummary.done }} / {{ progressSummary.total }} 项
            </span>
            <span v-if="progressSummary.percent === 100" class="track-all">
              全部完成，可以继续下一步了
            </span>
          </div>
          <el-progress
            :percentage="progressSummary.percent"
            :stroke-width="8"
            :show-text="false"
            :color="progressSummary.percent === 100 ? '#12b76a' : '#2f6fed'"
          />
        </div>

        <section class="block">
          <div class="block-head">
            <h2>每日安排</h2>
            <!-- 只有算得出「今天」才给「今日」：没有出发日时硬切过去只会看到一张空卡片 -->
            <div v-if="today" class="switch">
              <button
                type="button"
                :class="{ on: scheduleView === 'today' }"
                @click="scheduleView = 'today'"
              >
                今日
              </button>
              <button
                type="button"
                :class="{ on: scheduleView === 'all' }"
                @click="scheduleView = 'all'"
              >
                全部日程
              </button>
            </div>
          </div>

          <PlanTodayPanel
            v-if="today && scheduleView === 'today'"
            :today="today"
            :status="status"
            :marking="todayMarking"
            @mark="toggleToday"
            @anchor="startDialog = true"
          />

          <template v-else>
          <div v-for="day in plan.days ?? []" :key="day.dayIndex" class="day">
            <div class="day-head">
              <span class="day-index">{{ dayTitle(day) }}</span>
              <span v-if="day.city" class="day-city">{{ day.city }}</span>
              <span v-if="day.theme" class="day-theme">{{ day.theme }}</span>
            </div>

            <ul v-if="day.items?.length" class="items">
              <li
                v-for="(item, i) in day.items ?? []"
                :key="`${day.dayIndex}-${i}`"
                class="item"
                :class="{
                  done: progressOf(day, item)?.completed,
                  next: canTrack && progressOf(day, item)?.next,
                  clickable: canTrack && progressOf(day, item),
                }"
                @click="canTrack && toggleItem(day, item)"
              >
                <!-- 勾选框只负责显示，点击交给整行：14px 的方块在手机上几乎点不中 -->
                <div v-if="canTrack" class="item-check">
                  <el-checkbox
                    v-if="progressOf(day, item)"
                    :model-value="progressOf(day, item)?.completed"
                    :disabled="marking === `${day.dayIndex}#${item.itemOrder}`"
                  />
                </div>
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
            <!-- 改天数后新补出来的整天没有行程项，要说清是「还没安排」而不是页面丢了内容 -->
            <p v-else class="day-empty">
              这一天还没有具体安排。在对话里说一句想怎么过，比如「第 4 天想逛街和吃火锅」，就能补上。
            </p>
          </div>
          </template>
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
            <li
              v-for="v in versions"
              :key="v.id"
              :class="{ current: v.versionNo === plan.version }"
            >
              <span class="v-no">v{{ v.versionNo }}</span>
              <span class="v-note">{{ versionLabel(v) }}</span>
              <span class="v-time">{{ formatTime(v.createTime) }}</span>
              <!-- 当前版本没有「恢复到自己」的意义，后端也会拒，干脆不显示按钮 -->
              <button
                v-if="canRollbackTo(v.versionNo, status, plan.version)"
                type="button"
                class="v-rollback"
                :disabled="rollingBack !== null"
                @click="rollbackTo(v)"
              >
                {{ rollingBack === v.versionNo ? '回滚中…' : '回滚' }}
              </button>
              <span v-else-if="v.versionNo === plan.version" class="v-current">当前</span>
            </li>
          </ul>
          <p v-if="!canRollback(status)" class="v-hint">
            行程已结束，历史版本只能查看。要改内容请新建一份计划。
          </p>
        </section>
      </template>

      <el-empty v-else-if="!loading" description="找不到这个行程">
        <el-button type="primary" @click="router.push('/plans')">回到我的旅行</el-button>
      </el-empty>
    </div>

    <!-- saved 与 refresh 都走 load()：保存后版本列表也变了，重新拉一遍最省事 -->
    <PlanEditDialog v-model="editing" :plan="plan" @saved="load" @refresh="load" />
    <!-- 开始行程必须经过这一步：选定的日期就是第 1 天 -->
    <PlanStartDialog v-model="startDialog" :plan="plan" @started="load" />
    <!-- 调整建议：确认前不动正式计划，确认后由父组件关窗并刷新 -->
    <PlanAdjustDialog
      v-if="adjusting"
      :plan="plan"
      @close="adjusting = false"
      @applied="onAdjustApplied"
    />
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

.notice-title {
  font-weight: 600;
}

.notice-toggle {
  align-self: flex-start;
  padding: 0;
  border: none;
  background: none;
  font: inherit;
  color: #8a6316;
  text-decoration: underline;
  cursor: pointer;
}

.notice-toggle:hover {
  color: var(--at-primary);
}

.fact-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 6px 0 0;
  padding: 8px 0 0;
  border-top: 1px dashed #f0dcb6;
  list-style: none;
}

.fact-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}

/* `.notice span` 会覆盖单类名，所以这里带上父选择器保证优先级 */
.notice .fact-name {
  flex: none;
  font-weight: 600;
  color: #8a6316;
}

.notice .fact-count {
  flex: none;
  color: #a9761b;
}

.notice .fact-eg {
  flex: 1 1 140px;
  min-width: 0;
  font-size: 11px;
  color: #a89564;
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

.block-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.block-head h2 {
  margin: 0;
}

.switch {
  display: flex;
  padding: 2px;
  border: 1px solid var(--at-border);
  border-radius: 999px;
  background: #f7f9fb;
}

.switch button {
  padding: 3px 12px;
  font-size: 12px;
  color: var(--at-text-secondary);
  background: transparent;
  border: none;
  border-radius: 999px;
  cursor: pointer;
}

.switch button.on {
  color: var(--at-primary);
  background: #fff;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
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

/* 勾选区固定宽度，勾上/不勾都不会让时间列左右跳动 */
.item-check {
  display: flex;
  align-items: flex-start;
  padding-top: 1px;
  /* 点击一律由整行处理，避免「点了勾选框却没反应」 */
  pointer-events: none;
}

.item-check :deep(.el-checkbox) {
  height: auto;
  margin-right: 0;
}

.item.clickable {
  cursor: pointer;
}

.item.clickable:hover {
  background: #fafbfc;
}

/* 已完成：整条降饱和并加删除线，扫一眼就知道哪些没做 */
.item.done .item-title {
  color: #a8b1bd;
  text-decoration: line-through;
}

.item.done .item-time,
.item.done .item-facts,
.item.done .item-note {
  opacity: 0.55;
}

/* 下一项：左侧色条提示「该做这个了」，比单独一句文案更好扫 */
.item.next {
  position: relative;
}

.item.next::before {
  content: '';
  position: absolute;
  left: -12px;
  top: 10px;
  bottom: 10px;
  width: 3px;
  border-radius: 3px;
  background: var(--at-primary);
}

.track {
  margin-top: 18px;
  padding: 12px 14px;
  border: 1px solid var(--at-border);
  border-radius: 8px;
  background: #fff;
}

.track-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 9px;
  font-size: 13px;
}

.track-title {
  font-weight: 600;
  color: var(--at-text-primary);
}

.track-count {
  color: var(--at-text-secondary);
  font-variant-numeric: tabular-nums;
}

.track-all {
  color: #12b76a;
  font-size: 12px;
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

.day-empty {
  margin: 10px 0 0;
  padding: 12px 14px;
  border: 1px dashed var(--at-border);
  border-radius: 8px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--at-text-secondary);
  background: #fbfcfd;
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

/* 当前版本整行淡底，一眼看出「我现在在哪一版」 */
.versions li.current {
  background: #f8fafc;
  margin: 0 -8px;
  padding: 7px 8px;
  border-radius: 6px;
}

.v-rollback {
  flex: none;
  padding: 2px 10px;
  font-size: 11px;
  color: var(--at-primary);
  background: #fff;
  border: 1px solid #d8e0ea;
  border-radius: 999px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}

.v-rollback:hover:not(:disabled) {
  border-color: var(--at-primary);
}

.v-rollback:disabled {
  color: #b6bfca;
  border-color: #e8ecf1;
  cursor: not-allowed;
}

.v-current {
  flex: none;
  padding: 2px 10px;
  font-size: 11px;
  color: #8b95a3;
  background: #eef1f5;
  border-radius: 999px;
}

.v-hint {
  margin: 10px 0 0;
  font-size: 12px;
  color: #a8b1bd;
}
</style>
