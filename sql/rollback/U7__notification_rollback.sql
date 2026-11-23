-- 回滚 V7：删除通知表。
-- 通知是纯展示数据，删除不影响计划、版本与进度。

DROP TABLE IF EXISTS `t_notification`;
