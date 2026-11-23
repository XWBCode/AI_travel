/**
 * 计划生成中的等待文案。
 *
 * 真实模型出一份 3 天计划要 30-45s（换到 `jiaorong-deepseek-v4-pro` 后，7 天行程实测可到 170s），
 * 只显示「正在生成计划…」会让人以为页面卡死。这里只做一件事：把已等待秒数拼进去，
 * 并在等久了以后补一句人话，告诉用户「可以等」还是「快该有结果了」。
 *
 * 抽成纯函数是为了能单测——组件里的 setInterval 在后台标签会被浏览器降频
 * （Chrome 会降到 1 次/分钟），靠截图验证并不可靠。
 */

/** 前 3 秒不显示秒数：模型很快返回时数字闪一下反而像卡顿 */
const SHOW_AFTER_SECONDS = 3
/** 超过 30 秒才提「长行程要一分多钟」：一上来就说，会显得这系统本来就很慢 */
const SLOW_AFTER_SECONDS = 30
/** 超过 2 分钟给「上限」：与 Python 侧 LLM_TIMEOUT_SECONDS=240 对齐，不说空话 */
const VERY_SLOW_AFTER_SECONDS = 120

const HINT_SLOW = '（长行程要一分多钟，先别刷新，也别重复发送）'
const HINT_VERY_SLOW = '（超过 4 分钟会自动结束并告诉你原因，不用一直等）'

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
  let hint = ''
  if (seconds >= VERY_SLOW_AFTER_SECONDS) hint = HINT_VERY_SLOW
  else if (seconds >= SLOW_AFTER_SECONDS) hint = HINT_SLOW
  return `${message} 已等待 ${seconds} 秒${hint}`
}
