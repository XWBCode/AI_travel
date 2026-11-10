/**
 * 流式请求的错误翻译（纯函数，无浏览器/框架依赖，便于单测）。
 *
 * 三层来源要统一成一句人能看懂的话：
 * 1. 后端业务失败：Result 结构，带 message；
 * 2. 代理层失败（开发期 Vite / 生产 Nginx）：可能只有状态码；
 * 3. 网络层失败（请求根本没发出去）：连状态码都没有。
 */
export interface StreamError {
  /** HTTP 状态码；网络层失败（请求根本没发出去）时为 undefined */
  status?: number
  /** 提示给用户的可读文案 */
  message: string
  /** 是否值得让用户直接点「重试」——4xx 里只有 429 值得 */
  retryable: boolean
}

/** 无响应体时按状态码兜底的可读文案，避免只给用户看一个裸数字 */
export const STATUS_HINTS: Record<number, string> = {
  200: '响应格式异常（不是流式响应），请稍后重试',
  400: '请求参数有误，请调整后重试',
  401: '登录已过期，请重新登录',
  403: '无权访问该会话',
  404: '会话不存在，可能已被删除',
  429: '请求过于频繁，请稍后再试',
  500: '服务端处理失败，请稍后重试',
  502: '网关错误，后端可能正在重启，请稍后重试',
  503: '后端服务暂不可用（未启动或正在重启），请稍后重试',
  504: '后端响应超时，请稍后重试',
}

export function hintOf(status: number): string {
  return STATUS_HINTS[status] || `请求失败(${status})`
}

/**
 * 把一条失败响应翻译成可展示的错误对象。
 * 后端统一 Result 会带 message；代理层出错时可能是空 body 或纯文本，两者都要兜住。
 */
export async function toStreamError(resp: Response): Promise<StreamError> {
  const status = resp.status
  const raw = await resp.text().catch(() => '')
  return {
    status,
    message: extractMessage(raw) || hintOf(status),
    // 5xx / 429 是临时的，2xx 但不是流（服务端行为异常）也值得重来一次；
    // 其余 4xx 重试没有意义，得先改请求或重新登录。
    retryable: status === 429 || status >= 500 || (status >= 200 && status < 300),
  }
}

/** 从响应体里尽力取出错误描述：优先 JSON.message，其次纯文本，HTML 错误页直接丢弃 */
export function extractMessage(raw: string): string {
  const trimmed = (raw ?? '').trim()
  if (!trimmed) return ''
  if (trimmed.startsWith('{')) {
    try {
      const body = JSON.parse(trimmed) as { message?: unknown }
      return typeof body.message === 'string' ? body.message.trim() : ''
    } catch {
      return ''
    }
  }
  // Tomcat / Nginx 的默认错误页是 HTML，给用户看没有意义
  if (trimmed.startsWith('<')) return ''
  return trimmed.slice(0, 200)
}
