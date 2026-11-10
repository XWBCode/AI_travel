-- ============================================================
-- V3 · 补 t_travel_plan.missing_fields
--
-- 原因：存储契约 contracts/travel-plan/travel_plan_v1.json 的 basic 要求
--       missingFields 全程可读（列表页/详情页要据此提示「哪些信息缺失」），
--       但 V2 建表时只落了 origin_city 等 8 个字段，漏了这一列。
--
-- 为什么不改 V2：V2 已在多个环境执行过，Flyway 会校验历史脚本的 checksum，
--               改动会导致启动直接失败（这是保护，不是障碍）。新增版本号才是正确做法。
--
-- 注意：本文件不可重复执行（MySQL 8 的 ADD COLUMN 不支持 IF NOT EXISTS），
--       由 Flyway 的历史表保证只跑一次。手工执行时请先确认列不存在。
-- 回滚：sql/rollback/U3__missing_fields_rollback.sql
-- ============================================================

ALTER TABLE `t_travel_plan`
    ADD COLUMN `missing_fields` JSON NULL
        COMMENT 'Q-05：缺失字段名数组，与 basic 的 null 状态严格对应（S-4 校验）'
        AFTER `destinations`;
