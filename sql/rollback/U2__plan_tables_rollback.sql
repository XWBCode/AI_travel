-- ============================================================
-- U2 · 回滚 V2（删除 M1 计划相关 9 张表）
--
-- ！！！会永久删除数据，执行前必须备份：
--   mysqldump -u<user> -p ai_travel_dev t_travel_plan t_travel_day t_travel_item \
--     t_attraction t_transport_segment t_budget_item t_accommodation \
--     t_plan_version t_ai_generation_log > backup_v2.sql
--
-- Flyway 社区版不支持 undo 迁移，本脚本需人工执行；
-- 执行后还需删除 flyway_schema_history 中 version = 2 的记录，否则重启会跳过 V2。
-- ============================================================

SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS `t_ai_generation_log`;
DROP TABLE IF EXISTS `t_plan_version`;
DROP TABLE IF EXISTS `t_accommodation`;
DROP TABLE IF EXISTS `t_budget_item`;
DROP TABLE IF EXISTS `t_transport_segment`;
DROP TABLE IF EXISTS `t_attraction`;
DROP TABLE IF EXISTS `t_travel_item`;
DROP TABLE IF EXISTS `t_travel_day`;
DROP TABLE IF EXISTS `t_travel_plan`;

SET FOREIGN_KEY_CHECKS = 1;

DELETE
FROM `flyway_schema_history`
WHERE `version` = '2';
