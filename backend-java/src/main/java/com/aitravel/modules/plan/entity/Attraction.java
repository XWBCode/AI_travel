package com.aitravel.modules.plan.entity;

import com.aitravel.common.mybatis.BaseEntity;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 景点主数据（轻量版，04 §4.3）。
 * <p>
 * M1 只建表不使用：没有可信 POI 来源，硬造 ID 等于制造脏数据（Q-09）。
 * L1 接入 POI 工具后再写入，届时只更新 open_hours / ticket_price 两列，行程表结构不动。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("t_attraction")
public class Attraction extends BaseEntity {

    private String name;

    private String city;

    /** 区县 / 区域 */
    private String region;

    /** JSON：FactField&lt;string&gt; */
    private String openHours;

    /** JSON：FactField&lt;number&gt; */
    private String ticketPrice;

    /** NONE / MODEL_KNOWLEDGE / USER / TOOL / CACHE */
    private String source;
}
