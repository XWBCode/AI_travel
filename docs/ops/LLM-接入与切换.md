# LLM 接入与切换

> 适用：本地开发、验收复验、换供应商。只改 `.env`（已 gitignore），不动代码。
> 最后更新：2026-11-23

## 1. 三种接入方式

| 方式 | `LLM_PROVIDER` | 用途 | 特征 |
|---|---|---|---|
| Mock | `mock` | 打通链路、跑 UI、回归测试 | 响应带 `[MOCK]` 前缀，`real=false`，绝不冒充真实模型 |
| DeepSeek 直连 | `openai_compatible` | 真实模型验收 | 3 天计划约 **14s**，按量付费，余额为 0 时返回 402 |
| 交融网关 | `openai_compatible` | 公司额度、内网模型池 | 3 天计划 **85–100s**（比直连慢一个量级），需调大读超时 |

三者共用同一套 `OpenAICompatibleProvider`，切换只是换 `.env` 里三行。

## 2. 交融网关

```
LLM_BASE_URL=https://c4ai.ccccltd.cn/api/compatible/v1
LLM_MODEL=jiaorong-deepseek-v4-flash
```

**先验 key，再改配置。** 网关的失败形态是 `HTTP 200` + body 里塞错误，很容易被误读成「模型返回空内容」：

```bash
curl -s https://c4ai.ccccltd.cn/api/compatible/v1/models \
  -H "Authorization: Bearer $LLM_API_KEY"
```

- 返回 `{"object":"list","data":[...]}` → key 可用；
- 返回 `{"code":400,"message":"invalid apikey: sk-…"}` → **key 失效**（不是接法问题）。

鉴权只认 `Authorization: Bearer`，换成 `api-key` / `x-api-key` / `?apikey=` 一律 401。

### 2.1 2026-11-23 四把 key 的实测结果

| 来源 | 结果 |
|---|---|
| 交融 10 万 token/min | `invalid apikey` |
| 交融 100 万 token/min | `invalid apikey` |
| 公司 API | `invalid apikey` |
| **公司 API2** | **可用**，返回 42 个模型 |

同一批 key 里三把失效、一把可用，说明网关侧按 key 逐个判权，与我们的调用方式无关。
（失效的 `sk-8TBM…` 在 M2 时期就以「invalid apikey」的形式被记录在
`backend-ai/tests/test_provider_errors.py` 里，当时就已无效。）

### 2.2 模型选择

网关上有 `jiaorong-deepseek-v4-pro` / `-v4-flash` / `jiaorong-ds-v3` / `qwen3.5` / `glm-5` 等 42 个。
计划生成要「长 JSON + 严格按契约填字段」，实测：

| 模型 | 3 天计划耗时 | JSON 模式 | 结果 |
|---|---|---|---|
| `jiaorong-deepseek-v4-pro` | 97s | 支持 | 通过校验 |
| `jiaorong-deepseek-v4-flash` | 85s | 支持 | 通过校验 |

选 **flash**：两者都过校验，耗时差 12s，flash 便宜。

## 3. 超时必须一起调

```
Python 侧：LLM_TIMEOUT_SECONDS=240      # httpx 读超时
Java  侧：AI_READ_TIMEOUT_MS=300000     # 默认 300s，见 AiServiceProperties
```

**Java 的读超时必须大于 Python 的**，否则会出现「AI 已经算完、Java 已经放弃」的假失败：
用户看到「生成失败」，日志里 Python 却是一条成功记录。

历史上 Java 侧硬编码 180s，对 DeepSeek 直连够用，对交融网关不够——
7 天以上的行程会超过 3 分钟。已改为配置项（`app.ai.read-timeout-ms`），dev/prod 都有。

## 4. 切换清单

1. 改 `.env` 三行（`LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY`）；
2. 若换到慢链路，确认 `LLM_TIMEOUT_SECONDS` 与 `AI_READ_TIMEOUT_MS` 都调大；
3. 重启 Python（8000）与 Java（8080）——**两边都要重启**，Java 的读超时是启动时注入的；
4. `curl http://localhost:8000/v1/health` 应显示 `"real": true` 和正确的 `model`；
5. 跑一次 `python backend-java/scripts/verify_real_model.py --gen 1 --adjust 1` 验链路，再跑全量。

## 5. 密钥纪律

- key 只写进 `.env`，不写进报告、不写进日志、不写进测试文件；
- 网关会把请求里的 key 原样回显在错误体里，`mask_secret()` 负责抹掉（有单测钉住）；
- 一旦 key 在对话或文档里以明文出现过，就当它已泄露，去平台轮换。
