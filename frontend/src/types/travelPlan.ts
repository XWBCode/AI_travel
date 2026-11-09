/**
 * TravelPlan v1 前端类型
 *
 * 唯一来源：contracts/travel-plan/travel_plan_v1.json
 * 决策记录：docs/design/08-TravelPlan-v1-冻结决议.md
 *
 * 改动纪律：先改契约与决议文档，再改本文件。字段不一致时以契约与 08 为准。
 * 注意：本文件对应「存储/展示模型」；SSE plan_draft 阶段 planId 为 null，故此处放宽为可空。
 */

export type PlanStatus = 'DRAFT' | 'CONFIRMED' | 'IN_PROGRESS' | 'COMPLETED' | 'CANCELLED'

export type FactSource = 'NONE' | 'MODEL_KNOWLEDGE' | 'USER' | 'TOOL' | 'CACHE'

/** Q-03：偏好来源逐项标注 */
export type PreferenceSource = 'USER' | 'USER_DEFAULT' | 'MODEL_KNOWLEDGE'

/** Q-05：missingFields 固定词表，后端拒绝自由文本 */
export type MissingField =
  | 'originCity'
  | 'destination'
  | 'startDate'
  | 'endDate'
  | 'days'
  | 'travelers'
  | 'budget'
  | 'interests'
  | 'transport'
  | 'accommodation'

export type BudgetCategory = 'TRANSPORT' | 'ACCOMMODATION' | 'FOOD' | 'TICKET' | 'OTHER'

/** Q-04：INTRA_DAY 用 itemOrder，INTER_CITY 用 dayIndex + city */
export type TransportScope = 'INTRA_DAY' | 'INTER_CITY'

/**
 * 实时性字段包装。
 * L0 约定：value 恒 null、verified 恒 false、display 恒「待核实」、fetchedAt/expiresAt 恒 null。
 */
export interface FactField<T = unknown> {
  value: T | null
  display: string
  source: FactSource
  verified: boolean
  fetchedAt: string | null
  expiresAt: string | null
  note: string
}

export interface PlanBasic {
  originCity: string | null
  /** Q-01：主目的地，供列表页与标题使用 */
  destination: string | null
  /** Q-01：全部目的地，destinations[0] 应等于 destination（后端校验） */
  destinations: string[]
  startDate: string | null
  endDate: string | null
  days: number | null
  travelers: number | null
  missingFields: MissingField[]
}

export interface PlanPreferences {
  interests: string[]
  transport: string | null
  accommodation: string | null
  specialRequirements: string[]
  interestsSource: PreferenceSource
  transportSource: PreferenceSource
  accommodationSource: PreferenceSource
  specialRequirementsSource: PreferenceSource
}

export interface TravelItem {
  itemOrder: number
  /** Q-09：M1 无 POI 主数据，恒为 null */
  attractionId: string | null
  title: string
  /** Q-07：建议时间（HH:mm），非实时数据，UI 需标注 */
  startTime: string | null
  endTime: string | null
  /** 分钟；L0 恒为 null */
  duration: FactField<number>
  openHours: FactField<string>
  ticketPrice: FactField<number>
  note: string
}

export interface TransportSegment {
  scope: TransportScope
  fromItemOrder: number | null
  toItemOrder: number | null
  fromDayIndex: number | null
  toDayIndex: number | null
  fromCity: string | null
  toCity: string | null
  mode: string | null
  duration: FactField<number>
  distance: FactField<number>
  advice: string
}

export interface Accommodation {
  areaName: string
  suggestion: string
  priceRange: FactField<string>
}

export interface TravelDay {
  dayIndex: number
  date: string | null
  city: string
  theme: string | null
  items: TravelItem[]
  transportSegments: TransportSegment[]
  accommodation: Accommodation | null
}

export interface BudgetItem {
  category: BudgetCategory
  label: string
  /** Q-02：L0 恒为 null，区间口径只写在 note 里 */
  amount: FactField<number>
  note: string
}

export interface PlanBudget {
  currency: 'CNY'
  total: FactField<number>
  items: BudgetItem[]
  note: string
}

export interface TravelPlan {
  schemaVersion: '1.0'
  /** 新建/草稿阶段为 null，落库后由后端回填 */
  planId: string | null
  title: string
  status: PlanStatus
  /** 乐观锁版本号，PATCH 必须回传 */
  version: number
  basic: PlanBasic
  preferences: PlanPreferences
  days: TravelDay[]
  budget: PlanBudget
  risks: string[]
  /** 纯文本，非 Markdown（见 08 决议 B-4） */
  aiNote: string
  unverifiedFieldCount: number
}
