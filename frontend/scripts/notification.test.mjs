/**
 * 通知展示规则的纯函数测试。
 *
 * 时间文案是这里最容易错的一环：跨天、跨年、时区、时钟偏差四种情况里，
 * 只要一种算错，用户看到的就是「未来时间」或「51 小时前」这类明显不合理的字。
 *
 * 运行：npm run test:notification
 */
import {
  badgeText,
  notificationTarget,
  relativeTime,
  typeIcon,
} from '../src/utils/notification.ts'

let failures = 0

function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  if (!ok) failures++
  console.log(
    `${ok ? 'PASS' : 'FAIL'} ${name}` +
      (ok ? '' : ` -> ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`),
  )
}

// 固定「现在」：2026-11-23 16:00:00（周一）
const NOW = new Date(2026, 10, 23, 16, 0, 0)

// ---------- 图标 ----------

check('已知类型有图标', typeIcon('PLAN_STARTED'), '▶')
check('未知类型退化成圆点，不显示 undefined', typeIcon('SOMETHING_NEW'), '•')

// ---------- 相对时间 ----------

check('一分钟内：刚刚', relativeTime('2026-11-23T15:59:30', NOW), '刚刚')
check('时钟偏差也不算负时间', relativeTime('2026-11-23T16:02:00', NOW), '刚刚')
check('分钟级', relativeTime('2026-11-23T15:45:00', NOW), '15 分钟前')
check('当天小时级', relativeTime('2026-11-23T09:00:00', NOW), '7 小时前')
check('昨天带具体时刻', relativeTime('2026-11-22T21:05:00', NOW), '昨天 21:05')
check('今年更早只说日期', relativeTime('2026-11-02T08:00:00', NOW), '11 月 2 日')
check('跨年补上年份', relativeTime('2025-12-31T23:30:00', NOW), '2025 年 12 月 31 日')
check('空值不炸', relativeTime(null, NOW), '')
check('非法字符串不炸', relativeTime('not-a-date', NOW), '')
// 后端用 T 分隔且不带时区：必须按本地时间理解，否则东八区会整体差 8 小时
check('带 T 的本地时间按本地解析', relativeTime('2026-11-23T15:00:00', NOW), '1 小时前')
check('微秒精度也能解析', relativeTime('2026-11-23T15:00:00.000123', NOW), '1 小时前')

// ---------- 红点 ----------

check('0 条不显示', badgeText(0), '')
check('负数当没有', badgeText(-1), '')
check('个位数原样', badgeText(3), '3')
check('99 还是数字', badgeText(99), '99')
check('超过 99 折成 99+', badgeText(120), '99+')

// ---------- 跳转 ----------

check('有计划的跳详情', notificationTarget({ planId: '2119988130910269442' }), '/plans/2119988130910269442')
check('没有计划的不跳', notificationTarget({ planId: null }), null)
check('字段缺失也不跳', notificationTarget({}), null)

if (failures > 0) {
  console.error(`\n${failures} 项失败`)
  process.exit(1)
}
console.log('\n全部通过')
