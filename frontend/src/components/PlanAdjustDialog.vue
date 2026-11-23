<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { plansApi } from '@/api'
import { newRequestId } from '@/api/stream'
import type { PlanAdjustmentVO, TravelPlan } from '@/api/types'
import { canConfirm, opsSummary, renderOps, staleHint } from '@/utils/adjustment'

/**
 * 「说一句就能改」的调整建议框（R-23）。
 *
 * 流程刻意分成三段：说要求 → 看差异 → 确认。中间那段不能省：
 * 模型产出的是一组增量操作，用户必须能看见「到底要删哪一项、改哪一处」才敢点确认。
 * 确认之前正式计划一个字段都不会动，所以慢一点、等一会儿都不影响已有数据。
 *
 * 是否显示由父组件的 v-if 决定，组件自己不持有开关。
 *
 * 试过 el-dialog 的 v-model + 外部 prop 双向同步：点 X 关得掉，但「确认成功」这条路径上
 * 父组件已经把 modelValue 置 false，弹窗却留在原地，还会因为版本前进把自己算成「已过期」。
 * 与其和组件库内部的开关状态较劲，不如让「渲染与否」只有一个决定者。
 */
const props = defineProps<{ plan: TravelPlan | null }>()
const emit = defineEmits<{ close: []; applied: [] }>()

const instruction = ref('')
const proposal = ref<PlanAdjustmentVO | null>(null)
const phase = ref<'input' | 'loading' | 'preview'>('input')
const confirming = ref(false)
const elapsed = ref(0)
let ticker: number | undefined

/**
 * 幂等键的生命周期 = 「一次用户意图」。
 *
 * 模型要跑一分钟，超时/断网时用户点第二次是最自然的动作。如果每次都换新 ID，
 * 后端会当成两次新请求，落下两条 PENDING 建议；用户确认第一条后，第二条的
 * base_version 已经过期，点下去必然失败，表现就是「怎么点了没反应」。
 * 所以：同一句话重试复用同一个 ID，换了内容或拿到结果后作废。
 */
let pendingRequestId: string | null = null
let pendingText: string | null = null

const planVersion = computed(() => props.plan?.version ?? 0)
const rendered = computed(() => renderOps(proposal.value?.ops))
const summary = computed(() => opsSummary(proposal.value?.ops))
const stale = computed(() => staleHint(proposal.value, planVersion.value))
const confirmable = computed(() => canConfirm(proposal.value, planVersion.value))

onBeforeUnmount(stopTicker)

function startTicker(): void {
  stopTicker()
  elapsed.value = 0
  ticker = window.setInterval(() => {
    elapsed.value += 1
  }, 1000)
}

function stopTicker(): void {
  if (ticker) {
    window.clearInterval(ticker)
    ticker = undefined
  }
}

async function generate(): Promise<void> {
  const text = instruction.value.trim()
  if (!props.plan || !text || phase.value === 'loading') return
  phase.value = 'loading'
  startTicker()
  if (!pendingRequestId || pendingText !== text) {
    pendingRequestId = newRequestId()
    pendingText = text
  }
  try {
    proposal.value = await plansApi.proposeAdjustment(props.plan.planId, text, pendingRequestId)
    // 拿到了就作废：下一次「换一句再改」必须是一条全新的意图
    pendingRequestId = null
    pendingText = null
    phase.value = 'preview'
  } catch {
    // 30109 / 30110 的 message 已经是人话（「建议内容不合法：…」），拦截器会弹出来
    // 失败时保留 pendingRequestId：同一句话重试要复用，后端才知道这是重试而不是新要求
    phase.value = 'input'
    proposal.value = null
  } finally {
    stopTicker()
  }
}

async function confirm(): Promise<void> {
  if (!props.plan || !proposal.value || !confirmable.value || confirming.value) return
  confirming.value = true
  try {
    const result = await plansApi.confirmAdjustment(props.plan.planId, proposal.value.adjustmentId)
    const extra = result.clearedProgress > 0
      ? `，并清除了 ${result.clearedProgress} 条已完成的记录`
      : ''
    ElMessage.success(`已应用调整${extra}`)
    // 标记已处理：否则关闭动画期间 plan.version 已前进，弹窗会把自己算成「已过期」并劝用户重新生成
    proposal.value = { ...proposal.value, status: 'CONFIRMED' }
    // 先关再刷新：反过来的话，刷新把版本推上去，弹窗会在关闭前一刻把自己算成「已过期」
    emit('close')
    emit('applied')
  } catch {
    // 版本冲突（30108）由拦截器提示；建议此时已转为过期，用户重开即重新生成
  } finally {
    confirming.value = false
  }
}

/**
 * 放弃：顺手把建议标成已拒绝，避免列表里堆一串永远 PENDING 的记录。
 * 组件会被父组件卸载，无需自己清状态。
 */
function close(): void {
  const plan = props.plan
  if (plan && proposal.value && proposal.value.status === 'PENDING') {
    // 不 await：关窗不该等一次网络往返，失败也没关系（下次确认会因版本变化而过期）
    plansApi.rejectAdjustment(plan.planId, proposal.value.adjustmentId).catch(() => {})
  }
  emit('close')
}

function back(): void {
  proposal.value = null
  phase.value = 'input'
}
</script>

<template>
  <el-dialog
    :model-value="true"
    title="调整行程"
    width="520px"
    :close-on-click-modal="false"
    @update:model-value="emit('close')"
  >
    <div class="body">
      <template v-if="phase === 'input'">
        <p class="hint">说说想怎么改，比如「下午太累，把最后一项去掉」。</p>
        <el-input
          v-model="instruction"
          type="textarea"
          :rows="3"
          maxlength="500"
          show-word-limit
          placeholder="今天太累，下午不去了"
          @keydown.ctrl.enter="generate"
        />
        <p class="note">AI 只给建议，不会直接改行程。你确认之后才生效。</p>
      </template>

      <div v-else-if="phase === 'loading'" class="loading">
        <span class="spinner" />
        <p class="loading-title">AI 正在看你的行程…</p>
        <p class="note">已经等了 {{ elapsed }} 秒，通常需要 20–40 秒。</p>
      </div>

      <template v-else-if="proposal">
        <div class="summary-row">
          <p class="summary">{{ proposal.summary }}</p>
          <span class="count">{{ summary }}</span>
        </div>

        <ul class="op-list">
          <li v-for="(op, index) in rendered" :key="index" class="op" :class="op.kind">
            <div class="op-head">
              <span class="tag">{{ op.tag }}</span>
              <span class="op-title">{{ op.title }}</span>
            </div>
            <p v-for="(line, i) in op.lines" :key="i" class="op-line">{{ line }}</p>
          </li>
        </ul>

        <p v-if="stale" class="stale">{{ stale }}</p>
        <p v-else class="note">
          确认后会生成一个新版本（v{{ proposal.baseVersion }} → v{{ proposal.baseVersion + 1 }}），
          没被提到的行程项和已打的勾都不受影响。
        </p>
      </template>
    </div>

    <template #footer>
      <template v-if="phase === 'input'">
        <el-button size="small" @click="emit('close')">取消</el-button>
        <el-button type="primary" size="small" :disabled="!instruction.trim()" @click="generate">
          生成建议
        </el-button>
      </template>
      <template v-else-if="phase === 'loading'">
        <el-button size="small" disabled>正在生成…</el-button>
      </template>
      <template v-else>
        <el-button size="small" @click="back">改个说法</el-button>
        <el-button v-if="confirmable" size="small" @click="close">放弃</el-button>
        <el-button
          v-if="confirmable"
          type="primary"
          size="small"
          :loading="confirming"
          @click="confirm"
        >
          确认调整
        </el-button>
        <el-button v-else type="primary" size="small" @click="back">重新生成</el-button>
      </template>
    </template>
  </el-dialog>
</template>

<style scoped>
.body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 120px;
}

.hint {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--at-text);
}

.note {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--at-text-secondary);
}

.loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 24px 0;
}

.spinner {
  width: 22px;
  height: 22px;
  border: 2px solid var(--at-border);
  border-top-color: var(--at-primary);
  border-radius: 50%;
  animation: spin 0.9s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.loading-title {
  margin: 0;
  font-size: 13px;
  color: var(--at-text);
}

.summary-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.summary {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
  color: var(--at-text);
}

.count {
  flex: none;
  font-size: 12px;
  color: var(--at-text-secondary);
}

.op-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 300px;
  padding: 0;
  margin: 0;
  overflow-y: auto;
  list-style: none;
}

.op {
  padding: 8px 12px;
  border-left: 3px solid var(--at-border);
  border-radius: 0 8px 8px 0;
  background: var(--at-bg);
}

.op.REMOVE_ITEM {
  border-left-color: #c45656;
}

.op.ADD_ITEM {
  border-left-color: var(--at-primary);
}

.op.UPDATE_ITEM,
.op.UPDATE_DAY {
  border-left-color: #3b7ddd;
}

.op-head {
  display: flex;
  gap: 8px;
  align-items: baseline;
}

.tag {
  flex: none;
  font-size: 11px;
  color: var(--at-text-secondary);
}

.op-title {
  font-size: 13px;
  color: var(--at-text);
}

.op-line {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--at-text-secondary);
}

.stale {
  margin: 0;
  padding: 8px 12px;
  font-size: 12px;
  line-height: 1.7;
  color: #8a5a00;
  background: #fdf6e3;
  border-radius: 8px;
}
</style>
