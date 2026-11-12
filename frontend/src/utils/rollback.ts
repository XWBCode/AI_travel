import type { PlanVersionVO } from '@/api/types'

/**
 * 版本回滚的前端规则。
 *
 * 抽成纯函数而不是写在组件里，原因与 planProgress 相同：
 * 这些判断要能被单测覆盖，而组件里的交互只能靠截图，后台标签页还会被降频。
 */

/** 终态：内容是「这次旅行最终的样子」，回滚会让结论随操作变化 */
const FROZEN_STATUS = ['COMPLETED', 'CANCELLED']

export function canRollback(status: string): boolean {
  return !FROZEN_STATUS.includes(status)
}

/**
 * 某个版本能否作为回滚目标。
 *
 * @param versionNo      目标版本号
 * @param status         计划当前状态
 * @param currentVersion 计划当前版本号（回滚到自己没有意义，后端也会拒）
 */
export function canRollbackTo(
  versionNo: number,
  status: string,
  currentVersion: number,
): boolean {
  if (!canRollback(status)) return false
  return versionNo !== currentVersion
}

/**
 * 回滚确认文案。
 *
 * 必须说清三件事，否则用户不敢点：回到哪一版、历史会不会丢、已经打过的勾怎么办。
 * `completed` 为 0 时不要提进度——没问题却说「会清空 0 项」反而让人以为出了什么事。
 */
export function rollbackConfirmText(target: number, current: number, completed: number): string {
  const lines = [
    `把行程内容恢复到 v${target}？`,
    `当前是 v${current}，回滚后会生成 v${current + 1}，历史版本都会保留，随时能再回滚回来。`,
  ]
  if (completed > 0) {
    lines.push(
      `已经打勾的 ${completed} 项会被清空：行程条目需要重建，旧的勾选记录对不上新条目。`,
    )
  }
  return lines.join('\n')
}

/** 回滚成功提示：清过进度就如实说明，悄悄归零会让人以为是自己点错了 */
export function rollbackSuccessText(target: number, version: number, cleared: number): string {
  const base = `已恢复到 v${target}（当前 v${version}）`
  return cleared > 0 ? `${base}，同时清空了 ${cleared} 项勾选记录` : base
}

/** 版本列表里要展示的说明：v0 是 AI 初稿，其余用后端给的变更摘要 */
export function versionLabel(version: PlanVersionVO): string {
  if (version.versionNo === 0) return version.changeSummary || 'AI 生成初稿'
  return version.changeSummary || '快照'
}
