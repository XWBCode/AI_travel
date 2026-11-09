package com.aitravel.modules.plan.entity;

import com.aitravel.common.mybatis.BaseEntity;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalDate;

/**
 * 计划每日行程（04 §4.2）。{@code dayIndex} 从 1 开始，同一计划内唯一。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("t_travel_day")
public class TravelDay extends BaseEntity {

    private Long planId;

    /** 从 1 开始连续递增 */
    private Integer dayIndex;

    private LocalDate dayDate;

    private String city;

    private String theme;
}
