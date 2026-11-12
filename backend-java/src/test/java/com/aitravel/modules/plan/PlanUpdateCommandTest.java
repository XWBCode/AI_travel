package com.aitravel.modules.plan;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.plan.dto.PlanUpdateCommand;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.LocalDate;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 编辑命令的解析与归一化规则（纯逻辑，不连库）。
 * <p>
 * 这里验的是「用户改了天数，结束日期会不会跟着变」这类语义；
 * 「真的落到库里了吗」由 {@code PlanPersistenceTest} 负责。两者分工，不重复。
 */
class PlanUpdateCommandTest {

    private final ObjectMapper mapper = new ObjectMapper();

    // ---------------- 解析 ----------------

    @Test
    @DisplayName("解析：区分 未传 / 传 null / 传值")
    void parseDistinguishesAbsentFromNull() throws Exception {
        PlanUpdateCommand cmd = parse("{\"title\":\"成都四日\",\"originCity\":null}");
        assertTrue(cmd.title().present());
        assertEquals("成都四日", cmd.title().value());
        assertTrue(cmd.originCity().isExplicitNull(), "显式 null 必须能与「没传」区分开");
        assertFalse(cmd.destination().present(), "没传的字段应保持未出现");
    }

    @Test
    @DisplayName("解析：未知字段一律报错，避免拼错字段名后静默无效")
    void parseRejectsUnknownField() {
        BizException ex = assertThrows(BizException.class, () -> parse("{\"titl\":\"x\"}"));
        assertEquals(ErrorCode.BAD_REQUEST, ex.getErrorCode());
        assertTrue(ex.getMessage().contains("titl"));
    }

    @Test
    @DisplayName("解析：类型不对要说清是哪个字段")
    void parseRejectsWrongType() {
        assertEquals(ErrorCode.BAD_REQUEST,
                assertThrows(BizException.class, () -> parse("{\"days\":\"三天\"}")).getErrorCode());
        assertEquals(ErrorCode.BAD_REQUEST,
                assertThrows(BizException.class, () -> parse("{\"days\":3.5}")).getErrorCode());
        assertEquals(ErrorCode.BAD_REQUEST,
                assertThrows(BizException.class, () -> parse("{\"startDate\":\"2026/11/12\"}")).getErrorCode());
        assertEquals(ErrorCode.BAD_REQUEST,
                assertThrows(BizException.class, () -> parse("{\"title\":123}")).getErrorCode());
    }

    @Test
    @DisplayName("解析：空请求体与数组请求体都是 400")
    void parseRejectsNonObjectBody() {
        assertEquals(ErrorCode.BAD_REQUEST, assertThrows(BizException.class,
                () -> PlanUpdateCommand.parse(null)).getErrorCode());
        assertEquals(ErrorCode.BAD_REQUEST, assertThrows(BizException.class,
                () -> parse("[]")).getErrorCode());
    }

    // ---------------- 归一化 ----------------

    @Test
    @DisplayName("改天数：结束日期由出发日期 + 天数推导")
    void changingDaysDerivesEndDate() throws Exception {
        PlanUpdateCommand.Resolved r = parse("{\"days\":5}").resolve(current());
        assertEquals(5, r.days());
        assertEquals(LocalDate.of(2026, 11, 12), r.startDate());
        assertEquals(LocalDate.of(2026, 11, 16), r.endDate(), "11-12 起 5 天，最后一天是 11-16");
        assertTrue(r.changed());
        assertTrue(r.summary().contains("天数"));
    }

    @Test
    @DisplayName("改日期：天数由区间反推")
    void changingDatesDerivesDays() throws Exception {
        PlanUpdateCommand.Resolved r = parse(
                "{\"startDate\":\"2026-11-12\",\"endDate\":\"2026-11-14\"}").resolve(current());
        assertEquals(3, r.days());
        assertEquals(LocalDate.of(2026, 11, 14), r.endDate());
    }

    @Test
    @DisplayName("天数与结束日期同时给且矛盾：拒绝，不静默覆盖")
    void conflictingDaysAndEndDateIsRejected() {
        BizException ex = assertThrows(BizException.class,
                () -> parse("{\"days\":5,\"endDate\":\"2026-11-14\"}").resolve(current()));
        assertEquals(ErrorCode.BAD_REQUEST, ex.getErrorCode());
        assertTrue(ex.getMessage().contains("2026-11-16"), "报错要告诉用户正确的结束日期是哪天");
    }

    @Test
    @DisplayName("日期区间非法：结束早于开始、跨度超 30 天")
    void illegalRangeIsRejected() {
        assertEquals(ErrorCode.BAD_REQUEST, assertThrows(BizException.class,
                () -> parse("{\"startDate\":\"2026-11-12\",\"endDate\":\"2026-11-10\"}")
                        .resolve(current())).getErrorCode());
        assertEquals(ErrorCode.BAD_REQUEST, assertThrows(BizException.class,
                () -> parse("{\"startDate\":\"2026-11-01\",\"endDate\":\"2026-12-31\"}")
                        .resolve(current())).getErrorCode());
    }

    @Test
    @DisplayName("missingFields 跟着字段一起重算：补上出发地就移除，清空就加回")
    void missingFieldsFollowTheValues() throws Exception {
        PlanUpdateCommand.Current withoutOrigin = new PlanUpdateCommand.Current(
                "成都三日", null, "成都", List.of("成都"),
                LocalDate.of(2026, 11, 12), LocalDate.of(2026, 11, 14), 3, 2,
                List.of("originCity", "budget"));

        PlanUpdateCommand.Resolved filled = parse("{\"originCity\":\"杭州\"}").resolve(withoutOrigin);
        assertFalse(filled.missingFields().contains("originCity"));
        assertTrue(filled.missingFields().contains("budget"), "与本字段无关的缺失项要保留");

        PlanUpdateCommand.Resolved cleared = parse("{\"originCity\":null}").resolve(current());
        assertTrue(cleared.missingFields().contains("originCity"), "清空后必须重新列入 missingFields");
        assertNull(cleared.originCity());
    }

    @Test
    @DisplayName("清空目的地：destinations 必须清空（契约要求两者一致）")
    void clearingDestinationEmptiesDestinations() throws Exception {
        PlanUpdateCommand.Resolved r = parse("{\"destination\":null}").resolve(current());
        assertNull(r.destination());
        assertTrue(r.destinations().isEmpty());
        assertTrue(r.missingFields().contains("destination"));
    }

    @Test
    @DisplayName("多目的地计划不允许改主目的地")
    void multiDestinationEditIsRejected() {
        PlanUpdateCommand.Current multi = new PlanUpdateCommand.Current(
                "成都重庆五日", "杭州", "成都", List.of("成都", "重庆"),
                LocalDate.of(2026, 11, 12), LocalDate.of(2026, 11, 16), 5, 2, List.of());
        BizException ex = assertThrows(BizException.class,
                () -> parse("{\"destination\":\"西安\"}").resolve(multi));
        assertEquals(ErrorCode.BAD_REQUEST, ex.getErrorCode());
        assertTrue(ex.getMessage().contains("多个目的地"));
    }

    @Test
    @DisplayName("没有实际变化：changed=false，不产生新版本")
    void identicalValuesAreNotAChange() throws Exception {
        PlanUpdateCommand.Resolved r = parse(
                "{\"title\":\"成都三日\",\"days\":3,\"travelers\":2}").resolve(current());
        assertFalse(r.changed());
        assertNull(r.summary());
    }

    @Test
    @DisplayName("范围校验：天数 1-30、人数 1-50、标题非空且不超 100 字")
    void rangeValidation() {
        assertEquals(ErrorCode.BAD_REQUEST,
                assertThrows(BizException.class, () -> parse("{\"days\":0}").resolve(current())).getErrorCode());
        assertEquals(ErrorCode.BAD_REQUEST,
                assertThrows(BizException.class, () -> parse("{\"days\":31}").resolve(current())).getErrorCode());
        assertEquals(ErrorCode.BAD_REQUEST,
                assertThrows(BizException.class, () -> parse("{\"travelers\":51}").resolve(current())).getErrorCode());
        assertEquals(ErrorCode.BAD_REQUEST,
                assertThrows(BizException.class, () -> parse("{\"title\":\"   \"}").resolve(current())).getErrorCode());
        assertEquals(ErrorCode.BAD_REQUEST,
                assertThrows(BizException.class,
                        () -> parse("{\"title\":\"" + "长".repeat(101) + "\"}").resolve(current())).getErrorCode());
    }

    @Test
    @DisplayName("标题两端空白会被裁掉，不该原样存进库")
    void titleIsTrimmed() throws Exception {
        assertEquals("杭州四日", parse("{\"title\":\"  杭州四日  \"}").resolve(current()).title());
    }

    // ---------------- helpers ----------------

    private PlanUpdateCommand parse(String json) throws Exception {
        JsonNode body = mapper.readTree(json);
        return PlanUpdateCommand.parse(body);
    }

    private PlanUpdateCommand.Current current() {
        return new PlanUpdateCommand.Current(
                "成都三日", "杭州", "成都", List.of("成都"),
                LocalDate.of(2026, 11, 12), LocalDate.of(2026, 11, 14), 3, 2,
                List.of());
    }
}
