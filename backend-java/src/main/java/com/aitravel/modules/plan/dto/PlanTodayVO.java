package com.aitravel.modules.plan.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import lombok.Data;

import java.time.LocalDate;

/**
 * 当日执行视图（M2，R-21）。
 *
 * <p>和进度视图分开的原因：进度回答「整趟做到哪了」，当日视图回答「<b>现在</b>该做什么」。
 * 前者是汇总，后者要带一个明确的「第几天」——而这个天数是算出来的，不是用户选出来的，
 * 所以必须把计算依据（{@code date}、{@code phase}）一起回传：前端据此显示
 * 「第 3 天 / 共 5 天」「距出发 2 天」「已超期 1 天」，而不是自己再猜一遍。
 *
 * <p>{@code phase} 取值：
 * <ul>
 *   <li>{@code TODAY}：今天在行程区间内，{@code day} 就是今天的卡片；</li>
 *   <li>{@code BEFORE}：还没出发，{@code day} 是第 1 天的预览，{@code daysUntilStart} &gt; 0；</li>
 *   <li>{@code AFTER}：已过结束日但没点「完成」，{@code day} 停在最后一天，{@code daysSinceEnd} &gt; 0；</li>
 *   <li>{@code UNANCHORED}：计划没有出发日，无法把「今天」映射到任何一天，
 *       此时 {@code dayIndex} 与 {@code day} 都是 null，页面要引导用户补日期或直接开始旅行。</li>
 * </ul>
 *
 * <p>没有 {@code hint} 字段：这类文案由前端按 phase 渲染，放后端会让「一句话提示」
 * 变成需要跟着 UI 改的接口变更。
 */
@Data
@JsonInclude(JsonInclude.Include.NON_NULL)
public class PlanTodayVO {

    private String planId;

    private String status;

    private String title;

    /** 本次计算所用的「今天」（前端传入，缺省为服务端东八区当天） */
    private LocalDate date;

    /** TODAY / BEFORE / AFTER / UNANCHORED */
    private String phase;

    /** 今天对应第几天；UNANCHORED 时为 null */
    private Integer dayIndex;

    /** 行程总天数 */
    private Integer totalDays;

    /** BEFORE 时 >0：距出发还有几天 */
    private Integer daysUntilStart;

    /** AFTER 时 >0：超过结束日几天 */
    private Integer daysSinceEnd;

    /** 当日卡片；UNANCHORED 时为 null。空白日返回空 items，不是 null */
    private PlanProgressVO.DayProgress day;

    /** 整趟行程的进度，和当日无关，方便页头一直显示 */
    private int total;

    private int completed;

    private int percent;
}
