package com.aitravel.modules.plan.service;

import com.aitravel.modules.plan.entity.AiGenerationLog;
import com.aitravel.modules.plan.mapper.AiGenerationLogMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * 生成留痕写入。
 * <p>
 * 独立成 Bean 且用 {@link Propagation#REQUIRES_NEW}，原因是：校验失败的日志必须活下来。
 * 如果和落库共用一个事务，抛异常回滚会把「模型到底吐了什么」一起回滚掉——
 * 那正是排查线上问题时唯一有用的证据。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class GenerationLogWriter {

    private static final int RAW_LIMIT = 60_000;

    private final AiGenerationLogMapper logMapper;

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void write(Long userId, Long planId, String traceId, String provider, String model,
                      String rawResponse, boolean passed, String failReason, long latencyMs) {
        try {
            AiGenerationLog entity = new AiGenerationLog();
            entity.setUserId(userId);
            entity.setPlanId(planId);
            entity.setTraceId(traceId);
            entity.setProvider(provider);
            entity.setModel(model);
            entity.setRawResponse(truncate(rawResponse));
            entity.setValidateResult(passed ? "PASS" : "FAIL");
            entity.setFailReason(truncate(failReason, 500));
            entity.setLatencyMs((int) Math.min(latencyMs, Integer.MAX_VALUE));
            logMapper.insert(entity);
        } catch (Exception e) {
            // 留痕失败不得影响主流程，但必须留下错误日志（总纲 9.3：不得静默吞掉）
            log.error("写生成留痕失败 user={} trace={}", userId, traceId, e);
        }
    }

    private String truncate(String text) {
        return truncate(text, RAW_LIMIT);
    }

    private String truncate(String text, int limit) {
        if (text == null) {
            return null;
        }
        return text.length() <= limit ? text : text.substring(0, limit);
    }
}
