-- ============================================================
-- U3 · 回滚 V3（人工执行）
-- 警告：删列会丢失该列数据，执行前请先备份：
--   mysqldump -u<user> -p ai_travel_dev t_travel_plan > t_travel_plan_backup.sql
-- 执行后需同步删除 flyway_schema_history 中 version = '3' 的记录，否则重启会校验失败。
-- ============================================================

ALTER TABLE `t_travel_plan` DROP COLUMN `missing_fields`;

-- DELETE FROM flyway_schema_history WHERE version = '3';
