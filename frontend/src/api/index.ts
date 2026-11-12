import http from './http'
import type {
  ApiResult,
  Id,
  LoginResponse,
  MessageVO,
  PageResult,
  PlanSummaryVO,
  PlanUpdatePayload,
  PlanVersionVO,
  SessionVO,
  TravelPlan,
  UserVO,
} from './types'

export const authApi = {
  login: (username: string, password: string) =>
    http.post<ApiResult<LoginResponse>>('/auth/login', { username, password }).then((r) => r.data.data),

  register: (username: string, password: string, nickname?: string) =>
    http.post<ApiResult<UserVO>>('/auth/register', { username, password, nickname }).then((r) => r.data.data),

  me: () => http.get<ApiResult<UserVO>>('/auth/me').then((r) => r.data.data),
}

export const chatApi = {
  listSessions: () => http.get<ApiResult<SessionVO[]>>('/chat/sessions').then((r) => r.data.data),

  // sessionId 是雪花 ID 字符串，直接拼进路径，不要转 Number
  listMessages: (sessionId: Id) =>
    http.get<ApiResult<MessageVO[]>>(`/chat/sessions/${sessionId}/messages`).then((r) => r.data.data),

  renameSession: (sessionId: Id, title: string) =>
    http.patch<ApiResult<void>>(`/chat/sessions/${sessionId}`, { title }).then((r) => r.data),

  deleteSession: (sessionId: Id) =>
    http.delete<ApiResult<void>>(`/chat/sessions/${sessionId}`).then((r) => r.data),
}

/**
 * 计划接口。所有 id 都是雪花 ID 字符串，直接拼路径，不要转 Number
 * （19 位超出 JS 安全整数，转了就查不到）。
 */
export const plansApi = {
  list: (params: { page?: number; size?: number; status?: string } = {}) =>
    http
      .get<ApiResult<PageResult<PlanSummaryVO>>>('/plans', { params })
      .then((r) => r.data.data),

  detail: (id: Id) => http.get<ApiResult<TravelPlan>>(`/plans/${id}`).then((r) => r.data.data),

  versions: (id: Id) =>
    http.get<ApiResult<PlanVersionVO[]>>(`/plans/${id}/versions`).then((r) => r.data.data),

  /**
   * 编辑草稿基础信息。只有 DRAFT 能改，且必须带 baseVersion——
   * 版本不一致会被后端拒绝（30104），这是「两个标签页同时改」时的保护。
   */
  update: (id: Id, payload: PlanUpdatePayload) =>
    http.patch<ApiResult<TravelPlan>>(`/plans/${id}`, payload).then((r) => r.data.data),

  // 状态流转：后端按状态机拒绝非法跃迁，前端不做二次判断，只展示报错
  confirm: (id: Id) => http.post<ApiResult<void>>(`/plans/${id}/confirm`).then((r) => r.data),
  start: (id: Id) => http.post<ApiResult<void>>(`/plans/${id}/start`).then((r) => r.data),
  complete: (id: Id) => http.post<ApiResult<void>>(`/plans/${id}/complete`).then((r) => r.data),
  cancel: (id: Id) => http.post<ApiResult<void>>(`/plans/${id}/cancel`).then((r) => r.data),
}
