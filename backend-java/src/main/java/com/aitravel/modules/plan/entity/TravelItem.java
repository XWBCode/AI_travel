package com.aitravel.modules.plan.entity;

import com.aitravel.common.mybatis.BaseEntity;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalTime;

/**
 * 行程项（04 §4.2）。
 * <p>
 * {@code startTime / endTime} 是用户可调整的建议时间，不属于 FactField；
 * {@code duration / openHours / ticketPrice} 属于 FactField，L0 下 value 恒为 null。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("t_travel_item")
public class TravelItem extends BaseEntity {

    private Long dayId;

    /** Q-09：M1 恒为 null，接入 POI 主数据后填写 */
    private Long attractionId;

    /** 日内顺序，从 1 开始 */
    private Integer itemOrder;

    private String title;

    private LocalTime startTime;

    private LocalTime endTime;

    /** JSON：FactField&lt;number&gt; 分钟 */
    private String duration;

    /** JSON：FactField&lt;string&gt; */
    private String openHours;

    /** JSON：FactField&lt;number&gt; */
    private String ticketPrice;

    private String note;
}
