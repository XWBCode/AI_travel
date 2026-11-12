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
 * <p>
 * SSE 解析约定（与 Python 端、前端 {@code api/stream.ts}、README 契约三方一致）：
 * <pre>
 * event: delta
 * data: {"content":"增量文本"}
 * （空行结束一个事件块）
 * </pre>
 * 事件名在 {@code event:} 行，数据在 {@code data:} 行。<b>不能</b>只读 data 里的 type 字段——
 * 早期实现就是这么写的，导致事件名被整体丢弃（详见 docs/plan/2026-11-09-M1-开发计划.md 第 3 节）。
 */
@Slf4j
@Component
public class AiServiceClient {

    /** 解析用 ObjectMapper（无状态，线程安全） */
    private static final ObjectMapper MAPPER = new ObjectMapper();

    private final HttpClient httpClient;
    private final ObjectMapper objectMapper = new ObjectMapper();
    private final String baseUrl;

    public AiServiceClient(com.aitravel.common.config.AiServiceProperties props) {
        this.baseUrl = props.getBaseUrl();
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofMillis(props.getConnectTimeoutMs()))
                // SSE 走 HTTP/1.1：明文 HTTP/2 升级（h2c）对 uvicorn 无意义，只会让对端收到畸形请求
                .version(HttpClient.Version.HTTP_1_1)
                .build();
    }

    /**
     * 由调用方在回调里抛出，用于主动中止读取（例如客户端已断开）。
     * 中止不是错误：不触发 onError，也不记为失败。
     */
    public static class StreamAbortedException extends RuntimeException {
        public StreamAbortedException(String message) {
            super(message);
        }
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
                // 把对端的报错体带出来，否则只剩一个状态码，排查 422 这类问题只能靠猜
                String detail = readBodySafely(response.body());
                onError.accept("AI 服务返回状态码 " + response.statusCode()
                        + (detail.isBlank() ? "" : "：" + detail));
                return;
            }
            parseSse(response.body(), onDelta, onDone, onError);
        } catch (IOException e) {
            log.error("调用 AI 服务失败", e);
            String detail = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
            onError.accept("AI 服务连接失败：" + detail + "（请确认 " + baseUrl + " 已启动）");
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            onError.accept("请求被中断");
        }
    }

    /**
     * 非流式生成旅行计划（M1 WP4）。
     * <p>
     * 与 {@link #streamChat} 的关键差异：这里一次性拿完整 JSON，因为落库前必须整体校验，
     * 增量片段无法校验。返回体是 Python 的 {@code POST /v1/plan/generate} 原文，
     * 解析与业务判断都交给调用方（PlanService），本方法只负责传输。
     */
    public JsonNode generatePlan(List<Map<String, String>> turns, String traceId) {
        String json;
        try {
            json = objectMapper.writeValueAsString(Map.of("messages", turns));
        } catch (Exception e) {
            throw new com.aitravel.common.exception.BizException(
                    com.aitravel.common.api.ErrorCode.BAD_REQUEST, "计划请求序列化失败");
        }

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + "/v1/plan/generate"))
                .header("Content-Type", "application/json")
                .header("Accept", "application/json")
                .header("X-Trace-Id", traceId == null ? "" : traceId)
                // 单次计划生成上限 180s：Mock 下毫秒级，真实模型实测 30-45s，
                // 留出 7 天以上长行程的余量；仍小于 SSE 的 240s 兜底，超时由本层先报错
                .timeout(Duration.ofSeconds(180))
                .POST(HttpRequest.BodyPublishers.ofString(json, StandardCharsets.UTF_8))
                .build();

        try {
            HttpResponse<String> response = httpClient.send(request,
                    HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            if (response.statusCode() != 200) {
                throw new com.aitravel.common.exception.BizException(
                        com.aitravel.common.api.ErrorCode.AI_SERVICE_UNAVAILABLE,
                        "AI 服务返回状态码 " + response.statusCode());
            }
            return objectMapper.readTree(response.body());
        } catch (IOException e) {
            log.error("调用 AI 计划生成失败", e);
            String detail = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
            throw new com.aitravel.common.exception.BizException(
                    com.aitravel.common.api.ErrorCode.AI_SERVICE_UNAVAILABLE,
                    "AI 服务连接失败：" + detail + "（请确认 " + baseUrl + " 已启动）");
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new com.aitravel.common.exception.BizException(
                    com.aitravel.common.api.ErrorCode.AI_SERVICE_UNAVAILABLE, "计划生成被中断");
        }
    }

    /**
     * 只判意图，不生成计划（Python 侧是规则判定，不调模型，毫秒级）。
     * <p>
     * <b>失败不抛异常，返回 {@code null}</b>：这是一次「决策前的预判」，它挂了
     * 不该让整轮对话失败。调用方拿到 null 时按普通闲聊处理，并已在日志里留痕。
     * 超时给 5 秒也是同一考虑——预判慢到 5 秒，说明服务有问题，宁可降级。
     *
     * @return {@code data} 节点：{@code scene / needPlan / missingFields / slots / followUps}；
     *         服务不可用或返回异常时为 {@code null}
     */
    public JsonNode analyzeIntent(List<Map<String, String>> turns, String traceId) {
        String json;
        try {
            json = objectMapper.writeValueAsString(Map.of("messages", turns));
        } catch (Exception e) {
            log.warn("意图请求序列化失败，本次按普通对话处理: {}", e.getMessage());
            return null;
        }

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + "/v1/plan/intent"))
                .header("Content-Type", "application/json")
                .header("Accept", "application/json")
                .header("X-Trace-Id", traceId == null ? "" : traceId)
                .timeout(Duration.ofSeconds(5))
                .POST(HttpRequest.BodyPublishers.ofString(json, StandardCharsets.UTF_8))
                .build();

        try {
            HttpResponse<String> response = httpClient.send(request,
                    HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            if (response.statusCode() != 200) {
                log.warn("意图判定返回状态码 {}，本次按普通对话处理", response.statusCode());
                return null;
            }
            JsonNode root = objectMapper.readTree(response.body());
            return root.path("data");
        } catch (IOException e) {
            log.warn("意图判定调用失败，本次按普通对话处理: {}", e.getMessage());
            return null;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return null;
        }
    }

    /** 读取对端错误响应体（限量），失败时返回空串，不影响主流程。 */
    private static String readBodySafely(InputStream in) {
        try (InputStream body = in) {
            byte[] bytes = body.readAllBytes();
            String text = new String(bytes, StandardCharsets.UTF_8).trim();
            return text.length() > 300 ? text.substring(0, 300) + "..." : text;
        } catch (Exception e) {
            return "";
        }
    }

    /**
     * 按 SSE 规范解析事件流。包可见，便于单测直接喂字节流。
     *
     * @return 事件流的结束方式：正常结束 / 收到 done / 收到 error / 被主动中止
     */    static void parseSse(InputStream in, Consumer<String> onDelta, Consumer<JsonNode> onDone,
                         Consumer<String> onError) {
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            String eventName = null;
            StringBuilder data = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.isEmpty()) {
                    // 空行 = 一个事件块结束
                    if (dispatch(eventName, data.toString(), onDelta, onDone, onError)) {
                        return;
                    }
                    eventName = null;
                    data.setLength(0);
                    continue;
                }
                if (line.startsWith(":")) {
                    // 注释 / 心跳，忽略
                    continue;
                }
                if (line.startsWith("event:")) {
                    eventName = line.substring("event:".length()).trim();
                } else if (line.startsWith("data:")) {
                    if (data.length() > 0) {
                        data.append('\n');
                    }
                    data.append(line.substring("data:".length()).trim());
                }
            }
            // 流结束时最后一块可能没有以空行收尾
            dispatch(eventName, data.toString(), onDelta, onDone, onError);
        } catch (StreamAbortedException e) {
            log.info("读取 AI 响应已被主动中止: {}", e.getMessage());
        } catch (IOException e) {
            log.warn("读取 SSE 流中断: {}", e.getMessage());
            onError.accept("AI 响应流中断");
        }
    }

    /**
     * 分发单个事件块。
     *
     * @return true 表示事件流已终止（done / error / [DONE]），调用方应停止读取
     */
    private static boolean dispatch(String eventName, String rawData, Consumer<String> onDelta,
                                    Consumer<JsonNode> onDone, Consumer<String> onError) {
        if (rawData.isEmpty()) {
            return false;
        }
        if ("[DONE]".equals(rawData)) {
            return true;
        }
        JsonNode node;
        try {
            node = MAPPER.readTree(rawData);
        } catch (Exception e) {
            log.warn("解析 SSE 数据失败: {}", rawData);
            return false;
        }
        // 事件名优先取 event: 行；缺失时兼容早期约定的 data.type 字段
        String type = (eventName == null || eventName.isEmpty()) ? node.path("type").asText("") : eventName;
        switch (type) {
            case "delta" -> onDelta.accept(node.path("content").asText(""));
            case "done" -> {
                onDone.accept(node);
                return true;
            }
            case "error" -> {
                onError.accept(node.path("message").asText("AI 服务异常"));
                return true;
            }
            default -> log.debug("忽略未知 SSE 事件: name={} data={}", eventName, rawData);
        }
        return false;
    }
}
