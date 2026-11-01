# AI 旅行助手（Phase 1 骨架）

> 对应《AI 旅行助手 - 项目总纲与研发执行规范》V1.0 的 Phase 0 + Phase 1 交付。
> 当前完成：项目骨架搭建 + 登录注册 + AI 多轮对话（SSE 流式）+ 会话管理 + 消息持久化。

## 目录结构

```
ai-travel/
├── backend-java/          # Spring Boot 3 业务服务（核心业务 + 权限 + 落库）
│   └── src/main/java/com/aitravel/
│       ├── common/        # 统一响应/异常/JWT/拦截器/MyBatis 基类
│       └── modules/
│           ├── auth/      # 注册、登录、JWT 签发
│           ├── user/      # 用户实体（Phase 1 仅账号基础）
│           └── chat/      # 会话、消息、SSE 转发
├── backend-ai/            # FastAPI AI 服务（只做 AI，不碰业务库）
│   └── app/
│       ├── llm/           # Provider 抽象 + Prompt 管理
│       └── routers/       # /v1/chat/stream (SSE)、/v1/health
├── frontend/              # Vue3 + TS + Pinia + Element Plus
│   └── src/
│       ├── api/           # axios 封装 + SSE 解析
│       ├── views/         # 登录页 / AI 助手 / 占位页
│       └── store/         # 用户状态
├── sql/01_schema.sql      # MySQL 建表脚本
├── docker-compose.yml     # 可选：一键起 MySQL + Redis
├── start-all.bat          # Windows 一键启动
└── .env.example           # 环境变量模板
```

## 环境要求

| 组件 | 版本 |
|---|---|
| JDK | 17+ |
| Maven | 3.8+ |
| Python | 3.10+ |
| Node.js | 18+（推荐 pnpm） |
| MySQL | 8.0 |
| Redis | 6+（Phase 1 骨架暂未强依赖，Phase 2 起使用） |

## 快速启动

### 1. 准备环境变量

```bash
cp .env.example .env
# 编辑 .env：
#  - 填 MySQL 密码
#  - LLM_PROVIDER=openai_compatible（首次可保持 mock）
#  - LLM_API_KEY=你的 key（DeepSeek/通义/智谱等任一 OpenAI 兼容服务）
```

### 2. 初始化数据库

```bash
mysql -uroot -p < sql/01_schema.sql
```

### 3. 启动三个服务

**方式 A：Windows 一键启动** —— 双击 `start-all.bat`。

**方式 B：手动（推荐首次使用，方便看日志）**

```bash
# 终端 1：Python AI 服务
cd backend-ai
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 终端 2：Java 服务
cd backend-java
mvn spring-boot:run

# 终端 3：前端
cd frontend
pnpm install
pnpm dev
```

打开 http://localhost:5173 → 注册账号 → 进入「AI 助手」开始对话。

### 4.（可选）用 Docker 起 MySQL/Redis

```bash
docker compose up -d
# 建表脚本会自动执行
```

## LLM 配置说明

`backend-ai` 支持两种模式，通过 `.env` 的 `LLM_PROVIDER` 切换：

| 模式 | 说明 |
|---|---|
| `mock` | 本地假模型，响应带 `[MOCK]` 标记，用于验证链路（总纲 9.3：Mock 必须显式标记） |
| `openai_compatible` | 任意 OpenAI 兼容服务：DeepSeek / 通义(兼容模式) / 智谱 / Kimi / SiliconFlow / OpenAI |

示例（DeepSeek）：

```
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-xxxx
```

## SSE 事件协议（前后端契约）

`POST /api/chat/stream` 返回 `text/event-stream`：

| 事件 | data | 说明 |
|---|---|---|
| `meta` | `{"sessionId":1,"userMessageId":2}` | 流开始，前端据此绑定会话 |
| `delta` | `{"content":"增量文本"}` | 增量内容 |
| `done` | `{"messageId":3,"model":"...","latencyMs":123}` | 完成，消息已落库 |
| `error` | `{"code":50201,"message":"..."}` | 失败（已产出内容也会落库，标记 FAILED） |

## 数据流

```
Vue3 ──SSE──> Spring Boot ──HTTP/SSE──> FastAPI ──> LLM
  │                │
  └── 用户/会话/消息落库（MySQL）     上下文由 Java 组装后传入
```

关键原则（与总纲一致）：
- MySQL 是唯一事实来源，会话/消息全部落库，刷新后可恢复。
- Python 不连业务库，不持有用户权限数据。
- AI 输出中断/失败时，已生成内容保留并明确标记，不静默丢弃。
- Mock 数据显式标注 `[MOCK]`，不冒充真实能力。

## Phase 1 验收对照（总纲）

- [x] 用户可以注册、登录（JWT，BCrypt 密码哈希）
- [x] 创建多个独立 AI 会话，支持重命名/删除
- [x] SSE 流式对话，支持停止生成（中断连接）
- [x] 刷新页面后恢复历史会话与消息
- [ ] 会话标题自动生成（当前取首句前 10 字，Phase 2 接入 AI 生成）
- [ ] Redis 活跃上下文缓存（Phase 2 接入）

## 已知边界（下一阶段处理）

1. 「我的旅行 / 社区 / 设置」为占位页，对应 Phase 2-4。
2. 尚无 Redis 依赖，`application.yml` 中连接失败不阻塞启动（Phase 2 起启用）。
3. 前端 Element Plus 为全量引入，构建体积偏大，待按需优化。
4. `start-all.bat` 未做端口占用检测，启动前请确保 8000/8080/5173 空闲。
