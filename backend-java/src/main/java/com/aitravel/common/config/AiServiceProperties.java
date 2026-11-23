package com.aitravel.common.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.annotation.Configuration;

/**
 * app.ai.* 配置：Java 调用 Python AI 服务的参数。
 */
@Data
@Configuration
@ConfigurationProperties(prefix = "app.ai")
public class AiServiceProperties {

    private String baseUrl = "http://localhost:8000";

    private int connectTimeoutMs = 5000;

    /**
     * AI 流式响应可能较慢，读超时需要给足，但不能无限。
     * 实测：DeepSeek 直连 3 天计划约 14s；交融网关的 v4-pro / v4-flash 约 85–100s，
     * 且天上界随天数线性上升。180s 对 7 天以上的行程不够，默认放到 300s，可用环境变量覆盖。
     */
    private int readTimeoutMs = 300000;

    /**
     * 单次计划生成（非流式 /v1/plan/generate）的上限。
     * 与 readTimeoutMs 分开是因为它的历史语义不同：原来硬编码 180s，
     * 在交融网关上 7 天行程要 130–160s，长尾直接顶到 180s 被判超时——
     * 这类失败会被记成「AI 服务连接失败」，看起来像宕机，其实是等得不够。
     */
    private int planTimeoutMs = 300000;

    /** 调整建议（/v1/plan/adjust）上限：输出远小于整份计划，但仍给推理模型留余量 */
    private int adjustTimeoutMs = 180000;
}
