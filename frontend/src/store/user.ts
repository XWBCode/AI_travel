import { defineStore } from 'pinia'
import { ref } from 'vue'
import { authApi } from '@/api'
import { clearToken, getToken, setToken } from '@/api/http'
import type { UserVO } from '@/api/types'

export const useUserStore = defineStore('user', () => {
  const token = ref<string>(getToken())
  const user = ref<UserVO | null>(null)

  async function login(username: string, password: string): Promise<void> {
    const res = await authApi.login(username, password)
    token.value = res.accessToken
    setToken(res.accessToken)
    user.value = res.user
  }

  async function register(username: string, password: string, nickname?: string): Promise<void> {
    await authApi.register(username, password, nickname)
  }

  /** 刷新页面后恢复登录态：token 存在则拉一次用户信息做校验 */
  async function restore(): Promise<boolean> {
    if (!token.value) return false
    try {
      user.value = await authApi.me()
      return true
    } catch {
      logout()
      return false
    }
  }

  function logout(): void {
    token.value = ''
    user.value = null
    clearToken()
  }

  return { token, user, login, register, restore, logout }
})
