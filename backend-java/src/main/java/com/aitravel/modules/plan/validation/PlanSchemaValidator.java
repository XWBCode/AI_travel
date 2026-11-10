package com.aitravel.modules.plan.validation;

import com.aitravel.common.exception.BizException;
import com.aitravel.common.api.ErrorCode;
import com.aitravel.modules.plan.dto.PlanDraftDto;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SpecVersion;
import com.networknt.schema.ValidationMessage;
import lombok.extern.slf4j.Slf4j;
import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.io.InputStream;
import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * 计划草稿校验器。
 * <p>
 * 分两层，缺一不可：
 * <ol>
 *   <li><b>结构校验</b>：直接拿 {@code contracts/travel-plan/travel_plan_draft_v1.json}
 *       （构建时打进 classpath:schemas）做 JSON Schema 校验。刻意不在 Java 里另写一套字段规则——
 *       两套规则一定会漂移，而漂移的后果是「Python 拒了、Java 收了」这类最难查的问题。</li>
 *   <li><b>语义校验</b>：JSON Schema 表达不了跨字段约束，按
 *       {@code docs/design/08-TravelPlan-v1-冻结决议.md} 第 4 节的 S-1~S-6 逐条实现，
 *       另加 Q-12 的日期一致性。</li>
 * </ol>
 * 结构不合法时直接返回，不再跑语义规则：结构都错了，语义检查只会产出级联噪音。
 */
@Slf4j
@Component
public class PlanSchemaValidator {

    /** 契约在 classpath 中的位置，由 pom 的 &lt;resources&gt; 从 contracts/ 复制而来 */
    private static final String DRAFT_SCHEMA_PATH = "schemas/travel_plan_draft_v1.json";

    /**
     * S-4 参与比对的字段：<b>只有 basic 的 6 个字段</b>。
     * <p>
     * 刻意不含 {@code preferences.transport/accommodation}：Q-03 引入的
     * {@code transportSource=USER_DEFAULT} 已经表达了「用户没提、按默认值处理」，
     * 这与「未知」是两件事。把它们算进 S-4 会误拒合法计划（合规样例就踩到过）。
     * 也不含 {@code budget}（契约中恒为对象，未知体现在 total 的 FactField 状态）与
     * {@code interests}（空数组可能是用户明确说没有偏好）。
     */
    private static final List<String> DERIVABLE_MISSING_FIELDS = List.of(
            "originCity", "destination", "startDate", "endDate", "days", "travelers");

    private static final Set<String> CATEGORY_ALLOWED = Set.of(
            "TRANSPORT", "ACCOMMODATION", "FOOD", "TICKET", "OTHER");

    private final JsonSchema draftSchema;
    private final ObjectMapper mapper;

    public PlanSchemaValidator(ObjectMapper mapper) {
        this.mapper = mapper;
        JsonSchemaFactory factory = JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V202012);
        try (InputStream in = new ClassPathResource(DRAFT_SCHEMA_PATH).getInputStream()) {
            this.draftSchema = factory.getSchema(mapper.readTree(in));
        } catch (IOException e) {
            // 契约缺失属于部署事故：宁可启动失败，也不能让所有计划在无校验状态下落库
            throw new IllegalStateException("无法加载计划契约 " + DRAFT_SCHEMA_PATH
                    + "，请确认构建时已复制 contracts/travel-plan 到 classpath", e);
        }
        log.info("计划契约已加载: {}", DRAFT_SCHEMA_PATH);
    }

    /**
     * 校验但不抛异常，返回全部违规项（空列表表示通过）。
     */
    public List<PlanViolation> validate(JsonNode draft) {
        List<PlanViolation> violations = new ArrayList<>();
        if (draft == null || !draft.isObject()) {
            violations.add(new PlanViolation("SCHEMA_VIOLATION", "", "计划体必须是 JSON 对象"));
            return violations;
        }

        Set<ValidationMessage> schemaErrors = draftSchema.validate(draft);
        for (ValidationMessage vm : schemaErrors) {
            String path = vm.getInstanceLocation() == null ? "" : vm.getInstanceLocation().toString();
            violations.add(new PlanViolation("SCHEMA_VIOLATION", path, vm.getMessage()));
        }
        if (!violations.isEmpty()) {
            return violations;
        }

        checkDayIndexContinuity(draft, violations);
        checkItemOrder(draft, violations);
        checkDestinations(draft, violations);
        checkMissingFields(draft, violations);
        checkUnverifiedCount(draft, violations);
        checkInterCitySegments(draft, violations);
        checkDateConsistency(draft, violations);
        checkBudgetCategory(draft, violations);
        return violations;
    }

    /**
     * 校验并直接拒绝：任何一条违规都抛 {@link BizException}，调用方无需自己判断。
     */
    public void validateOrThrow(JsonNode draft) {
        List<PlanViolation> violations = validate(draft);
        if (violations.isEmpty()) {
            return;
        }
        String summary = violations.stream()
                .limit(3)
                .map(PlanViolation::toString)
                .reduce((a, b) -> a + "；" + b)
                .orElse("未知原因");
        int total = violations.size();
        throw new BizException(ErrorCode.PLAN_SCHEMA_VIOLATION,
                "计划未通过校验（共 " + total + " 处）：" + summary + (total > 3 ? " …" : ""));
    }

    // ---------------- S-1 ----------------
    private void checkDayIndexContinuity(JsonNode draft, List<PlanViolation> out) {
        JsonNode days = draft.path("days");
        JsonNode basicDays = draft.path("basic").path("days");
        if (basicDays.isNumber() && basicDays.asInt() != days.size()) {
            out.add(new PlanViolation("S-1", "/days",
                    "天数不一致：basic.days=" + basicDays.asInt() + "，实际 days=" + days.size()));
        }
        for (int i = 0; i < days.size(); i++) {
            int expect = i + 1;
            JsonNode node = days.get(i).path("dayIndex");
            if (!node.isNumber() || node.asInt() != expect) {
                out.add(new PlanViolation("S-1", "/days/" + i + "/dayIndex",
                        "dayIndex 必须从 1 连续递增，期望 " + expect + "，实际 " + node.asText()));
            }
        }
    }

    // ---------------- S-2 ----------------
    private void checkItemOrder(JsonNode draft, List<PlanViolation> out) {
        JsonNode days = draft.path("days");
        for (int d = 0; d < days.size(); d++) {
            JsonNode items = days.get(d).path("items");
            Set<Integer> seen = new HashSet<>();
            int max = 0;
            for (int i = 0; i < items.size(); i++) {
                JsonNode order = items.get(i).path("itemOrder");
                int value = order.isNumber() ? order.asInt() : -1;
                if (value < 1) {
                    out.add(new PlanViolation("S-2", "/days/" + d + "/items/" + i + "/itemOrder",
                            "itemOrder 必须是 ≥1 的整数"));
                } else if (!seen.add(value)) {
                    out.add(new PlanViolation("S-2", "/days/" + d + "/items/" + i + "/itemOrder",
                            "同一天内 itemOrder 重复：" + value));
                }
                max = Math.max(max, value);
            }
            // 连续：{1..n} 恰好等于 items 数量
            if (items.size() > 0 && (seen.size() != items.size() || max != items.size())) {
                out.add(new PlanViolation("S-2", "/days/" + d + "/items",
                        "itemOrder 必须从 1 连续到 " + items.size() + "，实际最大 " + max));
            }
        }
    }

    // ---------------- S-3 ----------------
    private void checkDestinations(JsonNode draft, List<PlanViolation> out) {
        JsonNode basic = draft.path("basic");
        JsonNode destination = basic.path("destination");
        JsonNode destinations = basic.path("destinations");

        if (destination.isNull()) {
            if (destinations.size() > 0) {
                out.add(new PlanViolation("S-3", "/basic/destinations",
                        "destination 为 null 时 destinations 必须为空"));
            }
            return;
        }
        String main = destination.asText();
        if (destinations.isEmpty() || !main.equals(destinations.get(0).asText())) {
            out.add(new PlanViolation("S-3", "/basic/destinations",
                    "destinations[0] 必须等于 destination（" + main + "）"));
        }
        Set<String> allowed = new LinkedHashSet<>();
        destinations.forEach(n -> allowed.add(n.asText()));
        JsonNode days = draft.path("days");
        for (int d = 0; d < days.size(); d++) {
            String city = days.get(d).path("city").asText(null);
            if (city != null && !allowed.contains(city)) {
                out.add(new PlanViolation("S-3", "/days/" + d + "/city",
                        "城市「" + city + "」不在 destinations 中：" + allowed));
            }
        }
    }

    // ---------------- S-4 ----------------
    /**
     * S-4：只比对 basic 的 6 个字段的 null 状态与 missingFields 是否一致。
     * 排除项与理由见 {@link #DERIVABLE_MISSING_FIELDS}。
     */
    private void checkMissingFields(JsonNode draft, List<PlanViolation> out) {
        JsonNode basic = draft.path("basic");
        JsonNode missing = basic.path("missingFields");
        Set<String> declared = new HashSet<>();
        missing.forEach(n -> declared.add(n.asText()));

        for (String field : DERIVABLE_MISSING_FIELDS) {
            JsonNode holder = basic.path(field);
            boolean isUnknown = holder.isMissingNode() || holder.isNull();
            boolean listed = declared.contains(field);
            if (isUnknown && !listed) {
                out.add(new PlanViolation("S-4", "/basic/missingFields",
                        "字段 " + field + " 为 null（未知）但未列入 missingFields"));
            } else if (!isUnknown && listed) {
                out.add(new PlanViolation("S-4", "/basic/missingFields",
                        "字段 " + field + " 已有值却列在 missingFields 中"));
            }
        }
    }

    // ---------------- S-5 ----------------
    private void checkUnverifiedCount(JsonNode draft, List<PlanViolation> out) {
        int actual = countUnverifiedFactFields(draft);
        JsonNode declaredNode = draft.path("unverifiedFieldCount");
        int declared = declaredNode.isNumber() ? declaredNode.asInt() : -1;
        if (declared != actual) {
            out.add(new PlanViolation("S-5", "/unverifiedFieldCount",
                    "自报 " + declared + "，重算 " + actual + "（不得自报不实）"));
        }
    }

    /** 递归统计 FactField（同时带 display 与 verified 的对象）中 verified=false 的数量 */
    public int countUnverifiedFactFields(JsonNode node) {
        int count = 0;
        if (node == null) {
            return 0;
        }
        if (node.isObject()) {
            if (node.has("verified") && node.has("display")) {
                return node.path("verified").asBoolean(true) ? 0 : 1;
            }
            for (JsonNode child : node) {
                count += countUnverifiedFactFields(child);
            }
        } else if (node.isArray()) {
            for (JsonNode child : node) {
                count += countUnverifiedFactFields(child);
            }
        }
        return count;
    }

    // ---------------- S-6 ----------------
    private void checkInterCitySegments(JsonNode draft, List<PlanViolation> out) {
        JsonNode days = draft.path("days");
        Set<Integer> dayIndexes = new HashSet<>();
        days.forEach(d -> dayIndexes.add(d.path("dayIndex").asInt()));

        for (int d = 0; d < days.size(); d++) {
            JsonNode segments = days.get(d).path("transportSegments");
            for (int s = 0; s < segments.size(); s++) {
                JsonNode seg = segments.get(s);
                if (!"INTER_CITY".equals(seg.path("scope").asText())) {
                    continue;
                }
                String path = "/days/" + d + "/transportSegments/" + s;
                int from = seg.path("fromDayIndex").asInt(-1);
                int to = seg.path("toDayIndex").asInt(-1);
                if (!dayIndexes.contains(from) || !dayIndexes.contains(to)) {
                    out.add(new PlanViolation("S-6", path,
                            "跨城段指向不存在的天：from=" + from + " to=" + to + "，实际天 " + dayIndexes));
                } else if (from >= to) {
                    out.add(new PlanViolation("S-6", path,
                            "跨城段必须 fromDayIndex < toDayIndex，实际 " + from + " → " + to));
                }
            }
        }
    }

    // ---------------- Q-12（08 决议：日期与天数矛盾时拒绝） ----------------
    private void checkDateConsistency(JsonNode draft, List<PlanViolation> out) {
        JsonNode basic = draft.path("basic");
        String startText = basic.path("startDate").asText(null);
        String endText = basic.path("endDate").asText(null);
        if (startText == null) {
            return;
        }
        LocalDate start;
        try {
            start = LocalDate.parse(startText);
        } catch (DateTimeParseException e) {
            out.add(new PlanViolation("Q-12", "/basic/startDate", "不是合法日期：" + startText));
            return;
        }
        JsonNode days = draft.path("days");
        for (int i = 0; i < days.size(); i++) {
            String dateText = days.get(i).path("date").asText(null);
            if (dateText == null) {
                continue;
            }
            LocalDate expect = start.plusDays(i);
            if (!expect.toString().equals(dateText)) {
                out.add(new PlanViolation("Q-12", "/days/" + i + "/date",
                        "第 " + (i + 1) + " 天日期应为 " + expect + "，实际 " + dateText));
            }
        }
        if (endText != null) {
            LocalDate end;
            try {
                end = LocalDate.parse(endText);
            } catch (DateTimeParseException e) {
                out.add(new PlanViolation("Q-12", "/basic/endDate", "不是合法日期：" + endText));
                return;
            }
            if (end.isBefore(start)) {
                out.add(new PlanViolation("Q-12", "/basic/endDate", "endDate 早于 startDate"));
            } else if (days.size() > 0 && !end.equals(start.plusDays(days.size() - 1L))) {
                out.add(new PlanViolation("Q-12", "/basic/endDate",
                        "endDate=" + end + " 与 " + days.size() + " 天行程不符（应为 "
                                + start.plusDays(days.size() - 1L) + "）"));
            }
        }
    }

    private void checkBudgetCategory(JsonNode draft, List<PlanViolation> out) {
        JsonNode items = draft.path("budget").path("items");
        for (int i = 0; i < items.size(); i++) {
            String category = items.get(i).path("category").asText(null);
            if (category != null && !CATEGORY_ALLOWED.contains(category)) {
                out.add(new PlanViolation("SCHEMA_VIOLATION", "/budget/items/" + i + "/category",
                        "未知预算分类：" + category));
            }
        }
    }

    /** 供 Service 反序列化用，避免各处重复 new ObjectMapper */
    public PlanDraftDto toDto(JsonNode draft) {
        try {
            return mapper.treeToValue(draft, PlanDraftDto.class);
        } catch (Exception e) {
            throw new BizException(ErrorCode.PLAN_SCHEMA_VIOLATION, "计划体无法解析：" + e.getMessage());
        }
    }
}
