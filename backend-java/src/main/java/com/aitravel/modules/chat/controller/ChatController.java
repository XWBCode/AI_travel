package com.aitravel.modules.chat.controller;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.api.TraceIdHolder;
import com.aitravel.common.web.CurrentUser;
import com.aitravel.common.web.TraceIdFilter;
import com.aitravel.modules.chat.dto.ChatTurn;
import com.aitravel.modules.chat.dto.SendMessageRequest;
import com.aitravel.modules.chat.entity.ChatMessage;
import com.aitravel.modules.chat.entity.ChatSession;
import com.aitravel.modules.chat.service.AiServiceClient;
import com.aitravel.modules.chat.service.ChatSessionService;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.slf4j.MDC;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * AI 对话接口（SSE 流式）。
 * <p>
 * 事件协议（前后端契约）：
 * <pre>
 * event: meta   data: {"sessionId":123,"userMessageId":456}
 * event: delta  data: {"content":"增量文本"}
 * event: done   data: {"messageId":456,"model":"...","latencyMs":123}
 * event: error  data: {"code":50201,"message":"..."}
 * </pre>
 * 与总纲 4.5 一致：流式只负责展示，落库与业务写入由 Java 负责。
 * <p>
 * 执行模型：控制器只做「落用户消息 + 组上下文 + 返回 emitter」，真正的读取与推送在
 * {@code aiStreamExecutor} 线程里跑。原因见 {@link com.aitravel.common.config.AiStreamConfig}。
 * 每个流必定以 done 或 error 收尾（R-05、05 文档第 4 节），客户端断开时按 CANCELED 落库（R-06）。
 */
@Slf4j
@RestController
@RequestMapping("/api/chat")
@RequiredArgsConstructor
public class ChatController {

    private final ChatSessionService chatSessionService;
    private final AiServiceClient aiServiceClient;
    private final ObjectMapper objectMapper;
    @Qualifier("aiStreamExecutor")
    private final ExecutorService aiStreamExecutor;

    @PostMapping(value = "/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter stream(@Valid @RequestBody SendMessageRequest req) {
        Long userId = CurrentUser.requireUserId();
        String traceId = TraceIdHolder.get();

        // 数据库写入放在流开始前完成，保证即使客户端断开也有完整记录
        ChatSession session = chatSessionService.resolveSession(userId, req);
        ChatMessage userMessage = chatSessionService.saveUserMessage(session.getId(), userId, req.getContent());
        List<ChatTurn> context = chatSessionService.buildContext(session.getId());

        List<Map<String, String>> turns = new ArrayList<>();
        context.forEach(t -> turns.add(Map.of("role", t.getRole(), "content", t.getContent())));
        if (turns.isEmpty() || !"user".equals(turns.get(turns.size() - 1).get("role"))) {
            turns.add(Map.of("role", "user", "content", req.getContent()));
        }

        // 3 分钟，与 AI 服务读超时对齐
        SseEmitter emitter = new SseEmitter(180_000L);
        AtomicBoolean canceled = new AtomicBoolean(false);

        emitter.onTimeout(() -> {
            canceled.set(true);
            log.warn("SSE 超时 sessionId={}", session.getId());
        });
        emitter.onError(e -> {
            canceled.set(true);
            log.warn("SSE 异常 sessionId={} err={}", session.getId(), e.getMessage());
        });
        emitter.onCompletion(() -> log.debug("SSE 结束 sessionId={}", session.getId()));

        try {
            aiStreamExecutor.execute(() -> {
                MDC.put(TraceIdFilter.MDC_KEY, traceId == null ? "" : traceId);
                try {
                    runStream(emitter, session.getId(), userMessage.getId(), userId, turns, traceId, canceled);
                } catch (Exception e) {
                    log.error("流式对话处理异常 sessionId={}", session.getId(), e);
                    completeWithError(emitter, ErrorCode.INTERNAL_ERROR.getCode(), "服务内部错误，请稍后重试");
                } finally {
                    MDC.remove(TraceIdFilter.MDC_KEY);
                }
            });
        } catch (RejectedExecutionException e) {
            log.warn("流式线程池已满，拒绝请求 sessionId={}", session.getId());
            completeWithError(emitter, ErrorCode.RATE_LIMITED.getCode(), "当前请求过多，请稍后再试");
        }
        return emitter;
    }

    /** 在独立线程里跑完一次完整对话，并保证「有始有终」地结账。 */
    private void runStream(SseEmitter emitter, Long sessionId, Long userMessageId, Long userId,
                           List<Map<String, String>> turns, String traceId, AtomicBoolean canceled) {
        StringBuilder buffer = new StringBuilder();
        long startAt = System.currentTimeMillis();
        AtomicReference<String> model = new AtomicReference<>("unknown");
        AtomicBoolean settled = new AtomicBoolean(false);

        Map<String, Object> meta = new LinkedHashMap<>();
        meta.put("sessionId", sessionId);
        meta.put("userMessageId", userMessageId);
        try {
            send(emitter, "meta", meta);
        } catch (Exception e) {
            // 建流阶段就失败，说明客户端已经走了，没必要再调用模型（省额度）
            log.warn("SSE meta 发送失败，放弃本次生成 sessionId={}: {}", sessionId, e.getMessage());
            canceled.set(true);
        }

        if (!canceled.get()) {
            aiServiceClient.streamChat(turns, traceId,
                    delta -> {
                        if (canceled.get()) {
                            return;
                        }
                        buffer.append(delta);
                        try {
                            send(emitter, "delta", Map.of("content", delta));
                        } catch (Exception e) {
                            // 客户端已断开（关页面 / 点停止）：中止读取，不再继续消耗模型额度（R-06）
                            log.debug("推送 delta 失败，客户端可能已断开 sessionId={}: {}", sessionId, e.getMessage());
                            canceled.set(true);
                            throw new AiServiceClient.StreamAbortedException("客户端已断开");
                        }
                    },
                    doneNode -> {
                        long latency = System.currentTimeMillis() - startAt;
                        String usedModel = doneNode.path("model").asText("unknown");
                        model.set(usedModel);
                        ChatMessage saved = chatSessionService.saveAssistantMessage(
                                sessionId, userId, buffer.toString(), usedModel, latency, "SUCCESS", null);
                        settled.set(true);
                        Map<String, Object> done = new LinkedHashMap<>();
                        done.put("messageId", saved.getId());
                        done.put("model", usedModel);
                        done.put("latencyMs", latency);
                        try {
                            send(emitter, "done", done);
                        } catch (Exception e) {
                            log.debug("推送 done 失败，客户端可能已断开");
                        }
                        emitter.complete();
                    },
                    errorMsg -> {
                        long latency = System.currentTimeMillis() - startAt;
                        // 已生成的部分内容同样落库并标记 FAILED，避免用户看到的内容凭空消失
                        chatSessionService.saveAssistantMessage(
                                sessionId, userId, buffer.toString(), model.get(), latency, "FAILED", errorMsg);
                        settled.set(true);
                        completeWithError(emitter, ErrorCode.AI_SERVICE_UNAVAILABLE.getCode(), errorMsg);
                    });
        }

        // 兜底：既没有 done 也没有 error（对端静默断开、超时、客户端中止），必须结账
        if (!settled.get()) {
            long latency = System.currentTimeMillis() - startAt;
            boolean aborted = canceled.get();
            boolean hasContent = !buffer.isEmpty();
            String status = aborted ? "CANCELED" : "FAILED";
            String reason = aborted ? "用户停止或连接已断开" : "AI 响应流意外结束";
            // 中止且一个字都没产出时不落空消息，避免历史里出现空白气泡
            if (hasContent || !aborted) {
                chatSessionService.saveAssistantMessage(
                        sessionId, userId, buffer.toString(), model.get(), latency, status, reason);
            }
            settled.set(true);
            if (aborted) {
                emitter.complete();
            } else {
                completeWithError(emitter, ErrorCode.AI_SERVICE_UNAVAILABLE.getCode(), reason);
            }
        }
    }

    private void send(SseEmitter emitter, String name, Map<String, Object> data) throws Exception {
        emitter.send(SseEmitter.event().name(name)
                .data(objectMapper.writeValueAsString(data), MediaType.APPLICATION_JSON));
    }

    private void completeWithError(SseEmitter emitter, int code, String message) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("code", code);
        payload.put("message", message);
        try {
            send(emitter, "error", payload);
        } catch (Exception ignored) {
            log.debug("推送 error 失败，客户端可能已断开");
        }
        emitter.complete();
    }
}
