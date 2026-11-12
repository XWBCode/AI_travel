/** 追问选项：`label` 给按钮看，`value` 是要拼进补答消息的短语 */
export interface FollowUpOption {
  label: string
  value: string
}

/** 一个缺失槽位的追问，`field` 用于记录用户选了哪一组 */
export interface FollowUpQuestion {
  field: string
  question: string
  options: FollowUpOption[]
}

/**
 * 把用户点选的选项拼成一条可以直接发的消息。
 *
 * 为什么是「原话 + 补答」而不是只发补答：
 * 意图判定是基于整句话做槽位抽取的，只发「3天」会丢掉「成都」，
 * 后端会再问一遍「想去哪个城市？」，用户会觉得系统没记住刚才说的话。
 *
 * 保留原话也保留了用户自己的表述（「2个人」「预算5000」），
 * 这些信息不必让用户再说一次。
 *
 * @param origin 触发追问的那句用户原话
 * @param picks  已选值，key 是槽位名，value 是选项的 `value`
 * @param fields 追问涉及的槽位顺序，决定拼接顺序
 */
export function buildFollowUpReply(
  origin: string,
  picks: Record<string, string>,
  fields: string[],
): string {
  const answers = fields.map((field) => (picks[field] ?? '').trim()).filter(Boolean)
  // 去掉原话末尾的标点，避免出现「成都。，玩3天」
  const base = origin.trim().replace(/[。！？!?，,、；;：:\s]+$/, '')
  if (answers.length === 0) return base
  if (!base) return answers.join('，')
  return `${base}，${answers.join('，')}`
}

/** 是否所有追问都已有答案（决定要不要自动提交） */
export function isFollowUpComplete(
  picks: Record<string, string>,
  fields: string[],
): boolean {
  return fields.length > 0 && fields.every((field) => Boolean((picks[field] ?? '').trim()))
}

/**
 * 解析 intent 事件里的 `followUps`。
 *
 * 服务端结构异常时**整组丢弃**而不是渲染半截按钮：一个没有选项的追问
 * 会让用户点不动，不如让正文的话照常显示，用户自己打字补充。
 */
export function parseFollowUps(raw: unknown): FollowUpQuestion[] {
  if (!Array.isArray(raw)) return []
  const result: FollowUpQuestion[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const record = item as Record<string, unknown>
    const field = typeof record.field === 'string' ? record.field.trim() : ''
    const question = typeof record.question === 'string' ? record.question.trim() : ''
    const options = Array.isArray(record.options) ? record.options : []
    const parsed = options
      .filter((option): option is Record<string, unknown> => Boolean(option) && typeof option === 'object')
      .map((option) => ({
        label: typeof option.label === 'string' ? option.label.trim() : '',
        value: typeof option.value === 'string' ? option.value.trim() : '',
      }))
      .filter((option) => option.label && option.value)
    if (!field || !question || parsed.length === 0) continue
    result.push({ field, question, options: parsed })
  }
  return result
}

