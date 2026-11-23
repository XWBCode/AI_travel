-- ============================================================
-- V7 · 站内通知（M2 第五块，R-30）
--
-- 通知是「给用户看的事件日志」，不是业务状态本身：删掉它不影响任何计划。
-- 所以它单独成表，且不参与任何状态判断——把「用户有没有看过某条消息」混进业务表，
-- 迟早会出现「因为通知没读所以计划改不了」这类荒谬逻辑。
--
-- read_at 用时间戳而不是布尔：将来要统计「多久才看到」时不用再加列，
-- 而 null 与「1970 年读过」在语义上本来就是两件事。
--
-- 记录范围只有一条判据：**这件事有没有让计划本身发生变化**。
--   进：确认 / 开始 / 完成 / 取消（状态迁移）、确认一条调整建议（版本前进）。
--   不进：勾选行程项（不产生版本，计划内容没变，而且每天十几次，进来只会把真正的三两件事淹掉）；
--         生成调整建议（计划没变，只是多了个待办，而且用户此刻正盯着那个弹窗）。
-- 等接入协作（多人看同一计划）时，同样的表按 actor≠recipient 生成即可，结构不用改。
-- 这条取舍记在 08 §8 的修订记录里，不是漏做。
--
-- 回滚：sql/rollback/U7__notification_rollback.sql
-- ============================================================

CREATE TABLE IF NOT EXISTS `t_notification`
(
    `id`          BIGINT       NOT NULL COMMENT '主键（雪花 ID）',
    `user_id`     BIGINT       NOT NULL COMMENT '接收人',
    `plan_id`     BIGINT                DEFAULT NULL COMMENT '关联计划，可空（系统类通知）',
    `type`        VARCHAR(32)  NOT NULL COMMENT 'PLAN_CONFIRMED / PLAN_STARTED / PLAN_COMPLETED / PLAN_CANCELLED / ADJUSTMENT_CONFIRMED',
    `title`       VARCHAR(120) NOT NULL COMMENT '一句话标题',
    `body`        VARCHAR(300)          DEFAULT NULL COMMENT '补充说明',
    `ref_id`      BIGINT                DEFAULT NULL COMMENT '关联对象 ID，例如 adjustment_id',
    `read_at`     DATETIME              DEFAULT NULL COMMENT '已读时间；NULL 表示未读',
    `create_time` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `deleted`     TINYINT      NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    KEY `idx_notify_user_created` (`user_id`, `create_time`),
    KEY `idx_notify_user_read` (`user_id`, `read_at`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci COMMENT ='站内通知：只影响展示，不参与业务状态判断';
