-- ============================================================
-- U5 · 回滚 V5（人工执行）
-- 警告：整表删除会丢失用户已勾选的执行进度（无法从其他表重建）。
--   执行前备份：mysqldump -u<user> -p ai_travel_dev t_travel_progress > t_travel_progress_backup.sql
-- 执行后需同步删除 flyway_schema_history 中 version = '5' 的记录，否则重启会校验失败。
-- ============================================================

DROP TABLE IF EXISTS `t_travel_progress`;

-- DELETE FROM flyway_schema_history WHERE version = '5';
