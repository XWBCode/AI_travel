-- ============================================================
-- AI 旅行助手 Phase 1 建表脚本（dev 环境基线）
-- 数据库：MySQL 8.0+
-- 字符集：utf8mb4（必须，涉及 emoji 与多语言地名）
-- 约定：主键 BIGINT 雪花 ID（由 MyBatis-Plus 生成），deleted 逻辑删除
--
-- 环境约定（见 docs/design/06 第 1 节）：
--   本脚本默认操作 dev 库 ai_travel_dev。test / prod 不得复用该库，
--   需在各自环境将库名替换为对应 schema 后执行（或交由 DBA 指定）。
--   应用账号只需本库权限，不授予 CREATE DATABASE，故连接串不带
--   createDatabaseIfNotExist。
-- ============================================================

CREATE DATABASE IF NOT EXISTS `ai_travel_dev`
    DEFAULT CHARACTER SET utf8mb4
    COLLATE utf8mb4_general_ci;

USE `ai_travel_dev`;

-- ----------------------------
-- 用户表
-- ----------------------------
DROP TABLE IF EXISTS `t_user`;
CREATE TABLE `t_user`
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
    -- 唯一索引必须包含 deleted，否则逻辑删除后将无法复用用户名
    UNIQUE KEY `uk_username` (`username`, `deleted`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='用户表';

-- ----------------------------
-- AI 会话表
-- ----------------------------
DROP TABLE IF EXISTS `t_chat_session`;
CREATE TABLE `t_chat_session`
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

-- ----------------------------
-- AI 消息表
-- ----------------------------
DROP TABLE IF EXISTS `t_chat_message`;
CREATE TABLE `t_chat_message`
(
    `id`                BIGINT   NOT NULL COMMENT '主键',
    `session_id`        BIGINT   NOT NULL COMMENT '会话 ID',
    `user_id`           BIGINT   NOT NULL COMMENT '所属用户，冗余字段便于越权校验',
    `role`              VARCHAR(16)  NOT NULL COMMENT 'user/assistant/tool/system',
    `content`           MEDIUMTEXT   NOT NULL COMMENT '消息内容（Markdown）',
    `status`            VARCHAR(16)  NOT NULL DEFAULT 'SUCCESS' COMMENT 'SUCCESS/STREAMING/FAILED/CANCELED',
    `error_message`     VARCHAR(500)          DEFAULT NULL COMMENT '失败原因',
    `model`             VARCHAR(64)           DEFAULT NULL COMMENT '模型名',
    `prompt_tokens`     INT                   DEFAULT NULL,
    `completion_tokens` INT                   DEFAULT NULL,
    `latency_ms`        BIGINT                DEFAULT NULL COMMENT '耗时毫秒',
    `create_time`       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time`       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `deleted`           TINYINT  NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    KEY `idx_session` (`session_id`, `deleted`, `id`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='AI 消息表';
