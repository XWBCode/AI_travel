<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { plansApi } from '@/api'
import type { TravelPlan } from '@/api/types'
import { clientDate, formatDateLabel, startDialogHint } from '@/utils/today'

/**
 * 「开始行程」确认框。
 *
 * 为什么要多这一步：开始旅行是唯一能把「第几天」钉到真实日期的动作。
 * 计划可能只有天数没有出发日（用户说「东京玩五天」就能生成），
 * 直接开始就得替用户猜一个日期——猜错的表现是他按第 3 天的安排跑到第 1 天的景点。
 * 所以把日期摆在明面上：默认取计划原值或本机今天，允许改，选完才开始。
 */
const props = defineProps<{ modelValue: boolean; plan: TravelPlan | null }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; started: [] }>()

const date = ref('')
const submitting = ref(false)

watch(
  () => props.modelValue,
  (open) => {
    if (open) date.value = props.plan?.basic?.startDate || clientDate()
  },
)

const hint = computed(() => startDialogHint(props.plan?.basic?.startDate))
const preview = computed(() => formatDateLabel(date.value))

function close(): void {
  emit('update:modelValue', false)
}

async function submit(): Promise<void> {
  if (!props.plan || !date.value || submitting.value) return
  submitting.value = true
  try {
    await plansApi.start(props.plan.planId, date.value)
    ElMessage.success('已开始行程，今天就是第 1 天')
    emit('started')
    close()
  } catch {
    // 状态机的拒绝（例如已被别的标签页开始）由拦截器提示，这里不叠第二个框
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="开始行程"
    width="420px"
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="body">
      <p class="hint">{{ hint }}</p>
      <el-date-picker
        v-model="date"
        type="date"
        value-format="YYYY-MM-DD"
        format="YYYY 年 M 月 D 日"
        placeholder="选择出发日期"
        style="width: 100%"
      />
      <p v-if="preview" class="preview">第 1 天：{{ preview }}</p>
      <p class="note">开始后行程变为「进行中」，每天按日期自动对应，行程项可以逐项打勾。</p>
    </div>
    <template #footer>
      <el-button size="small" @click="close">再想想</el-button>
      <el-button type="primary" size="small" :loading="submitting" @click="submit">开始行程</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.hint {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--at-text);
}

.preview {
  margin: 0;
  font-size: 12px;
  color: var(--at-primary);
}

.note {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--at-text-secondary);
}
</style>
