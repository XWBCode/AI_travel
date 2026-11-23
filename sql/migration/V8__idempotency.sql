-- ============================================================
-- V8: 幂等键（BUG-007）
--
-- 背景：05 §5 写着「写操作支持 clientRequestId 幂等键，重复提交返回首次结果」，
-- 但直到 MVP 验收都没有实现。实测 3 个并发相同请求 → 3 份计划（见 MVP-Reliability-Report）。
-- 真实模型一次生成 30-45 秒，网关/浏览器/用户连点的重试概率不低，后果是重复计划 +
-- 重复计费 + 会话里重复出现卡片。
--
-- 设计要点：
-- 1. 唯一约束建在 (user_id, request_id) 上，并发由 InnoDB 保证，不靠 synchronized
--    （单机锁在多实例部署下等于没加，而且会拖慢整个应用）。
-- 2. 状态三态：PROCESSING / SUCCESS / FAILED。
--    - PROCESSING：同一 ID 再进来直接拒绝，让调用方等第一次的结果，不要重复烧模型额度；
--    - SUCCESS：直接把首次结果返回（result_ref 指向计划 ID）；
--    - FAILED：允许重试，用条件更新把状态改回 PROCESSING 抢占。
-- 3. 这一行必须在**调用模型之前**提交（独立事务），否则第二个并发请求看不到它，
--    唯一约束也就起不到作用。这与通知的「跟业务同事务、失败就吞」正好相反：
--    幂等记录写不进去，宁可让业务失败，也不能让重复请求穿过去。
-- ============================================================

CREATE TABLE IF NOT EXISTS t_idempotency (
    id            BIGINT       NOT NULL COMMENT '雪花主键',
    user_id       BIGINT       NOT NULL COMMENT '用户 ID，幂等域按用户隔离',
    request_id    VARCHAR(64)  NOT NULL COMMENT '客户端生成的请求 ID（同一次提交的重试必须复用）',
    action        VARCHAR(32)  NOT NULL COMMENT '操作类型，例如 PLAN_GENERATE / PLAN_ADJUST',
    request_hash  CHAR(64)     NOT NULL COMMENT '请求体规范化后的 SHA-256，用于识别「同 ID 不同内容」',
    status        VARCHAR(16)  NOT NULL COMMENT 'PROCESSING / SUCCESS / FAILED',
    result_ref    VARCHAR(64)  NULL     COMMENT '成功时的结果引用（计划 ID 等）',
    error_code    INT          NULL     COMMENT '失败时的业务错误码，便于客户端区分能否重试',
    create_time   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted       TINYINT      NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    UNIQUE KEY uk_idem_user_request (user_id, request_id),
    KEY idx_idem_status_time (status, update_time)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci COMMENT ='写操作幂等键';
