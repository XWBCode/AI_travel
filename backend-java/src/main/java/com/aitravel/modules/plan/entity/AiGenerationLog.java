package com.aitravel.modules.plan.entity;

import com.aitravel.common.mybatis.BaseEntity;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * AI 生成留痕（04 §4.8，对应 R-14 / AC-07）。
 * <p>
 * 校验失败时也必须落一条，{@code rawResponse} 保留模型原始输出，
 * 否则「模型到底吐了什么」无从排查。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("t_ai_generation_log")
public class AiGenerationLog extends BaseEntity {

    /** 生成失败时可能还没有计划 */
    private Long planId;

    private Long userId;

    /** 关联全链路日志 */
    private String traceId;

    private String provider;

    private String model;

    /** 模型原始输出，含校验失败样本 */
    private String rawResponse;

    /** PASS / FAIL */
    private String validateResult;

    private String failReason;

    private Integer latencyMs;
}
