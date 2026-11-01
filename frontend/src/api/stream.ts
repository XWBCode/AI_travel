import { getToken } from '@/api/http'

export interface StreamHandlers {
  /** 会话建立后立即回调，用于拿到 sessionId */
  onMeta?: (meta: { sessionId: number; userMessageId: number }) => void
  onDelta: (content: string) => void
  onDone?: (info: { messageId: number; model: string; latencyMs: number }) => void
  onError?: (message: string) => void
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
  sessionId: number | null,
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
      const text = await resp.text().catch(() => '')
      handlers.onError?.(text || `请求失败(${resp.status})`)
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
    handlers.onError?.((e as Error).message || '网络异常')
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
      handlers.onError?.(String(payload.message ?? 'AI 服务异常'))
      break
    default:
      break
  }
}
