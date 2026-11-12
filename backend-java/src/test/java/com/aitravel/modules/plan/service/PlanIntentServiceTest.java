package com.aitravel.modules.plan.service;

import com.aitravel.modules.chat.service.AiServiceClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;

/**
 * 追问选项的回归测试。
 * <p>
 * 追问不能只回一句「大概玩几天？」，得给出可以点选的明确候选值，
 * 否则用户得自己猜该按什么格式回答。这里锁住三件事：
 * 1. 缺失字段能翻译成带候选值的问句；
 * 2. 候选值是可拼回自然语言的短语（「玩3天」而不是「3天」）；
 * 3. 未知字段、闲聊、服务不可用都不会硬造追问。
 */
@ExtendWith(MockitoExtension.class)
class PlanIntentServiceTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    @Mock
    private AiServiceClient aiServiceClient;

    @InjectMocks
    private PlanIntentService service;

    private static final List<Map<String, String>> TURNS =
            List.of(Map.of("role", "user", "content", "我想去成都"));

    private JsonNode intentJson(String json) throws Exception {
        return MAPPER.readTree(json);
    }

    @Test
    @DisplayName("缺天数时下发带候选值的追问，value 是可直接拼句的短语")
    void judge_buildsOptionsForDays() throws Exception {
        when(aiServiceClient.analyzeIntent(any(), anyString())).thenReturn(intentJson("""
                {"scene":"plan","needPlan":false,"missingFields":["days"],"followUps":["days"]}
                """));

        PlanIntentService.PlanIntent intent = service.judge(TURNS, "t-1");

        assertNotNull(intent);
        assertFalse(intent.needPlan());
        assertEquals(1, intent.followUps().size());

        PlanIntentService.FollowUpQuestion question = intent.followUps().get(0);
        assertEquals("days", question.field());
        assertEquals("大概玩几天？", question.question());
        assertTrue(question.options().size() >= 3, "至少给三个天数可选");
        assertTrue(question.options().stream().anyMatch(o -> "3 天".equals(o.label())));
        // 关键：拼进原话后要是人话，后端才能重新抽出天数
        assertTrue(question.options().stream().anyMatch(o -> "玩3天".equals(o.value())));
    }

    @Test
    @DisplayName("缺目的地时给出城市候选，value 带「去…玩」以保住语法结构")
    void judge_buildsOptionsForDestination() throws Exception {
        when(aiServiceClient.analyzeIntent(any(), anyString())).thenReturn(intentJson("""
                {"scene":"plan","needPlan":false,"missingFields":["destination"],"followUps":["destination"]}
                """));

        PlanIntentService.PlanIntent intent = service.judge(TURNS, "t-2");

        assertNotNull(intent);
        PlanIntentService.FollowUpQuestion question = intent.followUps().get(0);
        assertEquals("destination", question.field());
        assertEquals("想去哪个城市？", question.question());
        assertTrue(question.options().stream().anyMatch(o -> "成都".equals(o.label())));
        // 「成都」单独拼在后面抽不出目的地，「去成都玩」才可以
        assertTrue(question.options().stream().anyMatch(o -> "去成都玩".equals(o.value())));
    }

    @Test
    @DisplayName("两个都缺时按返回顺序给两组，最多两组")
    void judge_keepsAtMostTwoQuestions() throws Exception {
        when(aiServiceClient.analyzeIntent(any(), anyString())).thenReturn(intentJson("""
                {"scene":"plan","needPlan":false,
                 "missingFields":["destination","days","budget"],
                 "followUps":["destination","days","budget","unknownField"]}
                """));

        PlanIntentService.PlanIntent intent = service.judge(TURNS, "t-3");

        assertNotNull(intent);
        assertEquals(2, intent.followUps().size());
        assertEquals(List.of("destination", "days"),
                intent.followUps().stream().map(PlanIntentService.FollowUpQuestion::field).toList());
        // 未知字段不产生追问（我们没有对应的候选值）
        assertTrue(intent.questions().stream().noneMatch(q -> q.contains("budget")));
    }

    @Test
    @DisplayName("闲聊返回 null，不得打断普通对话")
    void judge_returnsNullForChat() throws Exception {
        when(aiServiceClient.analyzeIntent(any(), anyString())).thenReturn(intentJson("""
                {"scene":"chat","needPlan":false,"missingFields":[],"followUps":[]}
                """));

        assertNull(service.judge(TURNS, "t-4"));
    }

    @Test
    @DisplayName("需要生成却没有追问项时按闲聊处理，避免卡在无内容的追问上")
    void judge_returnsNullWhenNothingToAsk() throws Exception {
        when(aiServiceClient.analyzeIntent(any(), anyString())).thenReturn(intentJson("""
                {"scene":"plan","needPlan":false,"missingFields":[],"followUps":[]}
                """));

        assertNull(service.judge(TURNS, "t-5"));
    }

    @Test
    @DisplayName("意图服务不可用时降级为普通对话")
    void judge_returnsNullWhenServiceDown() {
        when(aiServiceClient.analyzeIntent(any(), anyString())).thenReturn(null);

        assertNull(service.judge(TURNS, "t-6"));
    }

    @Test
    @DisplayName("追问文案写清要补什么，并提示可以直接点选项")
    void followUpText_mentionsOptions() throws Exception {
        when(aiServiceClient.analyzeIntent(any(), anyString())).thenReturn(intentJson("""
                {"scene":"plan","needPlan":false,"missingFields":["days"],"followUps":["days"]}
                """));

        String text = service.buildFollowUpText(service.judge(TURNS, "t-7"));

        assertTrue(text.contains("大概玩几天？"), text);
        assertTrue(text.contains("选项"), "有候选值时要告诉用户可以点选：" + text);
    }
}
