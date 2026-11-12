/**
 * 计划编辑表单的回归测试（纯函数，不需要浏览器）。
 *
 * 背景：「改天数结束日期要跟着变」「选日期天数要跟着变」这两条规则，
 * 前端与后端各实现了一份（后端是权威，前端是为了让输入框别打架）。
 * 两边一旦不一致，用户就会看到「保存成功但值没变」或「天数与日期不一致」的报错。
 * 这里把前端的规则钉死，改坏了立刻能发现。
 *
 * 运行：npm run test:plan-edit
 */
import {
  addDays,
  buildPatch,
  canEditDestination,
  daysBetween,
  daysFromRange,
  endDateFromDays,
  hasChanges,
  validateDraft,
  withDays,
  withRange,
} from '../src/utils/planEdit.ts'

let failures = 0

function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  if (!ok) failures++
  console.log(
    `${ok ? 'PASS' : 'FAIL'} ${name}` +
      (ok ? '' : ` -> ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`),
  )
}

const base = {
  title: '成都三日游',
  originCity: '杭州',
  destination: '成都',
  days: 3,
  travelers: 2,
  startDate: '2026-11-20',
  endDate: '2026-11-22',
}

// ---------- 日期算术 ----------

check('跨月加天数', addDays('2026-11-29', 3), '2026-12-02')
check('跨年加天数', addDays('2026-12-30', 5), '2027-01-04')
// 用 UTC 计算，避免本地时区把日期算偏一天
check('区间含首尾', daysBetween('2026-11-20', '2026-11-22'), 3)
check('同一天是 1 天', daysBetween('2026-11-20', '2026-11-20'), 1)

// ---------- 联动规则 ----------

check('天数推结束日', endDateFromDays('2026-11-20', 4), '2026-11-23')
check('缺出发日推不出', endDateFromDays(null, 4), null)

const longer = withDays(base, 5)
check('改天数：结束日跟着走', [longer.days, longer.endDate], [5, '2026-11-24'])
check('改天数：出发日不动', longer.startDate, '2026-11-20')

const shifted = withRange(base, '2026-12-01', '2026-12-05')
check('改区间：天数反推', [shifted.days, shifted.startDate, shifted.endDate], [5, '2026-12-01', '2026-12-05'])

const cleared = withRange(base, null, null)
check('清空区间：保留原天数，不硬造日期', [cleared.days, cleared.startDate, cleared.endDate], [3, null, null])

// ---------- PATCH 请求体 ----------

check('没改动：空对象', buildPatch(base, { ...base }), {})
check('只改标题：不带其他字段', buildPatch(base, { ...base, title: '成都四日游' }), {
  title: '成都四日游',
})
check(
  '改天数：天数与结束日一起提交',
  buildPatch(base, withDays(base, 4)),
  { days: 4, endDate: '2026-11-23' },
)
check(
  '清空出发地：显式传 null（不是删掉这个 key）',
  buildPatch(base, { ...base, originCity: '   ' }),
  { originCity: null },
)
check('首尾空格不算改动', hasChanges(base, { ...base, title: '成都三日游  ' }), false)
check('有改动能被识别', hasChanges(base, withDays(base, 4)), true)

// ---------- 表单校验 ----------

check('标题不能为空', validateDraft({ ...base, title: '   ' }), '标题不能为空')
check('天数超上限', validateDraft({ ...base, days: 31 }), '天数应在 1-30 之间')
check('人数超上限', validateDraft({ ...base, travelers: 51 }), '人数应在 1-50 之间')
check('日期倒置', validateDraft({ ...base, startDate: '2026-11-22', endDate: '2026-11-20' }), '结束日期不能早于开始日期')
check('合法草稿通过', validateDraft(base), null)

// ---------- 目的地可编辑性 ----------

check(
  '单目的地可改',
  canEditDestination({ ...base, basic: { destinations: ['成都'] } }),
  true,
)
check(
  '多目的地不可改（与后端一致）',
  canEditDestination({ ...base, basic: { destinations: ['成都', '乐山'] } }),
  false,
)

if (failures > 0) {
  console.error(`\n${failures} 项失败`)
  process.exit(1)
}
console.log('\n全部通过')
