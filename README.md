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

## 设计文档（评审中，开工前必读）

`docs/design/` 下是 M1/M2 的专项设计文档包，**状态为草案，评审通过前不作为开发依据**。入口是 [00 · 设计文档总览与待决策清单](./docs/design/00-设计文档总览与待决策清单.md)。

| 文档 | 内容 |
|---|---|
| [01 MVP 范围与阶段重划](./docs/design/01-MVP范围与阶段重划.md) | 收敛范围，把总纲 Phase 0–6 重划为可交付里程碑 |
| [02 需求规格与验收标准](./docs/design/02-需求规格与验收标准.md) | EARS 需求、可量化验收标准、边界与异常 |
| [03 外部数据源选型与降级规范](./docs/design/03-外部数据源选型与降级规范.md) | L0/L1 分层、`FactField` 契约、无数据源时如何不编造 |
| [04 数据模型设计](./docs/design/04-数据模型设计.md) | 补全实体、ER 图、字段说明、版本快照粒度 |
| [05 接口与 AI 输出契约](./docs/design/05-接口与AI输出契约.md) | 计划 JSON Schema、校验降级、SSE 事件、限流成本 |
| [06 工程环境与发布规范](./docs/design/06-工程环境与发布规范.md) | 三环境、迁移、CI、回滚、合规 |

> 当前最重要的约束：**尚未取得大模型 key 与天气/地图数据源**，因此采用 L0/L1 分层——L0 阶段所有实时性字段标记「待核实」，禁止输出具体数值。详见 03。

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
#  - 填 MySQL 专用账号口令（见下一步）
#  - LLM_PROVIDER=openai_compatible（首次可保持 mock）
#  - LLM_API_KEY=你的 key（DeepSeek/通义/智谱等任一 OpenAI 兼容服务）
```

### 2. 初始化数据库

按 06 文档的环境隔离要求，dev 使用**独立库 `ai_travel_dev` + 专用账号**；应用账号只授予本库权限、不具备建库能力，所以库和账号要先建好（口令需与 `.env` 的 `MYSQL_PASSWORD` 一致）：

```bash
mysql -uroot -p <<'SQL'
CREATE DATABASE IF NOT EXISTS ai_travel_dev DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
CREATE USER IF NOT EXISTS 'ai_travel'@'localhost' IDENTIFIED BY '<替换为你的口令>';
GRANT ALL PRIVILEGES ON ai_travel_dev.* TO 'ai_travel'@'localhost';
SQL

mysql -uroot -p < sql/01_schema.sql
```

> 建表也可以完全交给应用：启动 Java 服务时 Flyway 会自动执行 `sql/migration/` 下的迁移脚本（V1 建基础表、V2 建计划表）。上面的手工脚本只是给「想先建好库再启动」的场景用，已执行过也不会冲突（V1 是幂等的）。

> 用 `docker compose up -d` 起 MySQL 时，库、账号与建表脚本都会按 `.env` 自动创建，可跳过本节。

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

### 5. 运行测试

```bash
cd backend-java

# 单元测试（无需数据库，随时可跑）
mvn test

# 含数据库集成测试（AC-07 / AC-09 的落库级证据）
# 需要先建独立测试库，并用环境变量传入口令——口令不进仓库
mysql -uroot -p -e "CREATE DATABASE IF NOT EXISTS ai_travel_test DEFAULT CHARACTER SET utf8mb4;
  GRANT ALL PRIVILEGES ON ai_travel_test.* TO 'ai_travel'@'localhost';"
MYSQL_PASSWORD=你的口令 mvn test
```

测试库固定为 `ai_travel_test`，**测试绝不会连到 `ai_travel_dev`**。
未设置 `MYSQL_PASSWORD` 时，`PlanPersistenceTest` 会自动跳过（不是失败）。

Python 侧：

```bash
cd backend-ai
./.venv/Scripts/python.exe -m pytest tests -q          # 单元 + 接口测试
./../backend-ai/.venv/Scripts/python.exe ../contracts/travel-plan/tools/validate_examples.py   # 契约自检
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

接入时要一起调的三项（只改 provider 不改这几项会失败）：

| 变量 | 建议值 | 为什么 |
|---|---|---|
| `LLM_MAX_TOKENS` | `8192` | 一份 3 天计划的 JSON 约 15k 字符，2048/4096 会被截断（`TRUNCATED`） |
| `LLM_TIMEOUT_SECONDS` | `240` | 真实模型一份 3 天计划实测 33-41s；该值要大于 Java 的 180s 读超时 |
| `LLM_JSON_MODE` | `true` | 网关不支持 `response_format: json_object` 时置 `false` |

> 换模型先用 `backend-ai/scripts/probe_model.py` 验证，不必起三端：
> `python scripts/probe_model.py --base-url ... --api-key ... --model ...`
> 它走线上同一条生成+校验路径，只有"契约校验通过"才算这个模型可用。
> 推理型模型（返回 `reasoning_content`）会拿推理过程消耗 `max_tokens`，容易截断且慢，计划场景不推荐。

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
