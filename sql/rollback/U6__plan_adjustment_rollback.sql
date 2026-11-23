-- 回滚 V6：删除调整建议表。
-- 注意：本脚本会丢弃全部调整建议记录（含 PENDING 的）。
-- 正式计划与版本快照不受影响——它们不在本表里，这正是这张表的设计目的。

DROP TABLE IF EXISTS `t_plan_adjustment`;
