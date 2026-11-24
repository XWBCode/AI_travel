package com.aitravel.modules.plan.dto;

import com.fasterxml.jackson.databind.JsonNode;
import lombok.Data;

import java.util.ArrayList;
import java.util.List;

/**
 * 计划天气（M3-1）。
 *
 * <p>要点是 <b>降级不是错误</b>：拿不到天气照样返回 200，把 {@code available} 置 false
 * 并给出 {@code reason}。前端只认一个开关，其余按原因出文案。用 4xx/5xx 会让详情页整体失败——
 * 用户想看的是行程，不是天气。
 *
 * <p>{@code reason} 取值与 Python 侧枚举逐字一致：
 * {@code PROVIDER_DISABLED / NO_START_DATE / NO_CITY / CITY_UNKNOWN / DATE_OUT_OF_RANGE /
 * UPSTREAM_TIMEOUT / UPSTREAM_ERROR / CIRCUIT_OPEN}。
 *
 * <p>其中 {@code UPSTREAM_ERROR} 与 {@code CIRCUIT_OPEN} 必须分开：
 * 前者是本次请求<b>真的出网了</b>、上游失败；后者是熔断器已打开、本次<b>没有出网</b>，
 * 属于保护系统的主动拒绝。两者都是毫秒级返回、都是 {@code available=false}，
 * 从外部看完全同形，所以判据只能是「有没有发请求」，不能看耗时。
 *
 * <p>{@code stale=true} 表示「缓存过期且上游这次没刷新成功，给你的是上次的旧值」。
 * 这种情况下照常展示数据，但必须标出来——不标就是拿旧数据冒充当前数据。
 */
@Data
public class PlanWeatherVO {

    private boolean available;

    /**
     * 原因。同一个字段承担两件事，靠 {@code available} / {@code stale} 区分场景：
     * <ul>
     *   <li>{@code available=false}：为什么这块没有天气；</li>
     *   <li>{@code stale=true}：数据仍是上次的旧值，这里说明<b>本次刷新为什么失败</b>。
     *       不是"没有天气"——旧值还在，只是不新了。</li>
     * </ul>
     * 前端按 reason 选文案，不从 {@code note} 里解析英文枚举。
     */
    private String reason;

    private String message;

    /** OPEN_METEO / NONE */
    private String source;

    /** CC BY 4.0 要求署名，前端必须原样展示 */
    private String attribution;

    private String fetchedAt;

    private String expiresAt;

    /** 附加说明，例如「按省会昆明代表云南」「数据为上次成功获取的结果」 */
    private String note;

    /** 旧值回退标记 */
    private boolean stale;

    private List<WeatherDayVO> days = new ArrayList<>();

    public static PlanWeatherVO unavailable(String reason, String message) {
        PlanWeatherVO vo = new PlanWeatherVO();
        vo.setAvailable(false);
        vo.setReason(reason);
        vo.setMessage(message);
        vo.setSource("NONE");
        return vo;
    }

    /**
     * 一天的三组天气。
     *
     * <p>字段本身是<b>契约里的 FactField</b>（value/display/source/verified/fetchedAt/expiresAt/note），
     * 用 {@link JsonNode} 原样透传而不是重新建模：Java 重写一遍就会和 Python 各维护一份口径，
     * 而 frontend 只需要 {@code display} 和 {@code verified}。
     */
    @Data
    public static class WeatherDayVO {

        /** 相对整段行程的天序号，不是相对本次返回的第一天 */
        private int dayIndex;

        private String date;

        /** 计划里的原始写法，例如「云南」 */
        private String city;

        /** 实际取坐标的城市，例如「昆明」 */
        private String resolvedCity;

        private JsonNode weather;

        private JsonNode temperature;

        private JsonNode precipitation;

        private JsonNode wind;
    }
}
