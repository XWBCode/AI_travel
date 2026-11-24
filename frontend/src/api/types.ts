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
  /** 失败原因；FAILED 且 content 为空时前端靠它显示提示，否则历史里只剩空气泡 */
  errorMessage?: string
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

/**
 * 回滚结果（`POST /api/plans/{id}/versions/{versionNo}/rollback`）。
 * <p>
 * `clearedProgress` 是本次回滚清掉的打勾记录数：回滚会重建行程项、itemId 全变，
 * 进度必须一起清空。这个字段要显示给用户，否则「进度为什么归零了」无从解释。
 */
export interface PlanRollbackResult {
  plan: TravelPlan
  /** 恢复自哪个版本 */
  restoredFrom: number
  /** 回滚后的新版本号（继续递增，不覆盖历史） */
  version: number
  clearedProgress: number
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
  /** 当日主题，当日执行页用它说明今天的主线 */
  theme?: string
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

/** 当日执行视图的相位：决定页面显示「今天」「还没出发」「已超期」还是「缺出发日期」 */
export type TodayPhase = 'TODAY' | 'BEFORE' | 'AFTER' | 'UNANCHORED'

/**
 * 当日执行视图（`GET /api/plans/{id}/today`，R-21）。
 *
 * `date` 是计算所用的「今天」——由前端按本机时区传上去，服务端原样回传。
 * 不在前端自己算「第几天」：一旦两端口径不同，用户在跨时区旅行时就会看到昨天的安排，
 * 而且这种偏差只在深夜出现，几乎无从复现。
 */
export interface PlanTodayVO {
  planId: Id
  status: string
  title: string
  /** YYYY-MM-DD */
  date: string
  phase: TodayPhase
  /** UNANCHORED 时为 null */
  dayIndex?: number | null
  totalDays?: number | null
  daysUntilStart?: number
  daysSinceEnd?: number
  /** UNANCHORED 时为 null；空白日返回 items 为空的卡片 */
  day?: ProgressDay | null
  total: number
  completed: number
  percent: number
}

/** 调整建议里的一项字段变更；before 由服务端从库里回填，前端不自己算 */
export interface AdjustmentChange {
  field: string
  before: string | null
  after: string | null
}

export type AdjustmentOpKind =
  | 'ADD_ITEM'
  | 'REMOVE_ITEM'
  | 'UPDATE_ITEM'
  | 'MOVE_ITEM'
  | 'REORDER_ITEM'
  | 'UPDATE_DAY'

/**
 * 一条增量操作。
 *
 * 注意这里没有「整份新计划」：整份替换会换掉全部 itemId，用户已经打的勾会跟着失效。
 * 调整只点名称要改的那几行，其余原样保留。
 */
export interface AdjustmentOp {
  op: AdjustmentOpKind
  itemId?: string
  dayIndex?: number
  title?: string
  startTime?: string | null
  endTime?: string | null
  note?: string | null
  fromDayIndex?: number
  toDayIndex?: number
  changes?: AdjustmentChange[]
  orderedItemIds?: string[]
}

/** AI 调整建议（`POST /api/plans/{id}/adjustments`，R-23） */
export interface PlanAdjustmentVO {
  adjustmentId: string
  planId: string
  /** 生成建议时计划的版本；与 planVersion 不同说明建议已过期 */
  baseVersion: number
  status: 'PENDING' | 'CONFIRMED' | 'REJECTED' | 'EXPIRED'
  instruction: string
  summary: string
  ops: AdjustmentOp[]
  planVersion: number
  createTime?: string
}

/** 确认调整后的结果 */
export interface AdjustmentConfirmResult {
  adjustmentId: string
  version: number
  clearedProgress: number
  summary: string
}

/** 通知事件类型（后端 Notification 常量，V7） */
export type NotificationType =
  | 'PLAN_CONFIRMED'
  | 'PLAN_STARTED'
  | 'PLAN_COMPLETED'
  | 'PLAN_CANCELLED'
  | 'ADJUSTMENT_CONFIRMED'

/** 站内通知（`GET /api/notifications`，R-30） */
export interface NotificationVO {
  notificationId: string
  planId?: string | null
  type: NotificationType
  title: string
  body?: string | null
  /** 关联对象 ID（如调整建议 ID），可空 */
  refId?: string | null
  read: boolean
  readAt?: string | null
  createTime?: string
}

/**
 * 通知列表的一页。比普通分页多一个 `unreadCount`——
 * 红点和列表必须来自同一次查询，否则会出现「红点 3 条、列表 2 条」。
 */
export interface NotificationPageVO {
  records: NotificationVO[]
  total: number
  page: number
  size: number
  unreadCount: number
}

/**
 * 天气降级原因（M3-1）。与后端 `PlanWeatherVO.reason` 逐字一致。
 *
 * 这些不是错误码，而是「为什么没有天气」的枚举——每一种对应一句用户能看懂、
 * 且能区分「我能做点什么」还是「只能等」的提示。
 */
export type WeatherReason =
  | 'PROVIDER_DISABLED'
  | 'NO_START_DATE'
  | 'NO_CITY'
  | 'CITY_UNKNOWN'
  | 'DATE_OUT_OF_RANGE'
  | 'UPSTREAM_TIMEOUT'
  | 'UPSTREAM_ERROR'
  /** 熔断器已打开，本次没有访问上游——与 UPSTREAM_ERROR（出网了但失败）是两件事 */
  | 'CIRCUIT_OPEN'

/** 一天的天气：四组 FactField 原样来自后端，展示层只读 `display` / `verified` */
export interface PlanWeatherDay {
  /** 相对整段行程的天序号，不是相对返回结果的第一天 */
  dayIndex: number
  date: string
  /** 计划里的原始写法，如「云南」 */
  city?: string | null
  /** 实际取坐标的城市，如「昆明」 */
  resolvedCity?: string | null
  weather?: FactField | null
  temperature?: FactField | null
  precipitation?: FactField | null
  wind?: FactField | null
}

/**
 * `GET /api/plans/{id}/weather`（M3-1）。
 *
 * `available=false` 时 `days` 为空、`reason` 有值——这是正常响应而不是错误，
 * 页面照常渲染，只把天气区域换成一行提示。
 */
export interface PlanWeatherVO {
  available: boolean
  reason?: WeatherReason | string | null
  message?: string | null
  source?: string | null
  /** CC BY 4.0 要求署名，有数据时必须原样展示 */
  attribution?: string | null
  fetchedAt?: string | null
  expiresAt?: string | null
  note?: string | null
  /** 缓存过期且刷新失败：数据是上次的，必须标出来 */
  stale?: boolean
  days: PlanWeatherDay[]
}
