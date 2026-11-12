<script setup lang="ts">
import { computed } from 'vue'
import type { FactField } from '@/api/types'

/**
 * 事实字段的展示单元。
 *
 * 为什么单独抽组件：契约规定 L0（未接实时数据）下 `value` 恒为 null、
 * `display` 恒为「待核实」。展示层只能照搬 `display`，不能自己算一个数字出来
 * ——那等于编造。把这个约束收到一个组件里，页面就没机会写错。
 */
const props = defineProps<{
  field?: FactField | null
  /** 字段名前缀，如「门票」「开放时间」 */
  label?: string
}>()

const verified = computed(() => props.field?.verified === true)
const text = computed(() => props.field?.display || '待核实')
const tip = computed(
  () =>
    props.field?.note ||
    (verified.value ? '' : '该数据未接入实时数据源，出发前请自行核实'),
)
</script>

<template>
  <span v-if="field" class="fact" :class="{ unverified: !verified }" :title="tip">
    <span v-if="label" class="fact-label">{{ label }}</span>
    <span class="fact-value">{{ text }}</span>
  </span>
</template>

<style scoped>
.fact {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  line-height: 1.4;
  color: var(--at-text-secondary);
}

.fact-label {
  color: #9aa4b0;
}

.fact-value {
  color: var(--at-text);
}

/*
 * 未核实用琥珀色而不是红色：这是「待确认」不是「出错了」。
 * 不再额外加「待核实」标签——契约里 display 本身就是「待核实」，
 * 再加一个标签会变成「待核实 待核实」。
 */
.fact.unverified .fact-value {
  color: #a9761b;
  padding: 0 4px;
  border-radius: 3px;
  background: #fdf3e0;
}
</style>
