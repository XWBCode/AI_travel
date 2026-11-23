package com.aitravel.modules.idempotency;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.idempotency.entity.IdempotencyRecord;
import com.aitravel.modules.idempotency.mapper.IdempotencyMapper;
import com.aitravel.modules.idempotency.service.IdempotencyService;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DuplicateKeyException;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 幂等判定的分支逻辑（BUG-007）。
 *
 * <p>不连数据库：这里验的是「遇到某种既有状态该怎么响应」，
 * 并发与唯一约束的真实性由 {@link IdempotencyDbTest} 证明。两层分开，
 * 免得一个测试既要造并发又要造状态，失败时不知道该怀疑谁。
 */
class IdempotencyServiceTest {

    private static final long USER = 11L;
    private static final String REQ = "req-1";
    private static final String ACTION = IdempotencyService.ACTION_PLAN_GENERATE;
    private static final String HASH = IdempotencyService.hashOf(ACTION, "user\u0001去成都三天\u0002");

    private final IdempotencyMapper mapper = mock(IdempotencyMapper.class);
    private final IdempotencyService service = new IdempotencyService(mapper);

    private IdempotencyRecord existing(String status) {
        IdempotencyRecord r = new IdempotencyRecord();
        r.setId(100L);
        r.setUserId(USER);
        r.setRequestId(REQ);
        r.setAction(ACTION);
        r.setRequestHash(HASH);
        r.setStatus(status);
        return r;
    }

    @Test
    @DisplayName("首次提交：插入成功即拿到执行权")
    void firstArrivalWins() {
        IdempotencyService.Decision d = service.begin(USER, REQ, ACTION, HASH);
        assertTrue(d.first());
        assertEquals(IdempotencyRecord.PROCESSING, d.record().getStatus());
        verify(mapper).insert(any(IdempotencyRecord.class));
    }

    @Test
    @DisplayName("同 ID 同内容且已成功：不回放执行，只回放结果")
    void successReplays() {
        when(mapper.insert(any(IdempotencyRecord.class))).thenThrow(new DuplicateKeyException("dup"));
        IdempotencyRecord old = existing(IdempotencyRecord.SUCCESS);
        old.setResultRef("888");
        when(mapper.selectOne(any())).thenReturn(old);

        IdempotencyService.Decision d = service.begin(USER, REQ, ACTION, HASH);
        assertFalse(d.first());
        assertEquals("888", d.record().getResultRef());
    }

    @Test
    @DisplayName("同 ID 不同内容：必须拒绝，不能把别人的结果当自己的")
    void differentPayloadConflicts() {
        when(mapper.insert(any(IdempotencyRecord.class))).thenThrow(new DuplicateKeyException("dup"));
        IdempotencyRecord old = existing(IdempotencyRecord.SUCCESS);
        old.setRequestHash("another-hash");
        when(mapper.selectOne(any())).thenReturn(old);

        BizException e = assertThrows(BizException.class, () -> service.begin(USER, REQ, ACTION, HASH));
        assertEquals(ErrorCode.IDEMPOTENCY_CONFLICT, e.getErrorCode());
    }

    @Test
    @DisplayName("同 ID 处理中：明确拒绝（30113），不重复消耗模型额度")
    void processingIsRejected() {
        when(mapper.insert(any(IdempotencyRecord.class))).thenThrow(new DuplicateKeyException("dup"));
        when(mapper.selectOne(any())).thenReturn(existing(IdempotencyRecord.PROCESSING));

        BizException e = assertThrows(BizException.class, () -> service.begin(USER, REQ, ACTION, HASH));
        assertEquals(ErrorCode.IDEMPOTENCY_PROCESSING, e.getErrorCode());
    }

    @Test
    @DisplayName("同 ID 上次失败：允许重试，用条件更新抢占")
    void failedCanRetry() {
        when(mapper.insert(any(IdempotencyRecord.class))).thenThrow(new DuplicateKeyException("dup"));
        when(mapper.selectOne(any())).thenReturn(existing(IdempotencyRecord.FAILED));
        when(mapper.update(any(), any())).thenReturn(1);

        IdempotencyService.Decision d = service.begin(USER, REQ, ACTION, HASH);
        assertTrue(d.first());
        assertEquals(IdempotencyRecord.PROCESSING, d.record().getStatus());
    }

    @Test
    @DisplayName("上次失败但别人已先重试：这次不能抢，报处理中")
    void failedRetryLosesRace() {
        when(mapper.insert(any(IdempotencyRecord.class))).thenThrow(new DuplicateKeyException("dup"));
        when(mapper.selectOne(any())).thenReturn(existing(IdempotencyRecord.FAILED));
        when(mapper.update(any(), any())).thenReturn(0);

        BizException e = assertThrows(BizException.class, () -> service.begin(USER, REQ, ACTION, HASH));
        assertEquals(ErrorCode.IDEMPOTENCY_PROCESSING, e.getErrorCode());
    }

    @Test
    @DisplayName("唯一键冲突却查不到记录：不冒充首次，让调用方重试")
    void unknownStateIsNotTreatedAsFirst() {
        when(mapper.insert(any(IdempotencyRecord.class))).thenThrow(new DuplicateKeyException("dup"));
        when(mapper.selectOne(any())).thenReturn(null);

        BizException e = assertThrows(BizException.class, () -> service.begin(USER, REQ, ACTION, HASH));
        assertEquals(ErrorCode.IDEMPOTENCY_PROCESSING, e.getErrorCode());
    }

    @Test
    @DisplayName("哈希只跟内容有关：顺序与角色都参与，内容变了哈希必须变")
    void hashIsStableAndSensitive() {
        String a = IdempotencyService.hashOf(ACTION, "user\u0001去成都三天\u0002");
        String b = IdempotencyService.hashOf(ACTION, "user\u0001去成都三天\u0002");
        String c = IdempotencyService.hashOf(ACTION, "user\u0001去成都四天\u0002");
        assertEquals(a, b);
        assertNotEquals(a, c);
        assertEquals(64, a.length());
    }

    @Test
    @DisplayName("空请求 ID 不该走到这里，但真走到了也不能写库")
    void blankRequestIdIsNotPersisted() {
        assertThrows(IllegalArgumentException.class, () -> service.begin(USER, "  ", ACTION, HASH));
        verify(mapper, never()).insert(any(IdempotencyRecord.class));
    }
}
