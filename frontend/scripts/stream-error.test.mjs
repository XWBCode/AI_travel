/**
 * 流式错误文案的回归测试（纯函数，不需要浏览器）。
 *
 * 背景：后端没启动时旧代码只会显示「请求失败(500)」，用户看不出是服务没起来。
 * 现在要求：有 body 用 body 的 message，没 body 按状态码给中文兜底，并把
 * 「能不能重试」标出来。
 *
 * 运行：npm run test:stream-error
 */
import { toStreamError, extractMessage, hintOf } from '../src/api/stream-error.ts'

let failures = 0

function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  if (!ok) failures++
  console.log(
    `${ok ? 'PASS' : 'FAIL'} ${name}` + (ok ? '' : ` -> ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`),
  )
}

// 1. 代理层 503 + JSON（本项目 vite.proxy.ts 的输出）
const proxy503 = new Response(
  JSON.stringify({ code: 50300, message: '后端服务不可用：可能尚未启动或正在重启，请稍后重试', data: null }),
  { status: 503, headers: { 'Content-Type': 'application/json; charset=utf-8' } },
)
const r1 = await toStreamError(proxy503)
check('503 取 body 里的中文原因', r1.message.includes('尚未启动或正在重启'), true)
check('503 标记为可重试', [r1.status, r1.retryable], [503, true])

// 2. 空 body 的 500（Vite 旧行为）：不能再出现裸「请求失败(500)」
const r2 = await toStreamError(new Response('', { status: 500 }))
check('空 body 500 走中文兜底', r2.message, '服务端处理失败，请稍后重试')
check('空 body 500 可重试', r2.retryable, true)

// 3. 后端 Result 业务失败（401/403/404）：给出明确原因且不建议重试
const r3 = await toStreamError(
  new Response(JSON.stringify({ code: 40100, message: '登录已过期，请重新登录', data: null }), {
    status: 401,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  }),
)
check('401 保留后端文案', r3.message, '登录已过期，请重新登录')
check('401 不建议重试', r3.retryable, false)

// 4. 429 是可重试的 4xx
const r4 = await toStreamError(new Response('', { status: 429 }))
check('429 可重试', [r4.message, r4.retryable], ['请求过于频繁，请稍后再试', true])

// 5. HTML 错误页不直接甩给用户
const r5 = await toStreamError(new Response('<html><body>502 Bad Gateway</body></html>', { status: 502 }))
check('HTML 错误页被丢弃，走兜底文案', r5.message, '网关错误，后端可能正在重启，请稍后重试')

// 6. 纯文本错误保留（截断到 200 字）
const r6 = await toStreamError(new Response('upstream connect failed: 10061', { status: 502 }))
check('纯文本错误保留原文', r6.message, 'upstream connect failed: 10061')

// 7. 辅助函数
check('未知状态码兜底', hintOf(418), '请求失败(418)')
check('非 JSON 的 message 提取为空', extractMessage('{"message":123}'), '')
check('空串不产出文案', extractMessage('   '), '')

// 8. 2xx 但不是流（后端把校验错误当成 200 + JSON 返回）：不能当成功
const r8 = await toStreamError(
  new Response(JSON.stringify({ code: 10001, message: '单条消息不能超过 4000 字', data: null }), {
    status: 200,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  }),
)
check('200 + JSON 取业务文案', r8.message, '单条消息不能超过 4000 字')
check('200 + 非流式响应标记可重试', r8.retryable, true)
check('200 且无 message 时给格式异常兜底', hintOf(200), '响应格式异常（不是流式响应），请稍后重试')

console.log(failures === 0 ? '\n全部通过' : `\n${failures} 项失败`)
process.exit(failures === 0 ? 0 : 1)
