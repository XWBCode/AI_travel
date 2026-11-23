/**
 * 天气展示口径（M3-1）。
 *
 * 这里集中三件事，都是"展示层不该自己发挥"的部分：
 *
 * 1. **降级文案**：`reason` → 一句人话。区分「用户能做点什么」（补充出发日期）
 *    和「只能等」（超出预报范围 / 上游超时），否则用户会白找半天设置。
 * 2. **只读 display**：天气数值一律取后端 FactField 的 `display`，前端不算、不猜、不换算。
 *    未核实的字段显示「暂无数据」，绝不留空让人误以为是 0。
 * 3. **署名与时效**：CC BY 4.0 要求展示来源；旧值（stale）必须说明是上次的。
 *
 * 纯函数，不引 vue，便于 `npm run test:weather` 直接跑。
 */
import type { FactField, PlanWeatherDay, PlanWeatherVO } from '../api/types'

/** 原因 → 文案。与后端枚举一一对应，键名写错会落到兜底句，不会静默空白 */
export const WEATHER_REASON_TEXT: Record<string, string> = {
  PROVIDER_DISABLED: '天气功能未开启',
  NO_START_DATE: '补充出发日期后可查看天气',
  NO_CITY: '计划里还没有城市，无法查询天气',
  CITY_UNKNOWN: '该地点暂无天气数据',
  // 与「没有日期」必须区分：前者用户改日期就行，后者只能等
  DATE_OUT_OF_RANGE: '出发日期超出预报范围，临近再看',
  UPSTREAM_TIMEOUT: '天气服务响应超时，稍后会自动重试',
  UPSTREAM_ERROR: '天气暂不可用',
}

const FALLBACK_REASON_TEXT = '天气暂不可用'

/**
 * 降级提示。
 *
 * 优先用服务端 `message`（后端已翻译过一次，且带上了具体原因），
 * 再退回本地映射表，最后才是兜底句——顺序反了会把后端更准确的话丢掉。
 */
export function reasonText(reason?: string | null, message?: string | null): string {
  if (message && message.trim()) return message.trim()
  if (reason && WEATHER_REASON_TEXT[reason]) return WEATHER_REASON_TEXT[reason]
  return FALLBACK_REASON_TEXT
}

/** 只有「补充出发日期」是用户当下能自己解决的，值得给一个操作入口 */
export function canFixByUser(reason?: string | null): boolean {
  return reason === 'NO_START_DATE'
}

/** 字段展示：核实过就照抄 display，没核实一律「暂无数据」 */
export function fieldText(field?: FactField | null): string {
  if (!field) return '暂无数据'
  if (field.verified === true && field.display) return field.display
  return '暂无数据'
}

export interface WeatherRow {
  dayIndex: number
  /** MM-DD，跨年行程也不会歧义 */
  dayLabel: string
  city: string
  weather: string
  temperature: string
  precipitation: string
  wind: string
  /** 四组里只要有一组是真值，就给正常样式；全空说明这天上游没给数据 */
  hasValue: boolean
}

function monthDay(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso ?? '')
  return match ? `${match[2]}-${match[3]}` : (iso ?? '')
}

/** 一天一行。城市优先用计划里的写法（用户认得出），旁边不重复显示解析后的城市 */
export function toRow(day: PlanWeatherDay): WeatherRow {
  const fields = [day.weather, day.temperature, day.precipitation, day.wind]
  return {
    dayIndex: day.dayIndex,
    dayLabel: monthDay(day.date),
    city: day.city || day.resolvedCity || '',
    weather: fieldText(day.weather),
    temperature: fieldText(day.temperature),
    precipitation: fieldText(day.precipitation),
    wind: fieldText(day.wind),
    hasValue: fields.some((f) => f?.verified === true),
  }
}

export function toRows(vo?: PlanWeatherVO | null): WeatherRow[] {
  if (!vo?.available) return []
  return (vo.days ?? []).map(toRow)
}

/**
 * 来源与时效。
 *
 * 署名是硬要求（CC BY 4.0），所以 `attribution` 为空时退回一句固定文案而不是不显示；
 * 时间取 `fetchedAt`，只给"多久之前"——绝对时间对判断"这数据还新不新"没有帮助。
 */
export function sourceLine(vo?: PlanWeatherVO | null, now: Date = new Date()): string {
  if (!vo?.available) return ''
  const parts: string[] = []
  parts.push(vo.attribution?.trim() || '数据来源 Open-Meteo（CC BY 4.0）')
  const relative = relativeTime(vo.fetchedAt, now)
  if (relative) parts.push(`更新于${relative}`)
  return parts.join(' · ')
}

/** 相对时间。返回空串表示没有可用时间，调用方直接不显示这一段 */
export function relativeTime(iso?: string | null, now: Date = new Date()): string {
  if (!iso) return ''
  const then = new Date(iso)
  if (Number.isNaN(then.getTime())) return ''
  const minutes = Math.floor((now.getTime() - then.getTime()) / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}

/**
 * 旧值提示。`stale=true` 时必须出现——不标就是把上次的数据冒充成这次的。
 */
export function staleText(vo?: PlanWeatherVO | null): string {
  return vo?.stale ? '数据为上次成功获取的结果，正在尝试更新' : ''
}

/** 城市别名说明（「按省会昆明代表云南」），有就显示，没有不占位 */
export function cityNote(vo?: PlanWeatherVO | null): string {
  const note = vo?.note ?? ''
  if (!note) return ''
  // 刷新失败的说明单独显示，不在城市说明里重复一遍
  return note.includes('上次成功获取') ? '' : note
}
