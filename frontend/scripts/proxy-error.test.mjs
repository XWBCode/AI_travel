/**
 * 代理错误分支的回归测试。
 *
 * 背景：后端没启动 / 正在重启时，Vite 默认会回一个 500 + 空 body，前端只能显示
 * 「请求失败(500)」，看起来像业务错误。vite.proxy.ts 把这个分支改写成了带中文
 * 说明的 503/502 JSON，这里锁住它的行为。
 *
 * 运行：npm run test:proxy
 */
import { onProxyError, describeProxyError } from '../vite.proxy.ts'

function mockRes() {
  return {
    headersSent: false,
    writableEnded: false,
    status: undefined,
    out: null,
    writeHead(status, headers) {
      this.status = status
      this.headers = headers
      this.headersSent = true
      return this
    },
    end(body) {
      this.out = body
      this.writableEnded = true
    },
  }
}

let failures = 0

function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  if (!ok) failures++
  console.log(
    `${ok ? 'PASS' : 'FAIL'} ${name}` + (ok ? '' : ` -> ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`),
  )
}

let res = mockRes()
onProxyError(Object.assign(new Error('connect ECONNREFUSED'), { code: 'ECONNREFUSED' }), res)
check('ECONNREFUSED 映射为 503/50300', [res.status, JSON.parse(res.out).code], [503, 50300])
check('ECONNREFUSED 文案说明后端未就绪', JSON.parse(res.out).message.includes('尚未启动或正在重启'), true)
check('Content-Type 为 JSON', res.headers['Content-Type'].startsWith('application/json'), true)

res = mockRes()
onProxyError(Object.assign(new Error('socket hang up'), { code: 'ECONNRESET' }), res)
check('ECONNRESET 映射为 503', res.status, 503)

res = mockRes()
onProxyError(Object.assign(new Error('boom'), { code: 'EPROTO' }), res)
check('无法归因的代理错误映射为 502/50200', [res.status, JSON.parse(res.out).code], [502, 50200])

res = mockRes()
res.headersSent = true
onProxyError(Object.assign(new Error('x'), { code: 'ECONNREFUSED' }), res)
check('响应头已提交时不再二次写入', [res.out, res.status], [null, undefined])

onProxyError(Object.assign(new Error('x'), { code: 'ECONNRESET' }), { upgrade: true })
check('res 不是 ServerResponse 时不抛错', true, true)

check('describeProxyError 返回可解析的 JSON body', typeof JSON.parse(describeProxyError({ code: 'ECONNREFUSED', message: '' }).body).message, 'string')

console.log(failures === 0 ? '\n全部通过' : `\n${failures} 项失败`)
process.exit(failures === 0 ? 0 : 1)
