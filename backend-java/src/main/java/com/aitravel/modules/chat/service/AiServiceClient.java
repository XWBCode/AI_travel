package com.aitravel.modules.chat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

/**
 * AI 服务 HTTP 客户端。
 * <p>
 * 只做一件事：把 Java 侧的消息转成 Python 服务的 SSE 请求，并按事件回调。
 * 不在此处做任何业务判断，也不缓存任何凭证。
 */
@Slf4j
@Component
public class AiServiceClient {

    private final HttpClient httpClient;
    private final ObjectMapper objectMapper = new ObjectMapper();
    private final String baseUrl;

    public AiServiceClient(com.aitravel.common.config.AiServiceProperties props) {
        this.baseUrl = props.getBaseUrl();
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofMillis(props.getConnectTimeoutMs()))
                .build();
    }

    /**
     * 发起流式对话。
     *
     * @param turns    历史消息（含本轮用户消息）
     * @param traceId  透传给 AI 服务，便于全链路追踪
     * @param onDelta  增量文本回调
     * @param onDone   完成回调，携带模型名与 token 用量
     * @param onError  错误回调
     */
    public void streamChat(List<Map<String, String>> turns, String traceId,
                           Consumer<String> onDelta,
                           Consumer<JsonNode> onDone,
                           Consumer<String> onError) {
        Map<String, Object> body = Map.of("messages", turns);
        String json;
        try {
            json = objectMapper.writeValueAsString(body);
        } catch (Exception e) {
            onError.accept("请求序列化失败");
            return;
        }

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + "/v1/chat/stream"))
                .header("Content-Type", "application/json")
                .header("Accept", "text/event-stream")
                .header("X-Trace-Id", traceId == null ? "" : traceId)
                .timeout(Duration.ofMinutes(3))
                .POST(HttpRequest.BodyPublishers.ofString(json, StandardCharsets.UTF_8))
                .build();

        try {
            HttpResponse<InputStream> response = httpClient.send(request, HttpResponse.BodyHandlers.ofInputStream());
            if (response.statusCode() != 200) {
                onError.accept("AI 服务返回状态码 " + response.statusCode());
                return;
            }
            readSse(response.body(), onDelta, onDone, onError);
        } catch (IOException e) {
            log.error("调用 AI 服务失败", e);
            onError.accept("AI 服务连接失败：" + e.getMessage());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            onError.accept("请求被中断");
        }
    }

    private void readSse(InputStream in, Consumer<String> onDelta, Consumer<JsonNode> onDone, Consumer<String> onError) {
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.isEmpty() || line.startsWith(":")) {
                    continue;
                }
                if (!line.startsWith("data:")) {
                    continue;
                }
                String payload = line.substring(5).trim();
                if ("[DONE]".equals(payload)) {
                    return;
                }
                try {
                    JsonNode node = objectMapper.readTree(payload);
                    String type = node.path("type").asText("");
                    switch (type) {
                        case "delta" -> onDelta.accept(node.path("content").asText(""));
                        case "done" -> {
                            onDone.accept(node);
                            return;
                        }
                        case "error" -> {
                            onError.accept(node.path("message").asText("AI 服务异常"));
                            return;
                        }
                        default -> log.debug("忽略未知 SSE 事件类型: {}", type);
                    }
                } catch (Exception e) {
                    log.warn("解析 SSE 数据失败: {}", payload, e);
                }
            }
        } catch (IOException e) {
            log.warn("读取 SSE 流中断: {}", e.getMessage());
            onError.accept("AI 响应流中断");
        }
    }
}
