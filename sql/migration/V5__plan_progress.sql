-- ============================================================
-- V5 · 行程执行进度（M2 第一块）
--
-- 04 §5 早就定义了 t_travel_progress，但一直没建表：M1 只做「生成/确认」，
-- 没有「执行中打勾」这个动作。M2 开始用户要在行程里逐项打勾，这才落地。
--
-- 语义：一行 = 一个「已勾选完成」的行程项。表里没有的就是没做。
--   - 不做 deleted 逻辑删除：取消勾选就是物理删除。否则再次勾选会撞 uk(plan_id,item_id)，
--     而把 deleted 加进唯一键又会在「勾-取消-再勾-再取消」时撞上第二条历史行。
--     进度是纯状态标记，没有留档价值，物理删除最诚实。
--   - completed_at 与行共存亡：有行即有完成时间，不用 NULL 表示「未完成」，
--     否则「从未勾选」与「取消勾选」在数据上无法区分，排查时容易误判。
--   - uk(plan_id,item_id) 是幂等的技术落地：重复点击由 ON DUPLICATE KEY UPDATE 吸收，
--     不依赖应用层「先查再插」的竞态窗口。
--
-- 不建外键（与 V2 一致）：item 删除时进度行随之失效，由业务层按 plan_id 整体清理，
-- 不在 DB 层制造级联删除的隐式行为。
--
-- 回滚：sql/rollback/U5__plan_progress_rollback.sql
-- ============================================================

CREATE TABLE IF NOT EXISTS `t_travel_progress`
(
    `id`           BIGINT   NOT NULL COMMENT '主键（雪花 ID）',
    `plan_id`      BIGINT   NOT NULL COMMENT '所属计划',
    `item_id`      BIGINT   NOT NULL COMMENT '行程项 ID',
    `user_id`      BIGINT   NOT NULL COMMENT '勾选人，用于归属校验',
    `completed_at` DATETIME NOT NULL COMMENT '勾选完成时间',
    `create_time`  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_plan_item` (`plan_id`, `item_id`),
    KEY `idx_progress_plan` (`plan_id`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci COMMENT ='行程执行进度：一行 = 一个已勾选的行程项';
