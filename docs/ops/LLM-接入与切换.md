# LLM 接入与切换

> 适用：本地开发、验收复验、换供应商。只改 `.env`（已 gitignore），不动代码。
> 最后更新：2026-11-23

## 1. 三种接入方式

| 方式 | `LLM_PROVIDER` | 用途 | 特征 |
|---|---|---|---|
| Mock | `mock` | 打通链路、跑 UI、回归测试 | 响应带 `[MOCK]` 前缀，`real=false`，绝不冒充真实模型 |
| DeepSeek 直连 | `openai_compatible` | 真实模型验收 | 3 天计划约 **14s**，按量付费，余额为 0 时返回 402 |
| 交融网关 | `openai_compatible` | 公司额度、内网模型池 | 3 天计划 **60–130s**、7 天 **160–210s**（比直连慢一个量级），必须调大读超时 |

三者共用同一套 `OpenAICompatibleProvider`，切换只是换 `.env` 里三行。

## 2. 交融网关

```
LLM_BASE_URL=https://c4ai.ccccltd.cn/api/compatible/v1
LLM_MODEL=jiaorong-deepseek-v4-pro
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

网关上有 `jiaorong-deepseek-v4-pro` / `-v4.1-flash` / `jiaorong-qwen3.5-397b-a17b` / `jiaorong-glm-5.2` /
`jiaorong-kimi-k2.6` 等 42 个。计划生成要「长 JSON + 严格按契约填字段」，五个旗舰档实测（同一批 3 条输入）：

| 模型 | 合规 | P50 | 最长 | 平均项数 | 备注 |
|---|---|---|---|---|---|
| **`jiaorong-deepseek-v4-pro`** | **3/3** | 90s | 166s | 12.3 | **当前上线**，内容最全 |
| `jiaorong-qwen3.5-397b-a17b` | 2/3 | 164s | 207s | 11.0 | 最慢；travelers 自相矛盾 |
| `jiaorong-glm-5.2` | 2/3 | 60s | 62s | 9.3 | `endTime` 输出整数 16 |
| `jiaorong-kimi-k2.6` | 3/3 | **33s** | 47s | 9.0 | 最快、内容最具体；真实链路 13/20 |
| `jiaorong-deepseek-v4-flash` | 3/3 | 111s | 136s | 9.0 | 真实链路 20/20，内容最模板 |

**为什么选 pro（2026-11-23 用户决定「不用管价格，用最好的」）**：
合规最稳（基准 3/3，且把 kimi 挂掉的 7 条边界样本全部救回，累计 12/12），
7 天行程能排到 20 项并覆盖 4 城。代价是慢（P50 90s、最长 171s），
所以 §3 的三个超时必须留足（Java 300s / Python 240s）。

**kimi-k2.6 是备选**：快 3 倍且愿意写具体景点（金沙遗址、宽窄巷子），
但它对 `missingFields` 双向规则不稳（20 条里挂 7 条，全是「没说人数却填了 travelers=1」）。
已加固 `prompts.py` 规则 4.1；等接入景点主数据后可以重新评估它。

**一条经验**：比「哪个模型分高」更重要的是**口径对齐**。评测脚本必须复刻 `generator.py` 的
S-5 重算（`unverifiedFieldCount` 服务端覆盖），否则所有模型都会被报「声明 0，实际 48」，
结论完全反过来。文档 §8.1 记了这次踩坑。

## 3. 超时必须一起调

Java 侧有**三个**独立超时，缺一个就会出现「AI 算完了、Java 已放弃」的假失败：

| 配置项 | 默认 | 管哪条路 |
|---|---|---|
| `AI_READ_TIMEOUT_MS` | 300s | `/chat/stream` 普通对话（SSE） |
| `AI_PLAN_TIMEOUT_MS` | 300s | `/v1/plan/generate` 计划生成（整包，慢） |
| `AI_ADJUST_TIMEOUT_MS` | 180s | `/v1/plan/adjust` 调整建议 |

Python 侧 `LLM_TIMEOUT_SECONDS=240` 要**小于**对应 Java 超时，让 Python 先报「模型超时」这个更准的原因。

**踩过的坑**：`generatePlan` 原来硬编码 180s，而交融网关上 7 天行程实测 130–160s、长尾超过 180s，
于是被记成 `AI 服务连接失败：request timed out`——人工排查会以为是服务宕机，
实际上是等得不够。真实复验 20 次里恰好命中 1 次（耗时精确 180055ms，这就是证据）。
三个超时现在都可配，dev/prod 的 yml 都有默认值。

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
