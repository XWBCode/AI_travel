package com.aitravel.common.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.annotation.Configuration;

/**
 * app.weather.* 配置：天气展示（M3-1）。
 *
 * <p>{@code provider} 默认 {@code none}，这不是「还没配」而是刻意的默认值：
 * Open-Meteo 免费端点明确禁止商业使用、数据以 CC BY 4.0 发布（须署名），
 * 生产环境不能因为「默认开着」就悄悄踩线。要打开必须在环境里显式写
 * {@code WEATHER_PROVIDER=open-meteo}，并且只用于开发/演示环境。
 */
@Data
@Configuration
@ConfigurationProperties(prefix = "app.weather")
public class WeatherProperties {

    /** none | open-meteo */
    private String provider = "none";

    /**
     * Java → Python 的天气查询超时。
     * <p>
     * Python 侧对上游给的是 connect 2s / read 3s，总预算约 5s；Java 再宽一点留序列化与网络余量。
     * 不能沿用 SSE 的 300s，也不能把天气请求放进 {@code ai-stream-*} 线程池——
     * 天气是页面附属信息，慢到 5s 就该降级，让用户先看到行程。
     */
    private int timeoutMs = 5000;

    /** 缓存时长。03 文档 §6：城市天气预报 30–60 分钟，取下界 */
    private int cacheTtlMinutes = 30;

    /** 缓存条数上限，防止长期运行内存无限增长（进程内缓存，单机状态） */
    private int cacheMaxEntries = 1000;

    public boolean isEnabled() {
        return provider != null && !provider.isBlank() && !"none".equalsIgnoreCase(provider);
    }
}
