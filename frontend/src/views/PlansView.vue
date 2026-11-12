<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { plansApi } from '@/api'
import { PLAN_STATUS_TEXT, type PlanSummaryVO } from '@/api/types'

/**
 * 我的旅行列表。
 *
 * 卡片只给「够不够判断下一步」的信息：标题、状态、目的地与天数、待核实数量。
 * 真正的行程放在详情页——列表页把人留在原地比什么都看不到更糟。
 */
const router = useRouter()

const plans = ref<PlanSummaryVO[]>([])
const total = ref(0)
const page = ref(1)
const size = 12
const status = ref('')
const loading = ref(false)

const FILTERS = [
  { label: '全部', value: '' },
  { label: '草稿', value: 'DRAFT' },
  { label: '已确认', value: 'CONFIRMED' },
  { label: '进行中', value: 'IN_PROGRESS' },
  { label: '已完成', value: 'COMPLETED' },
  { label: '已取消', value: 'CANCELLED' },
]

const isEmpty = computed(() => !loading.value && plans.value.length === 0)

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await plansApi.list({ page: page.value, size, status: status.value })
    plans.value = data.records
    total.value = data.total
  } catch {
    // http 拦截器已弹出具体原因，这里只需要保证页面不卡在加载态
    plans.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function changeFilter(value: string): void {
  status.value = value
  page.value = 1
  void load()
}

function changePage(value: number): void {
  page.value = value
  void load()
}

function open(plan: PlanSummaryVO): void {
  // id 是字符串，直接拼；转 Number 会丢精度
  router.push(`/plans/${plan.id}`)
}

async function confirmPlan(plan: PlanSummaryVO): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认后行程会从「草稿」变为「已确认」，并保存一个版本快照。确认「${plan.title}」吗？`,
      '确认行程',
      { confirmButtonText: '确认', cancelButtonText: '再想想' },
    )
  } catch {
    return
  }
  try {
    await plansApi.confirm(plan.id)
    ElMessage.success('已确认，出发前记得核实待定项')
    void load()
  } catch {
    // 状态机拒绝的原因由拦截器提示（例如已被其他操作改过状态）
  }
}

/** 后端给的是 ISO 本地时间，列表里只需要「日期 + 时分」 */
function formatTime(value?: string): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 列表里的次级信息，缺项就不显示，避免出现「· 人」这种空值 */
function metaOf(plan: PlanSummaryVO): string[] {
  const parts: string[] = []
  if (plan.destination) parts.push(plan.destination)
  if (plan.days) parts.push(`${plan.days} 天`)
  if (plan.travelers) parts.push(`${plan.travelers} 人`)
  if (plan.startDate) parts.push(`${plan.startDate} 出发`)
  return parts
}

onMounted(load)
</script>

<template>
  <div class="plans-page">
    <header class="page-head">
      <div>
        <h1>我的旅行</h1>
        <p class="sub">这里保存了所有生成过的行程。草稿确认后才会进入「已确认」，出发前请核实待定项。</p>
      </div>
      <el-button type="primary" @click="router.push('/chat')">去聊一个行程</el-button>
    </header>

    <div class="filters">
      <button
        v-for="f in FILTERS"
        :key="f.value"
        type="button"
        class="filter"
        :class="{ active: status === f.value }"
        @click="changeFilter(f.value)"
      >
        {{ f.label }}
      </button>
      <span class="total">共 {{ total }} 个行程</span>
    </div>

    <div v-loading="loading" class="list-wrap">
      <el-empty v-if="isEmpty" description="还没有行程">
        <p class="empty-tip">在 AI 助手里说一句「帮我规划成都三天」，就会出现在这里。</p>
        <el-button type="primary" @click="router.push('/chat')">去 AI 助手</el-button>
      </el-empty>

      <div v-else class="grid">
        <article
          v-for="plan in plans"
          :key="plan.id"
          class="card"
          :class="plan.status.toLowerCase()"
          @click="open(plan)"
        >
          <div class="card-head">
            <h3>{{ plan.title }}</h3>
            <span class="status" :class="plan.status.toLowerCase()">
              {{ PLAN_STATUS_TEXT[plan.status] ?? plan.status }}
            </span>
          </div>

          <div class="meta">
            <span v-for="(part, i) in metaOf(plan)" :key="part">
              <span v-if="i > 0" class="sep">·</span>{{ part }}
            </span>
          </div>

          <div v-if="plan.unverifiedFieldCount > 0" class="unverified">
            {{ plan.unverifiedFieldCount }} 个字段待核实
          </div>
          <div v-else class="verified">全部字段已核实</div>

          <footer class="card-foot">
            <span class="time">{{ formatTime(plan.updateTime) }}</span>
            <div class="ops" @click.stop>
              <el-button link type="primary" size="small" @click="open(plan)">查看</el-button>
              <el-button
                v-if="plan.status === 'DRAFT'"
                link
                type="primary"
                size="small"
                @click="confirmPlan(plan)"
              >
                确认
              </el-button>
            </div>
          </footer>
        </article>
      </div>
    </div>

    <div v-if="total > size" class="pager">
      <el-pagination
        layout="prev, pager, next"
        :current-page="page"
        :page-size="size"
        :total="Number(total)"
        @current-change="changePage"
      />
    </div>
  </div>
</template>

<style scoped>
.plans-page {
  height: 100%;
  overflow-y: auto;
  padding: 26px 32px 40px;
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  max-width: 1080px;
  margin: 0 auto 18px;
}

.page-head h1 {
  margin: 0 0 6px;
  font-size: 20px;
  font-weight: 600;
  color: var(--at-text);
}

.sub {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--at-text-secondary);
}

.filters {
  display: flex;
  align-items: center;
  gap: 6px;
  max-width: 1080px;
  margin: 0 auto 14px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--at-border);
}

.filter {
  padding: 5px 12px;
  border: 1px solid transparent;
  border-radius: 14px;
  background: transparent;
  font-size: 13px;
  color: var(--at-text-secondary);
  cursor: pointer;
}

.filter:hover {
  color: var(--at-primary);
}

.filter.active {
  background: var(--at-primary-light);
  border-color: #cfe4de;
  color: var(--at-primary);
}

.total {
  margin-left: auto;
  font-size: 12px;
  color: #a8b1bd;
}

.list-wrap {
  max-width: 1080px;
  margin: 0 auto;
  min-height: 220px;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
}

.card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px 18px 12px;
  border: 1px solid var(--at-border);
  border-radius: var(--at-radius);
  background: var(--at-surface);
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s;
}

.card:hover {
  border-color: #cfe4de;
  box-shadow: var(--at-shadow);
  transform: translateY(-1px);
}

/* 已取消的行程整体降权，避免和进行中的混在一起看 */
.card.cancelled {
  opacity: 0.65;
}

.card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.card-head h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  line-height: 1.4;
  color: var(--at-text);
}

.status {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  line-height: 1.6;
  background: #f2f4f7;
  color: var(--at-text-secondary);
}

.status.confirmed,
.status.in_progress {
  background: var(--at-primary-light);
  color: var(--at-primary);
}

.status.completed {
  background: #eef6ee;
  color: #3f7a48;
}

.meta {
  font-size: 12px;
  color: var(--at-text-secondary);
}

.sep {
  margin: 0 5px;
  color: #c6ccd4;
}

.unverified {
  align-self: flex-start;
  padding: 3px 8px;
  border-radius: 6px;
  background: #fdf3e0;
  border: 1px solid #f3ddb4;
  font-size: 11px;
  color: #a9761b;
}

.verified {
  font-size: 11px;
  color: #3f7a48;
}

.card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 4px;
  padding-top: 10px;
  border-top: 1px dashed var(--at-border);
}

.time {
  font-size: 11px;
  color: #a8b1bd;
}

.ops {
  display: flex;
  align-items: center;
  gap: 2px;
}

.empty-tip {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--at-text-secondary);
}

.pager {
  display: flex;
  justify-content: center;
  max-width: 1080px;
  margin: 20px auto 0;
}
</style>
