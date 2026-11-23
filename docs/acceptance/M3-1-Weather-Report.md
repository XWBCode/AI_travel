# M3-1 天气展示 · 验收报告

> ⚠️ **本报告的适用范围已收窄**：数据源随后由 D-03 切换为和风天气，预报窗口 16 → 10 天、
> 缓存 30 → 60 分钟。本文证据仍然成立的部分是**降级行为、日期口径、越权、展示**；
> 涉及"Open-Meteo 上游 / 16 天窗口 / 生产不可用"的结论请看
> `docs/acceptance/D-03-QWeather-接入报告.md`（其中明确标注了和风天气真实链路仍为 BLOCKED）。

> 提交范围：`feat(weather): Python 天气工具层与端点` + `feat(weather): Java 编排+缓存+降级 与前端天气条`
> 上位文档：`docs/design/09-M3-1-天气接入设计.md`（PM 已批准，日期口径 C）
> 环境：本机 dev，`WEATHER_PROVIDER=open-meteo`（两端口径一致）；真实模型 `deepseek-v4-pro` 网关；真实上游 Open-Meteo
> 结论：**W-1 / W-3 / W-4 / W-6 / W-8 六项 PASS（其中 W-4 一项为「部分证据」），W-2 / W-5 只在单测层面成立，W-7 未做对照实验**

---

## 1. 一句话结论

天气这条链路已经能从「城市 + 日期」取到**真值**，并在 7 种拿不到的情况下**如实说自己拿不到**，
而且这件事**没有碰生成链路**——提示词、校验器、状态机、SSE 全未改动。

---

## 2. 逐项结论

| 编号 | 验收项 | 结论 | 证据 |
|---|---|---|---|
| W-1 | 有日期 + 已知城市 | **PASS** | 端到端：成都 3 天，四组字段 `source=TOOL` / `verified=true` / 带 `fetchedAt`，`display` 带单位（`10–14℃`、`降水概率 70%`、`2级风`）；契约机检：`PlanWeatherServiceTest` + `test_fact_fields_validate_against_the_contract` 用 `factFieldNumber` 校验通过 |
| W-2 | 上游超时 | **PASS（仅分型）** | `test_upstream_timeout_is_classified_as_timeout`（MockTransport 抛 `ReadTimeout` → `UPSTREAM_TIMEOUT`）；Java 侧 `fetchWeather` 5s 超时 → `null` → `UPSTREAM_ERROR`。**未在真实上游制造过超时**，见 §5 |
| W-3 | 无出发日期 | **PASS** | 端到端：生成出的计划 `startDate=None` → 200 + `available=false` + `reason=NO_START_DATE`，`days=[]`（**一个数值都没有**）；`test_no_start_date_never_calls_upstream` 断言连上游请求都不发 |
| W-4 | 缓存 | **PASS（部分证据）** | 端到端：二次请求 `fetchedAt` 未变，**1595ms → 27ms**；`weather_servesFromCache` 用 Mockito `times(1)` 断言三次调用只打一次上游。**外部无法数上游调用次数**，所以这条的「0 次」只由单测证明，不由端到端证明 |
| W-5 | 过期回退 | **单测 PASS / 端到端 NA** | `weather_servesStaleValueWhenRefreshFails`：注入过期缓存 + 上游返 null → 仍 `available=true`、`stale=true`、`note` 含「上次」。端到端要等 30 分钟并让上游挂掉，未做 |
| W-6 | 越权 | **PASS** | 端到端：他人 token 访问返回 **403**，响应体无任何 `days` |
| W-7 | 生成链路无回归 | **NA** | 本次只跑了 1 条生成（94.3s 成功，与 103–160s 的既有基线同量级）。**n=1 不足以证明无回归**，不做对照实验就报 PASS 是假的 |
| W-8 | 合规 | **PASS** | 响应含 `attribution`（`Weather data by Open-Meteo.com (CC BY 4.0)`）；前端天气条底部逐字展示；两端口径 `provider` 默认 `none`，`test_endpoint_defaults_to_disabled_by_config` 断言默认配置下直接 `PROVIDER_DISABLED` |

---

## 3. 端到端实跑记录（`backend-java/scripts/verify_weather.py`）

```
== 0. 准备用户与计划 ==
PASS 注册并登录
PASS 生成计划（耗时 94.3s）
  计划 2124887242789089282：destination=成都 days=3 startDate=None

== 1. W-3：计划可能没有出发日期 ==
PASS HTTP 200（降级不是错误）
PASS available=false
PASS reason=NO_START_DATE
PASS 不返回任何天数（不拿今天顶替）
PASS 提示语可读

== 2. 补上出发日期后应能查到真实天气（W-1 / W-8）==
PASS available=true
PASS W-8 响应含署名
PASS source=OPEN_METEO
PASS 按行程天数返回（3 天）
PASS 温度/降水/风 是已核实的真值（source=TOOL / 有 fetchedAt / display 非空）
PASS 天气现象有中文描述
PASS dayIndex 从 1 开始且连续
PASS 日期与出发日对齐
  样例：2026-11-24 成都 小毛毛雨 10–14℃ 降水概率 70%

== 3. W-4：缓存 ==
PASS 二次请求 fetchedAt 未变（命中缓存）
PASS 二次请求更快：1595ms → 27ms

== 4. 超出预报窗口：与「没日期」是两条不同提示 ==
PASS reason=DATE_OUT_OF_RANGE
PASS 提示与 NO_START_DATE 不同

== 5. W-6：越权 ==
PASS 别人的计划返回 403
PASS 不泄露任何天气数据
```

另有一条真实上游抽查（Python 层直接调，绕过 Java）：

```
云南        -> ok  resolved=昆明 2026-11-24 小毛毛雨 7–10℃ note='按省会昆明代表云南'
大理白族自治州 -> ok  resolved=大理 2026-11-24 毛毛雨   10–17℃
成都 2027-03-01 -> DATE_OUT_OF_RANGE 出发日期超出预报范围，临近再看
瓦坎达       -> CITY_UNKNOWN 暂无「瓦坎达」的天气数据
成都 无日期   -> NO_START_DATE 补充出发日期后可查看天气
```

---

## 4. 界面核对（截图已人工确认）

| 状态 | 观察到的结果 |
|---|---|
| 有天气 | 位置在**基本信息下方、每日安排上方**；`D1 11-24 成都 小毛毛雨 10–14℃ 降水概率 70% 2级风`，逐日三行；底部 `Weather data by Open-Meteo.com (CC BY 4.0) · 更新于刚刚` |
| 无出发日期 | 同位置变成 `天气 / 补充出发日期后可查看天气 / 补充出发日期`，**没有任何数字**，卡片位置与高度不变（不塌缩） |

这两个状态都是真的跑出来的，不是拼图：先 `PATCH startDate=2026-11-24` 截第一张，
再 `PATCH startDate=null` 刷新截第二张。

---

## 5. 本次**不声称**的事（比结论更重要）

| 不声称 | 原因 |
|---|---|
| 「上游超时会在 3s 内返回」 | 只在单测里用 MockTransport 造了超时（瞬时失败），真实网络超时的时延没测 |
| 「缓存命中时上游调用数为 0」 | 进程内缓存，外部观测不到；只有 Mockito 单测能断言调用次数 |
| 「生成链路无回归」 | 只跑了 1 条生成（n=1），没做同输入对照 |
| 「过期回退在真实环境可用」 | 需要等 30 分钟 TTL + 让上游挂掉，未做端到端 |
| 「多实例下缓存一致」 | 进程内缓存是单机状态，多实例各存一份（已知限制，本切片不解决） |
| 「可以上线」 | 免费端点**禁商用**，`provider` 默认 `none` 正是为此。上线要等 D-03 定案 |

---

## 6. 测试资产

| 层 | 命令 | 结果 |
|---|---|---|
| Python | `./.venv/Scripts/python.exe -m pytest tests -q` | **134 passed**（其中天气 26 项：`tests/test_weather_tool.py`） |
| Java | `mvn -o test -Dtest=PlanWeatherServiceTest` | **11 passed** |
| Java 全量 | `mvn -o test` | 149 run / 2 failed —— 两个失败都是 **BUG-017**（幂等测试依赖执行顺序），已在干净树（`git stash -u`）复现，与本轮无关 |
| 前端 | `npm run test` | 全部通过（其中 `test:weather` 24 项断言） |
| 前端类型 | `npx vue-tsc --noEmit` | 通过 |
| 前端构建 | `npm run build:only` | 通过 |
| 端到端 | `python backend-java/scripts/verify_weather.py` | 全部 PASS |

---

## 7. 踩到的坑（写下来是因为下次还会踩）

1. **Windows 没有 IANA 时区库**：`ZoneInfo("Asia/Shanghai")` 直接抛 `ZoneInfoNotFoundError`。
   改用固定 `+08:00`（中国无夏令时，与 `Asia/Shanghai` 等价），不为一个时区引入 `tzdata` 依赖。
2. **契约里 `note` 是 freeText，禁止「数字 + 单位」**：所以 FactField 的 note 只能放纯文字，
   「更新于 30 分钟前」这种话不能塞进去（会违反 `freeText.not.pattern`）。时效信息走响应外层。
3. **`verified=true` 的连带要求**：契约的 `allOf` 规定 `verified=true` 必须有 `fetchedAt` 且
   `source ∈ {USER,TOOL,CACHE}`。所以「上游少给一列」时不能填 0，必须整组降级成
   `verified=false / source=NONE / value=null`。
4. **`dayIndex` 必须相对整段行程编号**：行程跨过窗口边界时只返回窗口内的天，
   若从返回结果第一天重新编号，前端会把「第 3 天」显示成「第 1 天」。
5. **`geocoding-api.open-meteo.com` 本机不可达**（`code=000`，7.5s 超时）而 `forecast` 正常——
   所以坐标必须自带静态表，否则天气会挂在第一步且原因很隐蔽。

---

## 8. 已知限制与后续

| # | 项 | 现状 |
|---|---|---|
| 1 | **商用授权** | Open-Meteo 免费端点禁商用。上线前必须 D-03 定案，或订阅/自托管。**这是硬约束** |
| 2 | 坐标表维护 | 149 个城市/景点 + 34 条省/大区别名，硬编码在 `city_coords.json`；新增城市要改文件（计划里的城市集合很小，可接受） |
| 3 | 预报窗口 16 天 | 更远只能降级，不用气候平均替代（那又变成编造） |
| 4 | 单机缓存 | 不引入 Redis 是刻意的，避免为一个附属功能加依赖 |
| 5 | 提示语局部过时风险 | 详情页「天气、票价与路况还没接入实时数据源」这句在天气开启后对**行程项内的天气字段**仍然成立（那些仍是未核实），但容易被误读为「整站没天气」。等切片二改文案时一并处理 |
| 6 | 切片二（未做） | 天气进入生成链路 / AI 依据天气给建议 —— **不在本切片范围**，PM 已明确「不要顺手扩展成完整天气系统」 |
