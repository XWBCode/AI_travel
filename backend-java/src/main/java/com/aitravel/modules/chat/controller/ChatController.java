package com.aitravel.modules.chat.controller;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.api.TraceIdHolder;
import com.aitravel.common.exception.BizException;
import com.aitravel.common.web.CurrentUser;
import com.aitravel.common.web.TraceIdFilter;
import com.aitravel.modules.chat.dto.ChatTurn;
import com.aitravel.modules.chat.dto.SendMessageRequest;
import com.aitravel.modules.chat.entity.ChatMessage;
import com.aitravel.modules.chat.entity.ChatSession;
import com.aitravel.modules.chat.service.AiServiceClient;
import com.aitravel.modules.chat.service.ChatSessionService;
import com.aitravel.modules.plan.service.PlanIntentService;
import com.aitravel.modules.plan.service.PlanService;
import com.fasterxml.jackson.databind.JsonNode;
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
 * event: meta          data: {"sessionId":123,"userMessageId":456}
 * event: intent        data: {"scene":"plan","needPlan":true,"missingFields":[],"followUps":[]}
 * // followUps 元素：{"field":"days","question":"大概玩几天？","options":[{"label":"3 天","value":"玩3天"}]}
 * event: plan_progress data: {"stage":"generating","message":"正在生成计划…"}
 * event: plan_draft    data: {"plan":{TravelPlan v1}}
 * event: plan_saved    data: {"planId":"...","version":0}
 * event: plan_rejected data: {"reason":"SCHEMA_VIOLATION","message":"..."}
 * event: delta         data: {"content":"增量文本"}
 * event: done          data: {"messageId":456,"model":"...","latencyMs":123}
 * event: error         data: {"code":50201,"message":"..."}
 * </pre>
 * 一轮对话只会走三条路之一：普通闲聊（delta）、计划追问（intent + delta）、
 * 计划生成（intent → plan_draft → plan_saved）。判定由 {@link PlanIntentService} 负责，
 * 它失败时无条件降级为普通闲聊。
 * <p>
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
    private final PlanService planService;
    private final PlanIntentService planIntentService;
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

        // 真实模型生成一份计划要 30-45s，240s 兜底必须大于 AiServiceClient 的 180s 上限，
        // 否则会出现「AI 侧还没超时、连接先被掐断」的假失败
        SseEmitter emitter = new SseEmitter(240_000L);
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

    /**
     * 一轮对话的路由：闲聊 / 追问 / 生成计划，三条路互斥。
     * <p>
     * 先发 meta 再判意图，是为了让前端第一时间拿到 sessionId（新会话的标题、
     * 左侧列表都依赖它），不必等意图判定返回。
     */
    private void runStream(SseEmitter emitter, Long sessionId, Long userMessageId, Long userId,
                           List<Map<String, String>> turns, String traceId, AtomicBoolean canceled) {
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
        if (canceled.get()) {
            return;
        }

        // 意图预判：返回 null 表示闲聊或预判不可用，一律走原链路
        PlanIntentService.PlanIntent intent = planIntentService.judge(turns, traceId);
        if (intent == null) {
            runChat(emitter, sessionId, userId, turns, traceId, canceled);
        } else if (!intent.needPlan()) {
            runFollowUp(emitter, sessionId, userId, intent, canceled);
        } else {
            runPlan(emitter, sessionId, userMessageId, userId, turns, traceId, intent, canceled);
        }
    }

    /** 普通对话：原链路保持原样，计划分支不得改变它的任何行为。 */
    private void runChat(SseEmitter emitter, Long sessionId, Long userId,
                         List<Map<String, String>> turns, String traceId, AtomicBoolean canceled) {
        StringBuilder buffer = new StringBuilder();
        long startAt = System.currentTimeMillis();
        AtomicReference<String> model = new AtomicReference<>("unknown");
        AtomicBoolean settled = new AtomicBoolean(false);

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

    /**
     * 计划意图但槽位不足：把追问话术当助手回复下发并落库。
     * <p>
     * 这里刻意不调用模型：要问什么由规则确定（只问 destination / days 两项），
     * 让模型润色只会引入不确定性，还可能出现「一边追问一边自作主张先排一版行程」。
     */
    private void runFollowUp(SseEmitter emitter, Long sessionId, Long userId,
                             PlanIntentService.PlanIntent intent, AtomicBoolean canceled) {
        long startAt = System.currentTimeMillis();

        Map<String, Object> event = new LinkedHashMap<>();
        event.put("scene", "plan");
        event.put("needPlan", false);
        event.put("missingFields", intent.missingFields());
        event.put("followUps", intent.followUps());
        sendSafe(emitter, "intent", event);

        String text = planIntentService.buildFollowUpText(intent);
        if (!canceled.get()) {
            sendSafe(emitter, "delta", Map.of("content", text));
        }

        long latency = System.currentTimeMillis() - startAt;
        ChatMessage saved = chatSessionService.saveAssistantMessage(
                sessionId, userId, text, "plan-assistant", latency, "SUCCESS", null);

        Map<String, Object> done = new LinkedHashMap<>();
        done.put("messageId", saved.getId());
        done.put("model", "plan-assistant");
        done.put("latencyMs", latency);
        sendSafe(emitter, "done", done);
        emitter.complete();
    }

    /**
     * 计划意图且槽位足够：生成 → 校验 → 落库 → 下发计划事件。
     * <p>
     * 复用 {@link PlanService#generate}：它已经是「生成→校验→事务落库→留痕」的完整单元，
     * 本方法只负责转发事件和落一条聊天消息。在这里重写校验等于让两套规则漂移。
     */
    private void runPlan(SseEmitter emitter, Long sessionId, Long userMessageId, Long userId,
                         List<Map<String, String>> turns, String traceId,
                         PlanIntentService.PlanIntent intent, AtomicBoolean canceled) {
        long startAt = System.currentTimeMillis();

        Map<String, Object> event = new LinkedHashMap<>();
        event.put("scene", "plan");
        event.put("needPlan", true);
        event.put("missingFields", intent.missingFields());
        event.put("followUps", List.of());
        sendSafe(emitter, "intent", event);

        // 真实模型可能要十几秒，先给前端一个明确状态，避免聊天框长时间空白
        sendSafe(emitter, "plan_progress",
                Map.of("stage", "generating", "message", "正在生成计划…"));

        try {
            JsonNode plan = planService.generate(userId, turns, sessionId, userMessageId);
            if (canceled.get()) {
                return;
            }

            sendSafe(emitter, "plan_draft", Map.of("plan", plan));

            Map<String, Object> saved = new LinkedHashMap<>();
            saved.put("planId", plan.path("planId").asText());
            saved.put("version", plan.path("version").asInt(0));
            sendSafe(emitter, "plan_saved", saved);

            // 摘要同时下发：卡片是结构化数据，但用户第一眼需要一句人话，
            // 且刷新后卡片不在、这句话还在（它已随消息落库）
            String summary = summarizePlan(plan);
            sendSafe(emitter, "delta", Map.of("content", summary));

            long latency = System.currentTimeMillis() - startAt;
            ChatMessage message = chatSessionService.saveAssistantMessage(
                    sessionId, userId, summary, "plan-assistant", latency, "SUCCESS", null);

            Map<String, Object> done = new LinkedHashMap<>();
            done.put("messageId", message.getId());
            done.put("model", "plan-assistant");
            done.put("latencyMs", latency);
            sendSafe(emitter, "done", done);
            emitter.complete();
        } catch (BizException e) {
            // 校验不过 / 信息不足：业务表已保证 0 条（AC-07），这里只负责把原因说清楚
            long latency = System.currentTimeMillis() - startAt;
            String message = e.getMessage() == null ? "计划生成失败" : e.getMessage();
            String reason = message.contains("信息不足") ? "NEED_MORE_INFO" : "SCHEMA_VIOLATION";

            Map<String, Object> rejected = new LinkedHashMap<>();
            rejected.put("reason", reason);
            rejected.put("message", message);
            sendSafe(emitter, "plan_rejected", rejected);

            chatSessionService.saveAssistantMessage(
                    sessionId, userId, "", "plan-assistant", latency, "FAILED", message);
            completeWithError(emitter, ErrorCode.AI_SERVICE_UNAVAILABLE.getCode(), message);
            log.warn("计划生成被拒 sessionId={} reason={} msg={}", sessionId, reason, message);
        }
    }

    /**
     * 生成一条能留在会话历史里的摘要。
     * <p>
     * 卡片是流里的事件，刷新页面就没了；文字摘要会随消息落库，保证历史可读。
     */
    private String summarizePlan(JsonNode plan) {
        JsonNode basic = plan.path("basic");
        String title = plan.path("title").asText("旅行计划");
        String destination = basic.path("destination").asText("");
        int days = basic.path("days").asInt(0);
        int travelers = basic.path("travelers").asInt(0);
        int unverified = plan.path("unverifiedFieldCount").asInt(0);

        StringBuilder sb = new StringBuilder("已生成《").append(title).append("》");
        if (!destination.isBlank()) {
            sb.append("：").append(destination);
        }
        if (days > 0) {
            sb.append("，").append(days).append(" 天");
        }
        if (travelers > 0) {
            sb.append("，").append(travelers).append(" 人");
        }
        sb.append("。\n\n");
        if (unverified > 0) {
            sb.append("其中 ").append(unverified)
                    .append(" 个字段标记为「待核实」——当前未接入实时天气/票价/路况，相关数值请以实际为准。\n\n");
        }
        sb.append("计划已保存，点开卡片可查看每日安排；确认无误后再开始行程。");
        return sb.toString();
    }

    /** 推送失败只说明客户端已断开，不属于业务错误，不让异常冒到上层。 */
    private void sendSafe(SseEmitter emitter, String name, Map<String, Object> data) {
        try {
            send(emitter, name, data);
        } catch (Exception e) {
            log.debug("推送 {} 失败，客户端可能已断开", name);
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
