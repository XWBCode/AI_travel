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

/** 分页容器，与后端 PageResult 对齐 */
export interface PageResult<T> {
  records: T[]
  total: number
  page: number
  size: number
}

/** 计划版本快照（确认或编辑时产生，只读展示） */
export interface PlanVersionVO {
  id: Id
  versionNo: number
  changeSummary?: string
  operatorId?: Id
  createTime?: string
}

/**
 * `PATCH /api/plans/{id}` 的请求体。
 * <p>
 * 只带要改的字段：不传 = 不改，传 null = 清空。`baseVersion` 用来做乐观锁，
 * 与库里不一致时后端返回 30104，前端应提示并刷新，而不是覆盖别人的修改。
 */
export interface PlanUpdatePayload {
  title?: string
  originCity?: string | null
  destination?: string | null
  days?: number | null
  travelers?: number | null
  startDate?: string | null
  endDate?: string | null
  baseVersion?: number
}

/** 计划状态机（05 文档 §5）：前端只按状态显示可执行的动作 */
export const PLAN_STATUS_TEXT: Record<string, string> = {
  DRAFT: '草稿',
  CONFIRMED: '已确认',
  IN_PROGRESS: '进行中',
  COMPLETED: '已完成',
  CANCELLED: '已取消',
}

/** 从状态到可执行动作的映射，避免前端各页面各写一套判断 */
export const PLAN_ACTIONS: Record<string, Array<'confirm' | 'start' | 'complete' | 'cancel'>> = {
  DRAFT: ['confirm', 'cancel'],
  CONFIRMED: ['start', 'cancel'],
  IN_PROGRESS: ['complete', 'cancel'],
  COMPLETED: [],
  CANCELLED: [],
}

/**
 * 执行进度（M2）。
 *
 * 不复用 TravelPlan：打一次勾不该把预算、跨城段、风险整份重传。
 * itemId 是字符串——雪花 ID 超出 JS 安全整数，用 number 会丢精度，
 * 表现为「点第 3 项却勾上了第 4 项」这种极难排查的错。
 */
export interface PlanProgressVO {
  planId: Id
  status: string
  total: number
  completed: number
  /** 0-100 整数，服务端算好，前后端不各算一遍 */
  percent: number
  days: ProgressDay[]
}

export interface ProgressDay {
  dayIndex: number
  date?: string | null
  city?: string
  total: number
  completed: number
  items: ProgressItem[]
}

export interface ProgressItem {
  itemId: Id
  itemOrder: number
  title: string
  startTime?: string
  endTime?: string
  note?: string
  completed: boolean
  completedAt?: string
  /** 下一个待完成项，全表只有一个 */
  next: boolean
}
