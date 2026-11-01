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

    /** AI 流式响应可能较慢，读超时需要给足，但不能无限 */
    private int readTimeoutMs = 180000;
}
