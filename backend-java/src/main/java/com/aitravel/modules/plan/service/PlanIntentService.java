package com.aitravel.modules.plan.service;

import com.aitravel.modules.chat.service.AiServiceClient;
import com.fasterxml.jackson.databind.JsonNode;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 聊天入口的意图预判。
 * <p>
 * 职责单一：判断这一轮该「生成计划」「追问槽位」还是「普通闲聊」，并把追问
 * 字段翻译成用户看得懂的问句。生成与落库仍然走 {@link PlanService}，这里不碰。
 * <p>
 * 判定交给 Python 的规则实现（{@code /v1/plan/intent}），本类只做协议适配：
 * 字段名对外是 {@code destination/days}，对用户是「想去哪个城市？」。
 * <p>
 * 设计取舍：预判失败时返回 {@code null} 而不是抛异常。意图服务只是「决策前的
 * 预判」，它挂了不该让整轮对话失败，调用方会降级为普通对话。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class PlanIntentService {

    /**
     * 真正卡住生成的两项（05 §3 硬性规则 3：追问最多 2 个）。
     * 其余缺失字段（出发地/兴趣/交通/住宿）由「待核实」承载，不追问。
     */
    private static final Map<String, String> QUESTIONS = Map.of(
            "destination", "想去哪个城市？",
            "days", "大概玩几天？"
    );

    /** 追问字段在追问文案里的编号前缀，避免用户看到技术字段名 */
    private static final int MAX_QUESTIONS = 2;

    private final AiServiceClient aiServiceClient;

    /**
     * @return 需要按计划流程处理时的判定结果；普通闲聊或预判不可用时返回 {@code null}
     */
    public PlanIntent judge(List<Map<String, String>> turns, String traceId) {
        JsonNode data = aiServiceClient.analyzeIntent(turns, traceId);
        if (data == null || data.isNull()) {
            return null;
        }
        if (!"plan".equals(data.path("scene").asText(""))) {
            return null;
        }

        boolean needPlan = data.path("needPlan").asBoolean(false);
        List<String> missing = readStrings(data.path("missingFields"));
        List<String> questions = new ArrayList<>();
        for (String field : readStrings(data.path("followUps"))) {
            String question = QUESTIONS.get(field);
            if (question != null && !questions.contains(question) && questions.size() < MAX_QUESTIONS) {
                questions.add(question);
            }
        }

        // 该生成却没有任何追问项：说明判定与槽位不一致，交给生成路径自行校验报错，
        // 避免这里把用户卡在一个「答了也没用」的追问上
        if (!needPlan && questions.isEmpty()) {
            log.warn("意图判定为 plan 但既不需要生成也无追问项，按普通对话处理 trace={}", traceId);
            return null;
        }

        log.info("意图预判 trace={} scene=plan needPlan={} missing={} questions={}",
                traceId, needPlan, missing, questions);
        return new PlanIntent(needPlan, missing, questions);
    }

    /**
     * 把追问拼成一段可以直接当助手回复的话。
     * <p>
     * 用编号列表而不是「请补充 days」这种技术表述——这句话会原样落库并展示给用户。
     */
    public String buildFollowUpText(PlanIntent intent) {
        List<String> questions = intent.questions();
        String lead = questions.size() > 1
                ? "好的，再确认两件事就能出计划："
                : "好的，再确认一件事就能出计划：";
        StringBuilder sb = new StringBuilder(lead);
        sb.append("\n\n");
        for (int i = 0; i < questions.size(); i++) {
            sb.append(i + 1).append(". ").append(questions.get(i)).append('\n');
        }
        sb.append("\n补充后我直接给你排行程。");
        return sb.toString();
    }

    private List<String> readStrings(JsonNode node) {
        List<String> values = new ArrayList<>();
        if (node != null && node.isArray()) {
            node.forEach(item -> {
                String text = item.asText("");
                if (!text.isBlank()) {
                    values.add(text);
                }
            });
        }
        return values;
    }

    /**
     * @param needPlan       槽位是否足够直接生成
     * @param missingFields  Schema basic 视角的缺失字段（含由「待核实」承载的可选项）
     * @param questions      需要向用户追问的问句，最多 2 条
     */
    public record PlanIntent(boolean needPlan, List<String> missingFields, List<String> questions) {
    }
}
