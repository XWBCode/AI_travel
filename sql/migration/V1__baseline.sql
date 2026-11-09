-- ============================================================
-- V1 · 基线（Phase 1 已有的三张表）
--
-- 与仓库根 sql/01_schema.sql 同源，差别只有两点：
--   1) 去掉 CREATE DATABASE / USE（Flyway 在已连接的库中执行）
--   2) 改用 CREATE TABLE IF NOT EXISTS，不含 DROP —— 迁移脚本绝不能删数据
--
-- 已有库（手工跑过 01_schema.sql）执行本脚本为空操作，仅记录版本；
-- 空库执行则一次性建好三张基础表，随后由 V2 建计划相关表。
-- 修改本文件后必须同步 sql/01_schema.sql，反之亦然。
-- ============================================================

CREATE TABLE IF NOT EXISTS `t_user`
(
    `id`              BIGINT       NOT NULL COMMENT '主键',
    `username`        VARCHAR(32)  NOT NULL COMMENT '登录名',
    `password_hash`   VARCHAR(100) NOT NULL COMMENT 'BCrypt 密码哈希',
    `nickname`        VARCHAR(32)           DEFAULT NULL COMMENT '昵称',
    `avatar_url`      VARCHAR(512)          DEFAULT NULL COMMENT '头像地址',
    `bio`             VARCHAR(200)          DEFAULT NULL COMMENT '个人简介',
    `role`            VARCHAR(16)  NOT NULL DEFAULT 'USER' COMMENT '角色：USER/ADMIN',
    `status`          TINYINT      NOT NULL DEFAULT 0 COMMENT '0=正常 1=禁用',
    `last_login_time` DATETIME              DEFAULT NULL COMMENT '最近登录时间',
    `create_time`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `deleted`         TINYINT      NOT NULL DEFAULT 0 COMMENT '0=未删除 1=已删除',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_username` (`username`, `deleted`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='用户表';

CREATE TABLE IF NOT EXISTS `t_chat_session`
(
    `id`                   BIGINT      NOT NULL COMMENT '主键',
    `user_id`              BIGINT      NOT NULL COMMENT '所属用户',
    `title`                VARCHAR(64) NOT NULL DEFAULT '新会话' COMMENT '会话标题',
    `last_message_preview` VARCHAR(200)         DEFAULT NULL COMMENT '最近消息摘要',
    `message_count`        INT         NOT NULL DEFAULT 0 COMMENT '消息条数',
    `create_time`          DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time`          DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `deleted`              TINYINT     NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    KEY `idx_user_time` (`user_id`, `deleted`, `update_time`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='AI 会话表';

CREATE TABLE IF NOT EXISTS `t_chat_message`
(
    `id`                BIGINT      NOT NULL COMMENT '主键',
    `session_id`        BIGINT      NOT NULL COMMENT '会话 ID',
    `user_id`           BIGINT      NOT NULL COMMENT '所属用户，冗余字段便于越权校验',
    `role`              VARCHAR(16) NOT NULL COMMENT 'user/assistant/tool/system',
    `content`           MEDIUMTEXT  NOT NULL COMMENT '消息内容（Markdown）',
    `status`            VARCHAR(16) NOT NULL DEFAULT 'SUCCESS' COMMENT 'SUCCESS/STREAMING/FAILED/CANCELED',
    `error_message`     VARCHAR(500)         DEFAULT NULL COMMENT '失败原因',
    `model`             VARCHAR(64)          DEFAULT NULL COMMENT '模型名',
    `prompt_tokens`     INT                  DEFAULT NULL,
    `completion_tokens` INT                  DEFAULT NULL,
    `latency_ms`        BIGINT               DEFAULT NULL COMMENT '耗时毫秒',
    `create_time`       DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time`       DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `deleted`           TINYINT     NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    KEY `idx_session` (`session_id`, `deleted`, `id`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='AI 消息表';
