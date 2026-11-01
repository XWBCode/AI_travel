import http from './http'
import type { ApiResult, LoginResponse, MessageVO, SessionVO, UserVO } from './types'

export const authApi = {
  login: (username: string, password: string) =>
    http.post<ApiResult<LoginResponse>>('/auth/login', { username, password }).then((r) => r.data.data),

  register: (username: string, password: string, nickname?: string) =>
    http.post<ApiResult<UserVO>>('/auth/register', { username, password, nickname }).then((r) => r.data.data),

  me: () => http.get<ApiResult<UserVO>>('/auth/me').then((r) => r.data.data),
}

export const chatApi = {
  listSessions: () => http.get<ApiResult<SessionVO[]>>('/chat/sessions').then((r) => r.data.data),

  listMessages: (sessionId: number) =>
    http.get<ApiResult<MessageVO[]>>(`/chat/sessions/${sessionId}/messages`).then((r) => r.data.data),

  renameSession: (sessionId: number, title: string) =>
    http.patch<ApiResult<void>>(`/chat/sessions/${sessionId}`, { title }).then((r) => r.data),

  deleteSession: (sessionId: number) =>
    http.delete<ApiResult<void>>(`/chat/sessions/${sessionId}`).then((r) => r.data),
}
