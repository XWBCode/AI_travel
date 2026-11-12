<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { marked } from 'marked'
import { ElMessage, ElMessageBox } from 'element-plus'
import { chatApi } from '@/api'
import { buildFollowUpReply, isFollowUpComplete, type FollowUpQuestion } from '@/api/followup'
import { streamChat, type PlanIntentPayload, type StreamError } from '@/api/stream'
import type { Id, MessageVO, SessionVO, TravelPlan } from '@/api/types'
import PlanCard from '@/components/PlanCard.vue'
import { formatProgress } from '@/utils/planProgress'

interface UiMessage {
  /** 本地临时气泡用负数，落库后用后端返回的雪花 ID 字符串 */
  id: number | Id
  role: 'user' | 'assistant'
  content: string
  status?: string
  model?: string
  /** 正在流式生成中，用于展示光标动画 */
  streaming?: boolean
  /** 失败原因，独立成条展示，不再塞进 markdown 正文里 */
  error?: string
  /** 该回复对应的用户提问，供「重试」原样重发 */
  retryContent?: string
  /** 失败是否值得重试（4xx 里除 429 外重试也没用） */
  canRetry?: boolean
  /** 本轮生成的计划草稿（已通过契约校验），渲染成卡片而不是 Markdown */
  plan?: TravelPlan
  /** 落库后的计划 ID，用于跳转详情 */
  planId?: Id
  /** 意图判定结果：needPlan=false 时是追问，用于展示待补充项 */
  intent?: PlanIntentPayload
  /** 追问的可选项，按槽位分组；为空则不渲染按钮 */
  followUps?: FollowUpQuestion[]
  /** 触发追问的那句原话，补答时拼在它后面重新提交 */
  originContent?: string
  /** 已选值：槽位名 → 选项 value */
  picks?: Record<string, string>
  /** 已经点选提交过，避免重复发送 */
  answered?: boolean
  /** 生成计划中的阶段提示，如「正在生成计划…」 */
  progress?: string
  /** 进度提示的起始时间戳，用来算「已等待 N 秒」 */
  progressStart?: number
  /** 计划被拒的原因（SCHEMA_VIOLATION / NEED_MORE_INFO），与 error 分开存 */
  rejectReason?: string
}

const sessions = ref<SessionVO[]>([])
const messages = ref<UiMessage[]>([])
const currentSessionId = ref<Id | null>(null)
const input = ref('')
const sending = ref(false)
const loadingMessages = ref(false)
const scrollBox = ref<HTMLElement | null>(null)
let abortController: AbortController | null = null
let tempId = -1

/**
 * 秒表：真实模型出一份计划要 30-45s，只显示「正在生成计划…」用户会怀疑卡死。
 * tick 只负责推动重渲染，秒数由 formatProgress 现算，避免存两份状态。
 */
const tick = ref(0)
let tickTimer: number | null = null

function startTick() {
  if (tickTimer !== null) return
  tickTimer = window.setInterval(() => {
    tick.value += 1
  }, 1000)
}

function stopTick() {
  if (tickTimer === null) return
  window.clearInterval(tickTimer)
  tickTimer = null
}

function progressText(m: UiMessage): string {
  void tick.value // 读一下，秒表走动时模板才会重算
  return formatProgress(m.progress ?? '', m.progressStart)
}

const suggestions = [
  '帮我规划一趟云南五日游，预算 5000，喜欢自然风景',
  '从北京出发，周末去周边适合徒步的地方',
  '第一次去日本，7 天，想兼顾城市和自然',
]

onMounted(loadSessions)

async function loadSessions(): Promise<void> {
  try {
    sessions.value = await chatApi.listSessions()
    if (sessions.value.length > 0 && currentSessionId.value === null) {
      await selectSession(sessions.value[0].id)
    }
  } catch {
    /* 拦截器已提示 */
  }
}

async function selectSession(id: Id): Promise<void> {
  if (sending.value) {
    ElMessage.warning('AI 正在回复，请先等待或停止生成')
    return
  }
  currentSessionId.value = id
  loadingMessages.value = true
  try {
    const list = await chatApi.listMessages(id)
    messages.value = list
      .filter((m: MessageVO) => m.role === 'user' || m.role === 'assistant')
      .map((m) => ({
        id: m.id,
        role: m.role as 'user' | 'assistant',
        content: m.content,
        status: m.status,
        model: m.model,
      }))
    await scrollToBottom()
  } finally {
    loadingMessages.value = false
  }
}

function newSession(): void {
  if (sending.value) return
  currentSessionId.value = null
  messages.value = []
  input.value = ''
}

async function send(): Promise<void> {
  const content = input.value.trim()
  if (!content || sending.value) return

  input.value = ''
  await submit(content, true)
}

/** 失败后重试：复用原来的提问与气泡，避免重复插入一条用户消息 */
async function retry(failed: UiMessage): Promise<void> {
  if (sending.value) return
  const content = failed.retryContent
  if (!content) return
  const index = messages.value.findIndex((m) => m.id === failed.id)
  if (index >= 0) messages.value.splice(index, 1)
  await submit(content, false)
}

/**
 * 点选追问选项。
 *
 * 多个缺失字段要逐组选，选满才提交：只补一半就发消息，后端会再问一遍剩下那个，
 * 用户会觉得自己点了个寂寞。单一问题时点一下即达成 complete，直接发送。
 *
 * 提交的是「原话 + 补答」而不是只有补答——槽位抽取看的是整句话，
 * 只发「玩3天」会丢掉「成都」。见 {@link buildFollowUpReply}。
 */
async function pickFollowUp(message: UiMessage, field: string, value: string): Promise<void> {
  if (sending.value || message.answered || !message.followUps?.length) return
  const picks = { ...(message.picks ?? {}), [field]: value }
  message.picks = picks

  const fields = message.followUps.map((q) => q.field)
  if (!isFollowUpComplete(picks, fields)) return

  const reply = buildFollowUpReply(message.originContent ?? '', picks, fields)
  if (!reply) return

  // 先落标记再发送：提交过程中按钮应保持禁用，避免连点发出两轮
  message.answered = true
  await submit(reply, true)
}

/**
 * 发起一轮对话。
 * @param pushUser 是否新插入一条用户气泡（重试时为 false，原气泡还在）
 */
async function submit(content: string, pushUser: boolean): Promise<void> {
  if (sending.value) return
  sending.value = true
  abortController = new AbortController()

  if (pushUser) {
    messages.value.push({ id: tempId--, role: 'user', content })
  }
  messages.value.push({
    id: tempId--,
    role: 'assistant',
    content: '',
    streaming: true,
    retryContent: content,
  })
  // 取数组里的代理对象：改 push 之前的原始对象不会触发视图更新
  const assistant = messages.value[messages.value.length - 1]

  await scrollToBottom()

  await streamChat(
    content,
    currentSessionId.value,
    {
      onMeta: (meta) => {
        if (currentSessionId.value === null) {
          currentSessionId.value = meta.sessionId
          // 新会话立即出现在左侧列表，标题由后端按首句生成
          sessions.value.unshift({
            id: meta.sessionId,
            title: content.length > 10 ? content.slice(0, 10) : content,
            messageCount: 1,
            createTime: new Date().toISOString(),
            updateTime: new Date().toISOString(),
          })
        }
      },
      onIntent: (payload) => {
        assistant.intent = payload
        // 只有「需要追问」且确实带了候选值时才渲染按钮：没有选项的追问
        // 会让用户点不动，那种情况正文里已经把问题写清楚了
        if (!payload.needPlan && payload.followUps.length > 0) {
          assistant.followUps = payload.followUps
          assistant.originContent = content
          assistant.picks = {}
        }
      },
      onPlanProgress: (info) => {
        assistant.progress = info.message
        assistant.progressStart = Date.now()
        startTick()
        void scrollToBottom()
      },
      onPlanDraft: (plan) => {
        // 卡片是结构化数据，不走 markdown；正文仍然由后端下发的摘要文本填充
        assistant.plan = plan
        assistant.progress = ''
        assistant.progressStart = undefined
        stopTick()
        void scrollToBottom()
      },
      onPlanSaved: (info) => {
        assistant.planId = info.planId
        void loadSessions()
      },
      onPlanRejected: (info) => {
        // 具体文案交给随后的 error 事件统一展示，这里只记录分类
        assistant.rejectReason = info.reason
        assistant.progress = ''
        stopTick()
      },
      onDelta: (piece) => {
        assistant.content += piece
        void scrollToBottom()
      },
      onDone: (info) => {
        assistant.status = 'SUCCESS'
        assistant.model = info.model
        assistant.streaming = false
        assistant.progress = ''
        stopTick()
        void loadSessions()
      },
      onError: (err: StreamError) => {
        assistant.status = 'FAILED'
        assistant.streaming = false
        assistant.progress = ''
        stopTick()
        // 已流出的部分内容保留；失败原因单独成条展示，便于用户判断该重试还是改提问
        assistant.error = err.message
        assistant.canRetry = err.retryable
      },
      onFinish: () => {
        assistant.streaming = false
        sending.value = false
        abortController = null
        void scrollToBottom()
      },
    },
    abortController.signal,
  )
}

function stop(): void {
  abortController?.abort()
  sending.value = false
}

async function removeSession(session: SessionVO, event: Event): Promise<void> {
  event.stopPropagation()
  try {
    await ElMessageBox.confirm(`确认删除会话「${session.title}」？`, '删除会话', { type: 'warning' })
  } catch {
    return
  }
  await chatApi.deleteSession(session.id)
  ElMessage.success('已删除')
  if (currentSessionId.value === session.id) {
    currentSessionId.value = null
    messages.value = []
  }
  await loadSessions()
}

function renderMarkdown(text: string): string {
  return marked.parse(text, { async: false, breaks: true }) as string
}

async function scrollToBottom(): Promise<void> {
  await new Promise((r) => setTimeout(r, 0))
  const el = scrollBox.value
  if (el) el.scrollTop = el.scrollHeight
}

function useSuggestion(text: string): void {
  input.value = text
}
</script>

<template>
  <div class="chat">
    <!-- 会话列表 -->
    <aside class="sessions">
      <el-button class="new-btn" type="primary" plain @click="newSession">+ 新建对话</el-button>
      <div class="session-list">
        <div
          v-for="s in sessions"
          :key="s.id"
          class="session-item"
          :class="{ active: s.id === currentSessionId }"
          @click="selectSession(s.id)"
        >
          <div class="session-title">{{ s.title }}</div>
          <div class="session-meta">
            <span>{{ s.messageCount }} 条</span>
            <span class="del" @click="removeSession(s, $event)">删除</span>
          </div>
        </div>
        <div v-if="sessions.length === 0" class="empty-sessions">还没有对话，从右侧开始提问吧</div>
      </div>
    </aside>

    <!-- 对话区 -->
    <section class="panel">
      <div ref="scrollBox" class="messages" v-loading="loadingMessages">
        <div v-if="messages.length === 0" class="welcome">
          <h2>想去哪里？</h2>
          <p>告诉我目的地、天数、预算和偏好，我来帮你把行程排明白。</p>
          <div class="suggestions">
            <div v-for="s in suggestions" :key="s" class="suggestion" @click="useSuggestion(s)">
              {{ s }}
            </div>
          </div>
        </div>

        <div v-for="m in messages" :key="m.id" class="bubble-row" :class="m.role">
          <div class="avatar" :class="m.role">{{ m.role === 'user' ? '我' : 'AI' }}</div>
          <div class="bubble">
            <div
              v-if="m.role === 'assistant'"
              class="markdown-body"
              v-html="renderMarkdown(m.content || '')"
            />
            <div v-else class="plain">{{ m.content }}</div>
            <div v-if="m.progress" class="plan-progress">
              <span class="spinner" />{{ progressText(m) }}
            </div>
            <PlanCard v-if="m.plan" :plan="m.plan" />
            <div v-if="m.followUps?.length" class="followup" :class="{ answered: m.answered }">
              <div v-for="q in m.followUps" :key="q.field" class="followup-row">
                <span class="followup-q">{{ q.question }}</span>
                <div class="followup-options">
                  <button
                    v-for="opt in q.options"
                    :key="opt.value"
                    type="button"
                    class="followup-chip"
                    :class="{ picked: m.picks?.[q.field] === opt.value }"
                    :disabled="sending || m.answered"
                    @click="pickFollowUp(m, q.field, opt.value)"
                  >
                    {{ opt.label }}
                  </button>
                </div>
              </div>
              <p v-if="!m.answered" class="followup-tip">选完自动继续，也可以直接打字补充</p>
            </div>
            <span v-if="m.streaming" class="caret" />
            <div v-if="m.model" class="model-tag">{{ m.model }}</div>
            <div v-if="m.error" class="bubble-error">
              <span class="err-icon">⚠️</span>
              <span class="err-text">{{ m.error }}</span>
              <el-button
                v-if="m.canRetry && !sending"
                link
                type="primary"
                size="small"
                class="retry-btn"
                @click="retry(m)"
              >
                重试
              </el-button>
            </div>
          </div>
        </div>
      </div>

      <div class="composer">
        <el-input
          v-model="input"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 6 }"
          resize="none"
          placeholder="例如：我想去云南玩五天，预算 5000，喜欢自然风景，不想太赶"
          :disabled="sending"
          @keydown.enter.exact.prevent="send"
        />
        <div class="composer-actions">
          <span class="hint">Enter 发送 · Shift + Enter 换行</span>
          <el-button v-if="sending" @click="stop">停止生成</el-button>
          <el-button type="primary" :loading="sending" :disabled="!input.trim()" @click="send">
            发送
          </el-button>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.chat {
  display: flex;
  height: 100%;
}

.sessions {
  width: 236px;
  border-right: 1px solid var(--at-border);
  background: #fbfcfd;
  display: flex;
  flex-direction: column;
  padding: 14px 12px;
}

.new-btn {
  width: 100%;
  margin-bottom: 12px;
}

.session-list {
  flex: 1;
  overflow-y: auto;
}

.session-item {
  padding: 10px 12px;
  border-radius: 10px;
  cursor: pointer;
  margin-bottom: 4px;
}

.session-item:hover {
  background: #f1f4f7;
}

.session-item.active {
  background: var(--at-primary-light);
}

.session-title {
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-meta {
  display: flex;
  justify-content: space-between;
  margin-top: 4px;
  font-size: 11px;
  color: #a8b1bd;
}

.del:hover {
  color: #f56c6c;
}

.empty-sessions {
  padding: 20px 8px;
  font-size: 12px;
  color: #a8b1bd;
  text-align: center;
}

.panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--at-surface);
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 28px 32px;
}

.welcome {
  max-width: 620px;
  margin: 8vh auto 0;
  text-align: center;
}

.welcome h2 {
  font-size: 22px;
  margin-bottom: 8px;
}

.welcome p {
  color: var(--at-text-secondary);
  font-size: 13px;
}

.suggestions {
  margin-top: 24px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.suggestion {
  padding: 12px 16px;
  border: 1px solid var(--at-border);
  border-radius: 12px;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: all 0.15s;
}

.suggestion:hover {
  border-color: var(--at-primary);
  background: var(--at-primary-light);
}

.bubble-row {
  display: flex;
  gap: 12px;
  margin-bottom: 22px;
  /* 显式占满可用宽度：否则 flex 行宽随内容浮动，失败提示这类固定下限宽度的
     气泡会把整行撑到容器外，出现横向滚动条 */
  width: 100%;
  max-width: 860px;
}

.bubble-row.user {
  flex-direction: row-reverse;
  margin-left: auto;
}

.avatar {
  flex: 0 0 32px;
  width: 32px;
  height: 32px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
}

.avatar.assistant {
  background: var(--at-primary-light);
  color: var(--at-primary);
}

.avatar.user {
  background: #eef1f6;
  color: #6b7785;
}

/*
 * 气泡宽度按内容走：fit-content 会取「一行放得下的最大宽度」，
 * 再由 max-width 兜住，这样中文不会因为 flex 的 min-content 收缩被挤成一列竖排。
 */
.bubble {
  padding: 12px 16px;
  border-radius: 14px;
  background: #fafbfc;
  border: 1px solid var(--at-border);
  width: fit-content;
  min-width: 0;
  max-width: 100%;
}

.user .bubble {
  background: var(--at-primary);
  border-color: var(--at-primary);
  color: #fff;
}

.plain {
  font-size: 14px;
  white-space: pre-wrap;
  line-height: 1.7;
}

.caret {
  display: inline-block;
  width: 6px;
  height: 14px;
  margin-left: 2px;
  background: var(--at-primary);
  animation: blink 1s steps(2, start) infinite;
  vertical-align: text-bottom;
}

@keyframes blink {
  to {
    visibility: hidden;
  }
}

.model-tag {
  margin-top: 8px;
  font-size: 11px;
  color: #a8b1bd;
}

/* 空正文不占位（失败且未产出任何内容时） */
.markdown-body:empty {
  display: none;
}

/* 计划生成中的状态提示：真实模型可能十几秒，不能让用户对着空泡等 */
.plan-progress {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--at-text-secondary);
}

.spinner {
  width: 12px;
  height: 12px;
  border: 2px solid #dfe3e9;
  border-top-color: var(--at-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/*
 * 追问选项：给明确候选值，点选即补齐。
 * 每组一个问句 + 若干按钮，多组时用户逐组选择，选满自动发送。
 */
.followup {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--at-border);
}

.followup-row + .followup-row {
  margin-top: 10px;
}

.followup-q {
  display: block;
  margin-bottom: 7px;
  font-size: 13px;
  color: var(--at-text-secondary);
}

.followup-options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.followup-chip {
  padding: 6px 14px;
  border: 1px solid var(--at-border);
  border-radius: 16px;
  background: #fff;
  font-size: 13px;
  line-height: 1.2;
  color: var(--at-text);
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}

.followup-chip:hover:not(:disabled) {
  border-color: var(--at-primary);
  color: var(--at-primary);
}

.followup-chip:disabled {
  cursor: default;
  opacity: 0.55;
}

.followup-chip.picked {
  border-color: var(--at-primary);
  background: var(--at-primary);
  color: #fff;
  opacity: 1;
}

/* 已提交后整体淡出，但保留用户选了什么，便于回看 */
.followup.answered .followup-q {
  opacity: 0.5;
}

.followup-tip {
  margin: 10px 0 0;
  font-size: 12px;
  color: #a8b1bd;
}

/* 失败提示条：与正文区分开，避免用户把错误文案当成回答的一部分 */
.bubble-error {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-top: 10px;
  padding: 8px 10px;
  border: 1px solid #f6d5d5;
  background: #fef4f4;
  border-radius: 8px;
  font-size: 12px;
  line-height: 1.5;
  color: #c45656;
}

.err-icon {
  flex: 0 0 auto;
}

.err-text {
  flex: 1;
  min-width: 0;
  word-break: break-word;
}

.retry-btn {
  flex: 0 0 auto;
  font-size: 12px;
}

.composer {
  border-top: 1px solid var(--at-border);
  padding: 14px 32px 18px;
}

.composer-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 10px;
}

.hint {
  margin-right: auto;
  font-size: 11px;
  color: #a8b1bd;
}
</style>
