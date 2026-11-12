/**
 * 当日执行页文案与判定的回归测试（纯函数，不需要浏览器）。
 *
 * 这组断言的来源：这些文案决定用户「以为自己处在行程的哪一天」。
 * 没出发时显示「第 1 天」、超期时显示「第 5 天」都不会报错，
 * 但会让人按错误的日期出门——所以每个相位单独钉住。
 *
 * 运行：npm run test:today
 */
import {
  canCheckToday,
  clientDate,
  dayProgressText,
  defaultScheduleView,
  defaultStartDate,
  emptyDayText,
  formatDateLabel,
  phaseLabel,
  phaseTitle,
  startDialogHint,
  unanchoredText,
} from '../src/utils/today.ts'

let failures = 0

function check(name, actual, expected) {
  const ok = actual === expected
  if (!ok) failures++
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}` + (ok ? '' : ` -> ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`))
}

function today(overrides) {
  return {
    planId: '1',
    status: 'IN_PROGRESS',
    title: '成都三日',
    date: '2026-11-21',
    phase: 'TODAY',
    dayIndex: 2,
    totalDays: 3,
    total: 4,
    completed: 1,
    percent: 25,
    ...overrides,
  }
}

// ---------------- 本机日期 ----------------
// 关键：不能受 UTC 影响。东八区 23:30 用 toISOString 会变成第二天。
check('晚上 23:30 仍是当天', clientDate(new Date(2026, 10, 21, 23, 30)), '2026-11-21')
check('凌晨 00:10 是当天', clientDate(new Date(2026, 10, 21, 0, 10)), '2026-11-21')
check('月份补零', clientDate(new Date(2026, 0, 5, 12, 0)), '2026-01-05')

// ---------------- 日期展示 ----------------
check('日期带星期', formatDateLabel('2026-11-21'), '11 月 21 日 · 周六')
check('日期不补零', formatDateLabel('2026-01-05'), '1 月 5 日 · 周一')
check('空日期', formatDateLabel(null), '')
check('非法日期原样返回', formatDateLabel('2026/11/21'), '2026/11/21')

// ---------------- 相位文案 ----------------
check('今天：第几天 + 共几天', phaseLabel(today()), '第 2 天 / 共 3 天')
check('今天但缺总天数', phaseLabel(today({ totalDays: null })), '第 2 天')
check('未出发', phaseLabel(today({ phase: 'BEFORE', dayIndex: 1, daysUntilStart: 2 })), '还有 2 天出发')
check('超期', phaseLabel(today({ phase: 'AFTER', dayIndex: 3, daysSinceEnd: 3 })), '已超期 3 天')
check('缺出发日期', phaseLabel(today({ phase: 'UNANCHORED', dayIndex: null })), '还没有出发日期')
check('无数据', phaseLabel(null), '')

check('今天标题', phaseTitle('TODAY'), '今天')
check('未出发标题不是「今天」', phaseTitle('BEFORE'), '出发前预览')
check('超期标题', phaseTitle('AFTER'), '行程已到尾声')
check('缺锚点标题', phaseTitle('UNANCHORED'), '算不出今天')

// ---------------- 空白日 ----------------
check(
  '今天的空白日要给出补充方式',
  emptyDayText('TODAY'),
  '今天还没有安排。在对话里说一句想怎么过，比如「今天想逛街吃火锅」，就能补上。',
)
check('未出发的空白日指向第 1 天', emptyDayText('BEFORE').startsWith('第 1 天还没有安排'), true)
check('超期的空白日不劝人补', emptyDayText('AFTER'), '这一天没有安排。')
check('缺锚点要解释原因', unanchoredText().includes('算不出'), true)

// ---------------- 能否打勾 ----------------
check('今天 + 进行中可打勾', canCheckToday(today(), 'IN_PROGRESS'), true)
check('今天但已确认（没开始）不可打勾', canCheckToday(today({ status: 'CONFIRMED' }), 'CONFIRMED'), false)
check('预览不可打勾', canCheckToday(today({ phase: 'BEFORE' }), 'IN_PROGRESS'), false)
check('超期不可打勾', canCheckToday(today({ phase: 'AFTER' }), 'IN_PROGRESS'), false)
check('无数据不可打勾', canCheckToday(null, 'IN_PROGRESS'), false)

// ---------------- 默认视图 ----------------
check('进行中默认看今日', defaultScheduleView('IN_PROGRESS', today()), 'today')
check('未出发默认看全部', defaultScheduleView('CONFIRMED', today({ phase: 'BEFORE' })), 'all')
check('缺锚点默认看全部', defaultScheduleView('IN_PROGRESS', today({ phase: 'UNANCHORED' })), 'all')
check('已完成默认看全部', defaultScheduleView('COMPLETED', today({ status: 'COMPLETED' })), 'all')
check('无数据默认看全部', defaultScheduleView('IN_PROGRESS', null), 'all')

// ---------------- 汇总文案 ----------------
check('当日进度', dayProgressText(today({ day: { total: 2, completed: 1, items: [] } })), '今天 1 / 2 项已完成')
check('空白日不显示进度', dayProgressText(today({ day: { total: 0, completed: 0, items: [] } })), '')
check('无卡片不显示进度', dayProgressText(today({ day: null })), '')

// ---------------- 开始旅行的出发日 ----------------
check('已有出发日就沿用', defaultStartDate('2026-11-20', new Date(2026, 10, 21, 10, 0)), '2026-11-20')
check('没有出发日用本机今天', defaultStartDate(null, new Date(2026, 10, 21, 10, 0)), '2026-11-21')
check('提示语说清会改第 1 天', startDialogHint(null).includes('选定的那天就是第 1 天'), true)
check('已有日期时提示可改', startDialogHint('2026-11-20').includes('2026-11-20'), true)

if (failures > 0) {
  console.error(`\n${failures} 项失败`)
  process.exit(1)
}
console.log('\n全部通过')
