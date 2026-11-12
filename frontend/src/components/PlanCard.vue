<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { PlanDay, TravelPlan } from '@/api/types'

const props = defineProps<{ plan: TravelPlan }>()

const router = useRouter()

/**
 * 卡片只存在于当前这条流里，刷新就没了；计划本身已落库。
 * 给一个明确入口，用户想细看时不必重新生成一遍。
 */
function openDetail(): void {
  if (!props.plan.planId) return
  // planId 是雪花 ID 字符串，直接拼；转 Number 会丢精度
  router.push(`/plans/${props.plan.planId}`)
}

/** 默认只展开两天，避免一条回复把整屏占满；用户可自行展开 */
const PREVIEW_DAYS = 2

const expanded = ref(false)

const allDays = computed<PlanDay[]>(() => props.plan.days ?? [])
const visibleDays = computed(() =>
  expanded.value ? allDays.value : allDays.value.slice(0, PREVIEW_DAYS),
)
const hasMore = computed(() => allDays.value.length > PREVIEW_DAYS)

const STATUS_TEXT: Record<string, string> = {
  DRAFT: '草稿',
  CONFIRMED: '已确认',
  IN_PROGRESS: '进行中',
  COMPLETED: '已完成',
  CANCELLED: '已取消',
}
const statusText = computed(() => STATUS_TEXT[props.plan.status] ?? props.plan.status)

/** 每日只给前三个标题，细节留给展开后的详情，卡片不是计划书 */
function itemTitles(day: PlanDay): string {
  return (day.items ?? [])
    .map((item) => item.title)
    .filter(Boolean)
    .slice(0, 3)
    .join(' · ')
}

const metaParts = computed(() => {
  const parts: string[] = []
  const basic = props.plan.basic
  if (basic.destination) parts.push(basic.destination)
  if (basic.days) parts.push(`${basic.days} 天`)
  if (basic.travelers) parts.push(`${basic.travelers} 人`)
  if (basic.startDate) parts.push(`${basic.startDate} 出发`)
  return parts
})
</script>

<template>
  <section class="plan-card">
    <header class="head">
      <div class="title-row">
        <span class="title">{{ plan.title }}</span>
        <span class="status" :class="plan.status.toLowerCase()">{{ statusText }}</span>
      </div>
      <div v-if="metaParts.length" class="meta">
        <span v-for="(part, i) in metaParts" :key="part" class="meta-item">
          <span v-if="i > 0" class="sep">·</span>{{ part }}
        </span>
      </div>
    </header>

    <div v-if="plan.unverifiedFieldCount > 0" class="unverified">
      <span class="dot" />
      {{ plan.unverifiedFieldCount }} 个字段待核实：未接入实时天气、票价与路况，请以实际为准
    </div>

    <ol class="days">
      <li v-for="day in visibleDays" :key="day.dayIndex" class="day">
        <div class="day-head">
          <span class="day-index">Day {{ day.dayIndex }}</span>
          <span v-if="day.city" class="day-city">{{ day.city }}</span>
          <span v-if="day.date" class="day-date">{{ day.date }}</span>
        </div>
        <div v-if="day.theme" class="day-theme">{{ day.theme }}</div>
        <div v-if="itemTitles(day)" class="day-items">{{ itemTitles(day) }}</div>
      </li>
    </ol>

    <footer class="foot">
      <button v-if="hasMore" type="button" class="link" @click="expanded = !expanded">
        {{ expanded ? '收起' : `展开全部 ${allDays.length} 天` }}
      </button>
      <span v-else class="filler" />
      <button type="button" class="link detail" @click="openDetail">查看详情 →</button>
    </footer>
  </section>
</template>

<style scoped>
.plan-card {
  margin-top: 12px;
  border: 1px solid var(--at-border);
  border-radius: var(--at-radius);
  background: var(--at-surface);
  overflow: hidden;
  box-shadow: var(--at-shadow);
}

.head {
  padding: 14px 16px 10px;
  border-bottom: 1px solid var(--at-border);
  background: linear-gradient(0deg, #fbfcfd, #ffffff);
}

.title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.title {
  font-size: 15px;
  font-weight: 600;
  color: var(--at-text);
}

.status {
  flex: none;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 11px;
  line-height: 18px;
  background: var(--at-primary-light);
  color: var(--at-primary);
}

.status.completed,
.status.cancelled {
  background: #eef1f5;
  color: var(--at-text-secondary);
}

.meta {
  margin-top: 6px;
  font-size: 12px;
  color: var(--at-text-secondary);
}

.sep {
  margin: 0 6px;
  color: #c8cdd4;
}

.unverified {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 8px 16px;
  font-size: 12px;
  line-height: 1.6;
  color: #a8701a;
  background: #fdf8ec;
  border-bottom: 1px solid #f4e8cc;
}

.dot {
  flex: none;
  width: 6px;
  height: 6px;
  margin-top: 7px;
  border-radius: 50%;
  background: #d9a441;
}

.days {
  margin: 0;
  padding: 4px 0;
  list-style: none;
}

.day {
  padding: 10px 16px;
  border-top: 1px dashed #eef1f5;
}

.day:first-child {
  border-top: none;
}

.day-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.day-index {
  font-size: 12px;
  font-weight: 600;
  color: var(--at-primary);
}

.day-city {
  font-size: 13px;
  color: var(--at-text);
}

.day-date {
  font-size: 11px;
  color: #a8b1bd;
}

.day-theme {
  margin-top: 2px;
  font-size: 12px;
  color: var(--at-text-secondary);
}

.day-items {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.7;
  color: #4a5766;
}

.foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px 10px;
  border-top: 1px solid var(--at-border);
}

.link {
  padding: 0;
  border: none;
  background: none;
  font-size: 12px;
  color: var(--at-primary);
  cursor: pointer;
}

.link:hover {
  text-decoration: underline;
}

.filler {
  flex: 1;
}

/* 右侧入口比左侧的展开按钮更强调：展开是辅助，去详情才是下一步 */
.detail {
  margin-left: auto;
  font-weight: 500;
}
</style>
