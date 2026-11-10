# 08 · TravelPlan v1 契约冻结决议（D-06）

> 状态：**已冻结** ｜ 冻结日期：2026-11-09 ｜ 决策人：技术负责人（待业务方复核）
> 唯一实现来源：`contracts/travel-plan/travel_plan_draft_v1.json`（AI 输出）与 `travel_plan_v1.json`（存储/展示）
> 本文档取代 `05-接口与AI输出契约.md` 第 2 节中与结论冲突的部分；05 的章节结构、责任边界、SSE 协议、降级策略继续有效。

## 1. 冻结结论

### 1.1 必须先定的四项（Q-01 ~ Q-04）

| # | 结论 | 与 07 建议的差异 |
|---|---|---|
| **Q-01 多城市** | `basic.destination` 保留主目的地；新增 `basic.destinations: string[]`（0..10，去重），**约定 `destinations[0] === destination`**，由 Java 校验；`destination=null` 时 `destinations` 必须为空 | 采纳 |
| **Q-02 预算区间** | L0 下 `budget.total.value` 与 `budgetItem.amount.value` **恒为 null**，`display` 固定 `待核实`；区间口径只写进 `budget.note`（禁数字） | 微调：`display` 统一为 `待核实`，不采用「AI 估算，需自行核实」——避免前端为两个常量分支，估算语义交给 `budget.note` |
| **Q-03 偏好来源** | 删除 `preferences.source`，改为 `interestsSource` / `transportSource` / `accommodationSource` / `specialRequirementsSource`，枚举 `USER / USER_DEFAULT / MODEL_KNOWLEDGE`；数组为空时不得标 `USER` | 采纳 |
| **Q-04 跨城交通** | `TransportSegment` 增加 `scope: INTRA_DAY \| INTER_CITY`：前者用 `fromItemOrder/toItemOrder`，后者用 `fromDayIndex/toDayIndex` + `fromCity/toCity`；两组字段互斥，另一组必须为 `null` | 采纳 |

### 1.2 直接按建议定（Q-05 ~ Q-14）

| # | 结论 |
|---|---|
| Q-05 | `missingFields` 固定 10 个词：`originCity / destination / startDate / endDate / days / travelers / budget / interests / transport / accommodation`，Schema 用 `enum` 硬约束 |
| Q-06 | `FactField.display` 在 L0 恒为常量 `待核实`，Schema 用 `const` |
| Q-07 | `startTime/endTime` 不算实时信息；UI 必须标注「建议时间」，`duration` 为 null 时文案不得写「游览 N 小时」（由自由文本规则兜底） |
| Q-08 | `unverifiedFieldCount` = 所有 `FactField` 中 `verified=false` 的实例数，Java 递归重算并与模型自报值比对 |
| Q-09 | `attractionId` 在 M1 恒为 `null`（Schema `type: "null"`），不建 POI 主数据 |
| Q-10 | `planId` 在 draft 中 `const: null`，落库后回填 |
| Q-11 | 拆两份 Schema：draft 的 `status` 只允许 `DRAFT`；存储模型允许 5 态并新增 `version` |
| Q-12 | 天数推导由 Java 负责；`days` 与日期区间矛盾时拒绝落库并提示澄清（不进 Schema） |
| Q-13 | 境外签证提示只进 `risks[]` 文本，文案要求带「以官方渠道为准」 |
| Q-14 | `expiresAt` 在 M1 恒为 `null` |

## 2. 相对 05 原稿的破坏性变更

这五处会直接影响前端类型与建表，**M1 不存在存量数据，因此不设兼容层**：

| # | 原稿 | 现在 | 原因 |
|---|---|---|---|
| B-1 | `preferences.source` 单一字段 | 四项 `*Source` | 无法区分「兴趣用户说了、交通用了默认值」（Q-03） |
| B-2 | `basic.destination` 单值 | 增加 `basic.destinations[]` | 「成都 + 乐山 4 天」是最常见需求，单值装不下（Q-01） |
| B-3 | `TransportSegment` 仅 `fromItemOrder/toItemOrder` | 增加 `scope` 与跨城四字段 | 原结构无法表达跨天跨城路段（Q-04） |
| B-4 | `aiNote` 为 Markdown | 改为纯文本，`maxLength 500` | 一旦允许 Markdown，模型会把表格与数字塞回来，AC-08 无法守 |
| B-5 | 存储模型无 `version` | 存储模型增加 `version: integer` | PATCH 乐观锁必需，05 遗漏 |

## 3. L0 的三条硬底线（怎么被技术强制）

不依赖 Prompt 自觉，全部有校验落点：

1. **实时值一律为空**：`FactField.value` 在 draft 中 `type: "null"`，`verified` 为 `const: false`，`fetchedAt/expiresAt` 为 `null`。模型即使输出 `60`，Schema 直接拒。
2. **自由文本不许夹带数字**：`risks / advice / note / aiNote / budget.note` 统一走 `freeText`，用 `not + pattern` 拦截「数字 + 元/℃/度/分钟/小时/公里/米/班次」。
   - 刻意**不拦**「数字 + 天/人」——天数人数来自用户输入，不是编造。
   - 全数字禁止会误伤「第 2 天」「两处距离较近」，不采用。
3. **未知就是 null**：`basic` 未知字段为 `null` 且必须出现在 `missingFields`；反向由 Java 校验（Schema 无法跨字段）。

## 4. Schema 表达不了、必须由 Java 校验的语义规则

JSON Schema 只能管单点结构，以下六条写进 WP4 的 `PlanSchemaValidator`，缺一不可：

| # | 规则 | 违反处理 |
|---|---|---|
| S-1 | `dayIndex` 从 1 连续递增，`days.length === basic.days`（`days` 非空时） | 拒绝，`SCHEMA_VIOLATION` |
| S-2 | 同一 `day` 内 `itemOrder` 唯一且从 1 连续 | 拒绝 |
| S-3 | `destinations[0] === destination`，且 `days[].city ⊆ destinations` | 拒绝 |
| S-4 | `missingFields` 与 `basic` 的 null 状态严格对应（该列的不列、不该列的列了都拒）。**范围限定 basic 的 6 个字段**：`originCity / destination / startDate / endDate / days / travelers` | 拒绝 |
| S-5 | `unverifiedFieldCount` 重算比对 | 拒绝（防自报不实） |
| S-6 | `transportSegment` 的 `fromDayIndex/toDayIndex` 必须指向存在的天，且 `fromDayIndex < toDayIndex` | 拒绝 |

### 4.1 WP4 实现时对规则的收敛（2026-11-10）

S-4 原表述里的「`basic` 的 null 状态」按字面实现会把 `preferences.transport / accommodation` 也纳入比对，
但 Q-03 引入的 `*Source = USER_DEFAULT` 已经表达了「用户没提、按默认值处理」，这与「未知」是两件事。
按字面实现会误拒合法计划——**合规样例就是这样被抓出来的**（样例里 `transport` 为 null 且未列入 `missingFields`）。

因此 S-4 明确收敛为 basic 的 6 个字段，排除项与理由：

| 排除字段 | 理由 |
|---|---|
| `preferences.transport / accommodation` | `*Source = USER_DEFAULT` 已表达「未指定但按默认」 |
| `budget` | 契约中恒为对象，未知体现在 `total` 的 FactField 状态 |
| `interests` | 空数组可能是用户明确表示「没有偏好」，不等于未知 |

同期修正合规样例一处缺陷：`destinations` 原为 `[成都, 乐山]`，但第 3 天城市是 `都江堰`，
违反 S-3（`days[].city ⊆ destinations`）。已补为 `[成都, 乐山, 都江堰]`。
这是「样例必须被校验器验证」的直接收益——Schema 只校验结构，只有语义校验才发现这类矛盾。


## 5. 文件与引用方式

```
contracts/travel-plan/
├── travel_plan_draft_v1.json     # AI 输出契约（最严）
├── travel_plan_v1.json           # 存储/展示模型（放宽 5 处）
├── examples/
│   ├── draft.valid.json          # 合规样例（24 个 FactField）
│   └── invalid_cases.json        # 10 组畸形变异，用于回归
└── tools/
    └── validate_examples.py      # 契约自检：语法 + 合规 + 畸形必须被拒
```

**为什么放 `contracts/` 而不是 05 原稿说的 `backend-ai/app/schemas/`**：契约是三方共享物，放任一端内部目录会让另外两端去读别人的源码树。`contracts/` 作为中立位置，由两侧构建各自引用：

- Java：`pom.xml` 增加 `<resource><directory>../contracts/travel-plan</directory><targetPath>schemas</targetPath>`，打进 classpath（WP4 落地）
- Python：开发期读项目根 `contracts/`，部署期用 `SCHEMA_DIR` 覆盖（WP3 落地）
- 前端：`frontend/src/types/travelPlan.ts` 手工对齐，由本文件的变更记录驱动

**改动纪律**：任何字段改动先改 `contracts/`，再跑 `validate_examples.py`，再同步前端类型与 04 文档；`schemaVersion` 不动、破坏性变更必须升到 `1.1` 并保留 v1 供回滚。

## 6. 自检结果（2026-11-09 实跑）

```
schema draft: OK (meta-schema)
schema stored: OK (meta-schema)
valid sample -> draft: PASS
valid sample -> stored: PASS
FactField instances=24, unverified=24, declared=24
invalid cases: 10/10 rejected
```

自检过程中抓到并修掉一个真实缺陷：draft 的 `FactField.value` 最初只写了描述、没写类型，导致「`verified=false` 但 `value=60`」能通过校验。现已改为 `type: "null"`，这正是「没有实际执行的测试不得声称通过」的价值。

## 7. 本次不冻结（明确边界，避免误以为已覆盖）

- `PlanSummary`（列表页视图模型）、分页信封：随 WP5 前端一起定
- `plan_draft` 用完整 JSON 还是 JSON Patch：**取完整 JSON**（M1 计划体量在 30KB 内，分片收益不抵复杂度），写入 05 第 8 节的待确认项
- 精度类问题：`FactField<number>` 的货币小数位、时区表达，M1 一律走 `CNY` 与 `Asia/Shanghai`
- POI / 天气 / 地图工具的真实数据结构：L1 接入时另立 v2，不往 v1 里塞
