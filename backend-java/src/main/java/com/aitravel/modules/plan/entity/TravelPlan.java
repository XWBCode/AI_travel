package com.aitravel.modules.plan.entity;

import com.aitravel.common.mybatis.BaseEntity;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalDate;
import java.time.LocalDateTime;

/**
 * 旅行计划主表（04 §4.1）。
 * <p>
 * JSON 列统一用 String 承接原始 JSON 文本：落库前已通过契约校验，
 * 存原文可保证「库里存的就是校验过的那份」，避免二次序列化差异。
 * 解析交给 Service 层的 ObjectMapper。
 * <p>
 * {@code version} 为乐观锁版本号，更新时必须使用
 * {@code WHERE id = ? AND version = ?} 条件更新（M1 WP4 实现）。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("t_travel_plan")
public class TravelPlan extends BaseEntity {

    private Long userId;

    private String title;

    /** DRAFT / CONFIRMED / IN_PROGRESS / COMPLETED / CANCELLED */
    private String status;

    private String originCity;

    /** 主目的地，列表页筛选用 */
    private String destination;

    /** Q-01：JSON 数组文本，[0] 必须等于 destination */
    private String destinations;

    private LocalDate startDate;

    private LocalDate endDate;

    private Integer days;

    private Integer travelers;

    /** JSON：总额 + 币种 + FactField */
    private String budget;

    /** JSON：兴趣/交通/住宿/特殊要求 + 四项 *Source */
    private String preferences;

    /** JSON：注意事项文本数组 */
    private String risks;

    /** AI 生成说明，纯文本（08 决议 B-4：不接受 Markdown） */
    private String aiNote;

    /** verified=false 的 FactField 数量，落库前由后端重算 */
    private Integer unverifiedFieldCount;

    /** 乐观锁版本号，每次更新 +1 */
    private Integer version;

    private LocalDateTime confirmedAt;

    private LocalDateTime startedAt;

    private LocalDateTime completedAt;
}
