package com.aitravel.common.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.annotation.Configuration;

/**
 * app.weather.* 配置：天气展示（M3-1 / D-03）。
 *
 * <p>{@code provider} 默认 {@code none}，这不是「还没配」而是刻意的默认值：
 * D-03 定案后生产用和风天气（QWeather），但凭据不在仓库里。默认开着的后果是
 * 每个部署都先吃一次 401，还可能触发对方的风控（官方明确写了持续错误请求可能冻结帐号）。
 * 要打开必须在环境里显式写 {@code WEATHER_PROVIDER=qweather} 并配齐凭据。
 *
 * <p>{@code open-meteo} 仍然可用，但只用于开发/CI：其免费端点禁商用、数据以 CC BY 4.0 发布（须署名）。
 */
@Data
@Configuration
@ConfigurationProperties(prefix = "app.weather")
public class WeatherProperties {

    /** none | qweather | open-meteo */
    private String provider = "none";

    /**
     * Java → Python 的天气查询超时。
     * <p>
     * Python 侧对上游给的是 connect 2s / read 3s，总预算约 5s；Java 再宽一点留序列化与网络余量。
     * 不能沿用 SSE 的 300s，也不能把天气请求放进 {@code ai-stream-*} 线程池——
     * 天气是页面附属信息，慢到 5s 就该降级，让用户先看到行程。
     */
    private int timeoutMs = 5000;

    /**
     * 缓存时长（分钟）。D-03 从 30 改成 60：和风天气官方「逐天预报推荐缓存 1–6 小时」。
     * <p>
     * 这只是主 TTL，另有硬边界「次日 00:10 必须失效」在 {@code PlanWeatherService} 里兜底——
     * 官方缓存文档点名了跨天不刷新会「整体错一天」的坑。
     */
    private int cacheTtlMinutes = 60;

    /** 缓存条数上限，防止长期运行内存无限增长（进程内缓存，单机状态） */
    private int cacheMaxEntries = 1000;

    public boolean isEnabled() {
        return provider != null && !provider.isBlank() && !"none".equalsIgnoreCase(provider);
    }
}
