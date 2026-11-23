import type {
  AdjustmentChange,
  AdjustmentOp,
  AdjustmentOpKind,
  PlanAdjustmentVO,
} from '@/api/types'

/**
 * 把调整建议的增量操作翻译成人话。
 *
 * 这些是纯函数而不是塞在模板里的三元表达式：差异展示是用户判断「要不要接受」的唯一依据，
 * 文案错了比接口挂了更糟——接口挂了用户知道重试，文案错了用户会照着一个错误的理解点确认。
 */

const FIELD_LABELS: Record<string, string> = {
  title: '标题',
  startTime: '开始时间',
  endTime: '结束时间',
  note: '备注',
  theme: '当日主题',
  city: '城市',
}

const OP_TAGS: Record<AdjustmentOpKind, string> = {
  ADD_ITEM: '新增',
  REMOVE_ITEM: '删除',
  UPDATE_ITEM: '修改',
  MOVE_ITEM: '移动',
  REORDER_ITEM: '重排',
  UPDATE_DAY: '修改',
}

export interface RenderedOp {
  kind: AdjustmentOpKind
  tag: string
  /** 主要对象，例如「第 2 天 · 都江堰」 */
  title: string
  /** 细节行，例如「开始时间：09:00 → 10:30」 */
  lines: string[]
}

export function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field
}

/** null 不渲染成空白：「清空备注」和「没填备注」在界面上必须能区分 */
export function formatValue(value: string | null | undefined): string {
  return value === null || value === undefined || value === '' ? '未设置' : value
}

function dayLabel(dayIndex: number | undefined): string {
  return dayIndex === undefined || dayIndex === null ? '未知天' : `第 ${dayIndex} 天`
}

function changeLine(change: AdjustmentChange): string {
  return `${fieldLabel(change.field)}：${formatValue(change.before)} → ${formatValue(change.after)}`
}

function timeRange(op: AdjustmentOp): string | null {
  const start = op.startTime ?? null
  const end = op.endTime ?? null
  if (!start && !end) return null
  return `${start ?? '不限'} – ${end ?? '不限'}`
}

export function renderOp(op: AdjustmentOp): RenderedOp {
  const tag = OP_TAGS[op.op] ?? '改动'
  switch (op.op) {
    case 'ADD_ITEM': {
      const lines: string[] = []
      const range = timeRange(op)
      if (range) lines.push(range)
      if (op.note) lines.push(op.note)
      return { kind: op.op, tag, title: `${dayLabel(op.dayIndex)} · ${op.title ?? '新项目'}`, lines }
    }
    case 'REMOVE_ITEM':
      return {
        kind: op.op,
        tag,
        title: `${dayLabel(op.dayIndex)} · ${op.title ?? '该行程项'}`,
        lines: [],
      }
    case 'UPDATE_ITEM':
      return {
        kind: op.op,
        tag,
        title: `${dayLabel(op.dayIndex)} · ${op.title ?? '该行程项'}`,
        lines: (op.changes ?? []).map(changeLine),
      }
    case 'MOVE_ITEM':
      return {
        kind: op.op,
        tag,
        title: op.title ?? '该行程项',
        lines: [`${dayLabel(op.fromDayIndex)} → ${dayLabel(op.toDayIndex)}`],
      }
    case 'REORDER_ITEM':
      return {
        kind: op.op,
        tag,
        title: `${dayLabel(op.dayIndex)}的顺序`,
        lines: [`共 ${op.orderedItemIds?.length ?? 0} 项重新排序`],
      }
    case 'UPDATE_DAY':
      return {
        kind: op.op,
        tag,
        title: dayLabel(op.dayIndex),
        lines: (op.changes ?? []).map(changeLine),
      }
    default:
      return { kind: op.op, tag, title: '未知改动', lines: [] }
  }
}

export function renderOps(ops: AdjustmentOp[] | undefined): RenderedOp[] {
  return (ops ?? []).map(renderOp)
}

/** 「3 处改动」；没有改动时给空串，由调用方决定说什么 */
export function opsSummary(ops: AdjustmentOp[] | undefined): string {
  const count = ops?.length ?? 0
  return count === 0 ? '' : `${count} 处改动`
}

/**
 * 建议还能不能确认。
 *
 * `baseVersion` 与计划当前版本不一致时不能确认：那份建议是基于旧计划算出来的，
 * 硬套会覆盖用户期间的修改。后端也会拦（30108），前端先拦是为了把话说清楚。
 */
export function canConfirm(adjustment: PlanAdjustmentVO | null, planVersion: number): boolean {
  return !!adjustment
    && adjustment.status === 'PENDING'
    && adjustment.baseVersion === planVersion
}

/** 过期时的提示语；未过期返回空串 */
export function staleHint(adjustment: PlanAdjustmentVO | null, planVersion: number): string {
  if (!adjustment || adjustment.status !== 'PENDING' || adjustment.baseVersion === planVersion) {
    return ''
  }
  return '这条建议生成后计划又被改过了，直接套用会覆盖那次修改。请重新生成。'
}
