import { getToken } from '@/api/http'
import type { Id } from '@/api/types'
import { toStreamError, type StreamError } from './stream-error'

export type { StreamError } from './stream-error'

export interface StreamHandlers {
  /** 会话建立后立即回调，用于拿到 sessionId */
  onMeta?: (meta: { sessionId: Id; userMessageId: Id }) => void
  onDelta: (content: string) => void
  onDone?: (info: { messageId: number; model: string; latencyMs: number }) => void
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
      break
  }
}
