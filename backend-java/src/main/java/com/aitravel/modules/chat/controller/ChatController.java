package com.aitravel.modules.chat.controller;

import com.aitravel.common.api.TraceIdHolder;
import com.aitravel.common.web.CurrentUser;
import com.aitravel.modules.chat.dto.ChatTurn;
import com.aitravel.modules.chat.dto.SendMessageRequest;
import com.aitravel.modules.chat.entity.ChatMessage;
import com.aitravel.modules.chat.entity.ChatSession;
import com.aitravel.modules.chat.service.AiServiceClient;
import com.aitravel.modules.chat.service.ChatSessionService;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

/**
 * AI 对话接口（SSE 流式）。
 * <p>
 * 事件协议（前后端契约）：
 * <pre>
 * event: meta   data: {"sessionId":123,"messageId":456}
 * event: delta  data: {"content":"增量文本"}
 * event: done   data: {"messageId":456,"model":"...","latencyMs":123}
 * event: error  data: {"code":50201,"message":"..."}
 * </pre>
 * 与总纲 4.5 一致：流式只负责展示，落库与业务写入由 Java 负责。
 */
@Slf4j
@RestController
@RequestMapping("/api/chat")
@RequiredArgsConstructor
public class ChatController {

    private final ChatSessionService chatSessionService;
    private final AiServiceClient aiServiceClient;
    private final ObjectMapper objectMapper;

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

        // 3 分钟，与 AI 服务读超时对齐；客户端断开不会阻塞线程池
        SseEmitter emitter = new SseEmitter(180_000L);
        StringBuilder buffer = new StringBuilder();
        long startAt = System.currentTimeMillis();
        AtomicReference<String> model = new AtomicReference<>("unknown");
        AtomicReference<Boolean> finished = new AtomicReference<>(false);

        try {
            emitter.send(SseEmitter.event().name("meta")
                    .data(objectMapper.writeValueAsString(Map.of(
                            "sessionId", session.getId(),
                            "userMessageId", userMessage.getId())), MediaType.APPLICATION_JSON));
        } catch (Exception e) {
            log.warn("SSE 建立失败: {}", e.getMessage());
            emitter.completeWithError(e);
            return emitter;
        }

        emitter.onTimeout(() -> log.warn("SSE 超时 sessionId={}", session.getId()));
        emitter.onError(e -> log.warn("SSE 异常 sessionId={} err={}", session.getId(), e.getMessage()));

        aiServiceClient.streamChat(turns, traceId,
                delta -> {
                    buffer.append(delta);
                    try {
                        emitter.send(SseEmitter.event().name("delta")
                                .data(objectMapper.writeValueAsString(Map.of("content", delta)),
                                        MediaType.APPLICATION_JSON));
                    } catch (Exception e) {
                        // 客户端可能已关闭页面，属于正常现象，不提升为错误
                        log.debug("推送 delta 失败，客户端可能已断开: {}", e.getMessage());
                    }
                },
                doneNode -> {
                    long latency = System.currentTimeMillis() - startAt;
                    String usedModel = doneNode.path("model").asText("unknown");
                    model.set(usedModel);
                    ChatMessage saved = chatSessionService.saveAssistantMessage(
                            session.getId(), userId, buffer.toString(), usedModel, latency, "SUCCESS", null);
                    finished.set(true);
                    try {
                        emitter.send(SseEmitter.event().name("done")
                                .data(objectMapper.writeValueAsString(Map.of(
                                        "messageId", saved.getId(),
                                        "model", usedModel,
                                        "latencyMs", latency)), MediaType.APPLICATION_JSON));
                    } catch (Exception ignored) {
                        log.debug("推送 done 失败，客户端可能已断开");
                    }
                    emitter.complete();
                },
                errorMsg -> {
                    long latency = System.currentTimeMillis() - startAt;
                    // 已生成的部分内容同样落库并标记 FAILED，避免用户看到的内容凭空消失
                    chatSessionService.saveAssistantMessage(
                            session.getId(), userId, buffer.toString(), model.get(), latency, "FAILED", errorMsg);
                    finished.set(true);
                    try {
                        emitter.send(SseEmitter.event().name("error")
                                .data(objectMapper.writeValueAsString(Map.of(
                                        "code", 50201, "message", errorMsg)), MediaType.APPLICATION_JSON));
                    } catch (Exception ignored) {
                        log.debug("推送 error 失败，客户端可能已断开");
                    }
                    emitter.complete();
                });
        return emitter;
    }
}
