<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/store/user'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const mode = ref<'login' | 'register'>('login')
const loading = ref(false)
const form = ref({ username: '', password: '', nickname: '' })

onMounted(async () => {
  // 已有有效 token 直接进主界面
  if (await userStore.restore()) {
    router.replace('/chat')
  }
})

async function submit(): Promise<void> {
  if (!form.value.username || !form.value.password) {
    ElMessage.warning('请填写用户名和密码')
    return
  }
  loading.value = true
  try {
    if (mode.value === 'register') {
      await userStore.register(form.value.username, form.value.password, form.value.nickname)
      ElMessage.success('注册成功，正在登录')
    }
    await userStore.login(form.value.username, form.value.password)
    const redirect = (route.query.redirect as string) || '/chat'
    router.replace(redirect)
  } catch {
    // 错误提示已由 axios 拦截器统一处理
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="brand">
        <div class="logo">✈</div>
        <h1>AI 旅行助手</h1>
        <p>从提问到出发，把旅行规划交给懂你的助手</p>
      </div>

      <el-form :model="form" @submit.prevent="submit">
        <el-form-item>
          <el-input v-model="form.username" placeholder="用户名" size="large" autocomplete="username" />
        </el-form-item>
        <el-form-item v-if="mode === 'register'">
          <el-input v-model="form.nickname" placeholder="昵称（可选）" size="large" />
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="form.password"
            type="password"
            placeholder="密码（至少 8 位）"
            size="large"
            show-password
            autocomplete="current-password"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-button type="primary" size="large" class="submit" :loading="loading" @click="submit">
          {{ mode === 'login' ? '登录' : '注册并登录' }}
        </el-button>
      </el-form>

      <div class="switch">
        <span v-if="mode === 'login'">还没有账号？<a @click="mode = 'register'">注册</a></span>
        <span v-else>已有账号？<a @click="mode = 'login'">去登录</a></span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  background: radial-gradient(circle at 20% 20%, #e6f2ef 0%, #f5f7fa 45%, #eef3f9 100%);
}

.login-card {
  width: 380px;
  padding: 40px 36px 28px;
  background: var(--at-surface);
  border-radius: 18px;
  box-shadow: 0 12px 40px rgba(31, 45, 61, 0.08);
}

.brand {
  text-align: center;
  margin-bottom: 28px;
}

.logo {
  font-size: 30px;
  width: 54px;
  height: 54px;
  line-height: 54px;
  margin: 0 auto 12px;
  border-radius: 16px;
  background: var(--at-primary-light);
}

.brand h1 {
  margin: 0 0 6px;
  font-size: 20px;
}

.brand p {
  margin: 0;
  font-size: 12px;
  color: var(--at-text-secondary);
}

.submit {
  width: 100%;
  background: var(--at-primary);
  border-color: var(--at-primary);
}

.switch {
  margin-top: 18px;
  text-align: center;
  font-size: 13px;
  color: var(--at-text-secondary);
}

.switch a {
  color: var(--at-primary);
  cursor: pointer;
}
</style>
