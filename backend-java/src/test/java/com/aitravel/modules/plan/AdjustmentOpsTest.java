package com.aitravel.modules.plan;

import com.aitravel.modules.plan.service.AdjustmentOps;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 调整操作的校验规则（纯逻辑，不连库）。
 *
 * <p>这些规则挡的都是「模型输出看起来像那么回事、落下去却会出问题」的情况：
 * 改不存在的 itemId、把开始时间写得晚于结束时间、重排只给一半列表、
 * 同一条建议里既删又改。每一条都对应一个真实会发生的失败模式。
 */
class AdjustmentOpsTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    /** 三天计划，第 1 天两项、第 2 天一项 */
    private AdjustmentOps.PlanView view() {
        Map<Integer, AdjustmentOps.DayView> days = new LinkedHashMap<>();
        days.put(1, new AdjustmentOps.DayView(1, 10L, "成都", "初到"));
        days.put(2, new AdjustmentOps.DayView(2, 20L, "成都", "深入"));
        days.put(3, new AdjustmentOps.DayView(3, 30L, "成都", "收尾"));

        Map<Long, AdjustmentOps.ItemView> items = new LinkedHashMap<>();
        items.put(100L, new AdjustmentOps.ItemView(100L, 1, 1, "宽窄巷子", "09:00", "11:00", "慢慢逛"));
        items.put(101L, new AdjustmentOps.ItemView(101L, 1, 2, "火锅", "12:00", "13:30", null));
        items.put(200L, new AdjustmentOps.ItemView(200L, 2, 1, "都江堰", "09:30", "15:00", null));
        return new AdjustmentOps.PlanView(days, items);
    }

    private JsonNode json(String text) {
        try {
            return MAPPER.readTree(text);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    @Test
    @DisplayName("修改操作不回读模型给的 before，而是用库里的当前值回填")
    void updateBackfillsBeforeFromCurrent() {
        List<AdjustmentOps.Op> ops = AdjustmentOps.normalize(json("""
                [{"op":"UPDATE_ITEM","itemId":"100","changes":[
                    {"field":"startTime","after":"10:30"},
                    {"field":"note","after":"改天再去"}
                ]}]
                """), view());

        assertEquals(1, ops.size());
        List<AdjustmentOps.Change> changes = ops.get(0).changes();
        assertEquals(2, changes.size());
        assertEquals("09:00", changes.get(0).before());
        assertEquals("10:30", changes.get(0).after());
        assertEquals("慢慢逛", changes.get(1).before());
    }

    @Test
    @DisplayName("值没变的修改被丢弃，整批为空时说明模型只是把原样抄了一遍")
    void unchangedValuesAreDropped() {
        List<AdjustmentOps.Op> ops = AdjustmentOps.normalize(json("""
                [{"op":"UPDATE_ITEM","itemId":"100","changes":[{"field":"startTime","after":"09:00"}]}]
                """), view());
        assertTrue(ops.isEmpty());
    }

    @Test
    @DisplayName("引用了不属于该计划的 itemId 一律拒绝")
    void unknownItemIdRejected() {
        AdjustmentOps.Invalid e = assertThrows(AdjustmentOps.Invalid.class, () ->
                AdjustmentOps.normalize(json("""
                        [{"op":"REMOVE_ITEM","itemId":"999"}]
                        """), view()));
        assertTrue(e.getMessage().contains("不在该计划里"));
    }

    @Test
    @DisplayName("时间必须是 HH:mm，25:00 这类值直接拒")
    void invalidTimeRejected() {
        assertThrows(AdjustmentOps.Invalid.class, () ->
                AdjustmentOps.normalize(json("""
                        [{"op":"UPDATE_ITEM","itemId":"100","changes":[{"field":"endTime","after":"25:00"}]}]
                        """), view()));
    }

    @Test
    @DisplayName("同一条建议里既删又改同一项：语义矛盾，拒绝而不是猜一个顺序")
    void removeAndUpdateConflict() {
        AdjustmentOps.Invalid e = assertThrows(AdjustmentOps.Invalid.class, () ->
                AdjustmentOps.normalize(json("""
                        [{"op":"REMOVE_ITEM","itemId":"100"},
                         {"op":"UPDATE_ITEM","itemId":"100","changes":[{"field":"note","after":"x"}]}]
                        """), view()));
        assertTrue(e.getMessage().contains("既被删除又被修改"));
    }

    @Test
    @DisplayName("同一天不能既增删又重排：重排要求完整列表，增删改变了这个列表")
    void reorderWithStructuralChangeConflict() {
        AdjustmentOps.Invalid e = assertThrows(AdjustmentOps.Invalid.class, () ->
                AdjustmentOps.normalize(json("""
                        [{"op":"ADD_ITEM","dayIndex":1,"title":"新项目"},
                         {"op":"REORDER_ITEM","dayIndex":1,"orderedItemIds":["100","101"]}]
                        """), view()));
        assertTrue(e.getMessage().contains("请拆成两条建议"));
    }

    @Test
    @DisplayName("重排必须列出该天全部行程项：只给一部分时没人知道剩下那些排哪")
    void reorderMustBeComplete() {
        AdjustmentOps.Invalid e = assertThrows(AdjustmentOps.Invalid.class, () ->
                AdjustmentOps.normalize(json("""
                        [{"op":"REORDER_ITEM","dayIndex":1,"orderedItemIds":["100"]}]
                        """), view()));
        assertTrue(e.getMessage().contains("只给了 1"));
    }

    @Test
    @DisplayName("移到同一天等于没改，直接丢弃而不是给用户看一行无效差异")
    void moveToSameDayDropped() {
        List<AdjustmentOps.Op> ops = AdjustmentOps.normalize(json("""
                [{"op":"MOVE_ITEM","itemId":"100","toDayIndex":1}]
                """), view());
        assertTrue(ops.isEmpty());
    }

    @Test
    @DisplayName("新增项目的开始时间不能晚于结束时间")
    void addWithReversedTimeRejected() {
        AdjustmentOps.Invalid e = assertThrows(AdjustmentOps.Invalid.class, () ->
                AdjustmentOps.normalize(json("""
                        [{"op":"ADD_ITEM","dayIndex":2,"title":"休息","startTime":"17:00","endTime":"15:00"}]
                        """), view()));
        assertTrue(e.getMessage().contains("不能晚于或等于"));
    }

    @Test
    @DisplayName("不认识的 op 类型拒绝，并列出允许的取值")
    void unknownOpTypeRejected() {
        AdjustmentOps.Invalid e = assertThrows(AdjustmentOps.Invalid.class, () ->
                AdjustmentOps.normalize(json("""
                        [{"op":"DELETE_DAY","dayIndex":2}]
                        """), view()));
        assertTrue(e.getMessage().contains("ADD_ITEM"));
    }

    @Test
    @DisplayName("字段越权：预算、事实字段、天数都不在白名单里")
    void fieldNotAllowedRejected() {
        assertThrows(AdjustmentOps.Invalid.class, () ->
                AdjustmentOps.normalize(json("""
                        [{"op":"UPDATE_ITEM","itemId":"100","changes":[{"field":"ticketPrice","after":"50"}]}]
                        """), view()));
        assertThrows(AdjustmentOps.Invalid.class, () ->
                AdjustmentOps.normalize(json("""
                        [{"op":"UPDATE_DAY","dayIndex":1,"changes":[{"field":"dayIndex","after":2}]}]
                        """), view()));
    }

    @Test
    @DisplayName("超过操作数上限直接拒：那已经不是「调整」而是重新生成")
    void tooManyOpsRejected() {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < AdjustmentOps.MAX_OPS + 1; i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append("{\"op\":\"REMOVE_ITEM\",\"itemId\":\"100\"}");
        }
        sb.append(']');
        assertThrows(AdjustmentOps.Invalid.class, () -> AdjustmentOps.normalize(json(sb.toString()), view()));
    }

    @Test
    @DisplayName("下发形态里 itemId 是字符串：雪花 ID 转成数字会让前端丢精度")
    void payloadKeepsItemIdAsString() {
        List<AdjustmentOps.Op> ops = AdjustmentOps.normalize(json("""
                [{"op":"REMOVE_ITEM","itemId":"200"}]
                """), view());
        Map<String, Object> node = AdjustmentOps.toPayload(ops).get(0);
        assertEquals("200", node.get("itemId"));
        assertEquals("都江堰", node.get("title"));
        assertNull(node.get("changes"));
    }
}
