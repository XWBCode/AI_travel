# CIRCUIT_OPEN 跨层验证报告（Python → Java → Vue）

> 任务来源：PM 2026-11-24 决定「独立 reason：批准实施」。
> 范围限制（PM 原话）：「本轮不改 Provider、不改熔断阈值、不改缓存策略、不增加管理接口。」
> 验证日期：2026-11-24　执行环境：本机 dev（MySQL + 真实 qweather 进程 + 故障替身实例）

---

## 1. 要解决的问题

A 项（连续故障 10 分钟）验证暴露出来的一件事：**熔断期间与上游真失败在外部完全同形**。

| | 真失败 | 熔断拦截 |
|---|---|---|
| HTTP | 200 | 200 |
| `available` | false | false |
| `reason`（改前） | `UPSTREAM_ERROR` | `UPSTREAM_ERROR` |
| 耗时 | 毫秒级 | 毫秒级 |
| 用户看到 | 「天气服务暂时不可用」 | 「天气服务暂时不可用」 |

两者对**用户**是一样的，但成因完全不同；对**运维**更糟：看到"上游错误"会去查一个
本轮压根没被碰过的接口，而真正该看的是"我们已经停了多久、什么时候试探"。

所以拆成两个 reason，判据只有一个、且可机检：**本次请求有没有出网。**

---

## 2. 改动清单（逐层）

### 2.1 Python（源头）

| 文件 | 改动 |
|---|---|
| `app/tools/weather_reasons.py` | 新增 `R_CIRCUIT_OPEN = "CIRCUIT_OPEN"`；文案「天气服务连续异常，已暂停刷新，稍后会自动恢复」；模块文档新增一节说明为什么两者必须分开、为什么不能用耗时判断 |
| `app/tools/weather.py` | `breaker.allow()` 为 false 的分支由 `UPSTREAM_ERROR` 改为 `CIRCUIT_OPEN`；日志改为「本次未访问上游」 |
| `tests/test_weather_tool.py` | 新增 `test_blocked_by_breaker_is_circuit_open_not_upstream_error`（走 open-meteo，证分支与 provider 无关） |
| `tests/test_weather_qweather.py` | 熔断用例改为断言 `CIRCUIT_OPEN`；新增 `test_failed_request_and_blocked_request_have_different_reasons`（同一个熔断器里"出网失败"与"没出网"给两个 reason + 两句不同文案） |

`R_UPSTREAM_ERROR` 的注释也改了：原来写「上游返回错误 / 结构异常 / **熔断打开**」，
现在去掉"熔断打开"——注释留着旧职责，下一个人就会照着旧注释把熔断又折回去。

### 2.2 Java（透传，不翻译）

| 文件 | 改动 |
|---|---|
| `PlanWeatherService.reasons()` | 白名单补 `CIRCUIT_OPEN`（原来只有 7 个） |
| `PlanWeatherService.reasons()` 注释 | 说明两者不可合并、判据是"有没有出网" |
| `PlanWeatherVO` 类注释 | reason 枚举清单同步补 `CIRCUIT_OPEN` |
| `PlanWeatherServiceTest` | 新增 `weather_circuitOpenIsNotSwallowedIntoUpstreamError`（显式断言 `!= UPSTREAM_ERROR`）、`reasons_containCircuitOpen` |

**Java 侧刻意不加翻译表。** 已有用例 `weather_reasonPassesThrough` 的断言就是
「Python 给的降级原因原样透传，不在 Java 重新翻译」——文案只有一个来源。
如果 Java 也翻译一份，就会出现"两端文案不一致但都觉得自己对"的经典问题。

### 2.3 Vue（文案）

| 文件 | 改动 |
|---|---|
| `src/api/types.ts` | `WeatherReason` 联合类型补 `'CIRCUIT_OPEN'` |
| `src/utils/weather.ts` | `WEATHER_REASON_TEXT` 补 `CIRCUIT_OPEN`，与 Python 文案逐字一致 |
| `scripts/weather.test.mjs` | 新增 4 项：有独立文案 / 与 `UPSTREAM_ERROR` 不同 / 说明会自己恢复 / `canFixByUser` 为 false（"只能等"，不该给操作入口） |

`canFixByUser('CIRCUIT_OPEN') === false` 是刻意的：熔断不是用户能自己修的东西，
给一个"去设置里看看"的入口只会让人白找。

---

## 3. 验证

### 3.1 三端单测（全量，不是只跑新增）

| 端 | 命令 | 结果 | 改前 |
|---|---|---|---|
| Python | `pytest -q` | **231 passed** | 229 |
| Java | `mvn -o test` | **190 run / 0 fail / 0 error / 65 skipped** | 188 |
| 前端 | `npm run test:weather` | **40 项全 PASS** | 36 |

跳过项（65）是 MySQL/Redis 集成用例，与本次改动无关，保持原状。

### 3.2 真实进程链路（13 项断言，全 PASS）

脚本：`backend-ai/scripts/verify_circuit_open_reason.py`
拓扑：`Java(8081) → Python(8100，上游被替身接管为固定 500)`；Vue 层在 §3.3 用同一套服务另测

> 用替身而不是真实账号，按 PM 要求：「不要通过真实账号故意制造 429、403」。
> 替身只替换 provider 的唯一出网点 `qweather._get`，产品代码零改动。

| # | 断言 | 实测 |
|---|---|---|
| 1.1–1.3 | 3 次真失败：200 + `available=false` + `UPSTREAM_ERROR` | HTTP 200 / 193ms、44ms、42ms，上游调用 1→2→3 |
| 1.1–1.3 | 这 3 次**确实出了网** | 调用计数每次 +1 |
| 2.1 | 第 4 次（熔断中）：200 + `available=false` + **`CIRCUIT_OPEN`** | HTTP 200 / 24ms |
| 2.2 | 熔断期间**没有出网** | 拦截前 3 → 拦截后 3 |
| 2.3 | 原因由 Python 透传，未被 Java 改写 | Java 拿到 `CIRCUIT_OPEN` |
| 2.4 | 两种原因给用户两句不同的话 | 「天气服务暂时不可用」 vs 「天气服务连续异常，已暂停刷新，稍后会自动恢复」 |
| 3.1 | 直连 Python 也是 `CIRCUIT_OPEN` | 2ms |
| 3.2 | 直连同样没出网 | 仍为 3 |
| 3.3 | 两端 reason 逐字一致 | Python=`CIRCUIT_OPEN`，Java=`CIRCUIT_OPEN` |

**整场 4 次请求只产生 3 次上游调用，熔断后 0 次新增。**

### 3.3 Vue 层（同一页面，前后两张对比）

同一条链路、同一个计划详情页，只改变上游状态：

| 时点 | 页面文案 | 上游调用累计 |
|---|---|---|
| 第 1 次加载（breaker=closed，真失败） | 「天气服务暂时不可用」 | 1 |
| 第 2、3 次加载（继续真失败） | 同上 | 2 → 3 |
| **第 4 次加载（breaker=open）** | **「天气服务连续异常，已暂停刷新，稍后会自动恢复」** | **3（不再增长）** |

这组对比是关键：同一页面、同一接口、同样的 `200 + available=false`，
用户看到的话变了，且变化与"上游调用不再增长"严格对齐。

---

## 4. 过程中踩到的坑（记录下来，避免重犯）

### 4.1 第二个前端实例被 CORS 403 挡住

为了不动正在跑的 5173，另起了一个 5174（`VITE_PROXY_TARGET` 指向 8081）。
登录直接 403 —— 不是代码问题：`app.cors.allowed-origins` 只放行 `http://localhost:5173`。
重启 8081 时补上 `CORS_ORIGINS=http://localhost:5173,http://localhost:5174` 才通。

**教训**：起第二个前端实例时，"换一个后端"只改了一半；来源白名单是另一处必须同步的配置。
这类问题会表现为"接口 403"，看起来像权限问题，实际是环境没配对。

### 4.2 熔断器是进程级单例，跨场次会串味

A 项验证时踩过一次（用 403 验完，熔断器已被打开到 1800s，下一场第 1 次请求就被拦）。
本次的处理是：验证脚本开头重置故障模式并清空调用日志；Vue 对比时**重启 Python 8100** 拿空熔断器。

### 4.3 验证脚本必须读 Java 信封

Java 统一响应是 `{code,message,data,traceId}`，`data` 里才是 `PlanWeatherVO`。
不剥这一层会读到 `available=None` 然后得出一个假红——A 项已经因此误判过一次。

---

## 5. 本次**不声称**的事

1. **不声称 `note` 字段已改好**：`stale=true` 时 Java 写的 note 仍是
   「本次刷新失败：`CIRCUIT_OPEN`」这种英文枚举。前端在 stale 时不再展示 note，
   所以用户在"旧值 + 熔断"下只看到一句通用提示。这一格没做，见 §6。
2. **不声称生产时序**：本次只验证"熔断拦截返回哪个 reason"，
   冷却时长、指数退避、半开探测的时序仍以 A 项报告为准（那边做过 10 分钟真实时序）。
3. **不声称多实例语义**：熔断器仍是进程内单例，两台机器各自计数。
4. **不声称真实 429/403**：全部用替身注入的等价响应。

---

## 6. 留给后续的事（不在本轮批准范围）

| 事项 | 状态 | 说明 |
|---|---|---|
| `note` 中暴露英文枚举 | 未做 | stale 场景下 note 写 `本次刷新失败：CIRCUIT_OPEN`；要不要翻成中文，需要先决定"翻译在谁那里做"（现在只有 Python 有一点翻译权） |
| 手动重置熔断入口 | **PM 不批准** | 理由：多实例下入口会带来实例定位问题，且目前没有证据表明确实造成业务损失 |
| M3-2（天气进入生成链路） | **PM 暂缓** | PM 原话：「我不希望在天气故障状态表达还不够清晰时，把天气进一步引入模型生成链路」 |

M3-2 暂缓的条件，按 PM 的意思是**先**把"故障状态表达清楚"，也就是本报告这一格。
现在这一格完成了，是否恢复 M3-2 由 PM 决定。
