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
