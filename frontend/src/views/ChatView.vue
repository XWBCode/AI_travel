<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { marked } from 'marked'
import { ElMessage, ElMessageBox } from 'element-plus'
import { chatApi } from '@/api'
import { streamChat } from '@/api/stream'
import type { MessageVO, SessionVO } from '@/api/types'

interface UiMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  status?: string
  model?: string
  /** 正在流式生成中，用于展示光标动画 */
  streaming?: boolean
}

const sessions = ref<SessionVO[]>([])
const messages = ref<UiMessage[]>([])
const currentSessionId = ref<number | null>(null)
const input = ref('')
const sending = ref(false)
const loadingMessages = ref(false)
const scrollBox = ref<HTMLElement | null>(null)
let abortController: AbortController | null = null
let tempId = -1

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

async function selectSession(id: number): Promise<void> {
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
  sending.value = true
  abortController = new AbortController()

  messages.value.push({ id: tempId--, role: 'user', content })
  const assistant: UiMessage = { id: tempId--, role: 'assistant', content: '', streaming: true }
  messages.value.push(assistant)

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
      onDelta: (piece) => {
        assistant.content += piece
        void scrollToBottom()
      },
      onDone: (info) => {
        assistant.status = 'SUCCESS'
        assistant.model = info.model
        assistant.streaming = false
        void loadSessions()
      },
      onError: (msg) => {
        assistant.status = 'FAILED'
        assistant.streaming = false
        // 流式已产出的内容保留，并在末尾标注失败，避免用户误以为回复完整
        assistant.content += `\n\n> ⚠️ 生成中断：${msg}`
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
            <span v-if="m.streaming" class="caret" />
            <div v-if="m.model" class="model-tag">{{ m.model }}</div>
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

.bubble {
  padding: 12px 16px;
  border-radius: 14px;
  background: #fafbfc;
  border: 1px solid var(--at-border);
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
