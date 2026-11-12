<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/store/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const menus = [
  { key: '/chat', label: 'AI 助手', icon: '💬', ready: true },
  { key: '/plans', label: '我的旅行', icon: '🧭', ready: true },
  { key: '/placeholder/community', label: '旅行社区', icon: '📖', ready: false },
  { key: '/placeholder/settings', label: '设置', icon: '⚙️', ready: false },
]

const active = computed(() => {
  if (route.path.startsWith('/chat')) return '/chat'
  // 详情页 /plans/:id 也要点亮「我的旅行」
  if (route.path.startsWith('/plans')) return '/plans'
  return route.path
})

function go(key: string): void {
  router.push(key)
}

function logout(): void {
  userStore.logout()
  router.replace('/login')
}

/**
 * 后端健康灯。
 * 之前后端没起时，用户只能在发消息后看到一句「请求失败(500)」，
 * 完全猜不到原因。这里直接轮询 /api/health，把服务状态摆在顶栏上。
 */
const serviceOnline = ref<boolean | null>(null)
let healthTimer: number | undefined

const healthClass = computed(() => {
  if (serviceOnline.value === null) return 'unknown'
  return serviceOnline.value ? 'ok' : 'bad'
})

const healthText = computed(() => {
  if (serviceOnline.value === null) return '检测中'
  return serviceOnline.value ? '服务正常' : '服务未连接'
})

async function probeHealth(): Promise<void> {
  try {
    // 用原生 fetch：健康探测失败不应该弹全局错误提示
    const resp = await fetch('/api/health', { headers: { Accept: 'application/json' } })
    serviceOnline.value = resp.ok
  } catch {
    serviceOnline.value = false
  }
}

onMounted(() => {
  void probeHealth()
  healthTimer = window.setInterval(probeHealth, 20_000)
  // 刷新页面后 pinia 是空的，顶栏会一直显示「未登录」。
  // token 还在的话补拉一次用户信息，让顶栏与后端登录态一致。
  if (!userStore.user) void userStore.restore()
})

onUnmounted(() => {
  if (healthTimer) window.clearInterval(healthTimer)
})
</script>

<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="logo-row">
        <span class="logo">✈</span>
        <span class="name">AI 旅行助手</span>
      </div>
      <nav>
        <div
          v-for="m in menus"
          :key="m.key"
          class="menu-item"
          :class="{ active: active === m.key, disabled: !m.ready }"
          @click="go(m.key)"
        >
          <span class="icon">{{ m.icon }}</span>
          <span>{{ m.label }}</span>
          <el-tag v-if="!m.ready" size="small" type="info" effect="plain" class="tag">待开发</el-tag>
        </div>
      </nav>
      <div class="sidebar-footer">
        <span class="version">Phase 1 · v0.1.0</span>
      </div>
    </aside>

    <div class="body">
      <header class="topbar">
        <span class="title">{{ route.meta.title || 'AI 旅行助手' }}</span>
        <div class="topbar-right">
          <span class="health" :class="healthClass" :title="`/api/health · ${healthText}`">
            <i class="dot" />{{ healthText }}
          </span>
          <el-dropdown>
            <span class="user">
              {{ userStore.user?.nickname || userStore.user?.username || '未登录' }}
              <el-icon><span>▾</span></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>
      <main class="content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  height: 100%;
}

.sidebar {
  width: 208px;
  background: var(--at-surface);
  border-right: 1px solid var(--at-border);
  display: flex;
  flex-direction: column;
  padding: 18px 12px;
}

.logo-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 8px 18px;
}

.logo {
  font-size: 20px;
}

.name {
  font-weight: 600;
  font-size: 15px;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 10px;
  font-size: 14px;
  color: var(--at-text-secondary);
  cursor: pointer;
  margin-bottom: 4px;
  transition: background 0.15s;
}

.menu-item:hover {
  background: #f5f7fa;
}

.menu-item.active {
  background: var(--at-primary-light);
  color: var(--at-primary);
  font-weight: 500;
}

.menu-item.disabled {
  opacity: 0.65;
}

.tag {
  margin-left: auto;
  transform: scale(0.85);
}

.sidebar-footer {
  margin-top: auto;
  padding: 0 8px;
  font-size: 11px;
  color: #a8b1bd;
}

.body {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.topbar {
  height: 56px;
  flex: 0 0 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  background: var(--at-surface);
  border-bottom: 1px solid var(--at-border);
}

.title {
  font-size: 15px;
  font-weight: 600;
}

.user {
  font-size: 13px;
  color: var(--at-text-secondary);
  cursor: pointer;
  outline: none;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.health {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid var(--at-border);
  color: var(--at-text-secondary);
  background: #fbfcfd;
  user-select: none;
}

.health .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #c0c8d2;
}

.health.ok .dot {
  background: #3cba92;
}

.health.bad {
  color: #c45656;
  border-color: #f6d5d5;
  background: #fef4f4;
}

.health.bad .dot {
  background: #f56c6c;
}

.content {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
</style>
