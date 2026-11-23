import type { NotificationType } from '@/api/types'

/**
 * 通知的展示规则。
 *
 * 这些是纯函数而不是写死在模板里：时间文案和时间本身一样容易被算错
 * （时区、跨天、刚刚），而「3 分钟前」写成「1 小时后」这种错，用户一眼就能看出来，
 * 却极难在界面上复现——所以它必须能被测。
 */

const TYPE_ICONS: Record<NotificationType, string> = {
  PLAN_CONFIRMED: '✓',
  PLAN_STARTED: '▶',
  PLAN_COMPLETED: '🏁',
  PLAN_CANCELLED: '✕',
  ADJUSTMENT_CONFIRMED: '✎',
}

/** 未知类型不抛错也不显示空白：后端加了新事件，旧前端至少还能看 */
export function typeIcon(type: string): string {
  return TYPE_ICONS[type as NotificationType] ?? '•'
}

/**
 * 后端下发的是不带时区的 LocalDateTime（`2026-11-23T16:02:15`）。
 * `new Date()` 对带 T 的字符串在部分环境会按 UTC 解释，所以统一换成空格形式，
 * 让浏览器按本地时间解析——否则东八区用户会看到「8 小时前」。
 */
function parseLocal(iso: string): Date {
  const trimmed = iso.replace('T', ' ').replace(/(\.\d{3})\d+/, '$1')
  return new Date(trimmed)
}

function pad(n: number): string {
  return n < 10 ? `0${n}` : String(n)
}

/**
 * 相对时间。分档考虑的是「用户此刻想知道的精度」：
 * 一分钟内不用报数字；当天的说几小时前；昨天以前说日期。
 *
 * @param now 便于测试注入；生产调用不传
 */
export function relativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return ''
  const at = parseLocal(iso)
  if (Number.isNaN(at.getTime())) return ''

  const diffMs = now.getTime() - at.getTime()
  // 时钟偏差（服务端比客户端快几秒）不该显示成「-1 分钟前」
  if (diffMs < 60_000) return '刚刚'
  if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)} 分钟前`

  const sameDay =
    at.getFullYear() === now.getFullYear() &&
    at.getMonth() === now.getMonth() &&
    at.getDate() === now.getDate()
  if (sameDay) return `${Math.floor(diffMs / 3_600_000)} 小时前`

  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1)
  const isYesterday =
    at.getFullYear() === yesterday.getFullYear() &&
    at.getMonth() === yesterday.getMonth() &&
    at.getDate() === yesterday.getDate()
  if (isYesterday) return `昨天 ${pad(at.getHours())}:${pad(at.getMinutes())}`

  const monthDay = `${at.getMonth() + 1} 月 ${at.getDate()} 日`
  // 跨年后「3 月 5 日」会有歧义，补上年份
  return at.getFullYear() === now.getFullYear()
    ? monthDay
    : `${at.getFullYear()} 年 ${monthDay}`
}

/** 红点数字：超过 99 就写 99+，不为了让数字准确而把顶栏撑开 */
export function badgeText(count: number): string {
  if (!count || count <= 0) return ''
  return count > 99 ? '99+' : String(count)
}

/** 点击通知跳去哪；没有关联计划的（将来的系统通知）不跳 */
export function notificationTarget(n: { planId?: string | null }): string | null {
  return n.planId ? `/plans/${n.planId}` : null
}
