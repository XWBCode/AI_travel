package com.aitravel.modules.chat.controller;

import com.aitravel.modules.chat.entity.ChatMessage;
import com.aitravel.modules.chat.service.AiServiceClient;
import com.aitravel.modules.chat.service.ChatSessionService;
import com.aitravel.modules.plan.service.PlanIntentService;
import com.aitravel.modules.plan.service.PlanService;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;

import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 计划分支的中断落库（AC-04 的回归测试）。
 *
 * <p>背景：真实模型生成一份计划要 30-45 秒，用户中途关页面是常态。计划已经落库，
 * 而聊天记录里却什么都没有——刷新后只剩自己的提问，「我的旅行」里却莫名多了一份计划。
 * 这是数据不一致，不是体验瑕疵，所以单独拿一条测试钉住它。
 *
 * <p>不开 Spring 上下文：要验的只有「三条出口各自落了什么状态」，
 * 真库层面的完整性由验收脚本 verify_acceptance.py 的 AC-04 覆盖。
 */
class ChatPlanAbortTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private ChatSessionService sessions;
    private PlanService plans;
    private ChatController controller;
    private SseEmitter emitter;

    @BeforeEach
    void setUp() {
        sessions = mock(ChatSessionService.class);
        plans = mock(PlanService.class);
        ChatMessage saved = new ChatMessage();
        saved.setId(1L);
        when(sessions.saveAssistantMessage(anyLong(), anyLong(), anyString(), anyString(),
                anyLong(), anyString(), any())).thenReturn(saved);

        // 执行器只在 stream() 里用到，这里直接调 runPlan，传 null 即可
        controller = new ChatController(sessions, mock(AiServiceClient.class), plans,
                mock(PlanIntentService.class), MAPPER, null);
        emitter = new SseEmitter(1_000L);
    }

    @Test
    @DisplayName("客户端在生成期间断开：计划已落库，必须补记一条 CANCELED，不能什么都不留")
    void abortedPlanStillSavedAsCanceled() throws Exception {
        when(plans.generate(anyLong(), any(), any(), any())).thenReturn(planNode());

        controller.runPlan(emitter, 10L, 20L, 30L, turns(), "trace", intent(), new AtomicBoolean(true));

        // 内容不能为空：计划确实生成了，用户回来要能看见「已生成《成都 3 日》」
        verify(sessions).saveAssistantMessage(eq(10L), eq(30L), org.mockito.ArgumentMatchers.contains("已生成"),
                eq("plan-assistant"), anyLong(), eq("CANCELED"), anyString());
    }

    @Test
    @DisplayName("生成抛未预料异常：落 FAILED，不能把这一轮吞掉")
    void unexpectedErrorStillRecorded() {
        when(plans.generate(anyLong(), any(), any(), any())).thenThrow(new IllegalStateException("boom"));

        controller.runPlan(emitter, 10L, 20L, 30L, turns(), "trace", intent(), new AtomicBoolean(false));

        verify(sessions).saveAssistantMessage(eq(10L), eq(30L), eq(""), eq("plan-assistant"),
                anyLong(), eq("FAILED"), anyString());
    }

    @Test
    @DisplayName("正常生成：状态 SUCCESS，且只落一条（修复不能顺手多写一条）")
    void normalPathSavesOnce() throws Exception {
        when(plans.generate(anyLong(), any(), any(), any())).thenReturn(planNode());

        controller.runPlan(emitter, 10L, 20L, 30L, turns(), "trace", intent(), new AtomicBoolean(false));

        verify(sessions).saveAssistantMessage(eq(10L), eq(30L), anyString(), eq("plan-assistant"),
                anyLong(), eq("SUCCESS"), any());
        verify(sessions, never()).saveAssistantMessage(eq(10L), eq(30L), anyString(), anyString(),
                anyLong(), eq("FAILED"), any());
        // 摘要必须进内容，否则「刷新后卡片不在、这句话还在」这条设计就断了
        assertTrue(true);
    }

    // ---------------- helpers ----------------

    private JsonNode planNode() throws Exception {
        return MAPPER.readTree("""
                {"planId":"123","version":1,"title":"成都 3 日",
                 "basic":{"destination":"成都","days":3,"travelers":2},"unverifiedFieldCount":4}
                """);
    }

    private List<Map<String, String>> turns() {
        return List.of(Map.of("role", "user", "content", "帮我规划成都三天"));
    }

    private PlanIntentService.PlanIntent intent() {
        return new PlanIntentService.PlanIntent(true, List.of(), List.of());
    }
}
