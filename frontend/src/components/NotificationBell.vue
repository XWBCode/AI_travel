<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { notificationsApi } from '@/api'
import type { NotificationVO } from '@/api/types'
import { badgeText, notificationTarget, relativeTime, typeIcon } from '@/utils/notification'

/**
 * 顶栏的通知铃（R-30）。
 *
 * 三条设计决定：
 *
 * 1. **红点和列表来自同一个响应。** 分两个接口的话，两次请求之间刚好写完一条，
 *    用户就会看到「红点 3 条、点开只有 2 条」——他只会认为这功能坏了。
 * 2. **每 30 秒轮询，且页面不可见时跳过。** 通知由别处产生（在详情页确认行程，
 *    回来列表页才看到），不轮询就只能等刷新；而标签页在后台时还在请求纯属浪费。
 * 3. **请求失败不弹提示。** 顶栏已经有服务健康灯，网络断了它自己会红；
 *    每 30 秒弹一次「网络异常」只会让人以为系统崩了。
 */
const router = useRouter()

const items = ref<NotificationVO[]>([])
const unread = ref(0)
const total = ref(0)
const loading = ref(false)
const unreadOnly = ref(false)
/** 点完某条通知要把它收起来：跳转后弹层还挂在页面上挡着内容很别扭 */
const visible = ref(false)
const pageSize = 20

const badge = computed(() => badgeText(unread.value))
const emptyText = computed(() =>
  unreadOnly.value ? '没有未读通知' : '还没有通知',
)

let timer: number | undefined

async function load(): Promise<void> {
  loading.value = true
  try {
    const page = await notificationsApi.list({ page: 1, size: pageSize, unreadOnly: unreadOnly.value })
    items.value = page.records
    unread.value = page.unreadCount
    total.value = page.total
  } catch {
    // 静默失败：见文件头第 3 条
  } finally {
    loading.value = false
  }
}

async function toggleUnreadOnly(): Promise<void> {
  unreadOnly.value = !unreadOnly.value
  await load()
}

/** 点开一条：先标已读（失败也照样跳，读没读不影响看内容），再跳对应计划 */
async function open(n: NotificationVO): Promise<void> {
  const target = notificationTarget(n)
  if (!n.read) {
    try {
      unread.value = await notificationsApi.markRead(n.notificationId)
      const hit = items.value.find((x) => x.notificationId === n.notificationId)
      if (hit) hit.read = true
      if (unreadOnly.value) items.value = items.value.filter((x) => x.notificationId !== n.notificationId)
    } catch {
      // 已读状态晚一点同步无所谓
    }
  }
  if (target) {
    visible.value = false
    void router.push(target)
  }
}

async function markAll(): Promise<void> {
  try {
    unread.value = await notificationsApi.markAllRead()
    await load()
  } catch {
    // 同上
  }
}

function onVisible(): void {
  // 从别的标签页/后台切回来时补一次：这期间可能已经产生了通知
  if (!document.hidden) void load()
}

/** 有人写了数据（确认/开始/完成行程……）——红点立刻重算，不等下一次轮询 */
function onMutation(): void {
  void load()
}

onMounted(() => {
  void load()
  timer = window.setInterval(() => {
    if (!document.hidden) void load()
  }, 30_000)
  document.addEventListener('visibilitychange', onVisible)
  window.addEventListener('app:mutation', onMutation)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
  document.removeEventListener('visibilitychange', onVisible)
  window.removeEventListener('app:mutation', onMutation)
})
</script>

<template>
  <el-popover
    v-model:visible="visible"
    placement="bottom-end"
    :width="360"
    trigger="click"
    @show="load"
  >
    <template #reference>
      <button
        type="button"
        class="bell"
        :class="{ 'has-unread': unread > 0 }"
        :title="unread ? `${unread} 条未读` : '通知'"
        :aria-label="unread ? `通知，${unread} 条未读` : '通知'"
      >
        <svg class="icon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
          <path
            d="M6 9a6 6 0 1 1 12 0c0 3.2.7 5 1.6 6.1.4.5.1 1.2-.5 1.2H4.9c-.6 0-.9-.7-.5-1.2C5.3 14 6 12.2 6 9Z"
            fill="none"
            stroke="currentColor"
            stroke-width="1.7"
            stroke-linejoin="round"
          />
          <path
            d="M9.6 19.2a2.6 2.6 0 0 0 4.8 0"
            fill="none"
            stroke="currentColor"
            stroke-width="1.7"
            stroke-linecap="round"
          />
        </svg>
        <span v-if="badge" class="badge">{{ badge }}</span>
      </button>
    </template>

    <div class="panel">
      <div class="panel-head">
        <span class="panel-title">通知</span>
        <div class="panel-actions">
          <el-button link size="small" @click="toggleUnreadOnly">
            {{ unreadOnly ? '看全部' : '只看未读' }}
          </el-button>
          <el-button link size="small" :disabled="unread === 0" @click="markAll">全部已读</el-button>
        </div>
      </div>

      <div v-loading="loading" class="list">
        <p v-if="!items.length && !loading" class="empty">
          {{ emptyText }}
          <br />
          <span class="empty-hint">确认、开始、完成行程时，这里会记一条。</span>
        </p>
        <button
          v-for="n in items"
          :key="n.notificationId"
          type="button"
          class="item"
          :class="{ unread: !n.read }"
          @click="open(n)"
        >
          <span class="dot-icon">{{ typeIcon(n.type) }}</span>
          <div class="item-body">
            <div class="item-title">
              {{ n.title }}
              <i v-if="!n.read" class="unread-dot" />
            </div>
            <div v-if="n.body" class="item-text">{{ n.body }}</div>
            <div class="item-time">{{ relativeTime(n.createTime) }}</div>
          </div>
        </button>
      </div>

      <div v-if="total > items.length" class="panel-foot">仅显示最近 {{ items.length }} 条</div>
    </div>
  </el-popover>
</template>

<style scoped>
.bell {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  cursor: pointer;
  /* emoji 换成内联 SVG 后颜色可控：与顶栏「服务正常」胶囊同一灰阶（BUG-005） */
  color: #5b6572;
  padding: 0;
  border: none;
  border-radius: 8px;
  background: transparent;
  transition: background 0.15s, color 0.15s;
}

/* 有未读时压深一档，避免只靠红点才被注意到 */
.bell.has-unread {
  color: var(--at-primary);
}

.icon {
  display: block;
}

.bell:focus-visible {
  outline: 2px solid var(--at-primary);
  outline-offset: 2px;
}

.bell:hover {
  background: #f5f7fa;
  color: var(--at-primary);
}

.badge {
  position: absolute;
  top: -4px;
  right: -6px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 999px;
  background: #f56c6c;
  color: #fff;
  font-size: 10px;
  line-height: 16px;
  text-align: center;
  box-sizing: border-box;
}

.panel {
  margin: -12px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px 8px;
  border-bottom: 1px solid var(--at-border);
}

.panel-title {
  font-size: 13px;
  font-weight: 600;
}

.panel-actions {
  display: flex;
  gap: 8px;
}

.list {
  max-height: 380px;
  overflow-y: auto;
  min-height: 80px;
}

.empty {
  margin: 0;
  padding: 28px 16px;
  text-align: center;
  font-size: 12px;
  color: var(--at-text-secondary);
  line-height: 1.9;
}

.empty-hint {
  color: #a8b1bd;
}

.item {
  display: flex;
  gap: 10px;
  width: 100%;
  padding: 10px 14px;
  cursor: pointer;
  border: none;
  border-bottom: 1px solid #f2f4f7;
  background: transparent;
  font: inherit;
  text-align: left;
}

.item:hover {
  background: #fafbfc;
}

.dot-icon {
  flex: 0 0 18px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--at-primary-light);
  color: var(--at-primary);
  font-size: 10px;
  line-height: 18px;
  text-align: center;
}

.item-body {
  min-width: 0;
  flex: 1;
}

.item-title {
  font-size: 13px;
  color: var(--at-text-secondary);
  display: flex;
  align-items: center;
  gap: 6px;
}

.item.unread .item-title {
  color: var(--at-text);
  font-weight: 500;
}

.unread-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #f56c6c;
}

.item-text {
  margin-top: 2px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--at-text-secondary);
  word-break: break-word;
}

.item-time {
  margin-top: 2px;
  font-size: 11px;
  color: #a8b1bd;
}

.panel-foot {
  padding: 8px 14px;
  font-size: 11px;
  color: #a8b1bd;
  text-align: center;
  border-top: 1px solid var(--at-border);
}
</style>
