/**
 * 生成等待文案的回归测试（纯函数，不需要浏览器）。
 *
 * 背景：真实模型出一份计划要 30-45s。第一版只显示「正在生成计划…」，
 * 用户会以为页面卡死；但秒数又不能一上来就显示——模型秒回时数字闪一下更像故障。
 *
 * 运行：npm run test:plan-progress
 */
import { formatProgress } from '../src/utils/planProgress.ts'

let failures = 0

function check(name, actual, expected) {
  const ok = actual === expected
  if (!ok) failures++
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}` + (ok ? '' : ` -> ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`))
}

const MSG = '正在生成计划…'
const T0 = 1_700_000_000_000

// 1. 没有起始时间：原样返回，不凭空造一个秒数
check('缺起始时间', formatProgress(MSG, undefined, T0), MSG)

// 2. 空提示：什么都不显示（进度条已由模板的 v-if 隐藏，这里只是兜底）
check('空提示', formatProgress('', T0, T0 + 10_000), '')

// 3. 前 3 秒不显示秒数
check('1 秒', formatProgress(MSG, T0, T0 + 1_000), MSG)
check('2 秒', formatProgress(MSG, T0, T0 + 2_999), MSG)

// 4. 到 3 秒开始显示
check('3 秒', formatProgress(MSG, T0, T0 + 3_000), `${MSG} 已等待 3 秒（长行程通常需要半分钟以上）`)

// 5. 40 秒（真实模型常见耗时）——秒数必须跟着走，不能写死 3 秒
check('40 秒', formatProgress(MSG, T0, T0 + 40_000), `${MSG} 已等待 40 秒（长行程通常需要半分钟以上）`)

// 6. 向下取整，不出现 12.7 秒这种值
check('小数秒', formatProgress(MSG, T0, T0 + 12_700), `${MSG} 已等待 12 秒（长行程通常需要半分钟以上）`)

// 7. 时钟回拨（时间戳比开始还早）时不显示负数
check('时钟回拨', formatProgress(MSG, T0, T0 - 5_000), MSG)

if (failures > 0) {
  console.error(`\n${failures} 项失败`)
  process.exit(1)
}
console.log('\n全部通过')
