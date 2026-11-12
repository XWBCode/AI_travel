import { getToken } from '@/api/http'
import type { Id, TravelPlan } from '@/api/types'
import { parseFollowUps, type FollowUpQuestion } from './followup'
import { toStreamError, type StreamError } from './stream-error'

export type { StreamError } from './stream-error'
export type { FollowUpOption, FollowUpQuestion } from './followup'

/**
 * 后端在生成计划前下发的意图判定（05 文档 §4）。
 * `followUps` 每项带中文问句与候选值，可直接渲染成可点选的追问。
 */
export interface PlanIntentPayload {
  scene: string
  needPlan: boolean
  missingFields: string[]
  followUps: FollowUpQuestion[]
}

export interface StreamHandlers {
  /** 会话建立后立即回调，用于拿到 sessionId */
  onMeta?: (meta: { sessionId: Id; userMessageId: Id }) => void
  /** 意图判定结果：决定这一轮是「追问」还是「生成计划」 */
  onIntent?: (intent: PlanIntentPayload) => void
  /** 计划生成中的阶段提示（真实模型耗时长，避免聊天框空白） */
  onPlanProgress?: (info: { stage: string; message: string }) => void
  /** 已通过契约校验的计划草稿 */
  onPlanDraft?: (plan: TravelPlan) => void
  /** 落库成功，携带可用于跳转详情的 planId */
  onPlanSaved?: (info: { planId: Id; version: number }) => void
  /** 生成被拒（校验不过 / 信息不足）。紧跟其后还会有 error 事件负责收尾 */
  onPlanRejected?: (info: { reason: string; message: string }) => void
  onDelta: (content: string) => void
  onDone?: (info: { messageId: Id; model: string; latencyMs: number }) => void
  onError?: (error: StreamError) => void
  onFinish?: () => void
}

/**
 * 消费后端 POST /api/chat/stream 的 SSE 流。
 *
 * 为什么不用 EventSource：
 * 1. EventSource 只支持 GET，无法传消息体；
 * 2. EventSource 无法自定义 Authorization 头。
 * 因此用 fetch + ReadableStream 手动解析。
 */
export async function streamChat(
  content: string,
  sessionId: Id | null,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  try {
    const resp = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        Authorization: `Bearer ${getToken()}`,
      },
      body: JSON.stringify({ content, sessionId }),
      signal,
    })

    if (!resp.ok || !resp.body) {
      handlers.onError?.(await toStreamError(resp))
      return
    }

    // 2xx 但内容不是 SSE：说明后端把错误当成普通 Result 返回了（例如统一异常
    // 处理回 200 + code!=0，或者被网关改写）。继续按流解析只会得到一个空气泡，
    // 用户既看不到回复也看不到原因，所以这里直接按失败处理。
    const contentType = resp.headers.get('content-type') ?? ''
    if (!contentType.includes('text/event-stream')) {
      handlers.onError?.(await toStreamError(resp))
      return
    }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // SSE 以空行分隔事件块
      const chunks = buffer.split('\n\n')
      buffer = chunks.pop() || ''
      for (const chunk of chunks) {
        dispatch(chunk, handlers)
      }
    }
    if (buffer.trim()) {
      dispatch(buffer, handlers)
    }
  } catch (e) {
    // 用户点击“停止生成”会触发 AbortError，这是预期行为
    if ((e as Error).name === 'AbortError') {
      return
    }
    handlers.onError?.({
      message: `网络异常：${(e as Error).message || '无法连接服务'}`,
      retryable: true,
    })
  } finally {
    handlers.onFinish?.()
  }
}

function dispatch(chunk: string, handlers: StreamHandlers): void {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of chunk.split('\n')) {
    if (line.startsWith('event:')) {
      event = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim())
    }
  }
  if (dataLines.length === 0) return
  const raw = dataLines.join('\n')
  let payload: Record<string, unknown>
  try {
    payload = JSON.parse(raw)
  } catch {
    return
  }
  switch (event) {
    case 'meta':
      handlers.onMeta?.(payload as never)
      break
    case 'intent':
      handlers.onIntent?.({
        scene: String(payload.scene ?? ''),
        needPlan: Boolean(payload.needPlan),
        missingFields: toStringArray(payload.missingFields),
        followUps: parseFollowUps(payload.followUps),
      })
      break
    case 'plan_progress':
      handlers.onPlanProgress?.({
        stage: String(payload.stage ?? ''),
        message: String(payload.message ?? '正在生成计划…'),
      })
      break
    case 'plan_draft':
      // 计划体较大且结构固定，交给调用方按 TravelPlan 使用；
      // 这里不做字段兜底，避免把「后端少给字段」掩盖成前端的默认值
      if (payload.plan) {
        handlers.onPlanDraft?.(payload.plan as never)
      }
      break
    case 'plan_saved':
      handlers.onPlanSaved?.({
        // 后端已保证以字符串下发（雪花 ID 超出 JS 安全整数）
        planId: String(payload.planId ?? ''),
        version: Number(payload.version ?? 0),
      })
      break
    case 'plan_rejected':
      handlers.onPlanRejected?.({
        reason: String(payload.reason ?? ''),
        message: String(payload.message ?? '计划生成失败'),
      })
      break
    case 'delta':
      handlers.onDelta(String(payload.content ?? ''))
      break
    case 'done':
      handlers.onDone?.(payload as never)
      break
    case 'error':
      // 后端已经明确告知失败原因（如 AI 服务不可用），保留原文并按可重试处理
      handlers.onError?.({
        message: String(payload.message ?? 'AI 服务异常'),
        retryable: true,
      })
      break
    default:
      // 未知事件直接忽略：新增事件不应让旧前端崩掉
      break
  }
}

function toStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}
