/**
 * SSE 注释行（心跳）不得变成用户可见消息。
 *
 * 背景（R7）：后端每 15s 发一条 `:ping` 注释行，防止反代把静默的长连接当作空闲掐断。
 * 按 SSE 规范注释不进 onmessage，但我们的解析器是手写的（不是 EventSource），
 * 「没有 data 就丢弃」这条规则必须被钉住——否则一次误改就会让页面多出一堆空消息，
 * 或者更糟：把 `:ping` 当成 payload 送进 JSON.parse，整天在控制台刷错。
 *
 * 运行：npm run test:sse-frame
 */
import { parseFrame } from '../src/api/sse-frame.ts'

let failures = 0

function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  if (!ok) failures++
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}` + (ok ? '' : ` -> ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`))
}

// 1. 心跳：单行注释，必须整帧丢弃
check('注释行 :ping', parseFrame(':ping\n\n'), null)
check('注释行带空格 : ping', parseFrame(': ping\n\n'), null)

// 2. 心跳夹在业务事件之间时，仍然只有业务事件被解析
check(
  '注释与事件混合',
  parseFrame('event:plan_progress\ndata:{"stage":"generating"}\n\n'),
  { event: 'plan_progress', data: '{"stage":"generating"}' },
)

// 3. 空帧（只有换行）不产生消息
check('空帧', parseFrame('\n'), null)
check('空字符串', parseFrame(''), null)

// 4. 普通 message（无 event 名）默认走 message
check('默认 event', parseFrame('data:{"content":"你好"}'), { event: 'message', data: '{"content":"你好"}' })

// 5. 多行 data 按规范用换行拼接
check('多行 data', parseFrame('data:a\ndata:b'), { event: 'message', data: 'a\nb' })

// 6. 只有 event 没有 data：不是有效帧（否则前端会收到一个没有载荷的事件）
check('只有 event', parseFrame('event:done\n\n'), null)

if (failures > 0) {
  console.error(`\n${failures} 项失败`)
  process.exit(1)
}
console.log('\n全部通过：心跳注释不会变成用户可见消息')
