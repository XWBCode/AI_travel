/**
 * 「待核实」信息的分类明细。
 *
 * 背景（BUG-004）：计划里每个 FactField 在 L0 都是「待核实」，一份 3 天行程轻松攒到 70+ 处。
 * 直接把这数字写在卡片上，用户读到的是「这软件什么都不知道」——**这是内部计量单位，
 * 不是用户的问题**。所以列表/对话里的卡片只说「部分信息待核实」，
 * 数字留在详情页，并且必须能点开看到「到底是哪几类」。
 *
 * 这里的计数是**展示用的分类**，权威总数仍以服务端 `unverifiedFieldCount` 为准
 * （08 文档 S-5 决议：服务端重算覆盖）。两侧口径一致时 `total` 与它相等；
 * 不一致也不影响展示——分组是给用户看的解释，不是新的真相来源。
 */

export interface UnverifiedGroup {
  key: string
  label: string
  /** 该类的待核实处数（事实字段个数） */
  count: number
  /** 涉及多少个不同的对象（行程项 / 预算项 / 交通段） */
  subjectCount: number
  /** 最多三个具体对象名，如「宽窄巷子」「武侯祠」 */
  subjects: string[]
}

export interface UnverifiedSummary {
  /** 实际扫描到的处数（用于与 `unverifiedFieldCount` 对照，不直接显示） */
  total: number
  groups: UnverifiedGroup[]
}

type AnyNode = Record<string, unknown>

/** 字段名 → 用户能看懂的说法。没列到的直接用字段名，宁可难看也不能瞎猜意思。 */
const FIELD_LABEL: Record<string, string> = {
  duration: '时长',
  openHours: '开放时间',
  ticketPrice: '门票',
  price: '价格',
  amount: '金额',
  distance: '距离',
  weather: '天气',
  temperature: '气温',
}

/** 分组顺序固定：先说行程（用户最关心），再说钱 */
const GROUP_ORDER = ['itinerary', 'transport', 'stay', 'budget', 'other'] as const
type GroupKey = (typeof GROUP_ORDER)[number]

const GROUP_LABEL: Record<GroupKey, string> = {
  itinerary: '行程安排',
  transport: '交通',
  stay: '住宿',
  budget: '预算',
  other: '其他',
}

const MAX_EXAMPLES = 3

/**
 * FactField 的形状判据：有 display 和 verified 两个键。
 * 不用 `value` 判断——L0 下 value 恒为 null，`in` 能命中但 `Boolean(null)` 不能。
 */
function isFactField(node: unknown): node is AnyNode {
  if (!node || typeof node !== 'object' || Array.isArray(node)) return false
  const o = node as AnyNode
  return typeof o.display === 'string' && 'verified' in o
}

function groupOf(path: string[]): GroupKey {
  const head = path[0] ?? ''
  if (head === 'days') return 'itinerary'
  if (head === 'transportSegments') return 'transport'
  if (head === 'accommodation') return 'stay'
  if (head === 'budget') return 'budget'
  return 'other'
}

/** 一处待核实挂在谁身上：行程项用标题，预算项用 label，交通用「成都 → 乐山」 */
function subjectOf(parent: AnyNode, path: string[]): string {
  if (typeof parent.title === 'string' && parent.title) return parent.title
  if (typeof parent.label === 'string' && parent.label) return parent.label
  const from = typeof parent.fromCity === 'string' ? parent.fromCity : ''
  const to = typeof parent.toCity === 'string' ? parent.toCity : ''
  if (from || to) return `${from || '出发地'} → ${to || '目的地'}`
  if (typeof parent.dayIndex === 'number') return `第 ${parent.dayIndex} 天`
  if (path[0] === 'accommodation') return '住宿'
  return ''
}

/**
 * 遍历计划，把待核实的 FactField 按大类归拢。
 *
 * 走的是通用递归而不是逐字段硬编码：契约以后新增事实字段（例如天气），
 * 这里不用改就能归到「其他」，不会悄悄漏算。
 */
export function groupUnverified(plan: unknown): UnverifiedSummary {
  const buckets = new Map<GroupKey, { group: UnverifiedGroup; seen: Set<string> }>()
  let total = 0

  function add(path: string[], parent: AnyNode, key: string): void {
    const groupKey = groupOf(path)
    let bucket = buckets.get(groupKey)
    if (!bucket) {
      bucket = {
        group: { key: groupKey, label: GROUP_LABEL[groupKey], count: 0, subjectCount: 0, subjects: [] },
        seen: new Set<string>(),
      }
      buckets.set(groupKey, bucket)
    }
    bucket.group.count += 1
    total += 1
    // 例子按「对象」去重：一个行程项的时长/开放时间/门票属于同一处安排，
    // 不去重会读成「宽窄巷子 · 时长、宽窄巷子 · 开放时间……」，等于没说
    const subject = subjectOf(parent, path) || FIELD_LABEL[key] || key
    if (bucket.seen.has(subject)) return
    bucket.seen.add(subject)
    bucket.group.subjectCount = bucket.seen.size
    if (bucket.group.subjects.length < MAX_EXAMPLES) bucket.group.subjects.push(subject)
  }

  function visit(node: unknown, path: string[]): void {
    if (Array.isArray(node)) {
      node.forEach((child, index) => visit(child, [...path, String(index)]))
      return
    }
    if (!node || typeof node !== 'object') return
    for (const [key, value] of Object.entries(node as AnyNode)) {
      const here = [...path, key]
      if (isFactField(value)) {
        add(here, node as AnyNode, key)
        // FactField 内部不会再有事实字段，不必继续下钻
        continue
      }
      visit(value, here)
    }
  }

  visit(plan, [])

  const groups = GROUP_ORDER.map((key) => buckets.get(key)?.group).filter(
    (group): group is UnverifiedGroup => Boolean(group && group.count > 0),
  )
  return { total, groups }
}

/**
 * 卡片上的分类提示：只报「哪几类」，不报数字。
 *
 * 返回空串表示没有可分类的信息（调用方据此退回到通用文案）。
 */
export function unverifiedCategoryText(summary: UnverifiedSummary): string {
  return summary.groups.map((group) => group.label).join(' · ')
}
