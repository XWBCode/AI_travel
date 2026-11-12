-- ============================================================
-- U4 · 回滚 V4（人工执行）
-- 警告：删列会丢失「计划来源会话」的追溯关系，执行前请先备份：
--   mysqldump -u<user> -p ai_travel_dev t_travel_plan > t_travel_plan_backup.sql
-- 执行后需同步删除 flyway_schema_history 中 version = '4' 的记录，否则重启会校验失败。
-- 注意：先删索引再删列，MySQL 8 不会自动回收连带的索引。
-- ============================================================

ALTER TABLE `t_travel_plan` DROP INDEX `idx_travel_plan_session`;
ALTER TABLE `t_travel_plan` DROP COLUMN `source_message_id`;
ALTER TABLE `t_travel_plan` DROP COLUMN `session_id`;

-- DELETE FROM flyway_schema_history WHERE version = '4';
