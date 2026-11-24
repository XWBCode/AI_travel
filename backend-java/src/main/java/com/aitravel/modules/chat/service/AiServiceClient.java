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
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
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
    /** 单次流式读取的总时长上限，与 app.ai.read-timeout-ms 一致 */
    private final Duration readTimeout;
    /** 计划生成与调整建议的上限（非流式，整包返回） */
    private final Duration planTimeout;
    private final Duration adjustTimeout;
    /** 天气查询上限（M3-1）：页面附属信息，慢到 5s 就该降级 */
    private final Duration weatherTimeout;
    /** 灰度身份签名密钥（M3-3 · S5）：空串 = 不发身份头，Python 侧按"不可信"处理 */
    private final String internalSecret;

    public AiServiceClient(com.aitravel.common.config.AiServiceProperties props) {
        this.baseUrl = props.getBaseUrl();
        this.readTimeout = Duration.ofMillis(props.getReadTimeoutMs());
        this.planTimeout = Duration.ofMillis(props.getPlanTimeoutMs());
        this.adjustTimeout = Duration.ofMillis(props.getAdjustTimeoutMs());
        this.weatherTimeout = Duration.ofMillis(props.getWeatherTimeoutMs());
        this.internalSecret = props.getInternalSecret() == null ? "" : props.getInternalSecret();
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
                .timeout(readTimeout)
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
                // 从硬编码 180s 改为配置（默认 300s）：交融网关 7 天行程实测 130–160s，
                // 180s 会稳定地把长尾误判成「连接失败」——用户看到的是宕机，其实是等得不够
                .timeout(planTimeout)
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
     * 生成调整建议（M2）。
     * <p>
     * 与 {@link #generatePlan} 的区别不只是端点：调整的输入是**当前计划的可编辑视图**
     * （Java 侧构造，带 itemId），输出是一组增量操作，不是完整计划。
     * 传完整存储模型没有意义——模型既不需要预算和偏好，也无法引用没有 itemId 的行程项。
     *
     * @param planView    {@code {title,totalDays,days:[{dayIndex,date,city,theme,items:[...]}]}}
     * @param instruction 用户原话
     * @param userId      当前登录用户（仅用于灰度身份签名，不进入请求体）
     * @return Python 返回的 {@code data} 节点：{@code summary / ops}
     */
    public JsonNode adjustPlan(JsonNode planView, String instruction, String traceId, Long userId) {
        String json;
        try {
            json = objectMapper.writeValueAsString(Map.of("instruction", instruction, "plan", planView));
        } catch (Exception e) {
            throw new com.aitravel.common.exception.BizException(
                    com.aitravel.common.api.ErrorCode.BAD_REQUEST, "调整请求序列化失败");
        }

        String safeTrace = traceId == null ? "" : traceId;
        HttpRequest.Builder builder = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + "/v1/plan/adjust"))
                .header("Content-Type", "application/json")
                .header("Accept", "application/json")
                .header("X-Trace-Id", safeTrace)
                // 比生成短：调整只改几项，输出远小于整份计划；默认 180s，可配
                .timeout(adjustTimeout);
        // 灰度身份（M3-3 · S5）：只在配了密钥且拿得到 userId 时才发。
        // 两个头必须成对出现——只发 X-User-Id 等于给对方一个可被冒用的字符串，
        // Python 侧也会因为缺签名直接判为不可信。
        String identitySig = userId == null ? "" : signIdentity(userId, safeTrace);
        if (!identitySig.isEmpty()) {
            builder.header("X-User-Id", String.valueOf(userId))
                    .header("X-Identity-Sig", identitySig);
        }
        HttpRequest request = builder
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
            JsonNode root = objectMapper.readTree(response.body());
            int code = root.path("code").asInt(0);
            if (code != 0) {
                // 模型给的建议不合法时，Python 侧已经翻译成人话，原样转给用户即可
                throw new com.aitravel.common.exception.BizException(
                        com.aitravel.common.api.ErrorCode.ADJUSTMENT_INVALID,
                        root.path("message").asText("调整建议生成失败"));
            }
            return root.path("data");
        } catch (IOException e) {
            log.error("调用 AI 调整建议失败", e);
            String detail = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
            throw new com.aitravel.common.exception.BizException(
                    com.aitravel.common.api.ErrorCode.AI_SERVICE_UNAVAILABLE,
                    "AI 服务连接失败：" + detail + "（请确认 " + baseUrl + " 已启动）");
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new com.aitravel.common.exception.BizException(
                    com.aitravel.common.api.ErrorCode.AI_SERVICE_UNAVAILABLE, "调整建议生成被中断");
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

    /**
     * 查询逐日天气（M3-1）。
     * <p>
     * <b>失败一律返回 null，不抛异常</b>：天气是计划详情页的附属信息，
     * 它挂了不该让整页失败。调用方（PlanWeatherService）拿到 null 后转成
     * {@code UPSTREAM_ERROR} 降级，用户看到的是一行提示而不是白屏。
     * <p>
     * 可预期的失败（城市未知、超出预报窗口、上游超时……）由 Python 侧用
     * {@code ok=false + reason} 表达，这里原样带回，不在 Java 翻译——
     * 文案归前端、判断归服务，中间的传输层不掺业务语义。
     *
     * @param city      计划里的城市，可为中文（会做 URL 编码）
     * @param startDate 出发日期 {@code yyyy-MM-dd}；为 null 表示计划没有日期
     * @param days      需要几天
     * @return Python 的响应体；服务不可用或响应非法时为 {@code null}
     */
    public JsonNode fetchWeather(String city, String startDate, int days, String traceId) {
        String query = "?days=" + days
                + "&city=" + URLEncoder.encode(city == null ? "" : city, StandardCharsets.UTF_8)
                + "&start_date=" + URLEncoder.encode(startDate == null ? "" : startDate, StandardCharsets.UTF_8);

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + "/v1/weather" + query))
                .header("Accept", "application/json")
                .header("X-Trace-Id", traceId == null ? "" : traceId)
                .timeout(weatherTimeout)
                .GET()
                .build();

        try {
            HttpResponse<String> response = httpClient.send(request,
                    HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            if (response.statusCode() != 200) {
                log.warn("天气查询返回状态码 {}（按降级处理）", response.statusCode());
                return null;
            }
            return objectMapper.readTree(response.body());
        } catch (IOException e) {
            log.warn("天气查询失败（按降级处理）: {}", e.getMessage());
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

    /**
     * 灰度身份签名：{@code HMAC-SHA256(secret, "<userId>|<traceId>")} 的小写十六进制。
     * <p>
     * 与 Python 侧 {@code inject_gate.sign} 必须逐字同算法——这是跨语言的协议，
     * 改一边不改另一边，灰度会静默地对所有人关闭（而不是报错）。
     *
     * @return 签名为空串表示"发不出可信身份"（没配密钥或算法异常），调用方应不发头
     */
    private String signIdentity(Long userId, String traceId) {
        if (internalSecret.isBlank()) {
            return "";
        }
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(internalSecret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            byte[] digest = mac.doFinal((userId + "|" + traceId).getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder(digest.length * 2);
            for (byte b : digest) {
                hex.append(Character.forDigit((b >> 4) & 0xF, 16));
                hex.append(Character.forDigit(b & 0xF, 16));
            }
            return hex.toString();
        } catch (Exception e) {
            // 签不出来就**不发**，让 Python 判为不可信：宁可灰度不生效，不可发出一个假身份
            log.warn("灰度身份签名失败，本次不发送身份头: {}", e.getClass().getSimpleName());
            return "";
        }
    }
}
