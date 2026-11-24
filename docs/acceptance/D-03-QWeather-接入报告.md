# D-03 实施报告：天气数据源切换到和风天气

> 日期：2026-11-23（初版）/ 2026-11-24（补真实链路验证）
> 依据：`docs/design/10-D-03-天气数据源决策记录.md`（PM 已批准）
> 范围：五步执行顺序 ②③④⑤ **全部完成**，真实 API 已打通（V15–V17 见 §3）

---

## 1. 一句话结论

**代码、离线验证、双 Provider 契约一致性、以及和风天气真实链路全部通过；12 项联调检查 0 失败。**

两条结论分开说：

- 已证明：结构、口径、降级、熔断、JWT 签名、跨天 TTL、10 天边界、两条路径产出同一份契约，
  以及**和风天气真实接受我们的 JWT 并返回可正确归一化的数据**（详见 §3）。
- 仍未证明：真实 429 限流的表现（没打到限流）、真实 403 INVALID HOST（用注入响应代替）、
  真实跨天 00:10 失效（要等实际跨天）、生产配额账单（要等出账）。


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
| `scripts/verify_weather_qweather.py`（新） | 真实联调脚本，12 项检查（原 7 项，第 7 项拆成 7a/7b/7c），**无凭据时返回退出码 2 而不是 0** |

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
| V15 | **和风天气真实 API 被接受** | **PASS（真跑）** | JWT 签发 → 真实 `200`，返回 3 天，`source=QWEATHER`。凭据：开发者 ID `Q10FA018F7` / 项目 `25KX2UNVWC` / 凭据 `TNWDJVUB7R` |
| V16 | 和风天气真实响应结构可被正确归一化 | **PASS（真跑）** | 真实响应逐项落到契约：日期 `2026-11-25 ~ 11-27`、降水概率落在 0–100、四组 FactField 过校验器、无「疑似时区错位」warning。**不是逐字段比对官方示例**，而是"能被正确解析且日期/口径都对得上" |
| V17 | 真实错误的表现与熔断 | **PASS（部分真跑）** | ① 真实 DNS 失败 → `UPSTREAM_ERROR` + `hard=False`，熔断保持 closed（连续失败 1）——这条是**真跑**；② 403 → `hard=True`、**只发 1 次请求**、breaker 立即 `open`、冷却 1800s——这条用**注入 403 响应**证明（不用真 403 是对的：错 host 在真实网络里是 DNS 失败，永远拿不到 403，用它证 hard 属于用错证据）；③ **真实 429 未触发**，仍是离线用例覆盖 |

### 真实链路实测（`scripts/verify_weather_qweather.py`）

```
[PASS] 0. 配置就绪 — host=kg6****.re.qweatherapi.com
[PASS] 1. JWT 签发 — kid=TNWDJVUB7R exp-iat=900
[PASS] 2. 真实调用 — 返回 3 天，source=QWEATHER
[PASS] 2b. FactField 合规
[PASS] 3. 降水概率口径 — 取值 [0, 0, 0]（应落在 0–100）
[PASS] 4. 日期口径 — 2026-11-25 ~ 2026-11-27
[PASS] 5. 10 天边界 — days=10
[PASS] 5b. 越窗口降级 — reason=DATE_OUT_OF_RANGE
[PASS] 6. 归因文案 — 天气服务由和风天气驱动
[PASS] 7a. 403→hard 立即熔断 — 请求次数=1 breaker=open
[PASS] 7b. 熔断后不再发请求 — 新增请求=0
[PASS] 7c. DNS 失败→soft 不熔断 — breaker=closed 连续失败=1

PASS 12 / FAIL 0 / SKIP 0   exit=0
```

> 第 7 项原先写的是"用错 host 必须拿到 403 并熔断"，**这条用例本身是错的**：
> 错 host 在 DNS 层就失败，到不了和风天气。首次跑确实红了，改法不是放宽断言，
> 而是拆成 7a/7b/7c 三条分别证「hard 立刻熔断」「熔断后不再请求」「DNS 失败不许误判成 hard」。

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

## 4. 本次仍不声称的事

1. **不声称真实 429 限流已验证**。没打到 QPM 上限，限流后的表现仍是离线用例覆盖。
2. **不声称真实 403 INVALID HOST 已验证**。403 分支用注入响应证明，因为真实错 host
   是 DNS 失败而非 403（见 V17 ②）。
3. **不声称跨天 00:10 硬失效在真实流量上生效**。逻辑有单测，但要等实际跨过一次零点。
4. **不声称生产配额与账单口径**。免费额度 5 万次/月是官方定价页数字，实际出账以控制台为准。
5. **不声称前端归因链接已被人工点验**。条款要求「可见、独立放置、可点击」，
   这一条只能人眼看，脚本证明不了。

---

## 5. 凭据接入记录（2026-11-24）

实际做法与初版方案的差异，记下来便于复现：

| 项 | 实际值 / 做法 |
|---|---|
| 开发者 ID | `Q10FA018F7`（控制台-设置） |
| API Host | `kg6****.re.qweatherapi.com`——**注意带 `.re.` 段**，不是文档示例的 `abcxyz.qweatherapi.com` 形态 |
| 项目 / 凭据 | 项目 `25KX2UNVWC`「天气」/ 凭据 `TNWDJVUB7R`，认证方式 **JWT** |
| 密钥 | 由账号主体本人生成，私钥 `backend-ai/ed25519-private.pem`（已在 `.gitignore`），公钥上传到控制台 |
| 公钥核对 | 控制台显示的 SHA-256 = 对「PEM 全文去掉结尾换行」算的 SHA-256，与本地私钥导出公钥一致才说明配对正确 |

> **配对核验方法（值得记住）**：控制台不给看公钥原文，只给 SHA-256。
> 它算的不是 SPKI DER，而是 `-----BEGIN PUBLIC KEY-----\n<base64>\n-----END PUBLIC KEY-----`
> 这段文本（**不含结尾换行**）的 SHA-256。用 `openssl pkey -in 私钥 -pubout | ...` 复算能直接对上。
> 对不上就是拿错私钥了——这时候去调 API 只会得到 401，且官方不告诉你原因。

配置写在 `backend-ai/.env`（已忽略入库）。`app/config.py` 不自动加载 `.env`，启动前需：

```bash
set -a; . ./.env; set +a
```

私钥路径相对 `backend-ai/`，必须在 backend-ai 下启动；从仓库根启动会解析不到，
这是刻意的——写死绝对路径换机器就失效。

### 一个必须说明的判断

初次跑联调时第 7 项红了（`reason=UPSTREAM_ERROR hard=False breaker=closed`）。
这**不是实现 bug**，是用例假设错了：错 host 在 DNS 层就失败，拿不到 403，
归 soft 是正确设计（网络抖动可自愈，一次 DNS 失败就熔断会把偶发放大成全挂）。
处置是拆成 7a/7b/7c 三条精确断言，**而不是把断言放宽到"报错就算过"**。

