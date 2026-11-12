package com.aitravel.modules.plan.dto;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import com.fasterxml.jackson.databind.JsonNode;

import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;

/**
 * {@code PATCH /api/plans/{id}} 的请求体：解析 + 语义归一化。
 *
 * <p>为什么不用带 {@code @Valid} 的 {@code @Data} 类：
 * <ul>
 *   <li>PATCH 必须区分「没传」「传 null」「传值」三种语义，普通字段做不到（见 {@link Patch}）；</li>
 *   <li>天数和日期是<b>互相推导</b>的关系，Bean Validation 表达不了「改天数要带着结束日期一起变」。</li>
 * </ul>
 * 因此解析与归一化都放在这里，做成不依赖 Spring / 数据库的纯逻辑，便于单测覆盖边界。
 *
 * <p><b>可编辑范围（M1）</b>：标题、出发地、目的地、天数、人数、起止日期。
 * 不含行程条目、预算金额、偏好——前两项属于 M2 的「行程调整」，
 * 预算在 L0（无实时数据源）下一改就是编造数字，只允许展示「待核实」。
 *
 * <p><b>归一化规则</b>（前端表单按同一套规则联动，见 {@code utils/planEdit.ts}）：
 * <ol>
 *   <li>显式给了天数且有出发日期 → 结束日期由天数推导；若同时显式给了不同的结束日期，
 *       说明调用方自相矛盾，直接拒绝而不是悄悄覆盖；</li>
 *   <li>没给天数、只给了日期 → 天数由日期区间反推；</li>
 *   <li>两者都没给 → 天数与日期保持原值。</li>
 * </ol>
 */
public record PlanUpdateCommand(
        Patch<String> title,
        Patch<String> originCity,
        Patch<String> destination,
        Patch<Integer> days,
        Patch<Integer> travelers,
        Patch<LocalDate> startDate,
        Patch<LocalDate> endDate,
        Integer baseVersion) {

    /** 请求体允许出现的字段；拼错的字段必须报错，否则前端改了没生效却以为成功 */
    private static final Set<String> ALLOWED_FIELDS = Set.of(
            "title", "originCity", "destination", "days", "travelers",
            "startDate", "endDate", "baseVersion");

    /**
     * S-4 参与比对的 6 个可推导字段，顺序即 missingFields 的重建顺序。
     * 与 {@code PlanSchemaValidator.DERIVABLE_MISSING_FIELDS} 保持一致（那边是契约校验的依据）。
     */
    private static final List<String> DERIVABLE = List.of(
            "originCity", "destination", "startDate", "endDate", "days", "travelers");

    private static final int MAX_TITLE = 100;
    private static final int MAX_CITY = 64;
    private static final int MAX_DAYS = 30;
    private static final int MAX_TRAVELERS = 50;
    private static final int MAX_SUMMARY = 200;

    // ---------------- 解析 ----------------

    public static PlanUpdateCommand parse(JsonNode body) {
        if (body == null || body.isNull() || !body.isObject()) {
            throw new BizException(ErrorCode.BAD_REQUEST, "请求体必须是 JSON 对象");
        }
        for (Iterator<String> it = body.fieldNames(); it.hasNext(); ) {
            String name = it.next();
            if (!ALLOWED_FIELDS.contains(name)) {
                throw new BizException(ErrorCode.BAD_REQUEST, "不支持修改的字段：" + name
                        + "（可改：标题、出发地、目的地、天数、人数、起止日期）");
            }
        }
        return new PlanUpdateCommand(
                text(body, "title"),
                text(body, "originCity"),
                text(body, "destination"),
                integer(body, "days"),
                integer(body, "travelers"),
                date(body, "startDate"),
                date(body, "endDate"),
                baseVersion(body));
    }

    private static Patch<String> text(JsonNode body, String field) {
        if (!body.has(field)) {
            return Patch.absent();
        }
        JsonNode node = body.get(field);
        if (node.isNull()) {
            return Patch.of(null);
        }
        if (!node.isTextual()) {
            throw bad(field, "应为字符串");
        }
        return Patch.of(node.asText());
    }

    private static Patch<Integer> integer(JsonNode body, String field) {
        if (!body.has(field)) {
            return Patch.absent();
        }
        JsonNode node = body.get(field);
        if (node.isNull()) {
            return Patch.of(null);
        }
        if (!node.isIntegralNumber() || !node.canConvertToInt()) {
            throw bad(field, "应为整数");
        }
        return Patch.of(node.intValue());
    }

    private static Patch<LocalDate> date(JsonNode body, String field) {
        if (!body.has(field)) {
            return Patch.absent();
        }
        JsonNode node = body.get(field);
        if (node.isNull()) {
            return Patch.of(null);
        }
        if (!node.isTextual()) {
            throw bad(field, "应为 YYYY-MM-DD 字符串");
        }
        String text = node.asText().trim();
        if (text.isEmpty()) {
            return Patch.of(null);
        }
        try {
            return Patch.of(LocalDate.parse(text));
        } catch (DateTimeParseException e) {
            throw bad(field, "日期格式应为 YYYY-MM-DD");
        }
    }

    private static Integer baseVersion(JsonNode body) {
        if (!body.has("baseVersion") || body.get("baseVersion").isNull()) {
            return null;
        }
        return integer(body, "baseVersion").value();
    }

    // ---------------- 归一化 ----------------

    /**
     * 把「字段补丁」应用到当前值上，得到最终要落库的完整基础信息。
     *
     * @param current 计划当前的基础信息
     * @return 归一化结果；{@code changed=false} 表示请求体没有带来任何变化（幂等保存）
     */
    public Resolved resolve(Current current) {
        String newTitle = title.present() ? requireTitle(title.value()) : current.title();
        String newOrigin = originCity.present() ? city(originCity.value(), "originCity") : current.originCity();
        String newDestination = destination.present()
                ? city(destination.value(), "destination") : current.destination();

        List<String> oldDestinations = current.destinations() == null ? List.of() : current.destinations();
        boolean destinationChanged = destination.present() && !Objects.equals(newDestination, current.destination());
        List<String> newDestinations;
        if (destinationChanged) {
            if (oldDestinations.size() > 1) {
                // 多目的地计划的每天城市都挂在 destinations 上，改主目的地会让 S-3 失效，
                // M1 不做这层联动，明确拒绝比造出脏数据好
                throw new BizException(ErrorCode.BAD_REQUEST,
                        "该计划包含多个目的地（" + String.join("、", oldDestinations)
                                + "），暂不支持直接改主目的地，请在对话里调整");
            }
            // 契约：destination 为 null 时 destinations 必须为空数组
            newDestinations = newDestination == null ? List.of() : List.of(newDestination);
        } else {
            newDestinations = oldDestinations;
        }

        Integer newDays = days.present() ? days.value() : current.days();
        Integer newTravelers = travelers.present() ? travelers.value() : current.travelers();
        LocalDate newStart = startDate.present() ? startDate.value() : current.startDate();
        LocalDate newEnd = endDate.present() ? endDate.value() : current.endDate();

        if (newDays != null) {
            requireRange(newDays, 1, MAX_DAYS, "天数");
        }
        if (newTravelers != null) {
            requireRange(newTravelers, 1, MAX_TRAVELERS, "人数");
        }

        if (days.hasValue() && newStart != null) {
            // 规则 1：天数为权威，结束日期跟着走
            LocalDate derived = newStart.plusDays(newDays - 1L);
            if (endDate.hasValue() && !derived.equals(endDate.value())) {
                throw bad("endDate", "天数与日期不一致：从 " + newStart + " 起 " + newDays
                        + " 天应为 " + derived + "，请只改其中一项");
            }
            newEnd = derived;
        } else if (!days.present() && (startDate.present() || endDate.present())) {
            // 规则 2：只改日期时，天数由区间反推
            if (newStart != null && newEnd != null) {
                if (newEnd.isBefore(newStart)) {
                    throw bad("endDate", "结束日期不能早于开始日期");
                }
                long span = ChronoUnit.DAYS.between(newStart, newEnd) + 1;
                if (span > MAX_DAYS) {
                    throw bad("endDate", "行程跨度最多 " + MAX_DAYS + " 天");
                }
                newDays = (int) span;
            }
        }

        List<String> newMissing = rebuildMissingFields(current.missingFields(),
                newOrigin, newDestination, newStart, newEnd, newDays, newTravelers);

        boolean changed = !Objects.equals(newTitle, current.title())
                || !Objects.equals(newOrigin, current.originCity())
                || !Objects.equals(newDestination, current.destination())
                || !Objects.equals(newDestinations, oldDestinations)
                || !Objects.equals(newStart, current.startDate())
                || !Objects.equals(newEnd, current.endDate())
                || !Objects.equals(newDays, current.days())
                || !Objects.equals(newTravelers, current.travelers())
                // 顺序变化不算改动，只有集合内容变了才算
                || !new HashSet<>(newMissing).equals(new HashSet<>(
                        current.missingFields() == null ? List.of() : current.missingFields()));

        String summary = changed
                ? describe(current, newTitle, newOrigin, newDestination,
                        newStart, newEnd, newDays, newTravelers)
                : null;

        return new Resolved(newTitle, newOrigin, newDestination, newDestinations,
                newStart, newEnd, newDays, newTravelers, newMissing, changed, summary);
    }

    private String requireTitle(String value) {
        String trimmed = value == null ? "" : value.trim();
        if (trimmed.isEmpty()) {
            throw bad("title", "标题不能为空");
        }
        if (trimmed.length() > MAX_TITLE) {
            throw bad("title", "标题不得超过 " + MAX_TITLE + " 字");
        }
        return trimmed;
    }

    /** 空串按「清空」处理：用户把输入框删空，意图就是不填，而不是填一个空字符串 */
    private String city(String value, String field) {
        String trimmed = value == null ? null : value.trim();
        if (trimmed == null || trimmed.isEmpty()) {
            return null;
        }
        if (trimmed.length() > MAX_CITY) {
            throw bad(field, "不得超过 " + MAX_CITY + " 字");
        }
        return trimmed;
    }

    private void requireRange(int value, int min, int max, String label) {
        if (value < min || value > max) {
            throw new BizException(ErrorCode.BAD_REQUEST, label + "应在 " + min + "-" + max + " 之间");
        }
    }

    /**
     * 重建 missingFields。
     * <p>
     * 只重算 6 个可推导字段，原数组里的 budget / interests / transport / accommodation
     * 原样保留——编辑基础信息不该顺带抹掉别处记录的缺失项（它们是 S-4 之外的信息）。
     */
    private List<String> rebuildMissingFields(List<String> oldMissing, String originCity, String destination,
                                              LocalDate startDate, LocalDate endDate,
                                              Integer days, Integer travelers) {
        Set<String> result = new LinkedHashSet<>();
        for (String field : DERIVABLE) {
            boolean unknown = switch (field) {
                case "originCity" -> originCity == null;
                case "destination" -> destination == null;
                case "startDate" -> startDate == null;
                case "endDate" -> endDate == null;
                case "days" -> days == null;
                case "travelers" -> travelers == null;
                default -> false;
            };
            if (unknown) {
                result.add(field);
            }
        }
        if (oldMissing != null) {
            for (String field : oldMissing) {
                if (!DERIVABLE.contains(field)) {
                    result.add(field);
                }
            }
        }
        return new ArrayList<>(result);
    }

    private String describe(Current current, String title, String originCity, String destination,
                            LocalDate startDate, LocalDate endDate, Integer days, Integer travelers) {
        List<String> parts = new ArrayList<>();
        if (!Objects.equals(title, current.title())) {
            parts.add("标题");
        }
        if (!Objects.equals(originCity, current.originCity())) {
            parts.add("出发地 " + orUnknown(current.originCity()) + "→" + orUnknown(originCity));
        }
        if (!Objects.equals(destination, current.destination())) {
            parts.add("目的地 " + orUnknown(current.destination()) + "→" + orUnknown(destination));
        }
        if (!Objects.equals(startDate, current.startDate()) || !Objects.equals(endDate, current.endDate())) {
            parts.add("日期 " + orUnknown(current.startDate()) + "~" + orUnknown(current.endDate())
                    + "→" + orUnknown(startDate) + "~" + orUnknown(endDate));
        }
        if (!Objects.equals(days, current.days())) {
            parts.add("天数 " + orUnknown(current.days()) + "→" + orUnknown(days));
        }
        if (!Objects.equals(travelers, current.travelers())) {
            parts.add("人数 " + orUnknown(current.travelers()) + "→" + orUnknown(travelers));
        }
        String summary = parts.isEmpty() ? "编辑基础信息" : "编辑：" + String.join("、", parts);
        return summary.length() <= MAX_SUMMARY ? summary : summary.substring(0, MAX_SUMMARY - 3) + "...";
    }

    private String orUnknown(Object value) {
        return value == null ? "未填" : String.valueOf(value);
    }

    private static BizException bad(String field, String reason) {
        return new BizException(ErrorCode.BAD_REQUEST, "参数 " + field + " 不合法：" + reason);
    }

    /** 计划当前的基础信息，用作归一化的基准 */
    public record Current(String title,
                          String originCity,
                          String destination,
                          List<String> destinations,
                          LocalDate startDate,
                          LocalDate endDate,
                          Integer days,
                          Integer travelers,
                          List<String> missingFields) {
    }

    /** 归一化结果：拿去落库 + 写快照摘要 */
    public record Resolved(String title,
                           String originCity,
                           String destination,
                           List<String> destinations,
                           LocalDate startDate,
                           LocalDate endDate,
                           Integer days,
                           Integer travelers,
                           List<String> missingFields,
                           boolean changed,
                           String summary) {
    }
}
