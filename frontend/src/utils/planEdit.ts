import type { PlanUpdatePayload, TravelPlan } from '@/api/types'

/**
 * 计划基础信息的编辑逻辑（纯函数，不碰 UI 与网络）。
 *
 * 放在这里而不是组件里，是因为「改天数要不要跟着改结束日期」这类规则
 * 必须有单测兜底——它和后端 `PlanUpdateCommand` 的归一化规则是一对，
 * 两边一旦不一致，用户就会看到「保存成功但值没变」或者「天数与日期打架」。
 */

export const MAX_DAYS = 30
export const MAX_TRAVELERS = 50
export const MAX_TITLE = 100
export const MAX_CITY = 64

/** 表单里的基础信息，日期用 `YYYY-MM-DD` 字符串，空值用 null */
export interface BasicDraft {
  title: string
  originCity: string
  destination: string
  days: number | null
  travelers: number | null
  startDate: string | null
  endDate: string | null
}

/** PATCH 请求体：只包含真正改动的字段 */
export type PlanPatch = PlanUpdatePayload

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

/** 用 UTC 做日期算术，避免本地时区把「11-20」算成「11-19」 */
function toUtc(date: string): number {
  const [y, m, d] = date.split('-').map(Number)
  return Date.UTC(y, m - 1, d)
}

function fromUtc(ms: number): string {
  const date = new Date(ms)
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`
}

/** 在日期上加减天数 */
export function addDays(date: string, days: number): string {
  return fromUtc(toUtc(date) + days * 86400000)
}

/** 区间包含首尾两天，所以 11-20 到 11-22 是 3 天 */
export function daysBetween(start: string, end: string): number {
  return Math.round((toUtc(end) - toUtc(start)) / 86400000) + 1
}

/** 由出发日 + 天数推结束日；任一为空则推不出来 */
export function endDateFromDays(startDate: string | null, days: number | null): string | null {
  if (!startDate || !days || days < 1) return null
  return addDays(startDate, days - 1)
}

/** 由日期区间反推天数 */
export function daysFromRange(startDate: string | null, endDate: string | null): number | null {
  if (!startDate || !endDate) return null
  const span = daysBetween(startDate, endDate)
  return span >= 1 ? span : null
}

/**
 * 改天数：出发日不变，结束日跟着走。
 * 没有出发日时不硬造日期——推算不出来就是推算不出来。
 */
export function withDays(draft: BasicDraft, days: number | null): BasicDraft {
  const next = { ...draft, days }
  if (draft.startDate && days) {
    next.endDate = endDateFromDays(draft.startDate, days)
  }
  return next
}

/**
 * 改日期区间：天数由区间反推。
 * 只选了单边（比如清空结束日）时，天数保持不变，交给后端按缺失字段处理。
 */
export function withRange(draft: BasicDraft, startDate: string | null, endDate: string | null): BasicDraft {
  const next = { ...draft, startDate, endDate }
  const derived = daysFromRange(startDate, endDate)
  if (derived !== null) {
    next.days = derived
  }
  return next
}

/** 从接口返回的计划里取出可编辑的基础信息 */
export function planToDraft(plan: TravelPlan): BasicDraft {
  const basic = plan.basic ?? {}
  return {
    title: plan.title ?? '',
    originCity: basic.originCity ?? '',
    destination: basic.destination ?? '',
    days: basic.days ?? null,
    travelers: basic.travelers ?? null,
    startDate: basic.startDate ?? null,
    endDate: basic.endDate ?? null,
  }
}

/**
 * 组装 PATCH 请求体。
 *
 * 只带变化过的字段有两个好处：请求体能直接看出「用户动了什么」，
 * 也避免把没碰过的字段原样回传、因格式差异被后端判成一次真实修改。
 */
export function buildPatch(original: BasicDraft, draft: BasicDraft): PlanPatch {
  const patch: PlanPatch = {}

  if (draft.title.trim() !== original.title) {
    patch.title = draft.title.trim()
  }
  if (draft.originCity.trim() !== (original.originCity ?? '')) {
    patch.originCity = draft.originCity.trim() || null
  }
  if (draft.destination.trim() !== (original.destination ?? '')) {
    patch.destination = draft.destination.trim() || null
  }
  if (draft.travelers !== original.travelers) {
    patch.travelers = draft.travelers
  }
  if (draft.days !== original.days) {
    patch.days = draft.days
  }
  if (draft.startDate !== original.startDate) {
    patch.startDate = draft.startDate
  }
  if (draft.endDate !== original.endDate) {
    patch.endDate = draft.endDate
  }
  return patch
}

export function hasChanges(original: BasicDraft, draft: BasicDraft): boolean {
  return Object.keys(buildPatch(original, draft)).length > 0
}

/** 表单校验：返回第一条错误文案，全部通过则返回 null */
export function validateDraft(draft: BasicDraft): string | null {
  if (!draft.title.trim()) return '标题不能为空'
  if (draft.title.trim().length > MAX_TITLE) return `标题不得超过 ${MAX_TITLE} 字`
  if (draft.originCity.trim().length > MAX_CITY) return `出发地不得超过 ${MAX_CITY} 字`
  if (draft.destination.trim().length > MAX_CITY) return `目的地不得超过 ${MAX_CITY} 字`
  if (draft.days !== null && (draft.days < 1 || draft.days > MAX_DAYS)) {
    return `天数应在 1-${MAX_DAYS} 之间`
  }
  if (draft.travelers !== null && (draft.travelers < 1 || draft.travelers > MAX_TRAVELERS)) {
    return `人数应在 1-${MAX_TRAVELERS} 之间`
  }
  if (draft.startDate && draft.endDate && draft.endDate < draft.startDate) {
    return '结束日期不能早于开始日期'
  }
  if (draft.startDate && draft.days && endDateFromDays(draft.startDate, draft.days) !== draft.endDate) {
    // 正常操作不会走到这里（天数与日期是联动的），除非有人手工改了输入框
    return '天数与日期区间不一致，请重新选择日期'
  }
  return null
}

/** 多目的地计划不允许在这里改主目的地（与后端规则一致） */
export function canEditDestination(plan: TravelPlan): boolean {
  const destinations = plan.basic?.destinations ?? []
  return destinations.length <= 1
}
