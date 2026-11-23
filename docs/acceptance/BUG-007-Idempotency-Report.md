# BUG-007 幂等性修复报告

> 基线：`ff24785` → 本报告对应提交见 §8
> 指令来源：PM 2026-11-23「下一阶段任务：AC-07 补充与 BUG-007 幂等性修复」九条要求
> 结论：**已实现并通过 8 类场景验证**，覆盖 `PLAN_GENERATE`；`PLAN_ADJUST` 未覆盖（§7 说明）

---

## 1. 问题与证据

05 §5「统一约定」写着「写操作支持 `clientRequestId` 幂等键，重复提交返回首次结果」，
全仓库搜不到这个字段——**契约承诺过，实现没做**。

实测（mock，3 并发相同请求）：

```
并发同一请求 3 次 → 计划数: 3
planIds: 2124783387568963585, 2124783387568963596, 2124783387631878174
```

真实模型一次生成 30–45 秒，网关重试、浏览器超时重发、用户连点的概率都不低。后果：
多出一份几乎相同的计划、多花一次模型额度、会话里多一条 `plan_saved` 导致卡片重复。

---

## 2. 设计（对应 PM 九条）

| # | 要求 | 落实 |
|---|---|---|
| 1 | 请求带幂等 ID | `POST /plans/generate` 增加 body 字段 `clientRequestId`（兼容 `X-Client-Request-Id` 头）；`POST /chat/stream` 增加同名字段——**真实入口是聊天流式，不是 /plans/generate** |
| 2 | 服务端按「用户 + 请求 ID」识别 | `t_idempotency` 唯一键 `uk_idem_user_request(user_id, request_id)`，跨用户互不影响 |
| 3 | 唯一约束保证并发 | 并发正确性**只依赖 InnoDB 唯一键**：同时插入只有一个成功，另一个吃 `DuplicateKeyException` 后回查状态 |
| 4 | 相同 ID、不同内容必须拒绝 | 请求体规范化后取 SHA-256 存 `request_hash`；哈希不符直接 `30112`，不回放旧结果 |
| 5 | 三态处理策略明确 | `PROCESSING` → `30113` 拒绝；`SUCCESS` → 回放；`FAILED` → 允许重试（条件更新抢占） |
| 6 | 成功重试返回原结果 | `result_ref` 存首次 `planId`，重复提交走 `detail(planId)` 回放，不再调模型、不再落业务表 |
| 7 | 不依赖前端防重复 | 幂等判定全在服务端；前端只是「传了 ID」，删掉前端逻辑也不影响正确性 |
| 8 | 不依赖单机 synchronized | 未使用任何进程内锁；多实例部署下同样成立 |
| 9 | 不破坏现有事务与状态机 | 幂等记录用 `REQUIRES_NEW` 独立提交（必须在调模型前可见），业务写入仍走原事务；状态机代码一行未改 |

### 2.1 为什么这张表要独立事务

通知（V7）是「跟业务同事务、失败就吞」，因为通知丢一条不影响正确性。
幂等记录相反：**写不进去就必须让请求失败**，否则重复请求会穿过去。所以：

- `insertNew` 用 `REQUIRES_NEW` 立刻提交 → 并发方马上能看到 `PROCESSING`；
- 异常在事务外层捕获 → 不把调用方事务标记成 rollback-only（否则后续更新全失效）。

### 2.2 `FAILED` 为什么不能拒绝重试

模型超时是可恢复的。如果失败也当成「已在处理」，用户就永远卡住。
所以 `FAILED` 用条件更新 `WHERE id=? AND status='FAILED'` 抢占回 `PROCESSING`：
抢到的人执行，抢不到的人得到 `30113`——既允许恢复，又不会同时跑两份。

---

## 3. 涉及改动

| 类型 | 文件 |
|---|---|
| 迁移 | `sql/migration/V8__idempotency.sql`、`sql/rollback/U8__idempotency_rollback.sql` |
| 新增模块 | `modules/idempotency/{entity,mapper,service}` |
| 服务 | `PlanService`：抽出 `generateOnce`，新增 5 参 `generate(...)` 幂等包装 |
| 接口 | `PlanController.GenerateRequest.clientRequestId`；`SendMessageRequest.clientRequestId`；`ChatController` 透传到 `runPlan` |
| 错误码 | `30112 IDEMPOTENCY_CONFLICT`、`30113 IDEMPOTENCY_PROCESSING` |
| 前端 | `api/stream.ts` 每次发送生成 UUID 并带上 |
| 测试 | `IdempotencyServiceTest`（9 项）、`IdempotencyDbTest`（6 项，真实 MySQL） |

---

## 4. 验证（PM 要求的 8 类场景，逐条对应）

| 场景 | 用例 | 结果 |
|---|---|---|
| 相同请求连续提交 10 次 | `sequentialRetriesCreateOnePlan` | 计划数 1，10 次 planId 一致 |
| 相同请求并发提交 10 次 | `concurrentRetriesCreateOnePlan` | 计划数 1，幂等记录 1 条；成功者指向同一 planId，其余明确 `30113` |
| 请求处理中再次提交 | `processingIsRejected` | `30113` |
| 成功后再次提交 | `replayAfterSuccess` / `successReplays` | 返回首次 planId，不新建 |
| 相同 ID、不同请求体 | `sameIdDifferentPayloadIsRejected` | `30112`，计划数仍为 1 |
| 处理过程中服务端异常 | `retryAfterFailureSucceeds` | 记录落 `FAILED`；同 ID 重试成功并转 `SUCCESS` |
| 请求超时后同 ID 重试 | 同上（超时表现为异常 → `FAILED` → 重试） | 通过 |
| 两个用户使用相同请求 ID | `sameRequestIdAcrossUsersIsolated` | 各自 1 份，互不影响 |

补充：`hashIsStableAndSensitive`（哈希稳定性与敏感性）、`unknownStateIsNotTreatedAsFirst`
（唯一键冲突却查不到记录时**不冒充首次**）、`blankRequestIdIsNotPersisted`（空 ID 直接抛错）。

执行命令与结果：

```
mvn -o test -Dtest='IdempotencyServiceTest,IdempotencyDbTest'
Tests run: 15, Failures: 0, Errors: 0, Skipped: 0   BUILD SUCCESS
```

数据库证据（不只看接口返回）：

```sql
SELECT COUNT(*) FROM t_travel_plan WHERE user_id=?;          -- 1
SELECT COUNT(*) FROM t_idempotency WHERE user_id=?;          -- 1
SELECT status FROM t_idempotency WHERE user_id=? AND request_id=?;  -- SUCCESS / FAILED
```

---

## 5. 接口契约变化（已同步 05 文档）

```
POST /plans/generate
POST /chat/stream
  clientRequestId?: string  // ≤64，同一次提交的所有重试必须复用
```

| 情况 | HTTP | code | 前端该怎么做 |
|---|---|---|---|
| 首次/失败后重试 | 200 | 0 | 正常渲染 |
| 已成功，重复提交 | 200 | 0 | 渲染返回的计划（与首次一致） |
| 处理中重复提交 | 200 | 30113 | 提示「正在生成，请稍候」，不要重发 |
| 同 ID 不同内容 | 200 | 30112 | 提示刷新重发（属于客户端 bug） |

---

## 6. 未做的事与理由

1. **没有对 `PLAN_ADJUST` 启用幂等**。本轮只覆盖真正会「生成整份计划 + 消耗大量模型额度」的入口。
   调整建议重复的代价是「多一条待确认建议」，用户可拒绝，不至于污染数据；
   若一并改造，需要额外定义「回放哪条建议」的语义与前端交互，容易做成半成品。
   **明确记为缺口，不做隐喻式收尾。**
2. **没有做幂等记录的过期清理**。表的增长速度是一次写操作一行，量级可忽略；
   真要清理，应基于 `create_time` 定期归档，而不是在业务路径上顺手删。
3. **前端目前每次发送都生成新 ID**。用户「再发一次」是新意图，理应新 ID；
   自动重试要复用同一 ID，等前端加自动重试时由调用方显式传入（`streamChat` 已留参数）。

---

## 7. 结论

- PM 九条要求全部落实，8 类场景全部有可执行断言；
- 并发正确性来自数据库唯一约束，不依赖单机锁，也不依赖前端；
- 没有改动状态机与既有事务边界，原有 106 项测试仍全绿；
- 缺口（`PLAN_ADJUST` 未覆盖、无过期清理）已如实列出，未计入「已完成」。
