package com.aitravel.modules.plan.entity;

import com.aitravel.common.mybatis.BaseEntity;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalDateTime;

/**
 * AI 调整建议（04 §4.7 / V6）。
 *
 * <p>这张表的核心作用是**闸门**：模型的产出只落在这里，永远不直接写 {@code t_travel_plan}。
 * AC-11 的验收方式是代码审计，所以「AI 改了计划」这种事故必须在结构上不可能发生，
 * 而不是靠调用方自觉。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("t_plan_adjustment")
public class PlanAdjustment extends BaseEntity {

    public static final String PENDING = "PENDING";
    public static final String CONFIRMED = "CONFIRMED";
    public static final String REJECTED = "REJECTED";
    public static final String EXPIRED = "EXPIRED";

    private Long planId;

    private Long userId;

    /**
     * 生成建议时计划的 {@code version}。
     * 确认时若与当前 version 不符，说明期间计划被改过，这条建议作废——
     * 否则会把用户在等待期间的手动修改静默覆盖掉。
     */
    private Integer baseVersion;

    /** {@link #PENDING} / {@link #CONFIRMED} / {@link #REJECTED} / {@link #EXPIRED} */
    private String status;

    /** 用户原话（「今天太累，下午不去了」），回看时能明白当初在改什么 */
    private String instruction;

    /** 一句话概括，通知与列表里直接用，不让前端再截断一次 */
    private String summary;

    /** 增量操作数组，格式见 {@code AdjustmentOps}；before 值由服务端回填 */
    private String opsJson;

    /** 拒绝原因或过期原因；确认成功时为 null */
    private String reason;

    private LocalDateTime decidedAt;
}
