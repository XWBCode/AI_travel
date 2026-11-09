-- ============================================================
-- V2 · M1 计划相关表（9 张）
--
-- 依据：docs/design/04-数据模型设计.md（ER 与字段说明）
--       docs/design/08-TravelPlan-v1-冻结决议.md（Q-01 destinations、Q-04 scope）
-- 环境：MySQL 8.0+
--
-- 约定（沿用 01_schema.sql）：
--   1) 主键 BIGINT 雪花 ID，由 MyBatis-Plus ASSIGN_ID 生成
--   2) 审计字段 create_time / update_time / deleted
--   3) 唯一索引包含 deleted，逻辑删除后可复用业务键
--   4) 不使用外键约束，关联完整性由应用层事务保证（与现有 t_chat_message 一致）
--   5) 全部 CREATE TABLE IF NOT EXISTS，可重复执行
--   6) JSON 列不设 DEFAULT（MySQL 8 限制），缺失即 NULL
--
-- 回滚：sql/rollback/U2__plan_tables_rollback.sql（需人工执行，会删表）
-- ============================================================

-- ----------------------------
-- 1. 计划主表
-- ----------------------------
CREATE TABLE IF NOT EXISTS `t_travel_plan`
(
    `id`                     BIGINT       NOT NULL COMMENT '主键',
    `user_id`                BIGINT       NOT NULL COMMENT '归属用户，越权校验依据',
    `title`                  VARCHAR(100) NOT NULL COMMENT '计划标题',
    `status`                 VARCHAR(20)  NOT NULL DEFAULT 'DRAFT' COMMENT 'DRAFT/CONFIRMED/IN_PROGRESS/COMPLETED/CANCELLED',
    `origin_city`            VARCHAR(64)           DEFAULT NULL COMMENT '出发城市',
    `destination`            VARCHAR(64)           DEFAULT NULL COMMENT '主目的地（列表页筛选用）',
    `destinations`           JSON NULL COMMENT 'Q-01 全部目的地数组，[0] 必须等于 destination',
    `start_date`             DATE                  DEFAULT NULL,
    `end_date`               DATE                  DEFAULT NULL,
    `days`                   INT                   DEFAULT NULL COMMENT '天数，未知存 NULL，禁止填 0 冒充',
    `travelers`              INT                   DEFAULT NULL,
    `budget`                 JSON NULL COMMENT '总额 + 币种 + FactField',
    `preferences`            JSON NULL COMMENT '兴趣/交通/住宿/特殊要求 + 四项 *Source',
    `risks`                  JSON NULL COMMENT '注意事项文本数组',
    `ai_note`                VARCHAR(1000)         DEFAULT NULL COMMENT 'AI 生成说明（纯文本，非 Markdown）',
    `unverified_field_count` INT          NOT NULL DEFAULT 0 COMMENT 'verified=false 的 FactField 数量',
    `version`                INT          NOT NULL DEFAULT 0 COMMENT '乐观锁版本号',
    `confirmed_at`           DATETIME              DEFAULT NULL,
    `started_at`             DATETIME              DEFAULT NULL,
    `completed_at`           DATETIME              DEFAULT NULL,
    `create_time`            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time`            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `deleted`                TINYINT      NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    KEY `idx_user_status` (`user_id`, `deleted`, `status`),
    KEY `idx_user_time` (`user_id`, `deleted`, `update_time`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='旅行计划主表';

-- ----------------------------
-- 2. 计划按天拆分
-- ----------------------------
CREATE TABLE IF NOT EXISTS `t_travel_day`
(
    `id`          BIGINT      NOT NULL COMMENT '主键',
    `plan_id`     BIGINT      NOT NULL,
    `day_index`   INT         NOT NULL COMMENT '从 1 开始连续递增',
    `day_date`    DATE                 DEFAULT NULL,
    `city`        VARCHAR(64) NOT NULL,
    `theme`       VARCHAR(64)          DEFAULT NULL,
    `create_time` DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `deleted`     TINYINT     NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_plan_day` (`plan_id`, `day_index`, `deleted`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='计划每日行程';

-- ----------------------------
-- 3. 行程项
-- ----------------------------
CREATE TABLE IF NOT EXISTS `t_travel_item`
(
    `id`            BIGINT       NOT NULL COMMENT '主键',
    `day_id`        BIGINT       NOT NULL,
    `attraction_id` BIGINT                DEFAULT NULL COMMENT 'Q-09：M1 恒为 NULL，接入 POI 后填写',
    `item_order`    INT          NOT NULL COMMENT '日内顺序，从 1 开始',
    `title`         VARCHAR(100) NOT NULL COMMENT '允许覆盖景点名，用户可自定义',
    `start_time`    TIME                  DEFAULT NULL COMMENT '建议时间，非实时数据',
    `end_time`      TIME                  DEFAULT NULL COMMENT '建议时间，非实时数据',
    `duration`      JSON NULL COMMENT 'FactField<number>，L0 恒为 null',
    `open_hours`    JSON NULL COMMENT 'FactField<string>，L0 恒为 null',
    `ticket_price`  JSON NULL COMMENT 'FactField<number>，L0 恒为 null',
    `note`          VARCHAR(500)          DEFAULT NULL,
    `create_time`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `deleted`       TINYINT      NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_day_item` (`day_id`, `item_order`, `deleted`),
    KEY `idx_attraction` (`attraction_id`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='行程项';

-- ----------------------------
-- 4. 景点主数据（M1 先建空表占位，不 upsert）
-- ----------------------------
CREATE TABLE IF NOT EXISTS `t_attraction`
(
    `id`           BIGINT       NOT NULL COMMENT '主键',
    `name`         VARCHAR(100) NOT NULL,
    `city`         VARCHAR(64)  NOT NULL,
    `region`       VARCHAR(64)           DEFAULT NULL COMMENT '区县/区域',
    `open_hours`   JSON NULL COMMENT 'FactField<string>',
    `ticket_price` JSON NULL COMMENT 'FactField<number>',
    `source`       VARCHAR(20)  NOT NULL DEFAULT 'NONE' COMMENT 'NONE/MODEL_KNOWLEDGE/USER/TOOL/CACHE',
    `create_time`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `deleted`      TINYINT      NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_city_name` (`city`, `name`, `deleted`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='景点主数据（轻量版）';

-- ----------------------------
-- 5. 交通段（Q-04 引入 scope，两类字段互斥）
-- ----------------------------
CREATE TABLE IF NOT EXISTS `t_transport_segment`
(
    `id`             BIGINT      NOT NULL COMMENT '主键',
    `plan_id`        BIGINT      NOT NULL,
    `day_id`         BIGINT               DEFAULT NULL COMMENT 'INTRA_DAY 时非空，INTER_CITY 时为 NULL',
    `scope`          VARCHAR(16) NOT NULL DEFAULT 'INTRA_DAY' COMMENT 'INTRA_DAY/INTER_CITY',
    `from_item_id`   BIGINT               DEFAULT NULL,
    `to_item_id`     BIGINT               DEFAULT NULL,
    `from_day_index` INT                  DEFAULT NULL COMMENT '跨城时用',
    `to_day_index`   INT                  DEFAULT NULL COMMENT '跨城时用',
    `from_city`      VARCHAR(64)          DEFAULT NULL COMMENT '跨城时用',
    `to_city`        VARCHAR(64)          DEFAULT NULL COMMENT '跨城时用',
    `mode`           VARCHAR(32)          DEFAULT NULL COMMENT '步行/公交/城际铁路等建议',
    `duration`       JSON NULL COMMENT 'FactField<number>，L0 恒为 null',
    `distance`       JSON NULL COMMENT 'FactField<number>，L0 恒为 null',
    `advice`         VARCHAR(500)         DEFAULT NULL COMMENT '不得含具体数值',
    `create_time`    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time`    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `deleted`        TINYINT     NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    KEY `idx_plan_scope` (`plan_id`, `scope`, `deleted`),
    KEY `idx_day` (`day_id`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='交通段';

-- ----------------------------
-- 6. 预算明细
-- ----------------------------
CREATE TABLE IF NOT EXISTS `t_budget_item`
(
    `id`          BIGINT      NOT NULL COMMENT '主键',
    `plan_id`     BIGINT      NOT NULL,
    `category`    VARCHAR(20) NOT NULL COMMENT 'TRANSPORT/ACCOMMODATION/FOOD/TICKET/OTHER',
    `label`       VARCHAR(32) NOT NULL,
    `amount`      JSON NULL COMMENT 'FactField<number>，L0 恒为 null',
    `note`        VARCHAR(500)         DEFAULT NULL COMMENT '区间口径只写这里，禁数字',
    `source`      VARCHAR(20)          DEFAULT NULL COMMENT '与 FactField.source 冗余，便于筛选',
    `create_time` DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `deleted`     TINYINT     NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    KEY `idx_plan` (`plan_id`, `deleted`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='预算明细';

-- ----------------------------
-- 7. 住宿建议
-- ----------------------------
CREATE TABLE IF NOT EXISTS `t_accommodation`
(
    `id`          BIGINT       NOT NULL COMMENT '主键',
    `plan_id`     BIGINT       NOT NULL,
    `day_id`      BIGINT                DEFAULT NULL COMMENT '按天推荐时可填',
    `area_name`   VARCHAR(64)  NOT NULL,
    `suggestion`  VARCHAR(200)          DEFAULT NULL,
    `price_range` JSON NULL COMMENT 'FactField<string>，L0 恒为 null',
    `source`      VARCHAR(20)           DEFAULT NULL,
    `create_time` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `deleted`     TINYINT      NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    KEY `idx_plan` (`plan_id`, `deleted`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='住宿建议';

-- ----------------------------
-- 8. 计划版本快照（D-05 采用整计划级 JSON 快照）
-- ----------------------------
CREATE TABLE IF NOT EXISTS `t_plan_version`
(
    `id`             BIGINT       NOT NULL COMMENT '主键',
    `plan_id`        BIGINT       NOT NULL,
    `version_no`     INT          NOT NULL COMMENT '与 t_travel_plan.version 保持一致，单调递增',
    `snapshot_json`  JSON         NOT NULL COMMENT '写入前必须通过计划 Schema 校验',
    `change_summary` VARCHAR(200)          DEFAULT NULL,
    `operator_id`    BIGINT                DEFAULT NULL COMMENT '操作人，审计用',
    `reason`         VARCHAR(200)          DEFAULT NULL,
    `create_time`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `deleted`        TINYINT      NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_plan_version` (`plan_id`, `version_no`, `deleted`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='计划版本快照';

-- ----------------------------
-- 9. AI 生成留痕（R-14：校验失败时也要能看到模型吐了什么）
-- ----------------------------
CREATE TABLE IF NOT EXISTS `t_ai_generation_log`
(
    `id`              BIGINT      NOT NULL COMMENT '主键',
    `plan_id`         BIGINT               DEFAULT NULL COMMENT '生成失败时可能没有计划',
    `user_id`         BIGINT               DEFAULT NULL,
    `trace_id`        VARCHAR(64)          DEFAULT NULL COMMENT '关联全链路日志',
    `provider`        VARCHAR(32)          DEFAULT NULL,
    `model`           VARCHAR(64)          DEFAULT NULL,
    `raw_response`    LONGTEXT             DEFAULT NULL COMMENT '模型原始输出，含校验失败样本',
    `validate_result` VARCHAR(16)          DEFAULT NULL COMMENT 'PASS/FAIL',
    `fail_reason`     VARCHAR(500)         DEFAULT NULL,
    `latency_ms`      INT                  DEFAULT NULL,
    `create_time`     DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time`     DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `deleted`         TINYINT     NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    KEY `idx_trace` (`trace_id`),
    KEY `idx_plan` (`plan_id`),
    KEY `idx_create_time` (`create_time`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='AI 生成留痕';
