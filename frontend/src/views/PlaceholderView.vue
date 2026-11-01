<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const meta: Record<string, { title: string; phase: string; desc: string }> = {
  plans: {
    title: '我的旅行',
    phase: 'Phase 2 / Phase 3',
    desc: '结构化旅行计划列表、计划详情、开始旅行与行程进度跟踪。当前版本仅完成 AI 对话闭环。',
  },
  community: {
    title: '旅行社区',
    phase: 'Phase 4',
    desc: '文章发布、Markdown 编辑、点赞收藏评论、用户主页与 AI 文章检索。',
  },
  settings: {
    title: '个人设置',
    phase: 'Phase 1（待补）',
    desc: '头像、昵称、简介修改，以及账号安全设置。',
  },
}

const info = computed(() => meta[String(route.params.module)] || {
  title: '功能建设中',
  phase: '-',
  desc: '该模块尚未进入开发排期。',
})
</script>

<template>
  <div class="placeholder">
    <div class="card">
      <div class="badge">待开发</div>
      <h2>{{ info.title }}</h2>
      <p>{{ info.desc }}</p>
      <div class="phase">计划阶段：{{ info.phase }}</div>
    </div>
  </div>
</template>

<style scoped>
.placeholder {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.card {
  max-width: 460px;
  padding: 36px 32px;
  background: var(--at-surface);
  border: 1px solid var(--at-border);
  border-radius: var(--at-radius);
  text-align: center;
}

.badge {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 20px;
  background: #f2f4f7;
  color: var(--at-text-secondary);
  font-size: 11px;
  margin-bottom: 14px;
}

h2 {
  margin: 0 0 10px;
  font-size: 18px;
}

p {
  margin: 0 0 16px;
  font-size: 13px;
  color: var(--at-text-secondary);
  line-height: 1.7;
}

.phase {
  font-size: 12px;
  color: #a8b1bd;
}
</style>
