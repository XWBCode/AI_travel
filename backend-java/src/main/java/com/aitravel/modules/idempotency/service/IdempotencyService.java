package com.aitravel.modules.idempotency.service;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.idempotency.entity.IdempotencyRecord;
import com.aitravel.modules.idempotency.mapper.IdempotencyMapper;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

/**
 * 幂等键服务（V8 / BUG-007）。
 *
 * <p>并发正确性<b>只依赖数据库唯一键</b>：两个请求同时插入 (user_id, request_id)，
 * InnoDB 只会让一个成功，另一个拿到 {@link DuplicateKeyException}，再回查状态决定怎么响应。
 * 不用 synchronized，也不做「先查再写」——后者在并发下必然出现两个请求都查到「不存在」。
 *
 * <p>事务边界很关键：
 * <ul>
 *   <li>{@link #insertNew} 用 {@code REQUIRES_NEW} 立刻提交，让并发方马上能看见这一行；</li>
 *   <li>异常在外层捕获，避免把调用方的事务标记成 rollback-only（那样后续更新会全部失效）；</li>
 *   <li>业务失败时把状态改成 FAILED，允许用同一个 ID 重试；</li>
 *   <li>业务成功时写入 result_ref，重复提交直接回放这个结果。</li>
 * </ul>
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class IdempotencyService {

    private final IdempotencyMapper mapper;

    public static final String ACTION_PLAN_GENERATE = "PLAN_GENERATE";
    public static final String ACTION_PLAN_ADJUST = "PLAN_ADJUST";

    /** 一次幂等判定的结果：first=true 表示本次抢到了执行权 */
    public record Decision(boolean first, IdempotencyRecord record) {
    }

    public static String hashOf(String action, String canonicalPayload) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] digest = md.digest((action + "\n" + canonicalPayload).getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException e) {
            // JDK 必然支持 SHA-256；真不支持就该直接崩，不能悄悄降级成「不校验内容」
            throw new IllegalStateException("SHA-256 不可用", e);
        }
    }

    /**
     * 尝试占用这个 (userId, requestId)。
     *
     * @return first=true 表示调用方需要真正执行一次业务；
     *         first=false 时 record 是已存在的那一行，由调用方决定回放还是报错
     */
    public Decision begin(Long userId, String requestId, String action, String requestHash) {
        // 空 ID 不是「不启用幂等」，而是调用方写错了：必须炸出来，不能让一次没有主键的写悄悄执行
        if (requestId == null || requestId.isBlank()) {
            throw new IllegalArgumentException("requestId 不能为空");
        }
        IdempotencyRecord record = new IdempotencyRecord();
        record.setUserId(userId);
        record.setRequestId(requestId);
        record.setAction(action);
        record.setRequestHash(requestHash);
        record.setStatus(IdempotencyRecord.PROCESSING);
        try {
            insertNew(record);
            return new Decision(true, record);
        } catch (DuplicateKeyException e) {
            IdempotencyRecord existing = findByUserAndRequest(userId, requestId);
            if (existing == null) {
                // 唯一键冲突却查不到：极端的隔离级别/主从延迟才会出现。宁可让调用方重试，
                // 也不能当成「首次」把业务再跑一遍。
                throw new BizException(ErrorCode.IDEMPOTENCY_PROCESSING, "请求状态未知，请稍后重试");
            }
            return resolveExisting(existing, action, requestHash);
        }
    }

    private Decision resolveExisting(IdempotencyRecord existing, String action, String requestHash) {
        if (!action.equals(existing.getAction()) || !requestHash.equals(existing.getRequestHash())) {
            throw new BizException(ErrorCode.IDEMPOTENCY_CONFLICT);
        }
        if (IdempotencyRecord.SUCCESS.equals(existing.getStatus())) {
            return new Decision(false, existing);
        }
        if (IdempotencyRecord.PROCESSING.equals(existing.getStatus())) {
            throw new BizException(ErrorCode.IDEMPOTENCY_PROCESSING);
        }
        // FAILED：允许重试。用条件更新抢占，抢不到说明别人已经先一步重试了
        boolean claimed = mapper.update(null, new LambdaUpdateWrapper<IdempotencyRecord>()
                .eq(IdempotencyRecord::getId, existing.getId())
                .eq(IdempotencyRecord::getStatus, IdempotencyRecord.FAILED)
                .set(IdempotencyRecord::getStatus, IdempotencyRecord.PROCESSING)
                .set(IdempotencyRecord::getErrorCode, null)
                .set(IdempotencyRecord::getUpdateTime, java.time.LocalDateTime.now())) == 1;
        if (!claimed) {
            throw new BizException(ErrorCode.IDEMPOTENCY_PROCESSING);
        }
        existing.setStatus(IdempotencyRecord.PROCESSING);
        existing.setErrorCode(null);
        log.info("幂等重试抢占成功 requestId={} action={}", existing.getRequestId(), existing.getAction());
        return new Decision(true, existing);
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void insertNew(IdempotencyRecord record) {
        mapper.insert(record);
    }

    public void succeed(Long id, String resultRef) {
        mapper.update(null, new LambdaUpdateWrapper<IdempotencyRecord>()
                .eq(IdempotencyRecord::getId, id)
                .set(IdempotencyRecord::getStatus, IdempotencyRecord.SUCCESS)
                .set(IdempotencyRecord::getResultRef, resultRef)
                .set(IdempotencyRecord::getErrorCode, null));
    }

    /** 业务失败：记 FAILED，允许同一 ID 重试；失败原因只记码，不记堆栈 */
    public void fail(Long id, Integer errorCode) {
        mapper.update(null, new LambdaUpdateWrapper<IdempotencyRecord>()
                .eq(IdempotencyRecord::getId, id)
                .set(IdempotencyRecord::getStatus, IdempotencyRecord.FAILED)
                .set(IdempotencyRecord::getErrorCode, errorCode));
    }

    public IdempotencyRecord findByUserAndRequest(Long userId, String requestId) {
        return mapper.selectOne(new LambdaQueryWrapper<IdempotencyRecord>()
                .eq(IdempotencyRecord::getUserId, userId)
                .eq(IdempotencyRecord::getRequestId, requestId));
    }
}
