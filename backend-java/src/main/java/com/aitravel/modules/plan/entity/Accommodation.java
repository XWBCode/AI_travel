package com.aitravel.modules.plan.entity;

import com.aitravel.common.mybatis.BaseEntity;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 住宿建议（04 §4.5）。{@code priceRange} 为 FactField&lt;string&gt;，L0 恒为 null。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("t_accommodation")
public class Accommodation extends BaseEntity {

    private Long planId;

    /** 按天推荐时填写 */
    private Long dayId;

    private String areaName;

    private String suggestion;

    /** JSON：FactField&lt;string&gt;，L0 恒为 null */
    private String priceRange;

    private String source;
}
