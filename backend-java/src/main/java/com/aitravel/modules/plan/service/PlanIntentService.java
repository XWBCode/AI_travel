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
     * <p>
     * 每个问题都带候选值：用户点一下就能答完，不必猜我们想要什么格式。
     * <p>
     * {@code value} 是**补答短语**而不是裸值（「去成都玩」而非「成都」）。它会被拼到
     * 用户原话后面重新判定，必须自己带上足够的语法信息——「帮我规划三天，成都」
     * 抽不出目的地，而「帮我规划三天，去成都玩」可以。见 {@code buildFollowUpReply}。
     */
    private static final Map<String, FollowUpQuestion> QUESTIONS = Map.of(
            "destination", new FollowUpQuestion("destination", "想去哪个城市？", List.of(
                    new FollowUpOption("成都", "去成都玩"),
                    new FollowUpOption("西安", "去西安玩"),
                    new FollowUpOption("杭州", "去杭州玩"),
                    new FollowUpOption("厦门", "去厦门玩"),
                    new FollowUpOption("北京", "去北京玩"),
                    new FollowUpOption("三亚", "去三亚玩")
            )),
            "days", new FollowUpQuestion("days", "大概玩几天？", List.of(
                    new FollowUpOption("2 天", "玩2天"),
                    new FollowUpOption("3 天", "玩3天"),
                    new FollowUpOption("4 天", "玩4天"),
                    new FollowUpOption("5 天", "玩5天")
            ))
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
        List<FollowUpQuestion> questions = new ArrayList<>();
        for (String field : readStrings(data.path("followUps"))) {
            FollowUpQuestion question = QUESTIONS.get(field);
            if (question != null && questions.stream().noneMatch(q -> q.field().equals(field))
                    && questions.size() < MAX_QUESTIONS) {
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
        List<FollowUpQuestion> questions = intent.followUps();
        String lead = questions.size() > 1
                ? "好的，再确认两件事就能出计划："
                : "好的，再确认一件事就能出计划：";
        StringBuilder sb = new StringBuilder(lead);
        sb.append("\n\n");
        for (int i = 0; i < questions.size(); i++) {
            sb.append(i + 1).append(". ").append(questions.get(i).question()).append('\n');
        }
        sb.append("\n直接点下面的选项，或者按自己的说法补充都行；补齐我就开始排行程。");
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
     * @param followUps      需要向用户追问的问题（含候选值），最多 2 条
     */
    public record PlanIntent(boolean needPlan, List<String> missingFields, List<FollowUpQuestion> followUps) {

        /** 仅问句，供落库文案与日志使用 */
        public List<String> questions() {
            return followUps.stream().map(FollowUpQuestion::question).toList();
        }
    }

    /**
     * 一个槽位的追问。
     *
     * @param field    槽位名，前端按它记录用户选了哪一组
     * @param question 展示给用户的问句
     * @param options  候选值，至少一个；用户点选后拼成一条补答消息
     */
    public record FollowUpQuestion(String field, String question, List<FollowUpOption> options) {
    }

    /**
     * @param label 按钮上的短标签（「3 天」）
     * @param value 拼进补答消息的短语（「玩3天」），需自带语法信息
     */
    public record FollowUpOption(String label, String value) {
    }
}
