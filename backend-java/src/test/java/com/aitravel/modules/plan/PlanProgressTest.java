package com.aitravel.modules.plan;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.chat.service.AiServiceClient;
import com.aitravel.modules.plan.dto.PlanProgressVO;
import com.aitravel.modules.plan.service.PlanProgressService;
import com.aitravel.modules.plan.service.PlanService;
import com.aitravel.support.PlanMutants;
import com.fasterxml.jackson.databind.JsonNode;
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

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.Mockito.when;

/**
 * M2 执行进度的落库级证据。
 *
 * <p>为什么不用 Mock 掉 Mapper 做单测：进度的幂等性靠 {@code uk(plan_id,item_id)} +
 * {@code ON DUPLICATE KEY UPDATE} 实现，这条约束只有连真库才验得出来——Mock 掉数据库时，
 * 「重复点击是否产生两行」这个真问题会被假通过盖住。
 */
@SpringBootTest
@ActiveProfiles("test")
@EnabledIfEnvironmentVariable(named = "MYSQL_PASSWORD", matches = ".+")
class PlanProgressTest {

    private static final long TEST_USER = 9_000_101L;
    private static final long OTHER_USER = 9_000_102L;

    @Autowired
    private PlanService planService;

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
    @DisplayName("只有「进行中」能打勾：草稿与已确认都拒绝，且不写进度行")
    void onlyInProgressAcceptsMarking() {
        long planId = generatePlan();
        List<Long> itemIds = itemIds(planId);

        BizException draft = assertThrows(BizException.class,
                () -> progressService.mark(planId, TEST_USER, itemIds.get(0), true));
        assertEquals(ErrorCode.PLAN_STATE_INVALID, draft.getErrorCode());

        planService.confirm(planId, TEST_USER);
        BizException confirmed = assertThrows(BizException.class,
                () -> progressService.mark(planId, TEST_USER, itemIds.get(0), true));
        assertEquals(ErrorCode.PLAN_STATE_INVALID, confirmed.getErrorCode());

        assertEquals(0, count("select count(*) from t_travel_progress where plan_id = ?", planId),
                "被拒绝的请求不得留下进度行");
    }

    @Test
    @DisplayName("行程项必须属于该计划：不存在或跨计划一律 PLAN_ITEM_NOT_FOUND")
    void itemMustBelongToPlan() {
        long planId = generatePlan();
        planService.confirm(planId, TEST_USER);
        planService.start(planId, TEST_USER);

        BizException missing = assertThrows(BizException.class,
                () -> progressService.mark(planId, TEST_USER, 123_456_789L, true));
        assertEquals(ErrorCode.PLAN_ITEM_NOT_FOUND, missing.getErrorCode());

        // 另一个用户的计划里的行程项，不能拿来在自己计划里打勾
        long otherPlan = generatePlan(OTHER_USER);
        List<Long> otherItems = itemIds(otherPlan);
        BizException foreign = assertThrows(BizException.class,
                () -> progressService.mark(planId, TEST_USER, otherItems.get(0), true));
        assertEquals(ErrorCode.PLAN_ITEM_NOT_FOUND, foreign.getErrorCode());

        assertEquals(0, count("select count(*) from t_travel_progress where plan_id = ?", planId));
    }

    @Test
    @DisplayName("幂等：同一项连点两次只留一行，完成数不翻倍")
    void markingIsIdempotent() {
        long planId = startedPlan();
        List<Long> itemIds = itemIds(planId);
        int total = itemIds.size();

        PlanProgressVO first = progressService.mark(planId, TEST_USER, itemIds.get(0), true);
        PlanProgressVO second = progressService.mark(planId, TEST_USER, itemIds.get(0), true);

        assertEquals(1, first.getCompleted());
        assertEquals(1, second.getCompleted(), "重复勾选不能变成 2");
        assertEquals(total, second.getTotal());
        assertEquals(1, count("select count(*) from t_travel_progress where plan_id = ?", planId),
                "重复勾选只能有一行（uk_plan_item 生效）");
        assertEquals(Math.round(100.0 / total), second.getPercent());
    }

    @Test
    @DisplayName("取消勾选是物理删除：行消失、完成数回退")
    void unmarkRemovesRow() {
        long planId = startedPlan();
        List<Long> itemIds = itemIds(planId);

        progressService.mark(planId, TEST_USER, itemIds.get(0), true);
        progressService.mark(planId, TEST_USER, itemIds.get(1), true);
        assertEquals(2, count("select count(*) from t_travel_progress where plan_id = ?", planId));

        PlanProgressVO after = progressService.mark(planId, TEST_USER, itemIds.get(0), false);
        assertEquals(1, after.getCompleted());
        assertEquals(1, count("select count(*) from t_travel_progress where plan_id = ?", planId));

        // 取消后再勾选必须能成功（逻辑删除 + 唯一键会在这里炸掉，这正是用物理删除的原因）
        PlanProgressVO again = progressService.mark(planId, TEST_USER, itemIds.get(0), true);
        assertEquals(2, again.getCompleted());
    }

    @Test
    @DisplayName("进度结构：每天汇总 + 全局 next 指向第一个未完成项")
    void progressStructureIsConsistent() {
        long planId = startedPlan();
        List<Long> itemIds = itemIds(planId);

        PlanProgressVO empty = progressService.progress(planId, TEST_USER);
        assertEquals(0, empty.getCompleted());
        assertEquals(0, empty.getPercent());
        assertEquals("IN_PROGRESS", empty.getStatus());
        assertTrue(empty.getDays().stream().allMatch(d -> d.getCompleted() == 0));

        // 全部未完成时，next 必须落在第一条
        PlanProgressVO.ItemProgress firstNext = empty.getDays().get(0).getItems().get(0);
        assertTrue(firstNext.isNext());

        progressService.mark(planId, TEST_USER, itemIds.get(0), true);
        PlanProgressVO after = progressService.progress(planId, TEST_USER);

        assertFalse(after.getDays().get(0).getItems().get(0).isNext(),
                "已完成项不再指向 next");
        // next 只有一个：不能再出现第二个 true
        long nextCount = after.getDays().stream()
                .flatMap(d -> d.getItems().stream())
                .filter(PlanProgressVO.ItemProgress::isNext)
                .count();
        assertEquals(1, nextCount, "next 全表只能有一个");

        // 每天的 total 之和等于全局 total
        int sum = after.getDays().stream().mapToInt(PlanProgressVO.DayProgress::getTotal).sum();
        assertEquals(after.getTotal(), sum);
        assertEquals(itemIds.size(), after.getTotal());

        // 行程项 ID 必须以字符串下发（雪花 ID 超出 JS 安全整数）
        String firstId = after.getDays().get(0).getItems().get(0).getItemId();
        assertTrue(firstId.chars().allMatch(Character::isDigit));
        assertTrue(Long.parseLong(firstId) > Integer.MAX_VALUE);
    }

    // ---------------- helpers ----------------

    /** 生成一份计划（Mock 草稿：3 天 4 个行程项） */
    private long generatePlan() {
        return generatePlan(TEST_USER);
    }

    private long generatePlan(long userId) {
        stubAiReturns(PlanMutants.validDraft());
        return planService.generate(userId, messages()).path("planId").asLong();
    }

    /** 直接推进到 IN_PROGRESS（打勾的前置状态） */
    private long startedPlan() {
        long planId = generatePlan();
        planService.confirm(planId, TEST_USER);
        planService.start(planId, TEST_USER);
        return planId;
    }

    private List<Long> itemIds(long planId) {
        return jdbc.queryForList("select i.id from t_travel_item i "
                + "join t_travel_day d on d.id = i.day_id where d.plan_id = ? "
                + "order by d.day_index, i.item_order", Long.class, planId);
    }

    private void stubAiReturns(JsonNode draft) {
        Map<String, Object> payload = Map.of("code", 0, "data", Map.of(
                "draft", Map.of("plan", draft),
                "done", Map.of("provider", "mock", "model", "mock-model")));
        when(aiServiceClient.generatePlan(anyList(), any()))
                .thenReturn((JsonNode) new com.fasterxml.jackson.databind.ObjectMapper()
                        .valueToTree(payload));
    }

    private List<Map<String, String>> messages() {
        return List.of(Map.of("role", "user", "content", "我想去成都玩三天，预算三千，喜欢美食"));
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
