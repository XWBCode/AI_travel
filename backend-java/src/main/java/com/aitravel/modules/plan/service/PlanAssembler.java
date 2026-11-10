package com.aitravel.modules.plan.service;

import com.aitravel.modules.plan.entity.Accommodation;
import com.aitravel.modules.plan.entity.BudgetItem;
import com.aitravel.modules.plan.entity.TravelDay;
import com.aitravel.modules.plan.entity.TravelItem;
import com.aitravel.modules.plan.entity.TravelPlan;
import com.aitravel.modules.plan.entity.TransportSegment;
import com.aitravel.modules.plan.mapper.AccommodationMapper;
import com.aitravel.modules.plan.mapper.BudgetItemMapper;
import com.aitravel.modules.plan.mapper.TravelDayMapper;
import com.aitravel.modules.plan.mapper.TravelItemMapper;
import com.aitravel.modules.plan.mapper.TransportSegmentMapper;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.time.LocalTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 把 9 张表还原成 {@code travel_plan_v1.json} 的存储模型。
 * <p>
 * 两种场景共用：详情接口返回、确认时写版本快照。共用一份实现是为了避免
 * 「详情看到的」与「快照存下的」不是同一份东西——那会让回滚变成不可验证的操作。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class PlanAssembler {

    /** 与契约 schemaVersion 一致 */
    public static final String SCHEMA_VERSION = "1.0";

    private static final DateTimeFormatter HH_MM = DateTimeFormatter.ofPattern("HH:mm");

    private final TravelDayMapper dayMapper;
    private final TravelItemMapper itemMapper;
    private final TransportSegmentMapper segmentMapper;
    private final BudgetItemMapper budgetItemMapper;
    private final AccommodationMapper accommodationMapper;
    private final ObjectMapper objectMapper;

    public ObjectNode assemble(TravelPlan plan) {
        ObjectNode root = objectMapper.createObjectNode();
        root.put("schemaVersion", SCHEMA_VERSION);
        root.put("planId", plan.getId());
        root.put("title", plan.getTitle());
        root.put("status", plan.getStatus());
        root.put("version", plan.getVersion() == null ? 0 : plan.getVersion());

        ObjectNode basic = root.putObject("basic");
        basic.put("originCity", plan.getOriginCity());
        basic.put("destination", plan.getDestination());
        basic.set("destinations", readJson(plan.getDestinations(), true));
        basic.put("startDate", plan.getStartDate() == null ? null : plan.getStartDate().toString());
        basic.put("endDate", plan.getEndDate() == null ? null : plan.getEndDate().toString());
        basic.put("days", plan.getDays());
        basic.put("travelers", plan.getTravelers());
        basic.set("missingFields", readJson(plan.getMissingFields(), true));

        root.set("preferences", readJson(plan.getPreferences(), false));
        root.set("budget", readJson(plan.getBudget(), false));
        root.set("risks", readJson(plan.getRisks(), true));
        root.put("aiNote", plan.getAiNote());
        root.put("unverifiedFieldCount",
                plan.getUnverifiedFieldCount() == null ? 0 : plan.getUnverifiedFieldCount());

        List<TravelDay> days = dayMapper.selectList(Wrappers.<TravelDay>lambdaQuery()
                .eq(TravelDay::getPlanId, plan.getId())
                .orderByAsc(TravelDay::getDayIndex));
        List<Long> dayIds = days.stream().map(TravelDay::getId).toList();

        List<TravelItem> allItems = dayIds.isEmpty() ? List.of()
                : itemMapper.selectList(Wrappers.<TravelItem>lambdaQuery()
                .in(TravelItem::getDayId, dayIds)
                .orderByAsc(TravelItem::getDayId)
                .orderByAsc(TravelItem::getItemOrder));
        Map<Long, List<TravelItem>> itemsByDay = new HashMap<>();
        for (TravelItem item : allItems) {
            itemsByDay.computeIfAbsent(item.getDayId(), k -> new ArrayList<>()).add(item);
        }

        List<TransportSegment> segments = segmentMapper.selectList(Wrappers.<TransportSegment>lambdaQuery()
                .eq(TransportSegment::getPlanId, plan.getId())
                .orderByAsc(TransportSegment::getDayId)
                .orderByAsc(TransportSegment::getScope));
        Map<Long, Integer> itemOrderById = new HashMap<>();
        allItems.forEach(i -> itemOrderById.put(i.getId(), i.getItemOrder()));
        Map<Long, Integer> dayIndexById = new HashMap<>();
        days.forEach(d -> dayIndexById.put(d.getId(), d.getDayIndex()));

        List<Accommodation> accommodations = accommodationMapper.selectList(Wrappers.<Accommodation>lambdaQuery()
                .eq(Accommodation::getPlanId, plan.getId()));
        Map<Long, Accommodation> accommodationByDay = new HashMap<>();
        accommodations.forEach(a -> {
            if (a.getDayId() != null) {
                accommodationByDay.put(a.getDayId(), a);
            }
        });

        ArrayNode dayArray = root.putArray("days");
        for (TravelDay day : days) {
            ObjectNode dayNode = dayArray.addObject();
            dayNode.put("dayIndex", day.getDayIndex());
            dayNode.put("date", day.getDayDate() == null ? null : day.getDayDate().toString());
            dayNode.put("city", day.getCity());
            dayNode.put("theme", day.getTheme());

            ArrayNode itemArray = dayNode.putArray("items");
            for (TravelItem item : itemsByDay.getOrDefault(day.getId(), List.of())) {
                ObjectNode itemNode = itemArray.addObject();
                itemNode.put("itemOrder", item.getItemOrder());
                itemNode.put("attractionId", item.getAttractionId());
                itemNode.put("title", item.getTitle());
                itemNode.put("startTime", formatTime(item.getStartTime()));
                itemNode.put("endTime", formatTime(item.getEndTime()));
                itemNode.set("duration", readJson(item.getDuration(), false));
                itemNode.set("openHours", readJson(item.getOpenHours(), false));
                itemNode.set("ticketPrice", readJson(item.getTicketPrice(), false));
                itemNode.put("note", item.getNote());
            }

            ArrayNode segArray = dayNode.putArray("transportSegments");
            List<TransportSegment> daySegments = segments.stream()
                    .filter(s -> day.getId().equals(s.getDayId()))
                    .toList();
            for (TransportSegment seg : daySegments) {
                segArray.add(segmentNode(seg, itemOrderById, dayIndexById));
            }

            Accommodation acc = accommodationByDay.get(day.getId());
            if (acc == null) {
                dayNode.putNull("accommodation");
            } else {
                ObjectNode accNode = dayNode.putObject("accommodation");
                accNode.put("areaName", acc.getAreaName());
                accNode.put("suggestion", acc.getSuggestion());
                accNode.set("priceRange", readJson(acc.getPriceRange(), false));
            }
        }

        ArrayNode budgetItems = objectMapper.createArrayNode();
        for (BudgetItem item : budgetItemMapper.selectList(Wrappers.<BudgetItem>lambdaQuery()
                .eq(BudgetItem::getPlanId, plan.getId())
                .orderByAsc(BudgetItem::getId))) {
            ObjectNode node = budgetItems.addObject();
            node.put("category", item.getCategory());
            node.put("label", item.getLabel());
            node.set("amount", readJson(item.getAmount(), false));
            node.put("note", item.getNote());
        }
        JsonNode budgetNode = root.get("budget");
        if (budgetNode instanceof ObjectNode obj) {
            obj.set("items", budgetItems);
        }
        return root;
    }

    private ObjectNode segmentNode(TransportSegment seg, Map<Long, Integer> itemOrderById,
                                   Map<Long, Integer> dayIndexById) {
        ObjectNode node = objectMapper.createObjectNode();
        node.put("scope", seg.getScope());
        Integer fromOrder = seg.getFromItemId() == null ? null : itemOrderById.get(seg.getFromItemId());
        Integer toOrder = seg.getToItemId() == null ? null : itemOrderById.get(seg.getToItemId());
        node.put("fromItemOrder", fromOrder);
        node.put("toItemOrder", toOrder);
        // 跨城段没有 day_id，按 DB 里冗余的 fromDayIndex 字段直接还原（建表时就留了列）
        node.put("fromDayIndex", seg.getFromDayIndex() != null ? seg.getFromDayIndex()
                : (seg.getDayId() == null ? null : dayIndexById.get(seg.getDayId())));
        node.put("toDayIndex", seg.getToDayIndex());
        node.put("fromCity", seg.getFromCity());
        node.put("toCity", seg.getToCity());
        node.put("mode", seg.getMode());
        node.set("duration", readJson(seg.getDuration(), false));
        node.set("distance", readJson(seg.getDistance(), false));
        node.put("advice", seg.getAdvice());
        return node;
    }

    private String formatTime(LocalTime time) {
        return time == null ? null : time.format(HH_MM);
    }

    /**
     * JSON 列 → JsonNode。空值按契约回填空数组或 null，避免详情接口返回结构缺失。
     */
    private JsonNode readJson(String raw, boolean arrayFallback) {
        if (raw == null || raw.isBlank()) {
            return arrayFallback ? objectMapper.createArrayNode() : objectMapper.nullNode();
        }
        try {
            return objectMapper.readTree(raw);
        } catch (Exception e) {
            log.error("JSON 列解析失败，按空值处理: {}", raw, e);
            return arrayFallback ? objectMapper.createArrayNode() : objectMapper.nullNode();
        }
    }
}
