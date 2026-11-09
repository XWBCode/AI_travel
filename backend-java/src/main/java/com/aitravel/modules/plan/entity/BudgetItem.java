package com.aitravel.modules.plan.entity;

import com.aitravel.common.mybatis.BaseEntity;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 预算明细（04 §4.5）。
 * <p>
 * L0 下 {@code amount.value} 恒为 null，区间口径只写在 {@code note} 里且不得含数字（Q-02）。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("t_budget_item")
public class BudgetItem extends BaseEntity {

    private Long planId;

    /** TRANSPORT / ACCOMMODATION / FOOD / TICKET / OTHER */
    private String category;

    private String label;

    /** JSON：FactField&lt;number&gt;，L0 恒为 null */
    private String amount;

    private String note;

    /** 与 FactField.source 冗余，便于按来源筛选 */
    private String source;
}
