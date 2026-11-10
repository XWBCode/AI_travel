import type { ServerResponse } from 'node:http'

/** 后端未启动 / 正在重启时，代理连不上目标会抛这些错误码 */
const CONNECT_ERRORS = new Set(['ECONNREFUSED', 'ECONNRESET', 'ETIMEDOUT', 'EPIPE', 'EHOSTUNREACH'])

export interface ProxyErrorPayload {
  status: number
  body: string
}

/**
 * 把代理层错误翻译成给前端的响应。
 *
 * Vite 默认的代理错误处理只会回 500 + 空 body（text/plain），前端拿不到任何
 * 原因，只能显示「请求失败(500)」——把「后端没起来」误报成了业务错误。
 * 这里改成带中文说明的 503/502 JSON，前端可直接展示。
 */
export function describeProxyError(err: NodeJS.ErrnoException): ProxyErrorPayload {
  const down = CONNECT_ERRORS.has(err.code ?? '')
  const status = down ? 503 : 502
  const message = down
    ? '后端服务不可用：可能尚未启动或正在重启，请稍后重试'
    : `后端连接异常：${err.message}`
  return {
    status,
    body: JSON.stringify({ code: down ? 50300 : 50200, message, data: null }),
  }
}

/** http-proxy 的 error 事件处理器（res 为对客户端的响应对象） */
export function onProxyError(err: NodeJS.ErrnoException, res: unknown): void {
  const response = res as ServerResponse | undefined
  if (!response || typeof response.writeHead !== 'function') return
  // 已经提交过响应头就不能再写，否则会二次提交报错
  if (response.headersSent || response.writableEnded) return
  const { status, body } = describeProxyError(err)
  response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' })
  response.end(body)
}
