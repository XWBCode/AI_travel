package com.aitravel.modules.plan.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import lombok.Data;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;

/**
 * 执行进度视图（M2）。
 *
 * <p>不复用 {@code TravelPlan} 详情：详情是「计划长什么样」，进度是「做到哪一步了」，
 * 两者更新频率差一个数量级——打一次勾不该把整份计划（含预算、跨城段、风险）重传一遍。
 *
 * <p>注意 {@code planId / itemId} 一律用字符串：雪花 ID 超出 JS 安全整数，
 * 用 number 下发会在前端丢精度，表现为「点第 3 项却勾上了第 4 项」。
 */
@Data
public class PlanProgressVO {

    private String planId;

    private String status;

    /** 全部可勾选行程项数 */
    private int total;

    private int completed;

    /** 0-100 整数，前端直接画进度条；服务端算好避免两端四舍五入规则不一致 */
    private int percent;

    private List<DayProgress> days;

    @Data
    public static class DayProgress {

        private Integer dayIndex;

        private LocalDate date;

        private String city;

        private int total;

        private int completed;

        private List<ItemProgress> items;
    }

    @Data
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public static class ItemProgress {

        private String itemId;

        private Integer itemOrder;

        private String title;

        private String startTime;

        private String endTime;

        /** 用户备注，保留用于执行时看注意事项 */
        private String note;

        private boolean completed;

        private LocalDateTime completedAt;

        /** 下一个待完成项：列表里高亮它，用户不用自己找该去哪 */
        private boolean next;
    }
}
