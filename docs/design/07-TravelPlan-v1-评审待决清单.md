# TravelPlan v1 Schema 评审待决清单

> **状态更新（2026-11-09）**：Q-01~Q-14 已全部给出结论并冻结，见 `08-TravelPlan-v1-冻结决议.md`；实现以 `contracts/travel-plan/` 下的两份 JSON Schema 为准。本文档保留为评审过程留痕，不再作为实现依据。

> 用途：D-06 冻结前的讨论稿。逐条给结论后，才把 05 文档落成 JSON Schema。
> 依据：`docs/design/05-接口与AI输出契约.md` 第 2 节、`04-数据模型设计.md`
> 结论填写方式：每条的「结论」列写「采纳建议 / 改成 X」，我据此冻结。

## 一、必须先定（不定就会返工建表或校验器）

| # | 问题 | 现状冲突 | 影响 | 建议 |
|---|---|---|---|---|
| **Q-01** | **多城市行程怎么表达** | `basic.destination` 是单个字符串，但「成都 + 乐山 4 天」是最常见需求；`days[].city` 能表达，`basic` 不能 | 计划标题、列表页筛选、跨城交通落库 | `basic.destination` 保持主目的地；新增 `basic.destinations: string[]`（1..n，首个 = 主目的地）。列表页仍展示主目的地，详情页展示全部 |
| **Q-02** | **预算区间与 `FactField<number>` 矛盾** | 04 文档 4.5 说「可以给用户框架性区间并标注 AI 估算」，但 `budget.total` / `BudgetItem.amount` 都是 `FactField<number>`，装不下区间；而 AC-08 又要求 L0 下具体数值出现次数 = 0 | 校验器要么放过区间（违反 AC-08），要么拒绝（用户拿不到任何预算参考） | L0 下 `amount.value` **恒为 null**、`display` 固定「AI 估算，需自行核实」；区间文字另开 `note` 字段（禁数字正则同样适用）。等 L1 有真实报价再填数值 |
| **Q-03** | **`preferences.source` 粒度太粗** | 一个 `source` 覆盖兴趣/交通/住宿/特殊要求四项，但实际常是「兴趣用户说了、交通用了默认值」 | 无法区分哪些偏好是用户明确给的（R-11 要求记录 `USER_DEFAULT`） | 改为逐项：`interestsSource` / `transportSource` / `accommodationSource` / `specialRequirementsSource`，枚举 `USER / USER_DEFAULT / MODEL_KNOWLEDGE` |
| **Q-04** | **跨城交通没有承载结构** | `TransportSegment` 用 `fromItemOrder/toItemOrder` 关联同一天的行程项，「第 2 天早上从成都到乐山」这种**跨天、跨城**的段落放不进去 | 04 的 `t_transport_segment` 建表会缺一类数据 | 引入 `scope`：`INTRA_DAY`（同日景点间，用 from/to itemOrder）与 `INTER_CITY`（跨城，用 `fromDayIndex/toDayIndex` + `fromCity/toCity`）。M1 只落 `INTER_CITY` 的建议文案，耗时距离仍为 null |

## 二、建议按下方结论直接定（我可以自己决定，但你有一票否决）

| # | 问题 | 建议结论 |
|---|---|---|
| Q-05 | `missingFields` 没有词表，前后端无法对齐 AC-06 | 固定枚举，禁止自由文本：`originCity / destination / startDate / endDate / days / travelers / budget / interests / transport / accommodation`；不在枚举内的一律视为非法输出 |
| Q-06 | `FactField.display` 在 L0 下写什么 | 固定常量 `待核实`（天气/票价/开放时间/耗时/预算统一）；`display` 同样受数字扫描约束，不得出现「约 2 小时」「30 元」 |
| Q-07 | `startTime/endTime` 算不算编造实时信息 | 不算。它们是**用户可改的建议时间**，UI 必须标注「建议时间」；但 `duration` 为 null 时，文案不得写「游览 2 小时」（同 Q-06 规则） |
| Q-08 | `unverifiedFieldCount` 怎么算 | 定义为「所有 `FactField` 实例中 `verified=false` 的数量」，由 Java 遍历重算并与模型自报值比对，不一致即拒绝（05 文档 2.1 已有此意，这里补口径） |
| Q-09 | `attractionId` 在 M1 填不填 | M1 **恒填 null**。不建 POI 库、不做景点主数据 upsert；`t_attraction` 表先建空表占位，L1 接入 POI 工具时再用。理由：现在没有任何可信景点主数据来源，硬造 ID 等于制造脏数据 |
| Q-10 | `schemaVersion` 与 `planId` 时序 | AI 输出必须 `schemaVersion="1.0"`、`planId=null`；`planId` 仅由 Java 落库后回填。Schema 里把 `planId` 约束为 `null`（AI 侧），另出一份「存储模型」允许非空 |
| Q-11 | `status` 枚举范围 | 拆两份 Schema：`travel_plan_draft_v1.json`（AI 输出，`status` 只允许 `DRAFT`）与 `travel_plan_v1.json`（存储/展示，允许 5 个状态）。避免前端类型里出现「AI 能改状态」的错觉 |

## 三、M1 先不做，但要留口

| # | 事项 | 处理 |
|---|---|---|
| Q-12 | 天数推导：只给 `2026-05-01 ~ 2026-05-04` 时 `days` 是否必填 | Java 可推导（含首尾 = 4 天）并回填，来源记 `USER`；若 `days` 与区间矛盾（如区间 3 天但 days=5）→ 拒绝落库，提示用户澄清 |
| Q-13 | 境外行程的签证/入境提示 | 只进 `risks[]` 文本，不建结构化字段；文案必须带「以官方渠道为准」 |
| Q-14 | `expiresAt` 何时用 | M1 恒 null。L1 接天气后才需要（快照过期重取） |

## 四、冻结后的动作

1. 生成 `backend-ai/app/schemas/travel_plan_draft_v1.json`（AI 输出契约，带硬约束 `if/then`）
2. 生成 `backend-ai/app/schemas/travel_plan_v1.json`（存储/展示模型）
3. 前端 `frontend/src/types/travelPlan.ts` 与 2 保持字段一一对应
4. Java `PlanSchemaValidator` 按 05 文档 2.1 的硬约束表实现，并补 AC-07 的 50 组畸形样例
5. 在 05 文档顶部追加「已确认」记录（确认人/日期/结论），不改写历史

## 结论汇总

请按需回复，例如：「Q-01 采纳，Q-02 采纳，Q-03 采纳，Q-04 改成 X」，其余我按第二节执行。
