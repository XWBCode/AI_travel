package com.aitravel.modules.plan.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import lombok.Data;

import java.util.List;

/**
 * TravelPlan v1 草稿（AI 输出 / 落库入参），与
 * {@code contracts/travel-plan/travel_plan_draft_v1.json} 一一对应。
 * <p>
 * 这里只做数据承载，<b>不承担任何校验</b>：结构由 Schema 校验，跨字段语义由
 * {@code PlanSchemaValidator} 的 S-1~S-6 负责。字段顺序、可空性与契约保持一致，
 * 便于对照排查。
 * <p>
 * 为什么不直接用 JsonNode：落库时要按类型映射到 9 张表，DTO 能在编译期暴露
 * 字段名写错的问题；JSON 原文仍会单独留存到 {@code t_ai_generation_log}。
 */
@Data
@JsonIgnoreProperties(ignoreUnknown = true)
public class PlanDraftDto {

    private String schemaVersion;

    /** Q-10：AI 侧恒为 null，落库后回填 */
    private Long planId;

    private String title;

    /** DRAFT */
    private String status;

    private Basic basic;

    private Preferences preferences;

    private List<Day> days;

    private Budget budget;

    private List<String> risks;

    private String aiNote;

    private Integer unverifiedFieldCount;

    @Data
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class Basic {
        private String originCity;
        private String destination;
        private List<String> destinations;
        private String startDate;
        private String endDate;
        private Integer days;
        private Integer travelers;
        private List<String> missingFields;
    }

    @Data
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class Preferences {
        private List<String> interests;
        private String transport;
        private String accommodation;
        private List<String> specialRequirements;
        private String interestsSource;
        private String transportSource;
        private String accommodationSource;
        private String specialRequirementsSource;
    }

    /**
     * L0 阶段的 FactField：{@code value} 恒为 null、{@code display} 恒为「待核实」。
     * number 与 string 两个变体结构相同，共用此类型。
     */
    @Data
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class FactField {
        private Object value;
        private String display;
        private String source;
        private Boolean verified;
        private String fetchedAt;
        private String expiresAt;
        private String note;
    }

    @Data
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class Day {
        private Integer dayIndex;
        private String date;
        private String city;
        private String theme;
        private List<Item> items;
        private List<TransportSegment> transportSegments;
        private Accommodation accommodation;
    }

    @Data
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class Item {
        private Integer itemOrder;
        /** Q-09：M1 恒为 null */
        private Long attractionId;
        private String title;
        private String startTime;
        private String endTime;
        private FactField duration;
        private FactField openHours;
        private FactField ticketPrice;
        private String note;
    }

    /** Q-04：INTRA_DAY 用 itemOrder 组，INTER_CITY 用 dayIndex + city 组，另一组必须为 null */
    @Data
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class TransportSegment {
        private String scope;
        private Integer fromItemOrder;
        private Integer toItemOrder;
        private Integer fromDayIndex;
        private Integer toDayIndex;
        private String fromCity;
        private String toCity;
        private String mode;
        private FactField duration;
        private FactField distance;
        private String advice;
    }

    @Data
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class Accommodation {
        private String areaName;
        private String suggestion;
        private FactField priceRange;
    }

    @Data
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class Budget {
        private String currency;
        private FactField total;
        private List<BudgetItem> items;
        private String note;
    }

    @Data
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class BudgetItem {
        private String category;
        private String label;
        private FactField amount;
        private String note;
    }
}
