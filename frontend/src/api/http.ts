import axios, { type AxiosInstance, type AxiosResponse } from 'axios'
import { ElMessage } from 'element-plus'
import type { ApiResult } from './types'

export const TOKEN_KEY = 'ai_travel_token'

/** 带业务错误码的异常，供调用方按 code 分流（如 30104 版本冲突） */
export interface ApiError extends Error {
  code?: number
}

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

const http: AxiosInstance = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

http.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (response: AxiosResponse<ApiResult<unknown>>) => {
    const body = response.data
    // 后端统一 Result，code 非 0 一律视为失败，避免出现“接口 200 但业务失败”被忽略
    if (body && typeof body.code === 'number' && body.code !== 0) {
      ElMessage.error(body.message || '请求失败')
      // 把业务码带出去：有些调用方要区分「版本冲突」和普通失败（如编辑保存）
      const error: ApiError = new Error(body.message || '请求失败')
      error.code = body.code
      return Promise.reject(error)
    }
    return response
  },
  (error) => {
    const status = error?.response?.status
    const body = error?.response?.data as ApiResult<unknown> | undefined
    if (status === 401) {
      clearToken()
      if (!location.hash.includes('/login')) {
        location.hash = '#/login'
      }
      ElMessage.warning('登录已过期，请重新登录')
    } else {
      ElMessage.error(body?.message || error.message || '网络异常')
    }
    return Promise.reject(error)
  },
)

export default http
