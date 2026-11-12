<script setup lang="ts">
import { computed } from 'vue'
import type { PlanTodayVO, ProgressItem } from '@/api/types'
import {
  canCheckToday,
  dayProgressText,
  emptyDayText,
  formatDateLabel,
  phaseLabel,
  phaseTitle,
  unanchoredText,
} from '@/utils/today'

/**
 * 当日执行卡片。
 *
 * 只做一件事：把「今天」摆出来。整趟行程仍然在「全部日程」里，切回去就能看到——
 * 所以这里不重复渲染其他天，也不做跨天跳转，避免用户以为自己漏了什么。
 */
const props = defineProps<{
  today: PlanTodayVO | null
  status: string
  /** 正在提交的行程项，防连点；null 表示空闲 */
  marking: string | null
}>()

const emit = defineEmits<{ mark: [itemId: string, completed: boolean]; anchor: [] }>()

const checkable = computed(() => canCheckToday(props.today, props.status))
const day = computed(() => props.today?.day ?? null)
const emptyText = computed(() => (props.today ? emptyDayText(props.today.phase) : ''))

function toggle(item: ProgressItem): void {
  if (!checkable.value || props.marking) return
  emit('mark', item.itemId, !item.completed)
}
</script>

<template>
  <div v-if="today" class="today">
    <div class="head">
      <div class="head-left">
        <span class="phase">{{ phaseTitle(today.phase) }}</span>
        <span class="date">{{ formatDateLabel(today.date) }}</span>
      </div>
      <span class="count">{{ phaseLabel(today) }}</span>
    </div>

    <!-- 没有出发日：说清为什么算不出，并给出唯一可行的下一步 -->
    <div v-if="today.phase === 'UNANCHORED'" class="unanchored">
      <p>{{ unanchoredText() }}</p>
      <p class="weak">选一个出发日开始，或者就按今天出发——之后每天会按日期自动对应。</p>
      <button v-if="status === 'CONFIRMED'" type="button" class="cta" @click="emit('anchor')">
        选择出发日期
      </button>
      <!-- 草稿还没确认：状态机不允许 DRAFT 直接开始，先引导回正确动作 -->
      <p v-else-if="status === 'DRAFT'" class="weak">先确认行程，确认后就能选出发日开始了。</p>
    </div>

    <div v-else-if="day" class="card">
      <div class="card-head">
        <span v-if="day.city" class="city">{{ day.city }}</span>
        <span v-if="day.theme" class="theme">{{ day.theme }}</span>
        <span v-if="dayProgressText(today)" class="sub">{{ dayProgressText(today) }}</span>
      </div>

      <ul v-if="day.items.length" class="items">
        <li
          v-for="item in day.items"
          :key="item.itemId"
          class="item"
          :class="{ done: item.completed, next: item.next, clickable: checkable }"
          @click="toggle(item)"
        >
          <!-- 勾选框只显示，点击交给整行：14px 的方块在手机上几乎点不中 -->
          <div v-if="checkable" class="check">
            <el-checkbox
              :model-value="item.completed"
              :disabled="marking === item.itemId"
            />
          </div>
          <div class="time">
            <span>{{ item.startTime || '—' }}</span>
            <span v-if="item.endTime" class="end">{{ item.endTime }}</span>
          </div>
          <div class="main">
            <div class="title">{{ item.title }}</div>
            <p v-if="item.note" class="note">{{ item.note }}</p>
          </div>
        </li>
      </ul>
      <p v-else class="empty">{{ emptyText }}</p>
    </div>

    <div class="overall">
      <span class="overall-text">整趟 {{ today.completed }} / {{ today.total }} 项</span>
      <el-progress
        :percentage="today.percent"
        :stroke-width="6"
        :show-text="false"
        :color="today.percent === 100 ? '#12b76a' : '#2f6fed'"
      />
    </div>
  </div>
</template>

<style scoped>
.today {
  padding: 14px 16px;
  border: 1px solid var(--at-border);
  border-radius: var(--at-radius);
  background: var(--at-surface);
}

.head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding-bottom: 10px;
  border-bottom: 1px dashed var(--at-border);
}

.head-left {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.phase {
  font-size: 14px;
  font-weight: 600;
  color: var(--at-text);
}

.date {
  font-size: 12px;
  color: var(--at-text-secondary);
}

.count {
  font-size: 12px;
  color: var(--at-primary);
  font-variant-numeric: tabular-nums;
}

.unanchored {
  padding: 12px 0 2px;
  font-size: 12px;
  line-height: 1.8;
  color: var(--at-text-secondary);
}

.unanchored p {
  margin: 0 0 4px;
}

.unanchored .weak {
  color: #a8b1bd;
}

.cta {
  margin-top: 6px;
  padding: 5px 12px;
  font-size: 12px;
  color: #fff;
  background: var(--at-primary);
  border: none;
  border-radius: 999px;
  cursor: pointer;
}

.card-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 10px 0 4px;
}

.city {
  font-size: 13px;
  font-weight: 600;
  color: var(--at-text);
}

.theme {
  font-size: 12px;
  color: var(--at-text-secondary);
}

.sub {
  margin-left: auto;
  font-size: 12px;
  color: #12b76a;
  font-variant-numeric: tabular-nums;
}

.items {
  margin: 0;
  padding: 0;
  list-style: none;
}

.item {
  display: flex;
  gap: 14px;
  padding: 10px 0;
}

.item + .item {
  border-top: 1px solid #f2f4f7;
}

.check {
  display: flex;
  align-items: flex-start;
  padding-top: 1px;
  /* 点击一律由整行处理 */
  pointer-events: none;
}

.check :deep(.el-checkbox) {
  height: auto;
  margin-right: 0;
}

.item.clickable {
  cursor: pointer;
}

.item.clickable:hover {
  background: #fafbfc;
}

.item.done .title {
  color: #a8b1bd;
  text-decoration: line-through;
}

.item.done .time,
.item.done .note {
  opacity: 0.55;
}

.item.next {
  position: relative;
}

.item.next::before {
  content: '';
  position: absolute;
  left: -12px;
  top: 10px;
  bottom: 10px;
  width: 3px;
  border-radius: 3px;
  background: var(--at-primary);
}

.time {
  flex-shrink: 0;
  width: 52px;
  font-size: 12px;
  color: var(--at-text-secondary);
  text-align: right;
}

.time .end {
  display: block;
  color: #b6bec8;
}

.main {
  flex: 1;
  min-width: 0;
}

.title {
  font-size: 13px;
  color: var(--at-text);
}

.note {
  margin: 5px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: #a8b1bd;
}

.empty {
  margin: 10px 0 0;
  padding: 12px 14px;
  border: 1px dashed var(--at-border);
  border-radius: 8px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--at-text-secondary);
  background: #fbfcfd;
}

.overall {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--at-border);
}

.overall-text {
  display: block;
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--at-text-secondary);
  font-variant-numeric: tabular-nums;
}
</style>
