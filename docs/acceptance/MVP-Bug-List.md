# MVP 缺陷清单

> 基线：`main` @ `2e27428`，验收期间修复后 HEAD `06b2b55`
> 分级口径：**P0** 数据/业务正确性受损、用户可见且不可恢复；**P1** 核心功能在常见输入下出错；
> **P2** 体验或测试资产缺口，不影响正确性；**P3** 观感。
> 纪律：每条缺陷先记录再修；修复必须带回归测试；不删失败的测试来「通过」。

---

## 汇总

| 编号 | 级别 | 标题 | 状态 | 提交 |
|---|---|---|---|---|
| BUG-001 | **P0** | 计划生成期间断连，整轮回答被吞 | 已修复 | `a676cd2` |
| BUG-002 | **P1** | 「想去云南」被整段当成城市名，整份计划目的地错误 | 已修复 | `06b2b55` |
| BUG-003 | P2 | Schema 畸形样例只有 10 组，验收目标 50 组 | 已修复（10+44=54 组） | 见 BUG-007 同批提交 |
| BUG-004 | P2 | 「74 个字段待核实」直接堆在卡片上，无解释 | **已修复** | 本轮（见下方 §BUG-004） |
| BUG-005 | P3 | 顶栏铃铛用 emoji，浅色底上对比度低 | **已修复** | 本轮（见下方 §BUG-005） |
| BUG-006 | P2 | 真实模型全链路未验证（外部阻塞） | 已解除（DeepSeek 已跑通；额度耗尽属运维，不是缺陷） | — |
| BUG-007 | P2 | `clientRequestId` 幂等键契约承诺过、实现没做 | **已修复** | 见 `BUG-007-Idempotency-Report.md` |
| BUG-008 | P1 | 真实模型漏写 FactField 的可空键（`fetchedAt`），整份计划被拒 | **已修复**（提示词加固） | — |
| BUG-009 | P1 | 真实模型长输出偶发 JSON 非法/截断 | **已修复归因**（`finish_reason` 抓取 + 分型提示），成功率仍受上限约束 | 见下方 §BUG-009 |
| BUG-011 | P2 | 失败回复刷新后成空白气泡（`error_message` 只落库、不外传） | **已修复** | `MessageVO.errorMessage` + 前端 error 映射 |
| BUG-012 | P1 | SSE 连接上限 240s 短于 AI 等待上限 300s，长尾会被「连接先断」误杀 | **已修复** | 本轮：由配置推导 `max(plan,read)+30s` |
| BUG-013 | P1 | 生成超时硬编码 180s，交融网关的 7 天行程 130–160s 长尾被判成「服务宕机」 | **已修复** | `01379cc`：三个超时全部配置化 |
| BUG-014 | P2 | 校验器要求跨城段 `fromDayIndex < toDayIndex`，提示词没写，模型输出 3→3 被整份拒绝 | **已修复** | 本轮：规则 6/7/2 补齐（见下方） |
| BUG-015 | P2 | 本地 `.env` 让单测真的调模型：2 条用例失败，且每次跑测试都在花钱 | **已修复** | 本轮：`tests/conftest.py` 强制 mock（见下方） |
| BUG-016 | **P1** | 提示词与校验器各写各的：同源检查一上线就查出 **Q-12 从未写进提示词** | **已修复**（结构性解法） | 本轮：`contracts/travel-plan/plan_rules_v1.json` + 两侧同源用例（见下方） |
| BUG-017 | P2 | `IdempotencyServiceTest` 依赖执行顺序：单独跑必红（MyBatis-Plus Lambda 缓存未初始化） | **已记录，未修**（等决定是否本轮处理） | 见下方 §BUG-017 |

---

## BUG-001（P0，已修复）计划生成期间断连，整轮回答被吞

**现象**

真实模型生成一份计划要 30–45 秒，用户中途关页面是常态。断连后：

- 计划已经提交到库（`t_travel_plan` + 9 张子表都写完了）；
- 聊天记录里只有用户自己的提问，**助手一条消息都没有**，连 FAILED 都没有。

用户刷新后看到：自己的问题孤零零挂着，回答凭空消失，而「我的旅行」里莫名多出一份计划。

**根因**

`ChatController.runPlan` 在 `planService.generate(...)` 之后：

```java
if (canceled.get()) {
    return;          // ← 什么都不写
}
```

`canceled` 由 `SseEmitter.onError` 置位——客户端断开时容器会触发它。于是「生成成功」这条路径
被一个裸 `return` 短路掉了，落库语句根本没执行。

同类问题还有一处：`catch` 只覆盖 `BizException`，其余异常（落库后的空指针、序列化失败）
会穿到外层统一处理，同样不留消息。

**为什么单测和功能脚本没抓到**

它们都跑「正常路径」：请求、拿到事件、结束。没有人模拟「中途把连接掐掉」。

**修复**（`a676cd2`）

1. 断连分支改为**落一条 `CANCELED`**，内容用计划摘要——计划确实生成了，用户回来要能看见；
2. 补 `catch (Exception)`：未预料异常落 `FAILED`，不再让这一轮静默消失；
3. 加 `settled` 标记，避免 `SUCCESS` 之后又被兜底补一条 `FAILED`；
4. `runPlan` 由 `private` 改为包级可见，仅为了能直接测三条出口（无逻辑变更）。

**回归测试**：`ChatPlanAbortTest` 3 项（断连 / 异常 / 正常各一条出口）。
**验收复测**：AC-04 从 FAIL（助手消息为空）→ PASS（`CANCELED`，且确认抓到了真实断连窗口）。

**遗留判断**：断连后**不删已生成的计划**。计划是用户要的东西，删掉才是二次伤害；
正确做法是记下来并告诉他「计划已生成并保存」。

---

## BUG-002（P1，已修复）「想去云南」被整段当成城市名

**现象**

在聊天框输入 `想去云南玩五天`（就是输入框占位提示的句式去掉主语「我」），得到：

- 标题《想去云南5日行程》
- 目的地「想去云南」
- 第 1~5 天的城市全是「想去云南」

**根因**

`backend-ai/app/plan/intent.py` 的 `_INTENT_PREFIX` 覆盖了「我想」「想要」「打算」「准备」，
但没有「想去 / 我要 / 我要去」。前缀没被剥掉，城市正则 `[\u4e00-\u9fa5]{2,8}` 就把
「想去云南」整段吃了进去。

**影响**

不是显示问题：`destination` 会写进计划、写进每天的城市、写进版本快照，
后续的确认、调整、回滚全都建立在错的目的地上。

**修复**（`06b2b55`）

前缀补 `想要/想去/我要/我们要`，并按「长写法在前」排序（正则按先后匹配）；
循环剥离本身就能把「我要去三亚」拆成 `我要 → 去 → 三亚`。

**回归测试**：`test_intent_colloquial_plan_request` 增补 3 组（10→13），并全量回归 26 条语料 26/26。
**验收复测**：AC-05 从 19/20（95%）→ 20/20（100%）；浏览器实机确认卡片变为《云南5日行程》。

---

## BUG-003（P2，未修复）Schema 畸形样例只有 10 组

**现象**：`contracts/travel-plan/examples/invalid_cases.json` 只有 10 组变异，02 §6 要求「注入 50 组」。

**影响**：AC-07 只能记 PARTIAL。不是校验器弱（10 组全部被拒，Java/Python 两侧另有 31 项断言），
而是**测试资产的样本量**没达标。

**建议**：把 10 组变异分别施加到多份合法基线上，凑够 50 组并纳入 CI。约半天工作量。

---

## BUG-004（P2，已修复）「74 个字段待核实」直接堆在卡片上

**现象**：计划卡片上有一行「74 个字段待核实：未接入实时天气、票价与路况，请以实际为准」。

**问题**：mock 计划几乎每个 FactField 都待核实，数字很大；真实模型会少一些，但同样会出现在首屏。
「74 个字段」是内部计量单位，用户看到的是「这软件什么都不知道」。

**修法（按「数字留在详情页、卡片只说类别」）**

| 位置 | 改前 | 改后 |
|---|---|---|
| 对话里的计划卡片 | `74 个字段待核实：未接入…` | `部分信息待核实（行程安排 · 预算）：天气、票价与路况尚未接入实时数据` |
| 我的旅行列表 | `74 个字段待核实` | `部分信息待核实`（hover 说明），数字只在详情页出现 |
| 计划详情页 | `74 个字段待核实` + 一句原因 | `共 48 处信息待核实` + 原因 + **「按类别看是哪几处」** |

详情页展开后按四类列：`行程安排 42 处 · 宽窄巷子街区漫步… 等 12 项`、`预算 6 处 · 交通、住宿、餐饮 等 6 项`。

**三个口径决定**

1. **分类是展示层派生，不改真相来源。** 权威总数仍是服务端 `unverifiedFieldCount`（08 S-5 决议），
   `src/utils/planFacts.ts` 只把计划结构里 `verified=false` 的 FactField 归类，用来解释这个数字；
   两侧对不上时不修数字，分组只是「让数字可被理解」。
2. **例子按对象去重。** 一个行程项的时长/开放时间/门票属于同一处安排，逐字段列会读成
   「宽窄巷子 · 时长、宽窄巷子 · 开放时间……」，等于没说。所以按对象聚合，并给「等 N 项」。
3. **未知位置进「其他」而不是丢掉。** 契约以后加天气等字段，这里不改代码也不会漏算。

**回归测试**：`frontend/scripts/plan-facts.test.mjs`（16 项，含计数、分组顺序、去重、缺失 `verified` 不算、未知位置归「其他」）。

---

## BUG-005（P3，已修复）顶栏铃铛对比度低

**现象**：铃铛用 emoji 🔔 渲染，在当前浅色顶栏上偏暗、体量偏小，有未读时靠红点才注意到。

**修法**：换成 18px 内联 SVG 描边图标，`currentColor` 取 `#5b6572`（与「服务正常」胶囊同一灰阶），
有未读时提亮为品牌色；hover 同时变色 + 浅底。按钮尺寸 26×26 固定，
红点定位沿用原有 `badge`（不再受 emoji 字体基线影响而抖动）。

**验证**：内置浏览器实测顶栏（818px 宽），铃铛与胶囊视觉权重一致，红点 64 不压字。

---

## BUG-006（P2，BLOCKED）真实模型全链路未验证

**现象**：模型网关返回 `HTTP 200` + `{"code":400,"message":"invalid apikey: sk-***"}`，key 已失效。

**影响**：所有涉及真实模型质量的结论都无法给出（生成质量、ops 质量、耗时分布）。
Mock 下跑通只能证明「链路通」，不能证明「模型行」。

**说明**：这不是代码缺陷，是外部阻塞。处理方式见 `MVP-Final-Release-Report.md` §2。

---

## 附：验收期间观察到但**不算缺陷**的行为

| 现象 | 判断 |
|---|---|
| 断连后计划仍保存在「我的旅行」里 | 有意为之：计划是用户要的结果，删掉才是二次伤害 |
| 从通知跳进详情页后点「返回」回到上一页（可能是聊天页） | 浏览器语义，符合预期 |
| 标记完成/开始不产生通知，勾选行程项也不产生 | 08 §8 S-8 已冻结的取舍，不是漏做 |
| 计数类接口必须用 `Integer` 而非 `Long` | 全局 Long→String 序列化规则的必然结果，已在代码里注明 |

---

## BUG-009（P1，已修复归因）真实模型长输出偶发非法 JSON / 截断

**现象**：真实模型复验 20 次里 3 次没拿到计划，日志只有 `plan_rejected{INVALID_FORMAT}`
和一句「JSON 解析失败」，看不出是被上限截断还是模型写坏了。

**根因**：`OpenAICompatibleProvider` 从头到尾没读过 `finish_reason`。
只能靠"末尾有没有 `}`"反推，这在两种情况下都会误判：
输出刚好在 `}` 处被截断（判成格式错），以及模型把 JSON 写坏但括号齐全（判成截断）。

**修复**（不违反 05 §3「不自动修复、不自动重试」的冻结决议）：

1. `stream_chat` 记录 `last_finish_reason`，`choices[0].finish_reason` 与顶层字段两处都取；
2. `extract_json(text, finish_reason)` 以 `finish_reason=length` 为 `TRUNCATED` 的权威判据；
3. `plan_rejected` 透出 `finishReason`；`TRUNCATED` 的提示改成
   「行程太长，输出被截断，请把天数说少一点或分批规划」，与格式错的「请重试」分开；
4. **仍然不重试**——重试一次就是再烧一次额度，且会把真实失败率抹平。

**验证**：`tests/test_plan.py` 4 项（截断分型、括号齐全的格式错、截断不发 draft、
`finishReason` 透传）+ `tests/test_provider_errors.py` 1 项（从 SSE 抓 `finish_reason`）。

**仍未解决的部分**（如实记）：这条修复让**失败可解释**，没有让**成功率变高**。
20 次里 3 次失败，如果确认是 `length`，解法是调大 `LLM_MAX_TOKENS` 或按天分段生成；
如果确认是 `stop`，那是模型本身稳定性问题，只能靠换模型或降低单次输出长度。
结论要等 key 恢复后重跑才能给出。

---

## BUG-012（P1，已修复）SSE 连接上限短于 AI 等待上限

**现象**（未等到线上表现，靠对配置发现的）：`ChatController` 里是
`new SseEmitter(240_000L)`，而 `AI_READ_TIMEOUT_MS` / `AI_PLAN_TIMEOUT_MS` 已经是 `300000`。

**为什么这是 P1**：两个值反向时，**最长的那批请求**会在 240s 被连接超时掐断，
而 Java 侧的 AI 等待还在继续。等模型 250s 返回后，计划正常校验、正常落库，
只是没人能收到——`canceled=true`，于是按 CANCELED 补记一条「连接中断，计划已生成并保存」。

结果是最坏的一种组合：**用户看到失败，数据却真的写进去了**。他重试就多出一份重复计划，
查问题时又会被「计划明明在」搞糊涂。这类「假失败」比真失败更难排查。

**根因不是笔误，是两处各写各的**：AI 超时从 180s 提到 300s（BUG-009 之后的事），
240s 是当年为了「大于 180s」挑的，没人把它和配置绑在一起；注释里还写着「必须大于 180s」，
改配置的人照着注释看反而以为没问题。

**修复**：

```java
long sseTimeoutMs() {
    long aiWait = Math.max(aiProperties.getPlanTimeoutMs(), aiProperties.getReadTimeoutMs());
    return aiWait + 30_000L;
}
```

- 取 `max(plan, read)` 而不是某一个：生成与流式两条路径都可能最长；
- +30s 余量留给落库、序列化与最后一帧；
- 配置再调大时，连接上限自动跟着走，不会再出现这种「改了 A 忘了 B」。

**回归测试**：`ChatPlanAbortTest#sseTimeoutOutlivesAiTimeout` 直接断言
`sseTimeoutMs() > planTimeoutMs` 且 `> readTimeoutMs`。这条测试的价值不在算术，
而在于**把「必须大于」从注释里搬进断言**——以前它只是句注释，所以会失效。

**顺带修正**：`05-接口与AI输出契约` 的超时表原来写「SSE emitter 340s 兜底」，
代码却是 240s。现在按实现改为 330s，并注明是推导值，避免文档再次和代码各说一套。

---

## BUG-014（P2，已修复）「跨城段必须 fromDayIndex < toDayIndex」只在校验器里，提示词没写

**怎么发现的**：上线模型 `jiaorong-deepseek-v4-pro` 跑完整 20 条生成，唯一一次失败是
`去西宁玩五天，想看看青海湖`（188s，`plan_rejected: SCHEMA_VIOLATION`）。查 `t_ai_generation_log`：

```
S-6 /days/2/transportSegments/0 : 跨城段必须 fromDayIndex < toDayIndex，实际 3 → 3
S-6 /days/2/transportSegments/1 : 跨城段必须 fromDayIndex < toDayIndex，实际 3 → 3
```

模型在「第 3 天」内部写了两条跨城段（西宁 ↔ 青海湖方向），`from` 和 `to` 都填 3。
按契约这是错的（跨城是「第 X 天结束 → 第 Y 天开始」），但**模型没有渠道知道**：
提示词规则 7 只说了跨城段要填哪些字段，没说 `from` 必须小于 `to`。

**这是第二类系统性缺陷**（第一类是 BUG-008 的 `fetchedAt` 缺失）：**校验器有规则、提示词没同步**。
校验器拒绝得越严格，模型就越容易踩到没被告知的红线，而且从用户视角看只是「这软件有时生成失败」。

**修复**（`prompts.py` 规则 2 / 6 / 7，均为把校验器已有约束写成模型能执行的话）：

| 校验器约束 | 原提示词 | 现在 |
|---|---|---|
| S-6 `fromDayIndex < toDayIndex` | 未提及 | 明确写出方向，并给出「同一天不是跨城，请改 INTRA_DAY 或不写」 |
| S-3 `destinations[0] == destination` | 只说「城市必须在 destinations 里」 | 补「`destinations[0]` 必须与 destination 逐字相同」 |
| S-1 `basic.days == days.length` | 只说 dayIndex 连续 | 补「按实际写出的天数填，不是按用户口头说的天数」 |

**验证**：用失败原句 `去西宁玩五天，想看看青海湖` 在真实链路复跑 **3/3 全部通过**
（102s / 170s / 165s）。n=3 只能说明「这一类不再必挂」，不能说明失败率归零——
下一轮 20 条复验才会给出新数字。

**遗留判断**：提示词与校验器的漂移是结构性问题，靠「这次记得同步」不解决。
→ **已由 BUG-016 结构性解决**：规则抽出为 `contracts/travel-plan/plan_rules_v1.json`，
提示词由它渲染，CI 三向交叉验证。

---

## BUG-015（P2，已修复）本地 `.env` 让单测真的去调模型

**怎么发现的**：写 CI 本地脚本时「顺手」把 `.env` 加载进来（想复用数据库口令），
结果 `pytest` 立刻红了 2 条：

```
FAILED tests/test_adjust.py::test_endpoint_returns_ops_for_mock_provider
FAILED tests/test_plan.py::test_plan_stream_sse - assert False is True
```

**根因**：`app/config.py` 的优先级是「环境变量 > config.yaml」，而测试模块里写的是
`os.environ.setdefault("LLM_PROVIDER", "mock")` —— `setdefault` 遇到已经存在的
`LLM_PROVIDER=openai_compatible` 就什么都不做。于是：

- 单元测试真的发 HTTP 到交融网关（跑一次 102 秒，之前 0.7 秒，就是因为**在等模型**）；
- 断言 `mock: true` 的两条用例必然失败；
- 更要紧的是：**每次跑单测都在消耗公司额度**，而且没人知道。

CI 里没有 `.env`，所以一直显示绿的——「本地红、CI 绿」的镜像问题，比反过来更难发现。

**修复**：新增 `backend-ai/tests/conftest.py`，在**任何测试模块导入 `app.main` 之前**
把 `LLM_PROVIDER` 强制写回 `mock`（并清空 key、把 base_url 指到不可达地址）。
选「强制」而不是 `setdefault`：测试不该因为开发机配了真实 key 就改变行为。

**验证**：`bash scripts/ci-local.sh --fast` 全绿，pytest 回到 **0.7 秒**（真调模型时 102 秒，
这个耗时差本身就是判据）。需要真实 provider 的用例（provider 错误分型）自己构造对象显式传参，
不依赖环境变量。

**顺带的产物**：这一轮同时落了 `.github/workflows/ci.yml` 与 `scripts/ci-local.sh`，
两处跑的是同一组命令；CI 用 MySQL service 容器跑集成测试，不跑真实模型（密钥不进 CI）。

---

## BUG-016（P1，已修复）提示词与校验器各写各的：同源检查一上，立刻查出第三条漏写

**怎么发现的**：修完 BUG-008（`fetchedAt` 漏写）和 BUG-014（S-6 没写）之后，两次都是
「发现一次、补一次提示词」。这明显不对——**同一种缺陷连着犯两次，说明流程里缺一道检查**。
于是先把规则当成数据：数一遍校验器到底会抛哪些 code，再逐条去提示词里找有没有对应的话。

结果第三条当场露出来：**Q-12**（日期与天数一致性）在 `PlanSchemaValidator.checkDateConsistency`
里执行，提示词从头到尾**一个字都没提**。规则 6 只说 dayIndex 和 basic.days，没说
`days[i].date` 必须等于 `startDate + i`。模型若把第 2 天写成 `startDate + 2`，整份计划被拒，
而它完全无从知道。同类缺陷，只是这次还没在实跑里撞上（20 条样本里恰好没触发）。

**根因**：约束的**所有权分散在两个仓库的两个语言里**——
Java 校验器拥有「什么算合法」，Python 提示词拥有「模型知道什么」，两者之间没有任何机器可验证的联系。
靠人记，就必然漏；而漏掉的代价是「用户偶发看到生成失败」，日志里只有一行 `SCHEMA_VIOLATION`。

**修复**：把规则抽成机器可读清单，两侧都从它读，并让 CI 在三个方向上互相咬住。

| 产物 | 作用 |
|---|---|
| `contracts/travel-plan/plan_rules_v1.json` | 唯一来源：16 条规则，每条含 `id` / 编号 / 是否由校验器执行 / 覆盖哪些校验器 `code` / 提示词原文 / 为什么这么写 |
| `backend-ai/app/plan/rules.py` | 渲染提示词的「硬性规则」整段。**渲染结果与改造前逐字节相同**（规则 1–10 一致），只新增规则 11（Q-12）——所以这次改动不是「重写提示词」，是「把已有提示词搬进数据文件」 |
| `backend-ai/tests/test_plan_prompt_sync.py`（6 项） | ① 提示词里的那一段必须等于渲染结果（堵住「绕过 JSON 手改提示词」）；② 清单声明的 code 必须在 Java 里真的抛过；③ **Java 抛的 code 必须在清单里有条目**（就是本条缺陷）；④ 每个 code 必须有反例 |
| `backend-java/.../PlanRuleCoverageTest.java`（3 项） | 每条规则都要有一条以 code 开头的畸形样例，且该样例**确实被这个 code 拒绝**；`enforced_by=validator` 的规则必须绑了 code |

**验证「检查真的会响」**（不然只是多写了几个绿测试）：临时把 Q-12 条目从清单里删掉，跑一遍：

```
FAILED tests/test_plan_prompt_sync.py::test_no_java_rule_left_out_of_the_contract
AssertionError: 这些校验规则在 Java 里执行，但 plan_rules_v1.json 没有对应条目：['Q-12']。
提示词因此不会提到它们 —— 这就是 BUG-014 的成因...
```

**顺带修好的三个隐患**（都是被新检查逼出来的，不是顺手改）：

1. **S-3 的反例一直是假绿**。`S-3 destinations[0] 不等于 destination` 这组变异把
   `destinations[0]` 改成了另一个已有城市，结果先撞上 Schema 的 `uniqueItems`（重复项），
   被结构层拦下——五十组「全部被拒」是真的，但**没测到 S-3**。改成交换 0/1 位后 S-3 才真正命中。
2. **S-6 没有任何反例**。BUG-014 之所以能一路绿到实跑，是因为 50 组变异里没有一条碰过跨城段天序号。
   已补第 51 组。
3. 两处 `enforced_by` 标错（预算分类、travelers 判定），被新用例当场判红。

**验证（实跑）**：提示词新增了规则 11，属于「模型可见的输入变了」，必须复跑。
见 `MVP-LLM-Quality-Report.md` §10。

**遗留**：清单只覆盖「跨字段硬约束」。字段级约束仍由 JSON Schema 独占（这是对的，
Schema 已经有 schema 校验器在两侧各跑一遍）。真正的收益是：**以后加规则，忘写提示词会红在 CI，
而不是红在用户那里**。

---

## BUG-017（P2，已记录未修）`IdempotencyServiceTest` 只在特定执行顺序下通过

**怎么发现的**：M3-1 做完后跑 `mvn test`，全量 149 条里红 2 条，都在幂等测试里：

```
[ERROR] IdempotencyServiceTest.failedCanRetry:102
        MybatisPlusException: can not find lambda cache for this entity [IdempotencyRecord]
[ERROR] IdempotencyServiceTest.failedRetryLosesRace:114
        Unexpected exception type thrown, expected: BizException but was: MybatisPlusException
```

**先做的一件事**：确认是不是本轮改坏的。做法是 `git stash -u` 把天气相关改动全部收走，
在干净树上单跑这一个测试类 → **一样红**（报告文件时间戳可证）。所以与本轮无关。
天气代码里没有任何 mapper、没有改 MyBatis 配置，也不参与这条路径。

**原因**：`IdempotencyService.resolveExisting` 用了 `Wrappers.<IdempotencyRecord>lambdaUpdate()`，
Lambda 条件构造器需要 MyBatis-Plus 的 `TableInfo` 静态缓存；这份缓存在**扫到 mapper 的 Spring
上下文启动时**才建立。而 `IdempotencyServiceTest` 是纯 Mockito 单测（`mock(IdempotencyMapper.class)`），
自己不会初始化它。javadoc 里提到的 `IdempotencyDbTest` 是唯一会建缓存的用例，
它需要真实 MySQL——本机没库时**整类被 skip**，于是缓存永远不存在。

**表现**：单跑红、全跑可能绿（取决于 `IdempotencyDbTest` 有没有先跑且没被 skip）。
这类「靠顺序绿」的测试比红更危险：它会让「本地红 / CI 绿」再出现一次（与 BUG-015 同一形态）。

**建议修法**（未实施，等定范围）：测试类加一段 `@BeforeAll` 显式初始化实体元信息，
不依赖上下文：

```java
@BeforeAll
static void initTableInfo() {
    TableInfoHelper.initTableInfo(
        new MapperBuilderAssistant(new MybatisConfiguration(), ""), IdempotencyRecord.class);
}
```

**为什么不顺手改**：M3-1 的范围被 PM 限定在「天气只读展示」，测试基建属于另一条线，
顺手改会让这轮的 diff 说不清责任。已登记，等排期。
