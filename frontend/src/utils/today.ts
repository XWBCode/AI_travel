import type { PlanTodayVO, TodayPhase } from '@/api/types'

/**
 * 当日执行页的文案与判定（纯函数）。
 *
 * 抽成纯函数的原因：这些文案决定「用户以为发生了什么」。比如没出发时若只显示
 * 「第 1 天」，用户会以为自己已经迟到了一天；超期时若显示「第 5 天」，用户会
 * 以为行程还在继续。这些分支没法在后台标签页里靠截图回归（定时器会降频），
 * 只能靠断言。
 */

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

/** 本机今天，YYYY-MM-DD。不用 toISOString：那是 UTC，东八区晚上 8 点后会早一天 */
export function clientDate(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

/** 2026-11-21 → 「11 月 21 日 · 周六」。按 UTC 构造，避免本地时区把日期推前一天 */
export function formatDateLabel(date?: string | null): string {
  if (!date) return ''
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date)
  if (!match) return date
  const [, y, m, d] = match
  const at = new Date(Date.UTC(Number(y), Number(m) - 1, Number(d)))
  return `${Number(m)} 月 ${Number(d)} 日 · ${WEEKDAYS[at.getUTCDay()]}`
}

/** 页头左侧那句「现在是第几天 / 还有几天出发」 */
export function phaseLabel(today: PlanTodayVO | null): string {
  if (!today) return ''
  switch (today.phase) {
    case 'TODAY':
      return today.totalDays ? `第 ${today.dayIndex} 天 / 共 ${today.totalDays} 天` : `第 ${today.dayIndex} 天`
    case 'BEFORE':
      return `还有 ${today.daysUntilStart} 天出发`
    case 'AFTER':
      return `已超期 ${today.daysSinceEnd} 天`
    default:
      return '还没有出发日期'
  }
}

/** 页头大标题：只有今天才配叫「今天」，其余相位必须说清是预览还是超期 */
export function phaseTitle(phase: TodayPhase): string {
  switch (phase) {
    case 'TODAY':
      return '今天'
    case 'BEFORE':
      return '出发前预览'
    case 'AFTER':
      return '行程已到尾声'
    default:
      return '算不出今天'
  }
}

/** 空白日的说明。三种相位说法不同：今天空 = 还能补，超期空 = 只是没安排 */
export function emptyDayText(phase: TodayPhase): string {
  switch (phase) {
    case 'TODAY':
      return '今天还没有安排。在对话里说一句想怎么过，比如「今天想逛街吃火锅」，就能补上。'
    case 'BEFORE':
      return '第 1 天还没有安排。出发前可以在对话里补一句，免得到了地方现找。'
    default:
      return '这一天没有安排。'
  }
}

/** 没有出发日时的引导语：说清「为什么算不出来」，而不是甩一句「无数据」 */
export function unanchoredText(): string {
  return '这份计划只有天数、没有出发日期，所以系统算不出「今天」对应第几天。'
}

/** 只有「今天 + 进行中」才能打勾：预览和超期都不该留下进度 */
export function canCheckToday(today: PlanTodayVO | null, status: string): boolean {
  return !!today && today.phase === 'TODAY' && status === 'IN_PROGRESS'
}

/** 默认落在「今日」还是「全部日程」：进行中且能算出今天，才默认今日 */
export function defaultScheduleView(status: string, today: PlanTodayVO | null): 'today' | 'all' {
  if (status !== 'IN_PROGRESS' || !today) return 'all'
  return today.phase === 'UNANCHORED' ? 'all' : 'today'
}

/** 当日进度的一句话总结，避免用户自己数 */
export function dayProgressText(today: PlanTodayVO | null): string {
  const day = today?.day
  if (!day || day.total === 0) return ''
  return `今天 ${day.completed} / ${day.total} 项已完成`
}

/** 开始旅行的默认出发日：计划有日期就沿用，没有就用本机今天 */
export function defaultStartDate(planStartDate: string | null | undefined, now: Date = new Date()): string {
  return planStartDate || clientDate(now)
}

/** 出发日选择器的提示语。有日期时说「可以改」，没有时说「这就是第 1 天」 */
export function startDialogHint(planStartDate: string | null | undefined): string {
  return planStartDate
    ? `计划里的出发日是 ${planStartDate}。如果今天才动身，改成就今天，第 1 天会跟着变。`
    : '这份计划还没有出发日期。选定的那天就是第 1 天，之后每天按日期自动对应。'
}
