/**
 * 计划生成中的等待文案。
 *
 * 真实模型出一份 3 天计划要 30-45s，只显示「正在生成计划…」会让人以为页面卡死。
 * 这里只做一件事：把已等待秒数拼进去。抽成纯函数是为了能单测——
 * 组件里的 setInterval 在后台标签会被浏览器降频（Chrome 会降到 1 次/分钟），
 * 靠截图验证并不可靠。
 */

/** 前 3 秒不显示秒数：模型很快返回时数字闪一下反而像卡顿 */
const SHOW_AFTER_SECONDS = 3

/**
 * @param message 后端下发的阶段提示，如「正在生成计划…」
 * @param startedAt 开始时间戳；缺省表示不值得计时（直接返回原提示）
 * @param now 当前时间戳，便于测试注入
 */
export function formatProgress(
  message: string,
  startedAt?: number,
  now: number = Date.now(),
): string {
  if (!message) return ''
  if (!startedAt) return message
  const seconds = Math.floor((now - startedAt) / 1000)
  if (seconds < SHOW_AFTER_SECONDS) return message
  return `${message} 已等待 ${seconds} 秒（长行程通常需要半分钟以上）`
}
