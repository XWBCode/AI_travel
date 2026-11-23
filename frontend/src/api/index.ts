import http from './http'
import { newRequestId } from './stream'
import type {
  ApiResult,
  Id,
  LoginResponse,
  MessageVO,
  NotificationPageVO,
  PageResult,
  AdjustmentConfirmResult,
  PlanAdjustmentVO,
  PlanProgressVO,
  PlanRollbackResult,
  PlanSummaryVO,
  PlanTodayVO,
  PlanUpdatePayload,
  PlanVersionVO,
  PlanWeatherVO,
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
  /**
   * 开始行程。带 startDate 表示用户确认了出发日——计划没有出发日时，
   * 这是唯一能把「第几天」钉到真实日期的入口；不传则沿用计划原值或服务端当天。
   */
  start: (id: Id, startDate?: string) =>
    http.post<ApiResult<void>>(`/plans/${id}/start`, startDate ? { startDate } : undefined).then((r) => r.data),
  complete: (id: Id) => http.post<ApiResult<void>>(`/plans/${id}/complete`).then((r) => r.data),
  cancel: (id: Id) => http.post<ApiResult<void>>(`/plans/${id}/cancel`).then((r) => r.data),

  /** 执行进度。任何状态可读，草稿阶段返回全 0 */
  progress: (id: Id) =>
    http.get<ApiResult<PlanProgressVO>>(`/plans/${id}/progress`).then((r) => r.data.data),

  /**
   * 计划天气（M3-1）。
   *
   * 注意：**降级不抛异常**。后端用 `available=false + reason` 表达"暂时没有天气"，
   * 所以这里不做 catch 分支的文案——调用方拿到的永远是一个可判断的对象。
   * 只有鉴权失败/计划不属于当前用户才会 reject（那是真的错误，页面本来就该失败）。
   */
  weather: (id: Id) =>
    http.get<ApiResult<PlanWeatherVO>>(`/plans/${id}/weather`).then((r) => r.data.data),

  /**
   * 当日执行视图。date 传本机今天（YYYY-MM-DD）：服务端时区与用户所在时区可能差一天，
   * 用服务器时间算会让跨时区用户在深夜看到昨天的行程。
   */
  today: (id: Id, date?: string) =>
    http
      .get<ApiResult<PlanTodayVO>>(`/plans/${id}/today`, { params: date ? { date } : undefined })
      .then((r) => r.data.data),

  /**
   * 勾选 / 取消勾选行程项，返回最新进度。
   * 后端幂等（uk_plan_item + ON DUPLICATE KEY），重复点不会产生重复行；
   * 只有「进行中」能写，其他状态返回 30103。
   */
  markProgress: (id: Id, itemId: Id, completed: boolean) =>
    http
      .post<ApiResult<PlanProgressVO>>(`/plans/${id}/progress`, { itemId, completed })
      .then((r) => r.data.data),

  /**
   * 回滚到指定版本。返回回滚后的详情——前端不用「回滚成功再拉一次详情」，
   * 两次请求之间页面会短暂显示旧内容，看起来像回滚没生效。
   */
  rollback: (id: Id, versionNo: number) =>
    http
      .post<ApiResult<PlanRollbackResult>>(`/plans/${id}/versions/${versionNo}/rollback`)
      .then((r) => r.data.data),

  /**
   * 让 AI 基于当前计划生成调整建议（R-23）。
   *
   * 只产出建议，不改计划：真正的写入发生在确认之后。所以这里慢一点（模型思考）
   * 不会影响任何已有数据，用户可以放心等。
   *
   * `clientRequestId` 是幂等键：同一次用户意图的重试必须复用同一个 ID，
   * 否则后端会把它当成两次新请求，落下两条待确认建议。调用方不传时自动生成——
   * 那等于「不重试也幂等」，只挡住网络层的自动重发，挡不住用户手动再点一次。
   */
  proposeAdjustment: (id: Id, instruction: string, clientRequestId: string = newRequestId()) =>
    http
      .post<ApiResult<PlanAdjustmentVO>>(`/plans/${id}/adjustments`, { instruction, clientRequestId })
      .then((r) => r.data.data),

  /** 确认调整：由 Java 应用增量操作并生成新版本 */
  confirmAdjustment: (id: Id, adjustmentId: string) =>
    http
      .post<ApiResult<AdjustmentConfirmResult>>(`/plans/${id}/adjustments/${adjustmentId}/confirm`)
      .then((r) => r.data.data),

  /** 放弃建议：不改计划、不产生版本 */
  rejectAdjustment: (id: Id, adjustmentId: string, reason?: string) =>
    http
      .post<ApiResult<void>>(`/plans/${id}/adjustments/${adjustmentId}/reject`, { reason })
      .then((r) => r.data),

  /** 最近若干条建议（含已处理），用于回看 */
  adjustments: (id: Id) =>
    http.get<ApiResult<PlanAdjustmentVO[]>>(`/plans/${id}/adjustments`).then((r) => r.data.data),
}

/**
 * 站内通知（R-30）。所有请求都带 `skipErrorToast`：
 * 轮询失败时顶栏的健康灯已经说明问题了，再每 30 秒弹一次红条毫无意义。
 */
export const notificationsApi = {
  list: (params: { page?: number; size?: number; unreadOnly?: boolean } = {}) =>
    http
      .get<ApiResult<NotificationPageVO>>('/notifications', { params, skipErrorToast: true })
      .then((r) => r.data.data),

  /** 标记单条已读；返回操作后的未读数，前端直接拿去更新红点 */
  markRead: (id: Id) =>
    http
      .post<ApiResult<number>>(`/notifications/${id}/read`, undefined, { skipErrorToast: true })
      .then((r) => r.data.data),

  markAllRead: () =>
    http
      .post<ApiResult<number>>('/notifications/read-all', undefined, { skipErrorToast: true })
      .then((r) => r.data.data),
}
