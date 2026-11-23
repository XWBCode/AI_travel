package com.aitravel.modules.plan.service;

import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * 调整建议的增量操作契约（R-23 / R-25）。
 *
 * <p><b>为什么是 ops 而不是一份新计划。</b>版本快照是整计划级 JSON，回滚语义是
 * 「整份替换 + 重建子表」，{@code itemId} 全部改变、进度清零——那对回滚是合适的，
 * 对日常调整是灾难：用户在东京勾完上午三个景点，说了句「下午那个不去了」，
 * 确认之后勾全没了。增量 ops 只碰它要碰的那几行，没提到的行程项原样保留。
 *
 * <p><b>before 一律由服务端回填。</b>模型只输出 {@code itemId + 字段 + after}。
 * 让模型抄 before 等于让它在「差异展示」里说第二遍真话——抄错一位，用户看到的
 * 「原值」就不是真正被改掉的值，而这种错没有任何地方会报出来。
 *
 * <p>本类不碰数据库，输入是一份只读的计划视图，因此可以脱库单测。
 * 服务端是最终裁判（AC-11 的工程落点）：Python 侧的同名校验只是让模型的自纠更早发生，
 * 不通过就无法进入这张表。
 */
public final class AdjustmentOps {

    /** 允许模型改的行程项字段。事实字段（duration/ticketPrice/openHours）一律不给改——
     *  它们是「待核实」的载体，模型没有任何依据去填，放开等于给编造开了后门。 */
    public static final List<String> ITEM_FIELDS = List.of("title", "startTime", "endTime", "note");

    /** 允许模型改的「天」字段。dayIndex 与日期不可改：那属于结构性变更，另走一条流程。 */
    public static final List<String> DAY_FIELDS = List.of("theme", "city");

    /** 单条建议的操作数上限。模型偶尔会试图「重排整趟行程」，那不是调整，是重新生成。 */
    public static final int MAX_OPS = 20;

    private static final Pattern HH_MM = Pattern.compile("^([01]\\d|2[0-3]):[0-5]\\d$");

    private AdjustmentOps() {
    }

    public enum Type {ADD_ITEM, REMOVE_ITEM, UPDATE_ITEM, MOVE_ITEM, REORDER_ITEM, UPDATE_DAY}

    /** 计划里的一天：只带校验需要的信息 */
    public record DayView(int dayIndex, Long dayId, String city, String theme) {
    }

    /** 计划里的一条行程项：before 值的唯一来源 */
    public record ItemView(Long itemId, int dayIndex, int itemOrder, String title,
                           String startTime, String endTime, String note) {
    }

    /**
     * 计划当前状态的只读视图。
     * {@code items} 按 {@code itemId} 索引，天然承担了「这个 item 是不是本计划的」这层归属校验。
     */
    public record PlanView(Map<Integer, DayView> days, Map<Long, ItemView> items) {
    }

    /** 一处字段变更；{@code before} 由服务端从库里的当前值回填 */
    public record Change(String field, String before, String after) {
    }

    /**
     * 一条规范化后的操作。不同 op 用到的字段不同，其余为 {@code null}：
     * <ul>
     *   <li>ADD_ITEM：dayIndex / title / startTime / endTime / note</li>
     *   <li>REMOVE_ITEM：itemId / title / dayIndex / itemOrder（后两项仅供展示）</li>
     *   <li>UPDATE_ITEM：itemId / title / dayIndex / changes</li>
     *   <li>MOVE_ITEM：itemId / title / fromDayIndex / toDayIndex</li>
     *   <li>REORDER_ITEM：dayIndex / orderedItemIds</li>
     *   <li>UPDATE_DAY：dayIndex / changes</li>
     * </ul>
     */
    public record Op(Type type, Integer dayIndex, Long itemId, String title, String startTime,
                     String endTime, String note, Integer fromDayIndex, Integer toDayIndex,
                     List<Change> changes, List<Long> orderedItemIds) {

        static Op add(Integer dayIndex, String title, String startTime, String endTime, String note) {
            return new Op(Type.ADD_ITEM, dayIndex, null, title, startTime, endTime, note,
                    null, null, List.of(), List.of());
        }

        static Op remove(ItemView item) {
            return new Op(Type.REMOVE_ITEM, item.dayIndex(), item.itemId(), item.title(), null, null, null,
                    null, null, List.of(), List.of());
        }

        static Op update(ItemView item, List<Change> changes) {
            return new Op(Type.UPDATE_ITEM, item.dayIndex(), item.itemId(), item.title(), null, null, null,
                    null, null, changes, List.of());
        }

        static Op move(ItemView item, int toDayIndex) {
            return new Op(Type.MOVE_ITEM, toDayIndex, item.itemId(), item.title(), null, null, null,
                    item.dayIndex(), toDayIndex, List.of(), List.of());
        }

        static Op reorder(int dayIndex, List<Long> orderedItemIds) {
            return new Op(Type.REORDER_ITEM, dayIndex, null, null, null, null, null,
                    null, null, List.of(), orderedItemIds);
        }

        static Op updateDay(DayView day, List<Change> changes) {
            return new Op(Type.UPDATE_DAY, day.dayIndex(), null, null, null, null, null,
                    null, null, changes, List.of());
        }
    }

    /** 建议内容不合法。消息会直接回给前端，必须是人话。 */
    public static class Invalid extends RuntimeException {
        public Invalid(String message) {
            super(message);
        }
    }

    /**
     * 把模型输出解析成可执行的操作列表。
     *
     * @throws Invalid 结构非法、引用了不存在的行程项、字段越权、或应用后必然违反契约
     */
    public static List<Op> normalize(JsonNode raw, PlanView view) {
        JsonNode array = unwrap(raw);
        if (array.size() > MAX_OPS) {
            throw new Invalid("一次调整最多 " + MAX_OPS + " 项变化，当前给了 " + array.size() + " 项");
        }

        List<Op> result = new ArrayList<>();
        Set<Long> removed = new HashSet<>();
        // 结构性操作（增 / 删）与重排不能落在同一天：重排给的是「完整的顺序列表」，
        // 而增删会改变这个列表的成员，两者同时存在时顺序以谁为准没有唯一答案
        Set<Integer> structurallyTouchedDays = new HashSet<>();
        Set<Integer> reorderedDays = new HashSet<>();

        for (int i = 0; i < array.size(); i++) {
            JsonNode node = array.get(i);
            if (!node.isObject()) {
                throw new Invalid("第 " + (i + 1) + " 项操作格式不正确");
            }
            Type type = parseType(node.path("op").asText(""), i);

            switch (type) {
                case ADD_ITEM -> result.add(parseAdd(node, view, i, structurallyTouchedDays));
                case REMOVE_ITEM -> {
                    ItemView item = requireItem(node, view, i);
                    if (!removed.add(item.itemId())) {
                        throw new Invalid("「" + item.title() + "」被重复删除");
                    }
                    structurallyTouchedDays.add(item.dayIndex());
                    result.add(Op.remove(item));
                }
                case UPDATE_ITEM -> {
                    ItemView item = requireItem(node, view, i);
                    requireNotRemoved(item, removed);
                    List<Change> changes = parseChanges(node, i, ITEM_FIELDS,
                            field -> currentItemValue(item, field));
                    // 全部 change 都被判为「值没变」时，这条 op 不该出现在 diff 里
                    if (!changes.isEmpty()) {
                        result.add(Op.update(item, changes));
                    }
                }
                case MOVE_ITEM -> {
                    ItemView item = requireItem(node, view, i);
                    requireNotRemoved(item, removed);
                    int to = requireDayIndex(node.path("toDayIndex"), view, i, "目标天");
                    if (to == item.dayIndex()) {
                        // 原天不动 = 没有变化，直接丢弃这条，不必让用户看到一行「移到第 2 天」
                        continue;
                    }
                    structurallyTouchedDays.add(item.dayIndex());
                    structurallyTouchedDays.add(to);
                    result.add(Op.move(item, to));
                }
                case REORDER_ITEM -> {
                    int dayIndex = requireDayIndex(node.path("dayIndex"), view, i, "天");
                    reorderedDays.add(dayIndex);
                    result.add(Op.reorder(dayIndex, parseOrder(node, view, dayIndex, i)));
                }
                case UPDATE_DAY -> {
                    int dayIndex = requireDayIndex(node.path("dayIndex"), view, i, "天");
                    DayView day = view.days().get(dayIndex);
                    List<Change> changes = parseChanges(node, i, DAY_FIELDS,
                            field -> "theme".equals(field) ? day.theme() : day.city());
                    if (!changes.isEmpty()) {
                        result.add(Op.updateDay(day, changes));
                    }
                }
            }
        }

        for (Integer day : reorderedDays) {
            if (structurallyTouchedDays.contains(day)) {
                throw new Invalid("第 " + day + " 天既增删了行程项又重排了顺序，请拆成两条建议");
            }
        }
        return result;
    }

    /** 兼容模型的两层包装（直接数组 / {"ops": [...]}），其余形态一律拒绝。 */
    private static JsonNode unwrap(JsonNode raw) {
        if (raw == null || raw.isNull() || raw.isMissingNode()) {
            throw new Invalid("模型没有返回任何调整操作");
        }
        JsonNode array = raw.isArray() ? raw : raw.path("ops");
        if (!array.isArray()) {
            throw new Invalid("调整操作必须是数组");
        }
        return array;
    }

    private static Type parseType(String text, int index) {
        if (text == null || text.isBlank()) {
            throw new Invalid("第 " + (index + 1) + " 项操作缺少 op 字段");
        }
        try {
            return Type.valueOf(text.trim().toUpperCase(Locale.ROOT));
        } catch (IllegalArgumentException e) {
            throw new Invalid("不支持的操作类型「" + text + "」，只允许 "
                    + "ADD_ITEM / REMOVE_ITEM / UPDATE_ITEM / MOVE_ITEM / REORDER_ITEM / UPDATE_DAY");
        }
    }

    private static Op parseAdd(JsonNode node, PlanView view, int index, Set<Integer> structurallyTouchedDays) {
        int dayIndex = requireDayIndex(node.path("dayIndex"), view, index, "天");
        String title = text(node.path("title"));
        if (title == null || title.isBlank()) {
            throw new Invalid("新增行程项缺少标题");
        }
        if (title.length() > 60) {
            throw new Invalid("行程项标题超过 60 字：" + abbreviate(title));
        }
        String start = parseTimeOrNull(node.path("startTime"), "开始时间");
        String end = parseTimeOrNull(node.path("endTime"), "结束时间");
        if (start != null && end != null && start.compareTo(end) >= 0) {
            throw new Invalid("「" + title + "」的开始时间不能晚于或等于结束时间");
        }
        String note = text(node.path("note"));
        if (note != null && note.length() > 200) {
            throw new Invalid("「" + title + "」的备注超过 200 字");
        }
        structurallyTouchedDays.add(dayIndex);
        return Op.add(dayIndex, title, start, end, note);
    }

    /** itemId 必须在 {@link PlanView#items()} 里——这一条同时挡掉了「拿别人的 itemId 来改」。 */
    private static ItemView requireItem(JsonNode node, PlanView view, int index) {
        JsonNode idNode = node.path("itemId");
        String raw = idNode.isNumber() ? idNode.asText() : text(idNode);
        if (raw == null || raw.isBlank()) {
            throw new Invalid("第 " + (index + 1) + " 项操作缺少 itemId");
        }
        long itemId;
        try {
            itemId = Long.parseLong(raw.trim());
        } catch (NumberFormatException e) {
            throw new Invalid("itemId 格式不正确：" + abbreviate(raw));
        }
        ItemView item = view.items().get(itemId);
        if (item == null) {
            throw new Invalid("这条建议要改的行程项不在该计划里（itemId=" + itemId + "）");
        }
        return item;
    }

    private static int requireDayIndex(JsonNode node, PlanView view, int index, String what) {
        if (!node.isNumber() && !(node.isTextual() && !node.asText().isBlank())) {
            throw new Invalid("第 " + (index + 1) + " 项操作缺少" + what + "序号");
        }
        int dayIndex;
        try {
            dayIndex = node.isNumber() ? node.asInt() : Integer.parseInt(node.asText().trim());
        } catch (NumberFormatException e) {
            throw new Invalid(what + "序号不是整数");
        }
        if (!view.days().containsKey(dayIndex)) {
            throw new Invalid("计划里没有第 " + dayIndex + " 天（共 " + view.days().size() + " 天）");
        }
        return dayIndex;
    }

    private static void requireNotRemoved(ItemView item, Set<Long> removed) {
        if (removed.contains(item.itemId())) {
            throw new Invalid("「" + item.title() + "」在同一条建议里既被删除又被修改");
        }
    }

    private static List<Change> parseChanges(JsonNode node, int index, List<String> allowed, ValueLookup before) {
        JsonNode array = node.path("changes");
        if (!array.isArray() || array.isEmpty()) {
            throw new Invalid("第 " + (index + 1) + " 项修改操作没有给出 changes");
        }
        Map<String, Change> byField = new LinkedHashMap<>();
        for (JsonNode change : array) {
            String field = text(change.path("field"));
            if (field == null || !allowed.contains(field)) {
                throw new Invalid("不支持修改字段「" + field + "」，只允许 " + String.join(" / ", allowed));
            }
            String after = validateValue(field, change.path("after"));
            String currentBefore = before.get(field);
            // 值没变就不进 diff：模型经常把「保持原样」也列出来，那会让用户以为改了什么
            if (sameValue(currentBefore, after)) {
                continue;
            }
            if (byField.containsKey(field)) {
                throw new Invalid("字段「" + field + "」在同一条操作里被改了两次");
            }
            byField.put(field, new Change(field, currentBefore, after));
        }
        return new ArrayList<>(byField.values());
    }

    private static String currentItemValue(ItemView item, String field) {
        return switch (field) {
            case "title" -> item.title();
            case "startTime" -> item.startTime();
            case "endTime" -> item.endTime();
            case "note" -> item.note();
            default -> null;
        };
    }

    /** 只做「值本身是否合法」的判断；跨字段一致性（开始 < 结束）在应用阶段统一校验。 */
    private static String validateValue(String field, JsonNode node) {
        String value = text(node);
        if ("title".equals(field)) {
            if (value == null || value.isBlank()) {
                throw new Invalid("标题不能改成空");
            }
            if (value.length() > 60) {
                throw new Invalid("标题超过 60 字");
            }
            return value;
        }
        if ("startTime".equals(field) || "endTime".equals(field)) {
            if (value == null) {
                return null;
            }
            if (!HH_MM.matcher(value).matches()) {
                throw new Invalid("时间必须是 HH:mm 格式，收到「" + abbreviate(value) + "」");
            }
            return value;
        }
        if (value != null && value.length() > 200) {
            throw new Invalid("备注超过 200 字");
        }
        return value;
    }

    private static List<Long> parseOrder(JsonNode node, PlanView view, int dayIndex, int index) {
        JsonNode array = node.path("orderedItemIds");
        if (!array.isArray() || array.isEmpty()) {
            throw new Invalid("第 " + (index + 1) + " 项重排操作没有给出 orderedItemIds");
        }
        List<Long> ordered = new ArrayList<>();
        Set<Long> seen = new HashSet<>();
        for (JsonNode idNode : array) {
            String raw = idNode.isNumber() ? idNode.asText() : text(idNode);
            long id;
            try {
                id = Long.parseLong(raw == null ? "" : raw.trim());
            } catch (NumberFormatException e) {
                throw new Invalid("重排列表里有非法的 itemId：" + abbreviate(String.valueOf(raw)));
            }
            ItemView item = view.items().get(id);
            if (item == null || item.dayIndex() != dayIndex) {
                throw new Invalid("第 " + dayIndex + " 天的重排列表里混进了不属于这一天的行程项");
            }
            if (!seen.add(id)) {
                throw new Invalid("重排列表里「" + item.title() + "」出现了两次");
            }
            ordered.add(id);
        }
        // 必须给出该天的完整顺序：只给一部分时，「没提到的那些排在哪儿」没有答案，
        // 而这种含糊最后会变成用户看到顺序莫名其妙地变了
        long expected = view.items().values().stream().filter(i -> i.dayIndex() == dayIndex).count();
        if (ordered.size() != expected) {
            throw new Invalid("第 " + dayIndex + " 天有 " + expected + " 项，重排列表只给了 " + ordered.size() + " 项");
        }
        return ordered;
    }

    private static String parseTimeOrNull(JsonNode node, String label) {
        String value = text(node);
        if (value == null) {
            return null;
        }
        if (!HH_MM.matcher(value).matches()) {
            throw new Invalid(label + "必须是 HH:mm 格式，收到「" + abbreviate(value) + "」");
        }
        return value;
    }

    private static String text(JsonNode node) {
        if (node == null || node.isNull() || node.isMissingNode()) {
            return null;
        }
        String value = node.asText("");
        return value.isBlank() ? null : value.trim();
    }

    private static boolean sameValue(String a, String b) {
        return a == null ? b == null : a.equals(b);
    }

    private static String abbreviate(String text) {
        return text == null || text.length() <= 30 ? text : text.substring(0, 30) + "…";
    }

    @FunctionalInterface
    private interface ValueLookup {
        String get(String field);
    }

    /** 把规范化后的 ops 序列化成存库/下发的 JSON。{@code itemId} 一律字符串，避免前端丢精度。 */
    public static List<Map<String, Object>> toPayload(List<Op> ops) {
        List<Map<String, Object>> payload = new ArrayList<>();
        for (Op op : ops) {
            Map<String, Object> node = new LinkedHashMap<>();
            node.put("op", op.type().name());
            switch (op.type()) {
                case ADD_ITEM -> {
                    node.put("dayIndex", op.dayIndex());
                    node.put("title", op.title());
                    node.put("startTime", op.startTime());
                    node.put("endTime", op.endTime());
                    node.put("note", op.note());
                }
                case REMOVE_ITEM -> {
                    node.put("itemId", String.valueOf(op.itemId()));
                    node.put("dayIndex", op.dayIndex());
                    node.put("title", op.title());
                }
                case UPDATE_ITEM -> {
                    node.put("itemId", String.valueOf(op.itemId()));
                    node.put("dayIndex", op.dayIndex());
                    node.put("title", op.title());
                    node.put("changes", changesPayload(op.changes()));
                }
                case MOVE_ITEM -> {
                    node.put("itemId", String.valueOf(op.itemId()));
                    node.put("title", op.title());
                    node.put("fromDayIndex", op.fromDayIndex());
                    node.put("toDayIndex", op.toDayIndex());
                }
                case REORDER_ITEM -> {
                    node.put("dayIndex", op.dayIndex());
                    node.put("orderedItemIds", op.orderedItemIds().stream().map(String::valueOf).toList());
                }
                case UPDATE_DAY -> {
                    node.put("dayIndex", op.dayIndex());
                    node.put("changes", changesPayload(op.changes()));
                }
            }
            payload.add(node);
        }
        return payload;
    }

    private static List<Map<String, Object>> changesPayload(List<Change> changes) {
        List<Map<String, Object>> list = new ArrayList<>();
        for (Change c : changes) {
            Map<String, Object> node = new LinkedHashMap<>();
            node.put("field", c.field());
            node.put("before", c.before());
            node.put("after", c.after());
            list.add(node);
        }
        return list;
    }

    /** 供测试与日志使用：把 ops 压成一行可读摘要 */
    public static Map<Type, Integer> counts(List<Op> ops) {
        Map<Type, Integer> map = new HashMap<>();
        for (Op op : ops) {
            map.merge(op.type(), 1, Integer::sum);
        }
        return map;
    }
}
