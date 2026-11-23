# D-03 实施报告：天气数据源切换到和风天气

> 日期：2026-11-23
> 依据：`docs/design/10-D-03-天气数据源决策记录.md`（PM 已批准）
> 范围：PM 给的五步执行顺序之 ②③④ 完成，⑤ **BLOCKED**（缺凭据，需账号主体操作）

---

## 1. 一句话结论

**代码、离线验证、双 Provider 契约一致性都已完成并通过；和风天气真实链路一步都没跑过。**

这不是"差不多做完了"，而是两条必须分开说的结论：

- 已经能证明的：结构、口径、降级、熔断、JWT 签名、跨天 TTL、10 天边界、
  以及**两条真实路径产出同一份契约**（详见 §3）。
- 完全没证明的：和风天气**是否接受我们的 JWT**、真实响应是否符合官方示例、
  真 429 的表现、真实跨天行为（详见 §4）。

真实链路要等凭据。我不会用"本地 mock 通过"冒充"接入完成"。

---

## 2. 交付清单

### Python（数据源层重构，契约唯一入口不变）

| 文件 | 作用 |
|---|---|
| `app/tools/weather_reasons.py`（新） | 降级原因枚举 + 文案。契约的一部分，只允许"新增取值"或"改文案" |
| `app/tools/weather_providers/base.py`（新） | `NormalizedDay` / `ProviderResult` / `ProviderFailure`，**单位口径在这一层定死** |
| `app/tools/weather_providers/open_meteo.py`（新） | Open-Meteo 调用 + WMO 码表（只服务于本 provider） |
| `app/tools/weather_providers/qweather.py`（新） | 和风天气调用 + 归一化 + 日期口径双轨校验 + 错误分型 |
| `app/tools/weather_providers/__init__.py`（新） | provider 注册表（加数据源只改这里） |
| `app/tools/weather_jwt.py`（新） | Ed25519 JWT 签发与缓存（15 分钟、提前 120s 换新、401 可强制失效） |
| `app/tools/weather_circuit.py`（新） | 熔断器（hard 立刻开 / soft 连续 3 次开 / 半开翻倍） |
| `app/tools/weather.py`（改） | 只剩编排：城市解析 → 窗口裁剪 → 调度 provider → 拼 FactField |
| `app/config.py` / `config/config.yaml`（改） | 新增 `qweather_*` 凭据项、熔断参数；`cache_ttl_minutes` 30 → 60 |
| `app/routers/weather.py`（改） | `days` 上限 16 → 10；把配置透传给 provider |
| `requirements.txt`（改） | 新增 `PyJWT==2.10.1`、`cryptography==44.0.0` |
| `scripts/verify_weather_qweather.py`（新） | 真实联调脚本，7 项检查，**无凭据时返回退出码 2 而不是 0** |

### Java（只改了 4 处，全是 PM 点名的）

| 文件 | 改动 |
|---|---|
| `WeatherProperties.java` | `cacheTtlMinutes` 30 → 60；注释改为 D-03 口径 |
| `PlanWeatherService.java` | `MAX_FORECAST_DAYS` 16 → **10**；`source` 兜底 `OPEN_METEO` → **`NONE`**；缓存 key 加**请求自然日**；`putCached` 增加"次日 00:10"硬边界（东八区，不依赖服务器时区） |

### 前端（唯一一处：归因）

| 文件 | 改动 |
|---|---|
| `src/utils/weather.ts` | 新增 `attributionUrl` / `attributionText`；兜底署名改为中性的「天气数据由第三方服务提供」 |
| `src/components/WeatherStrip.vue` | 署名渲染为超链（`target=_blank` + `rel=noopener noreferrer`），样式独立、不与正文混淆 |

### 文档

`docs/design/10-D-03-天气数据源决策记录.md` 新增 §9（PM 批准结论 + 8 条实施约束）与
§10（**4 处实施差异**，含"响应根是 `days` 且每天没有 date 字段"这个核验时漏掉的点）、
`.env.example` 补和风天气凭据段。

---

## 3. 验证矩阵（逐项标注"凭什么算通过"）

| # | 检查项 | 结论 | 证据 |
|---|---|---|---|
| V1 | JWT 的 header/payload 逐字符合官方口径 | **PASS（离线）** | `test_jwt_carries_exactly_the_official_claims`：`alg=EdDSA`、`kid=凭据ID`、`iss=开发者ID`、`sub=项目ID`、`exp-iat=900`、无 `aud/nbf`、签名 64 字节 |
| V2 | 签名能被公钥验证 | **PASS（离线）** | `test_jwt_signature_verifies_with_the_public_key`（用真实 Ed25519 验签） |
| V3 | token 缓存 / 提前换新 / 401 强制失效 | **PASS（离线）** | 假时钟注入，3 条用例；Ed25519 是确定性签名，故用路径而非字符串比较 |
| V4 | 降水概率 0–1 → 0–100 | **PASS（离线）** | `0.64 → 64`、`0.0 → 0%（verified=true 不是"没拿到"）` |
| V5 | 风级直接用 `wind.scale` | **PASS（离线）** | `scale=5 → "5级风"`，不再用 m/s 换算 |
| V6 | 只取白天天气现象 | **PASS（离线）** | 上游白天「小雨」/ 夜间「晴间多云」，断言取到「小雨」 |
| V7 | unit 非 ℃ 时降级而非换算 | **PASS（离线）** | `°F → value=null / verified=false / 暂无数据` |
| V8 | 日期口径（`22:00Z` → 本地次日） | **PASS（离线）** | `2026-11-22T22:00Z → 2026-11-23`；另有"上游口径变了就以数据为准"的用例 |
| V9 | 熔断：hard 立刻开 / soft 3 次 / 半开翻倍 | **PASS（离线）** | `403/429 只发 1 次请求`、`500 单次不熔断`、`半开失败冷却翻倍` |
| V10 | 跨天 TTL = `min(now+60min, 次日 00:10)` | **PASS（离线）** | `23:50 → 次日 00:10`；`12:00 → 13:00`；响应 `expiresAt` 实测不越过零点 |
| V11 | 10 天窗口前后端一致 | **PASS（离线 + 真实上游口径）** | Python `FORECAST_WINDOW_DAYS=10` / `qweather.MAX_DAYS=10` / Java `MAX_FORECAST_DAYS=10` / 路由 `le=10`；真实上游实跑见 V13 |
| V12 | 双 Provider 产出同一份契约 | **PASS（离线）** | 两条真实归一化路径的产物都过 `factFieldNumber` 校验器；且四组 `display` 逐字相同 |
| V13 | Open-Meteo **真实上游**链路仍然可用（证明重构没打死老路径） | **PASS（实跑）** | 临时实例 8100：3 天真实数据；`days=10 → 10 天（11-24~12-03）`；`days=11 → 422`；越窗口 → `DATE_OUT_OF_RANGE` + 200；`ttlMinutes=60`、`expiresAt=+1h` |
| V14 | 默认配置关闭（W-8 合规） | **PASS（离线）** | `test_endpoint_defaults_to_disabled_by_config`：默认 `PROVIDER_DISABLED` 且不碰上游 |
| V15 | **和风天气真实 API 被接受** | **BLOCKED** | 无凭据，一次都没调过 |
| V16 | 和风天气真实响应结构 == 官方示例 | **BLOCKED** | 只有文档示例作依据 |
| V17 | 真实 429 / 403 的表现 | **BLOCKED** | 用假 host 演练过"错误分型 → 熔断"这条分支（返回 FAIL 而非崩溃），但没触发真 403 |

### 实跑数字

| 套件 | 结果 |
|---|---|
| Python 单测 | **172 passed**（其中天气 64：坐标/窗口/降级 26 + 和风天气与双 provider 38） |
| Java 单测 | **152 run / 0 fail / 65 skipped**（天气 14） |
| 前端天气纯函数 | **36 项全过** |
| 真实上游（Open-Meteo） | 3 天 / 10 天 / 越窗口 / 422 四种情形逐条实跑 |

> 那 65 个 skipped 不记 PASS。它们是需要外部依赖（DB/Redis）的集成测试，
> 本轮没动到它们，也没有新增"靠执行顺序才绿"的用例——天气两个测试文件都加了
> `autouse` 的熔断器/token 缓存重置，就是为了不重复 BUG-017 那类问题。

---

## 4. 本次不声称的事

1. **不声称"和风天气已接入"**。真实调用 0 次。`source=QWEATHER` 在生产上还没出现过。
2. **不声称 JWT 会被上游接受**。签名结构对、能自验签，但"和风天气认不认"只有调用才知道。
3. **不声称日期口径在真实数据上正确**。V8 证明的是"按官方示例的形态算对"；
   真实接口若把"天"的起点改了，只有联调能发现。所以脚本里单独有一项日期口径检查。
4. **不声称 Java 端到端**。Java 侧只跑了单测，运行中的 8080 实例还是改动前的构建。
5. **不声称配额/成本已验证**。50,000 次/月是文档数字，真实计费口径要等账号后台。

---

## 5. 下一步（只能由账号主体做）

和风天气账号注册需要本人邮箱/手机号、并在控制台上传公钥。这一步不由开发侧代办。

```bash
# 1) 生成 Ed25519 密钥（私钥留在本地，*.pem 已在 .gitignore）
openssl genpkey -algorithm ED25519 -out ed25519-private.pem
openssl pkey -pubout -in ed25519-private.pem > ed25519-public.pem

# 2) 控制台：创建项目 → 添加凭据 → 身份认证选 JSON Web Token → 粘贴 ed25519-public.pem
#    记录：API Host、开发者ID、项目ID、凭据ID

# 3) 配好环境变量后跑真实联调（7 项检查，SKIP 不算通过）
cd backend-ai
export QWEATHER_API_HOST=... QWEATHER_DEVELOPER_ID=... QWEATHER_PROJECT_ID=... \
       QWEATHER_KEY_ID=... QWEATHER_PRIVATE_KEY_PATH=./secrets/ed25519-private.pem \
       WEATHER_PROVIDER=qweather
./.venv/Scripts/python.exe scripts/verify_weather_qweather.py
```

凭据到位后需要补做：V15–V17 三项，以及本报告 §3 里所有"离线"项在真实数据上复跑一遍，
然后才能把 `M3-1-Weather-Report.md` 里"生产不可用"的结论替换掉。
