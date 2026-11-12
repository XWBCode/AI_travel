<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { plansApi } from '@/api'
import type { ApiError } from '@/api/http'
import type { TravelPlan } from '@/api/types'
import {
  MAX_DAYS,
  MAX_TRAVELERS,
  type BasicDraft,
  buildPatch,
  canEditDestination,
  hasChanges,
  planToDraft,
  validateDraft,
  withDays,
  withRange,
} from '@/utils/planEdit'

/**
 * 编辑计划的基础信息。
 *
 * 只开放「能被用户确认的事实」：标题、出发地、目的地、人数、天数、日期。
 * 行程条目（几点去哪、门票多少）不在这里改——那属于 M2 的行程调整，
 * 而且 L0 阶段的门票/时长本来就没有可靠数据，让人手填只会制造假信息。
 */
const props = defineProps<{
  modelValue: boolean
  plan: TravelPlan | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  saved: [plan: TravelPlan]
  refresh: []
}>()

const draft = ref<BasicDraft>(emptyDraft())
const original = ref<BasicDraft>(emptyDraft())
const saving = ref(false)

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const destinationLocked = computed(() => (props.plan ? !canEditDestination(props.plan) : false))

const dateRange = computed<[string, string] | null>(() =>
  draft.value.startDate && draft.value.endDate
    ? [draft.value.startDate, draft.value.endDate]
    : null,
)

const dirty = computed(() => hasChanges(original.value, draft.value))

/** 天数改了，结束日期跟着动；这里必须走 withDays，不能让两个输入框各改各的 */
function onDaysChange(value: number | undefined): void {
  draft.value = withDays(draft.value, value ?? null)
}

function onRangeChange(value: [string, string] | null): void {
  draft.value = withRange(draft.value, value?.[0] ?? null, value?.[1] ?? null)
}

function reset(): void {
  if (!props.plan) return
  original.value = planToDraft(props.plan)
  draft.value = { ...original.value }
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) reset()
  },
)

async function save(): Promise<void> {
  if (!props.plan || saving.value) return
  const invalid = validateDraft(draft.value)
  if (invalid) {
    ElMessage.warning(invalid)
    return
  }
  const patch = buildPatch(original.value, draft.value)
  if (Object.keys(patch).length === 0) {
    visible.value = false
    return
  }

  saving.value = true
  try {
    const updated = await plansApi.update(props.plan.planId, {
      ...patch,
      // 乐观锁：带上打开表单时看到的版本，别人改过就会被后端挡住
      baseVersion: props.plan.version,
    })
    ElMessage.success('已保存')
    emit('saved', updated)
    visible.value = false
  } catch (error) {
    if ((error as ApiError).code === 30104) {
      // 冲突时不能静默丢弃：告诉用户发生了什么，并载入最新内容让他重新判断
      ElMessage.warning('这个行程刚被别处修改过，已为你载入最新内容，请确认后重新编辑')
      emit('refresh')
      visible.value = false
    }
    // 其余错误（校验失败等）已由请求拦截器提示
  } finally {
    saving.value = false
  }
}

function emptyDraft(): BasicDraft {
  return {
    title: '',
    originCity: '',
    destination: '',
    days: null,
    travelers: null,
    startDate: null,
    endDate: null,
  }
}
</script>

<template>
  <el-dialog v-model="visible" title="编辑行程信息" width="520px" :close-on-click-modal="false">
    <el-form label-width="76px" label-position="left">
      <el-form-item label="标题">
        <el-input v-model="draft.title" maxlength="100" show-word-limit placeholder="例如：成都四日美食之旅" />
      </el-form-item>

      <el-form-item label="出发地">
        <el-input v-model="draft.originCity" maxlength="64" placeholder="不填则显示为待补充" />
      </el-form-item>

      <el-form-item label="目的地">
        <el-input
          v-model="draft.destination"
          maxlength="64"
          :disabled="destinationLocked"
          placeholder="不填则显示为待补充"
        />
        <div v-if="destinationLocked" class="hint">
          这个行程包含多个目的地，改主目的地请回到对话里说明
        </div>
      </el-form-item>

      <el-form-item label="出行人数">
        <el-input-number v-model="draft.travelers" :min="1" :max="MAX_TRAVELERS" controls-position="right" />
      </el-form-item>

      <el-form-item label="天数">
        <el-input-number
          :model-value="draft.days ?? undefined"
          :min="1"
          :max="MAX_DAYS"
          controls-position="right"
          @change="onDaysChange"
        />
        <span class="suffix">天</span>
      </el-form-item>

      <el-form-item label="日期">
        <el-date-picker
          :model-value="dateRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="出发日期"
          end-placeholder="返程日期"
          unlink-panels
          @change="onRangeChange"
        />
      </el-form-item>
    </el-form>

    <p class="tip">
      天数与日期会自动对齐：改天数时结束日期跟着变，选完日期区间时天数跟着变。
      行程里的景点、门票要改，请在对话里说明，那里能一次改完。
    </p>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" :disabled="!dirty" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.hint {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--at-text-secondary);
}

.suffix {
  margin-left: 8px;
  font-size: 12px;
  color: var(--at-text-secondary);
}

.tip {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: #a8b1bd;
}
</style>
