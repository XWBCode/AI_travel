package com.aitravel.modules.plan.validation;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;

import java.util.EnumSet;
import java.util.Set;

/**
 * 计划状态机（04 §6）。
 * <p>
 * 设计要点：状态迁移只在服务端判定，<b>不接受客户端指定目标状态</b>；每个动作接口只对应
 * 一条合法边，非法边直接抛 {@link ErrorCode#PLAN_STATE_INVALID}。
 * 这是 AC-09「未确认不得变 CONFIRMED」的技术落点——确认必须走
 * {@code POST /api/plans/{id}/confirm}，而该方法只允许 DRAFT 起点。
 */
public enum PlanStatus {

    DRAFT("草稿"),
    CONFIRMED("已确认"),
    IN_PROGRESS("进行中"),
    COMPLETED("已完成"),
    CANCELLED("已取消");

    private final String label;

    PlanStatus(String label) {
        this.label = label;
    }

    public String getLabel() {
        return label;
    }

    /** 允许的迁移边 */
    public Set<PlanStatus> allowedTargets() {
        return switch (this) {
            case DRAFT -> EnumSet.of(CONFIRMED, CANCELLED);
            case CONFIRMED -> EnumSet.of(IN_PROGRESS, CANCELLED);
            case IN_PROGRESS -> EnumSet.of(COMPLETED, CANCELLED);
            // 终态：不再接受任何迁移
            case COMPLETED, CANCELLED -> EnumSet.noneOf(PlanStatus.class);
        };
    }

    public boolean canMoveTo(PlanStatus target) {
        return allowedTargets().contains(target);
    }

    public static PlanStatus parse(String raw) {
        if (raw == null) {
            throw new BizException(ErrorCode.PLAN_STATE_INVALID, "计划状态为空");
        }
        try {
            return valueOf(raw);
        } catch (IllegalArgumentException e) {
            throw new BizException(ErrorCode.PLAN_STATE_INVALID, "未知计划状态：" + raw);
        }
    }

    /**
     * 断言迁移合法，否则抛出带可读信息的业务异常。
     */
    public PlanStatus requireTransitionTo(PlanStatus target) {
        if (!canMoveTo(target)) {
            throw new BizException(ErrorCode.PLAN_STATE_INVALID,
                    "不允许从「" + label + "」变更为「" + target.label + "」");
        }
        return target;
    }
}
