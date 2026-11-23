-- ============================================================
-- V6 · AI 调整建议（M2 第四块，R-23 / R-24 / R-25）
--
-- 这张表存在的唯一理由是 AC-11：「AI 直接写正式计划的次数 = 0」。
-- 模型的输出落在 ops_json 里，必须先经过 Java 校验、再等用户点确认，
-- 才可能变成 t_travel_item 上的写操作。表就是那道闸门。
--
-- 为什么存 ops 而不是存整份新计划：
--   版本快照（t_plan_version）是整计划级 JSON，回滚语义是「整份替换 + 重建子表」，
--   itemId 会全部改变、进度清零。那对回滚是合适的，对日常调整是灾难——
--   用户在东京勾完上午三个景点，说了句「下午那个不去了」，确认后勾全没了。
--   所以调整走按 itemId 的增量 ops，未涉及的行程项保持原行、进度不动。
--
-- base_version 是防覆盖的锚：
--   用户 10:00 收到建议、10:05 自己手改了一处、10:06 点确认。没有这个字段，
--   AI 的建议会把 10:05 的修改静默覆盖掉，且不会报任何错。
--   确认时 base_version 必须等于计划的当前 version，否则判过期。
--
-- status 四态（04 §4.7）：PENDING → CONFIRMED / REJECTED / EXPIRED。
--   EXPIRED 不走定时任务扫描，在确认时惰性判定——计划已推进或已离开可调整状态就转过期。
--   一个从未被点开过的建议，用户也不关心它什么时候「过期」，没必要为它养一个调度器。
--
-- before 值不由模型提供：模型只给 itemId + 字段 + after，before 由 Java 从库里的
-- 当前值回填。模型抄错一位就是「差异展示与实际改的东西不一致」，这种 bug 用户看不出来。
--
-- 不建外键（与 V2/V5 一致）。
--
-- 回滚：sql/rollback/U6__plan_adjustment_rollback.sql
-- ============================================================

CREATE TABLE IF NOT EXISTS `t_plan_adjustment`
(
    `id`          BIGINT       NOT NULL COMMENT '主键（雪花 ID）',
    `plan_id`     BIGINT       NOT NULL COMMENT '所属计划',
    `user_id`     BIGINT       NOT NULL COMMENT '发起人，用于归属校验',
    `base_version` INT         NOT NULL COMMENT '生成建议时计划的 version，确认时比对，防止覆盖用户期间的修改',
    `status`      VARCHAR(16)  NOT NULL COMMENT 'PENDING / CONFIRMED / REJECTED / EXPIRED',
    `instruction` VARCHAR(500) NOT NULL COMMENT '用户原话，用于回看「我当初让它改什么」',
    `summary`     VARCHAR(200) NOT NULL COMMENT '一句话概括，列表与通知里直接用',
    `ops_json`    MEDIUMTEXT   NOT NULL COMMENT '增量操作数组，before 由服务端回填',
    `reason`      VARCHAR(200)          DEFAULT NULL COMMENT '拒绝原因或过期原因',
    `decided_at`  DATETIME              DEFAULT NULL COMMENT '确认 / 拒绝的时间',
    `create_time` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `deleted`     TINYINT      NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    KEY `idx_adjust_plan_status` (`plan_id`, `status`),
    KEY `idx_adjust_plan_created` (`plan_id`, `create_time`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci COMMENT ='AI 调整建议：AI 只写此表，用户确认后由 Java 写正式计划';
