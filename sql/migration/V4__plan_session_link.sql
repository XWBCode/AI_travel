-- ============================================================
-- V4 · 计划关联来源会话与消息
--
-- 原因：M1 打通「聊天里一句话出计划」后，计划是从某次对话里长出来的，
--       但 V2 建表时没有任何反向指针，导致：
--       1. 聊天卡片的「查看详情」回不到原会话；
--       2. 无法回答「这个计划是哪句话生成的」；
--       3. 同一会话反复调整时，没法按会话聚合版本。
--
-- 两列都可空：`/api/plans/generate` 仍允许脱离会话直接生成（脚本、后台补数）。
-- 不建外键：计划与会话分属不同模块，且会话删除后计划应保留（用户的历史行程）。
--
-- 注意：本文件不可重复执行（MySQL 8 的 ADD COLUMN 不支持 IF NOT EXISTS），
--       由 Flyway 的历史表保证只跑一次。
-- 回滚：sql/rollback/U4__plan_session_link_rollback.sql
-- ============================================================

ALTER TABLE `t_travel_plan`
    ADD COLUMN `session_id` BIGINT NULL
        COMMENT '来源会话 ID，脱离会话生成时为 NULL' AFTER `user_id`,
    ADD COLUMN `source_message_id` BIGINT NULL
        COMMENT '触发本次生成的用户消息 ID' AFTER `session_id`,
    ADD INDEX `idx_travel_plan_session` (`session_id`);
