/** 与后端 Result 结构一一对应 */
export interface ApiResult<T> {
  code: number
  message: string
  data: T
  traceId?: string
}

/**
 * 主键类型。
 * 后端用雪花算法生成 19 位 ID，超出 JS 的 Number.MAX_SAFE_INTEGER，
 * 因此一律以字符串下发（见后端 JacksonConfig）。前端千万不要转成 number，
 * 否则 2119988130910269442 会被四舍五入成 2119988130910269400，回传就查不到。
 */
export type Id = string

export interface UserVO {
  id: Id
  username: string
  nickname: string
  avatarUrl?: string
  bio?: string
  role: string
  createTime?: string
}

export interface LoginResponse {
  accessToken: string
  expiresIn: number
  user: UserVO
}

export interface SessionVO {
  id: Id
  title: string
  lastMessagePreview?: string
  messageCount: number
  createTime: string
  updateTime: string
}

export interface MessageVO {
  id: Id
  sessionId: Id
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string
  status: string
  model?: string
  createTime: string
}

/* ---------------- 旅行计划（contracts/travel-plan/travel_plan_v1.json） ---------------- */

/**
 * 事实字段。L0（未接入实时数据源）下 value 恒为 null、display 恒为「待核实」，
 * 展示层只能用它，不能自己去算数值——否则就是编造（AC-08）。
 */
export interface FactField {
  value?: unknown
  display: string
  note?: string
  source?: string
  verified: boolean
}

export interface PlanItem {
  itemOrder: number
  title: string
  startTime?: string
  endTime?: string
  duration?: FactField
  openHours?: FactField
  ticketPrice?: FactField
  note?: string
}

export interface PlanDay {
  dayIndex: number
  date?: string | null
  city?: string
  theme?: string
  items?: PlanItem[]
}

export interface BudgetItem {
  category: string
  label: string
  amount?: FactField
  note?: string
}

export interface TravelPlanBasic {
  originCity?: string | null
  destination?: string | null
  destinations?: string[]
  startDate?: string | null
  endDate?: string | null
  days?: number | null
  travelers?: number | null
  missingFields?: string[]
}

export interface TravelPlan {
  schemaVersion: string
  planId: Id
  title: string
  status: string
  version: number
  basic: TravelPlanBasic
  budget?: { note?: string; items?: BudgetItem[] }
  risks?: string[]
  aiNote?: string
  unverifiedFieldCount: number
  days?: PlanDay[]
}

export interface PlanSummaryVO {
  id: Id
  title: string
  destination?: string
  status: string
  startDate?: string
  endDate?: string
  days?: number
  travelers?: number
  unverifiedFieldCount: number
  version: number
  updateTime: string
}
