package com.aitravel.modules.plan.dto;

import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

/**
 * 调整建议的下发形态。
 *
 * <p>{@code ops} 里已经补好了 {@code before}，前端可以直接渲染「原值 → 新值」，
 * 不需要自己再去找一遍原计划对比——两端各算一遍差异，必然有一天会不一致。
 */
@Data
public class PlanAdjustmentVO {

    /** 雪花 ID 一律字符串下发，避免 JS 丢精度 */
    private String adjustmentId;

    private String planId;

    /** 生成建议时计划的版本号，前端据此显示「基于第 N 版」 */
    private Integer baseVersion;

    /** PENDING / CONFIRMED / REJECTED / EXPIRED */
    private String status;

    private String instruction;

    private String summary;

    /** 规范化后的增量操作，格式见 AdjustmentOps#toPayload */
    private List<Map<String, Object>> ops;

    /** 计划当前版本；与 baseVersion 不同说明建议已过期 */
    private Integer planVersion;

    private LocalDateTime createTime;
}
