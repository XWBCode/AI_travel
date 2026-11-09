package com.aitravel.modules.plan.entity;

import com.aitravel.common.mybatis.BaseEntity;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 计划版本快照（04 §5，D-05 采用整计划级 JSON 快照）。
 * <p>
 * {@code snapshotJson} 写入前必须通过计划 Schema 校验；回滚不得把「待核实」变成「已核实」。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("t_plan_version")
public class PlanVersion extends BaseEntity {

    private Long planId;

    /** 与 t_travel_plan.version 保持一致，同一计划内单调递增 */
    private Integer versionNo;

    /** JSON：整计划快照 */
    private String snapshotJson;

    private String changeSummary;

    /** 操作人，审计用 */
    private Long operatorId;

    private String reason;
}
