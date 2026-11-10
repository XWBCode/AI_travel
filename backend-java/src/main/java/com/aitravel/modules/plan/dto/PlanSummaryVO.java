package com.aitravel.modules.plan.dto;

import lombok.Data;

import java.time.LocalDate;
import java.time.LocalDateTime;

/**
 * 列表页视图模型（08 §7 明确：PlanSummary 随 WP5 一起定，此处先给最小集）。
 * <p>
 * 刻意不含 {@code budget} / {@code days} 明细：列表只用于挑选，点进去看详情，
 * 避免一次列表把 30KB 的计划体全查出来。
 */
@Data
public class PlanSummaryVO {

    private Long id;
    private String title;
    private String destination;
    private String status;
    private LocalDate startDate;
    private LocalDate endDate;
    private Integer days;
    private Integer travelers;
    private Integer unverifiedFieldCount;
    private Integer version;
    private LocalDateTime updateTime;
}
