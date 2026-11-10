package com.aitravel.support;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.springframework.core.io.ClassPathResource;

import java.io.InputStream;
import java.io.UncheckedIOException;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Consumer;

/**
 * AC-07 的畸形样例集：合规样例 + 50 组「只改一处」的变异。
 * <p>
 * 抽成公共工具的原因：校验器单测要证明「全被拒」，集成测试要证明「一条都没落库」，
 * 两边必须用<b>同一份样例</b>。各自复制一份的话，很容易出现「单测拒了 50 组、
 * 落库测试只跑了 30 组」这种看起来都绿、实际没覆盖的情况。
 */
public final class PlanMutants {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private static JsonNode cachedValid;

    private PlanMutants() {
    }

    /** 合规基准样例（classpath:schemas/examples/draft.valid.json，由 pom 从 contracts/ 复制） */
    public static JsonNode validDraft() {
        if (cachedValid == null) {
            try (InputStream in = new ClassPathResource("schemas/examples/draft.valid.json").getInputStream()) {
                cachedValid = MAPPER.readTree(in);
            } catch (Exception e) {
                throw new UncheckedIOException(new java.io.IOException("无法加载契约样例", e));
            }
        }
        return cachedValid.deepCopy();
    }

    public static ObjectMapper mapper() {
        return MAPPER;
    }

    /** 恰好 50 组，任何一组「没被拒」都说明契约或校验器有漏洞 */
    public static List<Mutant> all() {
        List<Mutant> list = new ArrayList<>();

        // ---- 结构层（Schema）----
        list.add(m("schemaVersion 非法", r -> r.put("schemaVersion", "2.0")));
        list.add(m("planId 非 null", r -> r.put("planId", 123)));
        list.add(m("status 非 DRAFT", r -> r.put("status", "CONFIRMED")));
        list.add(m("title 为空", r -> r.put("title", "")));
        list.add(m("title 超长", r -> r.put("title", "x".repeat(101))));
        list.add(m("缺 title", r -> r.remove("title")));
        list.add(m("多余根字段", r -> r.put("hack", 1)));
        list.add(m("days 为空数组", r -> r.set("days", MAPPER.createArrayNode())));
        list.add(m("basic.days=0", r -> at(r, "/basic").put("days", 0)));
        list.add(m("basic.days=31", r -> at(r, "/basic").put("days", 31)));
        list.add(m("travelers=0", r -> at(r, "/basic").put("travelers", 0)));
        list.add(m("startDate 非日期", r -> at(r, "/basic").put("startDate", "2026/11/20")));
        list.add(m("attractionId 非 null", r -> at(r, "/days/0/items/0").put("attractionId", 42)));
        list.add(m("startTime 格式错", r -> at(r, "/days/0/items/0").put("startTime", "9:30")));
        list.add(m("item 缺少字段", r -> at(r, "/days/0/items/0").remove("title")));
        list.add(m("item 多余字段", r -> at(r, "/days/0/items/0").put("price", 100)));
        list.add(m("currency 非 CNY", r -> at(r, "/budget").put("currency", "USD")));
        list.add(m("预算分类非法", r -> at(r, "/budget/items/0").put("category", "SHOPPING")));
        list.add(m("住宿缺 areaName", r -> at(r, "/days/0/accommodation").remove("areaName")));
        list.add(m("risks 非数组", r -> r.set("risks", MAPPER.getNodeFactory().textNode("天气多变"))));
        list.add(m("missingFields 非枚举值", r -> array(r, "/basic/missingFields").add("vibe")));
        list.add(m("跨城段 scope 非法", r -> at(r, "/days/1/transportSegments/0").put("scope", "CROSS_BORDER")));

        // ---- L0 硬底线 1：实时值必须为空 ----
        list.add(m("duration.value 有值", r -> at(r, "/days/0/items/0/duration").put("value", 60)));
        list.add(m("openHours.value 有值", r -> at(r, "/days/0/items/0/openHours").put("value", "09:00-17:00")));
        list.add(m("ticketPrice.value 有值", r -> at(r, "/days/0/items/0/ticketPrice").put("value", 80)));
        list.add(m("budget.total.value 有值", r -> at(r, "/budget/total").put("value", 3000)));
        list.add(m("预算明细 amount 有值", r -> at(r, "/budget/items/0/amount").put("value", 1200)));
        list.add(m("住宿价格区间有值", r -> at(r, "/days/0/accommodation/priceRange").put("value", "300-500")));
        list.add(m("跨城 duration 有值", r -> at(r, "/days/1/transportSegments/0/duration").put("value", 30)));
        list.add(m("跨城 distance 有值", r -> at(r, "/days/1/transportSegments/0/distance").put("value", 12)));

        // ---- FactField 元数据 ----
        list.add(m("display 非「待核实」", r -> at(r, "/days/0/items/0/duration").put("display", "约 2 小时")));
        list.add(m("source 非法", r -> at(r, "/days/0/items/0/duration").put("source", "AI_GUESS")));
        list.add(m("verified 为 true", r -> at(r, "/days/0/items/0/duration").put("verified", true)));
        list.add(m("fetchedAt 非 null", r -> at(r, "/days/0/items/0/duration").put("fetchedAt", "2026-11-09T10:00:00")));

        // ---- L0 硬底线 2：自由文本不得夹带数值 ----
        list.add(m("aiNote 含耗时", r -> r.put("aiNote", "景点之间车程 2 小时，建议早出发")));
        list.add(m("budget.note 含金额", r -> at(r, "/budget").put("note", "整体预算约 3000 元，按中等消费估算")));
        list.add(m("item.note 含距离", r -> at(r, "/days/0/items/0").put("note", "从地铁口步行 500 米即到")));
        list.add(m("交通 advice 含时长", r -> at(r, "/days/1/transportSegments/0").put("advice", "全程约 40 分钟，建议提前购票")));

        // ---- S-1 天序号 ----
        list.add(m("S-1 dayIndex 跳号", r -> at(r, "/days/1").put("dayIndex", 5)));
        list.add(m("S-1 天数与 basic.days 不一致", r -> at(r, "/basic").put("days", 2)));

        // ---- S-2 日内顺序 ----
        list.add(m("S-2 itemOrder 重复", r -> at(r, "/days/0/items/1").put("itemOrder", 1)));
        list.add(m("S-2 itemOrder 跳号", r -> at(r, "/days/0/items/1").put("itemOrder", 5)));

        // ---- S-3 目的地一致性 ----
        list.add(m("S-3 destinations[0] 不等于 destination", r ->
                array(r, "/basic/destinations").set(0, MAPPER.getNodeFactory().textNode("乐山"))));
        list.add(m("S-3 当天城市不在 destinations", r -> at(r, "/days/0").put("city", "重庆")));
        list.add(m("S-3 destination 为 null 但 destinations 非空", r -> at(r, "/basic").putNull("destination")));

        // ---- S-4 / S-5 / Q-12 ----
        list.add(m("S-4 已填字段却列入 missingFields", r -> array(r, "/basic/missingFields").add("days")));
        list.add(m("S-4 为 null 但未列入 missingFields", r -> at(r, "/basic").putNull("originCity")));
        list.add(m("S-5 自报数量不符", r -> r.put("unverifiedFieldCount", 3)));
        list.add(m("Q-12 日期与序号错位", r -> at(r, "/days/0").put("date", "2026-11-21")));
        list.add(m("Q-12 endDate 早于 startDate", r -> at(r, "/basic").put("endDate", "2026-11-01")));

        return list;
    }

    /** 施加第 index 组变异（0-based），返回变异后的副本 */
    public static JsonNode mutate(int index) {
        Mutant mutant = all().get(index);
        JsonNode node = validDraft();
        mutant.mutator().accept((ObjectNode) node);
        return node;
    }

    private static Mutant m(String name, Consumer<ObjectNode> mutator) {
        return new Mutant(name, mutator);
    }

    /** 取指定 JSON Pointer 的可写对象；路径不存在时直接失败，避免变异「悄悄没生效」 */
    private static ObjectNode at(ObjectNode root, String pointer) {
        JsonNode node = root.at(pointer);
        if (!(node instanceof ObjectNode objectNode)) {
            throw new IllegalStateException("路径不是对象，样例结构可能变了：" + pointer);
        }
        return objectNode;
    }

    private static ArrayNode array(ObjectNode root, String pointer) {
        JsonNode node = root.at(pointer);
        if (!(node instanceof ArrayNode arrayNode)) {
            throw new IllegalStateException("路径不是数组，样例结构可能变了：" + pointer);
        }
        return arrayNode;
    }

    public record Mutant(String name, Consumer<ObjectNode> mutator) {
    }
}
