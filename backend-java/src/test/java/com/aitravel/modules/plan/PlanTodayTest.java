package com.aitravel.modules.plan;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.chat.service.AiServiceClient;
import com.aitravel.modules.plan.dto.PlanTodayVO;
import com.aitravel.modules.plan.service.PlanDates;
import com.aitravel.modules.plan.service.PlanProgressService;
import com.aitravel.modules.plan.service.PlanService;
import com.aitravel.modules.plan.service.PlanTodayService;
import com.aitravel.support.PlanMutants;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.Mockito.when;

/**
 * 当日执行视图的落库级证据（M2，R-21）。
 *
 * <p>这一块最容易「看起来能用」而实际错位的是四种情况，所以每种都有独立用例：
 * <ul>
 *   <li>计划<b>没有出发日</b>——今天与第几天之间没有锚点，必须返回 UNANCHORED 而不是猜第 1 天；</li>
 *   <li>「开始旅行」<b>锚定日期</b>——回填 startDate/endDate 与全部 day_date，且只改日期不动行程；</li>
 *   <li>未出发 / 已超期——越界要给出可读的说明，不能空卡片；</li>
 *   <li>改天数删尾部整天——进度行必须跟着清，否则总项数会被幽灵进度撑破。</li>
 * </ul>
 */
@SpringBootTest
@ActiveProfiles("test")
@EnabledIfEnvironmentVariable(named = "MYSQL_PASSWORD", matches = ".+")
class PlanTodayTest {

    private static final long TEST_USER = 9_000_301L;
    private static final long OTHER_USER = 9_000_302L;

    @Autowired
    private PlanService planService;

    @Autowired
    private PlanTodayService todayService;

    @Autowired
    private PlanProgressService progressService;

    @Autowired
    private JdbcTemplate jdbc;

    @MockBean
    private AiServiceClient aiServiceClient;

    @BeforeEach
    void cleanBefore() {
        cleanUp();
    }

    @AfterEach
    void cleanAfter() {
        cleanUp();
    }

    @Test
    @DisplayName("有出发日：今天落在第 2 天，卡片带当日城市、行程项与「下一项」标记")
    void todayMapsToDayIndex() {
        long planId = generatePlan();          // 样例：2026-11-20 起，3 天，共 4 项
        planService.confirm(planId, TEST_USER);

        PlanTodayVO vo = todayService.today(planId, TEST_USER, LocalDate.of(2026, 11, 21));

        assertEquals("TODAY", vo.getPhase());
        assertEquals(2, vo.getDayIndex());
        assertEquals(3, vo.getTotalDays());
        assertEquals(LocalDate.of(2026, 11, 21), vo.getDate());
        assertEquals("乐山", vo.getDay().getCity());
        assertEquals(1, vo.getDay().getTotal());
        assertTrue(vo.getDay().getItems().get(0).isNext(), "当日只有一项且未完成，它必须是「下一项」");
        assertEquals(4, vo.getTotal(), "整趟总数不随当天变化");
        assertEquals(0, vo.getPercent());
    }

    @Test
    @DisplayName("还没出发：返回 BEFORE + 第 1 天预览，并给出距出发天数")
    void beforeStartPreviewsFirstDay() {
        long planId = generatePlan();
        planService.confirm(planId, TEST_USER);

        PlanTodayVO vo = todayService.today(planId, TEST_USER, LocalDate.of(2026, 11, 18));

        assertEquals("BEFORE", vo.getPhase());
        assertEquals(1, vo.getDayIndex());
        assertEquals(2, vo.getDaysUntilStart());
        assertNull(vo.getDaysSinceEnd());
        assertEquals(2, vo.getDay().getTotal(), "预览的是第 1 天的两项");
        assertEquals("成都", vo.getDay().getCity());
    }

    @Test
    @DisplayName("已过结束日但没点完成：停在最后一天，并说明超期几天")
    void afterEndStaysOnLastDay() {
        long planId = generatePlan();
        planService.confirm(planId, TEST_USER);

        PlanTodayVO vo = todayService.today(planId, TEST_USER, LocalDate.of(2026, 11, 25));

        assertEquals("AFTER", vo.getPhase());
        assertEquals(3, vo.getDayIndex());
        assertEquals(3, vo.getDaysSinceEnd(), "11-22 结束，11-25 已超期 3 天");
        assertEquals("都江堰", vo.getDay().getCity());
    }

    @Test
    @DisplayName("没有出发日：返回 UNANCHORED，不给天数也不给卡片，绝不猜第 1 天")
    void unanchoredPlanSaysSo() {
        long planId = generatePlan(noDateDraft());
        planService.confirm(planId, TEST_USER);

        PlanTodayVO vo = todayService.today(planId, TEST_USER, LocalDate.of(2026, 11, 21));

        assertEquals("UNANCHORED", vo.getPhase());
        assertNull(vo.getDayIndex());
        assertNull(vo.getDay());
        assertEquals(4, vo.getTotal(), "没有锚点也要能显示整趟进度");
        assertEquals(3, vo.getTotalDays());
    }

    @Test
    @DisplayName("开始旅行锚定：回填 startDate/endDate 与全部 day_date，并留下可回滚的快照")
    void startAnchorsMissingDate() {
        long planId = generatePlan(noDateDraft());
        planService.confirm(planId, TEST_USER);
        int versionsBefore = count("select count(*) from t_plan_version where plan_id = ?", planId);
        assertTrue(allDayDatesNull(planId), "前置条件：没有出发日时天表日期应为空");

        planService.start(planId, TEST_USER, LocalDate.of(2026, 11, 20));

        assertEquals("IN_PROGRESS", status(planId));
        assertEquals(LocalDate.of(2026, 11, 20), date("start_date", planId));
        assertEquals(LocalDate.of(2026, 11, 22), date("end_date", planId));
        assertEquals(List.of("2026-11-20", "2026-11-21", "2026-11-22"), dayDates(planId));
        assertEquals(versionsBefore + 1, count("select count(*) from t_plan_version where plan_id = ?", planId),
                "改了出发日就是改了内容，必须留一份可回滚的快照");

        PlanTodayVO vo = todayService.today(planId, TEST_USER, LocalDate.of(2026, 11, 20));
        assertEquals("TODAY", vo.getPhase());
        assertEquals(1, vo.getDayIndex());
    }

    @Test
    @DisplayName("开始旅行不改日期时：沿用计划原出发日，不产生多余快照")
    void startKeepsExistingDate() {
        long planId = generatePlan();
        planService.confirm(planId, TEST_USER);
        int versionsBefore = count("select count(*) from t_plan_version where plan_id = ?", planId);

        planService.start(planId, TEST_USER, null);

        assertEquals(LocalDate.of(2026, 11, 20), date("start_date", planId));
        assertEquals(List.of("2026-11-20", "2026-11-21", "2026-11-22"), dayDates(planId));
        assertEquals(versionsBefore, count("select count(*) from t_plan_version where plan_id = ?", planId),
                "内容没变就不该凭空多一版");
    }

    @Test
    @DisplayName("开始旅行指定了别的出发日：整体平移日期，行程项与天序号不动")
    void startWithExplicitDateShiftsCalendar() {
        long planId = generatePlan();
        planService.confirm(planId, TEST_USER);
        List<Long> itemsBefore = itemIds(planId);

        planService.start(planId, TEST_USER, LocalDate.of(2026, 12, 1));

        assertEquals(LocalDate.of(2026, 12, 1), date("start_date", planId));
        assertEquals(LocalDate.of(2026, 12, 3), date("end_date", planId));
        assertEquals(List.of("2026-12-01", "2026-12-02", "2026-12-03"), dayDates(planId));
        assertEquals(itemsBefore, itemIds(planId), "锚定日期只贴日期，不该重建行程项");
    }

    @Test
    @DisplayName("进度只算仍存在的行程项：删掉尾部整天时连带清掉它的进度行")
    void shrinkingDaysClearsOrphanProgress() {
        long planId = generatePlan();
        long day3Item = itemIds(planId).get(3);
        // 直接写一行进度：这条用例要验的是「删天时清理」，不是「怎么勾上的」
        jdbc.update("insert into t_travel_progress(id, plan_id, item_id, user_id, completed_at) "
                + "values (?, ?, ?, ?, now())", 9_000_399_001L, planId, day3Item, TEST_USER);

        planService.update(planId, TEST_USER, body("{\"days\": 2}"));

        assertEquals(0, count("select count(*) from t_travel_progress where item_id = ?", day3Item),
                "行程项没了，进度行不能留下");
        PlanTodayVO vo = todayService.today(planId, TEST_USER, LocalDate.of(2026, 11, 21));
        assertEquals(3, vo.getTotal(), "4 项减去被删掉的第 3 天 1 项");
        assertEquals(0, vo.getCompleted());
    }

    @Test
    @DisplayName("空白日：今天这天没有安排时返回空卡片，而不是 null")
    void emptyDayReturnsEmptyCard() {
        long planId = generatePlan();
        planService.confirm(planId, TEST_USER);
        planService.start(planId, TEST_USER, null);
        // 改成 4 天：新补出来的第 4 天是空白天
        jdbc.update("update t_travel_plan set days = 4 where id = ?", planId);
        jdbc.update("insert into t_travel_day(id, plan_id, day_index, day_date, city) "
                + "values (?, ?, 4, '2026-11-23', '成都')", 9_000_399_002L, planId);

        PlanTodayVO vo = todayService.today(planId, TEST_USER, LocalDate.of(2026, 11, 23));

        assertEquals("TODAY", vo.getPhase());
        assertEquals(4, vo.getDayIndex());
        assertNotNull(vo.getDay());
        assertEquals(0, vo.getDay().getTotal());
        assertTrue(vo.getDay().getItems().isEmpty());
    }

    @Test
    @DisplayName("终态没有「今天」：已完成 / 已取消都拒绝，并说明原因")
    void terminalStatesReject() {
        long done = generatePlan();
        planService.confirm(done, TEST_USER);
        planService.start(done, TEST_USER, null);
        planService.complete(done, TEST_USER);
        BizException completed = assertThrows(BizException.class,
                () -> todayService.today(done, TEST_USER, LocalDate.of(2026, 11, 21)));
        assertEquals(ErrorCode.PLAN_STATE_INVALID, completed.getErrorCode());

        long cancelled = generatePlan();
        planService.cancel(cancelled, TEST_USER);
        BizException cancel = assertThrows(BizException.class,
                () -> todayService.today(cancelled, TEST_USER, LocalDate.of(2026, 11, 21)));
        assertEquals(ErrorCode.PLAN_STATE_INVALID, cancel.getErrorCode());
    }

    @Test
    @DisplayName("不传日期：按服务端东八区当天算，且相位一定是四选一")
    void defaultDateFallsBackToServerToday() {
        long planId = generatePlan();
        planService.confirm(planId, TEST_USER);

        PlanTodayVO vo = todayService.today(planId, TEST_USER, null);

        assertEquals(PlanDates.today(), vo.getDate());
        assertTrue(List.of("TODAY", "BEFORE", "AFTER").contains(vo.getPhase()));
    }

    @Test
    @DisplayName("越权：看别人的「今天」一律拒绝，且不暴露计划是否存在")
    void otherUsersPlanIsForbidden() {
        long planId = generatePlan();
        BizException ex = assertThrows(BizException.class,
                () -> todayService.today(planId, OTHER_USER, LocalDate.of(2026, 11, 21)));
        assertNotEquals(ErrorCode.PLAN_STATE_INVALID, ex.getErrorCode());
        assertTrue(ex.getErrorCode() == ErrorCode.FORBIDDEN || ex.getErrorCode() == ErrorCode.PLAN_NOT_FOUND,
                "按不存在或无权访问处理，实际：" + ex.getErrorCode());
    }

    @Test
    @DisplayName("勾选后当日进度与整趟进度一起更新")
    void progressReflectsMarking() {
        long planId = generatePlan();
        planService.confirm(planId, TEST_USER);
        planService.start(planId, TEST_USER, null);

        PlanTodayVO before = todayService.today(planId, TEST_USER, LocalDate.of(2026, 11, 20));
        assertTrue(before.getDay().getItems().get(0).isNext());
        long itemId = Long.parseLong(before.getDay().getItems().get(0).getItemId());
        progressService.mark(planId, TEST_USER, itemId, true);

        PlanTodayVO after = todayService.today(planId, TEST_USER, LocalDate.of(2026, 11, 20));
        assertEquals(1, after.getDay().getCompleted());
        assertEquals(1, after.getCompleted());
        assertEquals(25, after.getPercent());
        assertTrue(after.getDay().getItems().get(0).isCompleted());
        assertTrue(after.getDay().getItems().get(1).isNext(), "第 1 项做完后，「下一项」要顺延到第 2 项");
    }

    // ---------------- helpers ----------------

    private long generatePlan() {
        return generatePlan(PlanMutants.validDraft());
    }

    private long generatePlan(JsonNode draft) {
        Map<String, Object> payload = Map.of("code", 0, "data", Map.of(
                "draft", Map.of("plan", draft),
                "done", Map.of("provider", "mock", "model", "mock-model")));
        when(aiServiceClient.generatePlan(anyList(), any()))
                .thenReturn((JsonNode) new ObjectMapper().valueToTree(payload));
        return planService.generate(TEST_USER,
                List.of(Map.of("role", "user", "content", "我想去成都玩三天，预算三千，喜欢美食")))
                .path("planId").asLong();
    }

    /** 合法但没有出发日的草稿：S-4 要求 null 字段必须列入 missingFields */
    private JsonNode noDateDraft() {
        ObjectNode draft = (ObjectNode) PlanMutants.validDraft();
        ObjectNode basic = (ObjectNode) draft.path("basic");
        basic.putNull("startDate");
        basic.putNull("endDate");
        ArrayNode missing = (ArrayNode) basic.path("missingFields");
        missing.add("startDate");
        missing.add("endDate");
        for (JsonNode day : draft.path("days")) {
            ((ObjectNode) day).putNull("date");
        }
        return draft;
    }

    private JsonNode body(String json) {
        try {
            return new ObjectMapper().readTree(json);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    private String status(long planId) {
        return jdbc.queryForObject("select status from t_travel_plan where id = ?", String.class, planId);
    }

    private LocalDate date(String column, long planId) {
        return jdbc.queryForObject("select " + column + " from t_travel_plan where id = ?",
                LocalDate.class, planId);
    }

    private List<String> dayDates(long planId) {
        return jdbc.queryForList("select date_format(day_date, '%Y-%m-%d') from t_travel_day "
                + "where plan_id = ? and deleted = 0 order by day_index", String.class, planId);
    }

    private boolean allDayDatesNull(long planId) {
        return count("select count(*) from t_travel_day where plan_id = ? and day_date is not null",
                planId) == 0;
    }

    private List<Long> itemIds(long planId) {
        return jdbc.queryForList("select i.id from t_travel_item i "
                + "join t_travel_day d on d.id = i.day_id where d.plan_id = ? and i.deleted = 0 "
                + "order by d.day_index, i.item_order", Long.class, planId);
    }

    private int count(String sql, Object... args) {
        Integer value = jdbc.queryForObject(sql, Integer.class, args);
        return value == null ? 0 : value;
    }

    private void cleanUp() {
        jdbc.update("delete from t_travel_progress where plan_id in "
                + "(select id from t_travel_plan where user_id in (?, ?))", TEST_USER, OTHER_USER);
        jdbc.update("delete from t_travel_item where day_id in (select id from t_travel_day "
                        + "where plan_id in (select id from t_travel_plan where user_id in (?, ?)))",
                TEST_USER, OTHER_USER);
        jdbc.update("delete from t_transport_segment where plan_id in "
                + "(select id from t_travel_plan where user_id in (?, ?))", TEST_USER, OTHER_USER);
        jdbc.update("delete from t_budget_item where plan_id in "
                + "(select id from t_travel_plan where user_id in (?, ?))", TEST_USER, OTHER_USER);
        jdbc.update("delete from t_accommodation where plan_id in "
                + "(select id from t_travel_plan where user_id in (?, ?))", TEST_USER, OTHER_USER);
        jdbc.update("delete from t_plan_version where plan_id in "
                + "(select id from t_travel_plan where user_id in (?, ?))", TEST_USER, OTHER_USER);
        jdbc.update("delete from t_travel_day where plan_id in "
                + "(select id from t_travel_plan where user_id in (?, ?))", TEST_USER, OTHER_USER);
        jdbc.update("delete from t_travel_plan where user_id in (?, ?)", TEST_USER, OTHER_USER);
        jdbc.update("delete from t_ai_generation_log where user_id in (?, ?)", TEST_USER, OTHER_USER);
    }
}
