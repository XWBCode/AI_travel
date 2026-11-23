<script setup lang="ts">
import { computed } from 'vue'
import type { PlanWeatherVO } from '@/api/types'
import {
  attributionText,
  attributionUrl,
  canFixByUser,
  cityNote,
  reasonText,
  relativeTime,
  staleText,
  toRows,
} from '@/utils/weather'

/**
 * 计划天气条（M3-1）。
 *
 * 位置由 PM 定：详情页顶部、基本信息下方、行程列表上方——天气是「行前判断」信息，
 * 用户进页面第一眼要能判断这几天合适吗，塞进每天行程里就失去了概览价值。
 *
 * 三条展示纪律：
 * 1. **降级不换位置、不抽掉这块**。没有天气时同位置显示一行提示，页面高度不来回跳。
 * 2. **数值只照抄 `display`**。前端不做换算、不补 0、不猜，`verified=false` 一律「暂无数据」。
 * 3. **署名必须显示**（CC BY 4.0）。`stale=true` 时额外标明这是上次的数据。
 */
const props = defineProps<{
  weather: PlanWeatherVO | null
  /** 没有出发日期时给一个补填入口；不传就不显示按钮 */
  canAnchor?: boolean
}>()

const emit = defineEmits<{ (e: 'anchor'): void }>()

const rows = computed(() => toRows(props.weather))
const degraded = computed(() => !props.weather?.available)
const degradeText = computed(() =>
  reasonText(props.weather?.reason, props.weather?.message),
)
const showAnchorButton = computed(() => props.canAnchor && canFixByUser(props.weather?.reason))
const attribution = computed(() => attributionText(props.weather))
/**
 * 归因链接。和风天气的条款要求「和风天气 / QWeather + 超链 https://www.qweather.com」，
 * 只写文字不挂链属于不合规——所以链接不是"锦上添花"，是许可条件的一部分。
 */
const attributionHref = computed(() => attributionUrl(props.weather?.attribution))
const updated = computed(() => relativeTime(props.weather?.fetchedAt))
const stale = computed(() => staleText(props.weather))
const note = computed(() => cityNote(props.weather))
</script>

<template>
  <div class="weather" :class="{ degraded }">
    <div class="weather-head">
      <span class="weather-title">天气</span>
      <span v-if="!degraded" class="weather-scope">按行程日期逐日显示</span>
    </div>

    <!-- 降级：一行提示 + 可能存在的补日期入口。不要在这里渲染任何数字 -->
    <div v-if="degraded" class="weather-degrade">
      <span class="degrade-text">{{ degradeText }}</span>
      <button v-if="showAnchorButton" type="button" class="degrade-action" @click="emit('anchor')">
        补充出发日期
      </button>
    </div>

    <template v-else>
      <ul class="weather-days">
        <li v-for="row in rows" :key="row.dayIndex" class="weather-day">
          <span class="day-no">D{{ row.dayIndex }}</span>
          <span class="day-date">{{ row.dayLabel }}</span>
          <span class="day-city">{{ row.city }}</span>
          <span class="day-weather">{{ row.weather }}</span>
          <span class="day-temp">{{ row.temperature }}</span>
          <span class="day-rain">{{ row.precipitation }}</span>
          <span class="day-wind">{{ row.wind }}</span>
        </li>
      </ul>
      <div class="weather-foot">
        <span class="weather-source">
          <a
            v-if="attributionHref"
            :href="attributionHref"
            target="_blank"
            rel="noopener noreferrer"
            class="source-link"
          >{{ attribution }}</a>
          <template v-else>{{ attribution }}</template>
          <template v-if="updated"> · 更新于{{ updated }}</template>
        </span>
        <span v-if="stale" class="weather-stale">{{ stale }}</span>
        <span v-if="note" class="weather-note">{{ note }}</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
/*
 * 整块用浅底卡片而不是表格线：它是"附加信息"，视觉权重不能压过行程本身。
 * 降级态只换文字颜色，容器尺寸与位置保持一致。
 */
.weather {
  margin: 14px 0 4px;
  padding: 12px 14px;
  border: 1px solid var(--at-border);
  border-radius: var(--at-radius);
  background: var(--at-surface);
}

.weather-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 8px;
}

.weather-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--at-text);
}

.weather-scope {
  font-size: 12px;
  color: var(--at-text-secondary);
}

.weather-days {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.weather-day {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: var(--at-text);
  flex-wrap: wrap;
}

.day-no {
  min-width: 26px;
  font-size: 12px;
  color: var(--at-text-secondary);
}

.day-date {
  min-width: 48px;
  font-variant-numeric: tabular-nums;
  color: var(--at-text-secondary);
}

.day-city {
  min-width: 56px;
}

.day-weather {
  min-width: 64px;
}

.day-temp {
  min-width: 72px;
  font-variant-numeric: tabular-nums;
}

.day-rain,
.day-wind {
  min-width: 84px;
  font-size: 12px;
  color: var(--at-text-secondary);
}

.weather-foot {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 8px;
  font-size: 12px;
  color: var(--at-text-secondary);
}

.weather-stale {
  color: #a9761b;
}

/* 归因链接不能做成"正文里的蓝字"：官方要求独立、可见、不与正文混淆 */
.source-link {
  color: var(--at-text-secondary);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.source-link:hover {
  color: var(--at-primary);
}

.weather-note {
  color: var(--at-text-secondary);
}

/* 降级态：不隐藏、不塌缩，只把语气降下来 */
.weather-degrade {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: var(--at-text-secondary);
}

.degrade-action {
  padding: 0;
  border: none;
  background: none;
  color: var(--at-primary);
  font-size: 13px;
  cursor: pointer;
}

.degrade-action:hover {
  text-decoration: underline;
}
</style>
