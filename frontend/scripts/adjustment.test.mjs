/**
 * 调整建议差异文案的纯函数测试。
 *
 * 为什么单独测：这段文案是用户判断「要不要接受这次调整」的唯一依据。
 * 视觉上「删除」和「修改」只差一个字的标签色，但点错一次的结果完全不同。
 *
 * 运行：npm run test:adjust
 */
import {
  canConfirm,
  fieldLabel,
  formatValue,
  opsSummary,
  renderOp,
  renderOps,
  staleHint,
} from '../src/utils/adjustment.ts'

let failures = 0

function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  if (!ok) failures++
  console.log(
    `${ok ? 'PASS' : 'FAIL'} ${name}` +
      (ok ? '' : ` -> ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`),
  )
}

// ---------- 字段与取值 ----------

check('已知字段用中文', fieldLabel('startTime'), '开始时间')
check('未知字段原样显示，不吞掉信息', fieldLabel('foo'), 'foo')
check('null 显示为未设置', formatValue(null), '未设置')
check('空串也算未设置', formatValue(''), '未设置')
check('有值原样返回', formatValue('15:30'), '15:30')

// ---------- 六种操作的渲染 ----------

check('删除：标出天与对象', renderOp({
  op: 'REMOVE_ITEM', itemId: '1', dayIndex: 2, title: '都江堰',
}), {
  kind: 'REMOVE_ITEM', tag: '删除', title: '第 2 天 · 都江堰', lines: [],
})

check('新增：带时间段与备注', renderOp({
  op: 'ADD_ITEM', dayIndex: 2, title: '回酒店休息',
  startTime: '15:00', endTime: '17:00', note: '上午走得多',
}), {
  kind: 'ADD_ITEM', tag: '新增', title: '第 2 天 · 回酒店休息',
  lines: ['15:00 – 17:00', '上午走得多'],
})

check('新增：没有时间就不显示时间段', renderOp({
  op: 'ADD_ITEM', dayIndex: 1, title: '自由活动',
}), {
  kind: 'ADD_ITEM', tag: '新增', title: '第 1 天 · 自由活动', lines: [],
})

check('修改：每处变更一行，原值来自服务端', renderOp({
  op: 'UPDATE_ITEM', itemId: '1', dayIndex: 1, title: '宽窄巷子',
  changes: [{ field: 'startTime', before: '09:00', after: '10:30' }],
}), {
  kind: 'UPDATE_ITEM', tag: '修改', title: '第 1 天 · 宽窄巷子',
  lines: ['开始时间：09:00 → 10:30'],
})

check('修改：清空备注要看得见「→ 未设置」', renderOp({
  op: 'UPDATE_ITEM', itemId: '1', dayIndex: 1, title: '宽窄巷子',
  changes: [{ field: 'note', before: '慢慢逛', after: null }],
}).lines, ['备注：慢慢逛 → 未设置'])

check('移动：显示从天到天', renderOp({
  op: 'MOVE_ITEM', itemId: '1', title: '都江堰', fromDayIndex: 3, toDayIndex: 1,
}), {
  kind: 'MOVE_ITEM', tag: '移动', title: '都江堰', lines: ['第 3 天 → 第 1 天'],
})

check('重排：说明项数，不列具体顺序', renderOp({
  op: 'REORDER_ITEM', dayIndex: 1, orderedItemIds: ['a', 'b', 'c'],
}), {
  kind: 'REORDER_ITEM', tag: '重排', title: '第 1 天的顺序', lines: ['共 3 项重新排序'],
})

check('改天：主题变更', renderOp({
  op: 'UPDATE_DAY', dayIndex: 2,
  changes: [{ field: 'theme', before: '初到', after: '慢下来' }],
}), {
  kind: 'UPDATE_DAY', tag: '修改', title: '第 2 天', lines: ['当日主题：初到 → 慢下来'],
})

check('空 ops 渲染为空列表，不产生占位行', renderOps([]), [])
check('undefined ops 也不炸', renderOps(undefined), [])

// ---------- 概览 ----------

check('概览只数条数', opsSummary([{ op: 'REMOVE_ITEM' }, { op: 'ADD_ITEM' }]), '2 处改动')
check('没有改动时概览为空串', opsSummary([]), '')

// ---------- 能否确认 ----------

const pending = {
  adjustmentId: '1', planId: '9', baseVersion: 3, status: 'PENDING',
  instruction: 'x', summary: 'y', ops: [], planVersion: 3,
}

check('版本一致可以确认', canConfirm(pending, 3), true)
check('版本不一致不能确认：会覆盖期间的修改', canConfirm(pending, 4), false)
check('已处理的建议不能再次确认', canConfirm({ ...pending, status: 'CONFIRMED' }, 3), false)
check('没有建议时不能确认', canConfirm(null, 3), false)

check('版本一致时没有过期提示', staleHint(pending, 3), '')
check('版本不一致给出可执行的提示', staleHint(pending, 4).includes('重新生成'), true)

if (failures > 0) {
  console.error(`\n${failures} 项失败`)
  process.exit(1)
}
console.log('\n全部通过')
