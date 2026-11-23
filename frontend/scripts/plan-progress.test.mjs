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

// 4. 到 3 秒开始显示，但先不给「长行程」的暗示
check('3 秒', formatProgress(MSG, T0, T0 + 3_000), `${MSG} 已等待 3 秒`)

// 5. 40 秒（pro 模型常见耗时的下沿）——秒数必须跟着走，且开始解释「为什么还没好」
check('40 秒', formatProgress(MSG, T0, T0 + 40_000),
  `${MSG} 已等待 40 秒（长行程要一分多钟，先别刷新，也别重复发送）`)

// 6. 向下取整，不出现 12.7 秒这种值
check('小数秒', formatProgress(MSG, T0, T0 + 12_700), `${MSG} 已等待 12 秒`)

// 6.1 29 秒还是安静版，30 秒才升级提示——差 1 秒不能同一句话
check('29 秒', formatProgress(MSG, T0, T0 + 29_000), `${MSG} 已等待 29 秒`)
check('30 秒', formatProgress(MSG, T0, T0 + 30_000),
  `${MSG} 已等待 30 秒（长行程要一分多钟，先别刷新，也别重复发送）`)

// 6.2 超过 2 分钟要交底：说清「会自动结束」，避免用户以为要无限等
check('120 秒', formatProgress(MSG, T0, T0 + 120_000),
  `${MSG} 已等待 120 秒（超过 4 分钟会自动结束并告诉你原因，不用一直等）`)

// 7. 时钟回拨（时间戳比开始还早）时不显示负数
check('时钟回拨', formatProgress(MSG, T0, T0 - 5_000), MSG)

if (failures > 0) {
  console.error(`\n${failures} 项失败`)
  process.exit(1)
}
console.log('\n全部通过')
