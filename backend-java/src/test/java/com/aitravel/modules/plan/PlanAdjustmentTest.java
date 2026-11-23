package com.aitravel.modules.plan;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.chat.service.AiServiceClient;
import com.aitravel.modules.plan.dto.PlanAdjustmentVO;
import com.aitravel.modules.plan.entity.PlanAdjustment;
import com.aitravel.modules.plan.service.PlanAdjustmentService;
import com.aitravel.modules.plan.service.PlanProgressService;
import com.aitravel.modules.plan.service.PlanService;
import com.aitravel.support.PlanMutants;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
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

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.Mockito.when;

/**
 * M2 调整建议的落库级证据。
 *
 * <p>这块最容易「看起来对了其实全错」的地方是三处副作用，全部只有连真库才验得出：
 * <ul>
 *   <li><b>增量而非重建</b>：未被点名的行程项必须保持原 itemId，用户打的勾才留得住。
 *       若图省事照搬回滚的整份替换，功能测试照样绿，用户的进度却全丢了。</li>
 *   <li><b>删项要连带清进度</b>：行程项没了、勾选行还在，统计会算出「完成数 &gt; 总数」。</li>
 *   <li><b>重排会撞唯一键</b>：{@code uk_day_item(day_id, item_order, deleted)} 是真唯一键，
 *       不先把整天的序号挪到负区间就会 Duplicate entry。连着重排两次才暴露。</li>
 * </ul>
 */
@SpringBootTest
@ActiveProfiles("test")
@EnabledIfEnvironmentVariable(named = "MYSQL_PASSWORD", matches = ".+")
class PlanAdjustmentTest {

    private static final long TEST_USER = 9_000_301L;
    private static final long OTHER_USER = 9_000_302L;

    private static final ObjectMapper MAPPER = new ObjectMapper();

    @Autowired
    private PlanService planService;

    @Autowired
    private PlanProgressService progressService;

    @Autowired
    private PlanAdjustmentService adjustmentService;

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
    @DisplayName("确认调整：只改被点名的项，itemId 不变，已打的勾还在")
    void confirmKeepsItemIdsAndProgress() {
        long planId = startedPlan();
        List<Long> ids = itemIds(planId);
        progressService.mark(planId, TEST_USER, ids.get(0), true);

        int before = version(planId);
        String noteBefore = note(ids.get(0));
        stubAdjust("给第一项补个备注", """
                [{"op":"UPDATE_ITEM","itemId":"%d","changes":[{"field":"note","after":"从北门进更顺"}]}]
                """.formatted(ids.get(0)));

        PlanAdjustmentVO proposal = adjustmentService.propose(planId, TEST_USER, "第一项从北门进", "");
        assertEquals(PlanAdjustment.PENDING, proposal.getStatus());
        // before 由服务端回填，模型根本不知道原值
        assertEquals(noteBefore, firstChangeBefore(proposal));

        adjustmentService.confirm(planId, TEST_USER, Long.valueOf(proposal.getAdjustmentId()));

        assertEquals(before + 1, version(planId));
        assertEquals(ids, itemIds(planId), "未被点名的行程项不该换 ID");
        assertEquals("从北门进更顺", note(ids.get(0)));
        assertEquals(1, count("select count(*) from t_travel_progress where plan_id = ?", planId),
                "改备注不该影响已完成的勾选");
    }

    @Test
    @DisplayName("删除行程项：连带清掉它的进度，不清会让完成数超过总数")
    void removeClearsItsProgress() {
        long planId = startedPlan();
        List<Long> ids = itemIds(planId);
        progressService.mark(planId, TEST_USER, ids.get(1), true);
        assertEquals(1, count("select count(*) from t_travel_progress where plan_id = ?", planId));

        stubAdjust("下午不去了", """
                [{"op":"REMOVE_ITEM","itemId":"%d"}]
                """.formatted(ids.get(1)));

        PlanAdjustmentVO proposal = adjustmentService.propose(planId, TEST_USER, "下午那个不去了", "");
        var result = adjustmentService.confirm(planId, TEST_USER, Long.valueOf(proposal.getAdjustmentId()));

        assertEquals(1, result.clearedProgress());
        assertEquals(ids.size() - 1, itemIds(planId).size());
        assertEquals(0, count("select count(*) from t_travel_progress where plan_id = ?", planId));
        // 删掉的是第 1 天的第 2 项，剩下那一项必须重新落到 1：留个空洞会让下一次插入撞唯一键
        assertEquals(List.of(1), jdbc.queryForList(
                "select item_order from t_travel_item i join t_travel_day d on d.id = i.day_id "
                        + "where d.plan_id = ? and d.day_index = 1 and i.deleted = 0 order by item_order",
                Integer.class, planId));
    }

    @Test
    @DisplayName("连续两次重排都不撞 uk_day_item：重排要先把整天序号挪到负区间")
    void reorderTwiceDoesNotHitUniqueKey() {
        long planId = startedPlan();
        List<Long> ids = itemIds(planId);
        List<Long> day1 = ids.subList(0, 2);

        adjustOrder(planId, List.of(day1.get(1), day1.get(0)));
        assertEquals(day1.get(1), itemIds(planId).get(0), "第一次重排生效");

        // 第二次是真正容易炸的那次：同一天、同两个位置再换一遍
        adjustOrder(planId, List.of(day1.get(0), day1.get(1)));
        assertEquals(day1.get(0), itemIds(planId).get(0), "第二次重排生效");
    }

    @Test
    @DisplayName("建议生成后计划被改过：确认时判过期，绝不硬套覆盖用户的修改")
    void staleBaseVersionExpires() {
        long planId = startedPlan();
        List<Long> ids = itemIds(planId);
        stubAdjust("改个备注", """
                [{"op":"UPDATE_ITEM","itemId":"%d","changes":[{"field":"note","after":"x"}]}]
                """.formatted(ids.get(0)));
        PlanAdjustmentVO proposal = adjustmentService.propose(planId, TEST_USER, "改备注", "");

        // 模拟用户在等待期间自己改了一处（任何写操作都会让 version 前进）
        jdbc.update("update t_travel_plan set version = version + 1 where id = ?", planId);

        BizException e = assertThrows(BizException.class, () ->
                adjustmentService.confirm(planId, TEST_USER, Long.valueOf(proposal.getAdjustmentId())));
        assertEquals(ErrorCode.ADJUSTMENT_STALE, e.getErrorCode());
        assertEquals(PlanAdjustment.EXPIRED,
                jdbc.queryForObject("select status from t_plan_adjustment where id = ?", String.class,
                        proposal.getAdjustmentId()));
    }

    @Test
    @DisplayName("拒绝建议：状态转 REJECTED，正式计划一个字段都不动")
    void rejectKeepsPlanUntouched() {
        long planId = startedPlan();
        List<Long> ids = itemIds(planId);
        int before = version(planId);
        String noteBefore = note(ids.get(0));

        stubAdjust("改个备注", """
                [{"op":"UPDATE_ITEM","itemId":"%d","changes":[{"field":"note","after":"x"}]}]
                """.formatted(ids.get(0)));
        PlanAdjustmentVO proposal = adjustmentService.propose(planId, TEST_USER, "改备注", "");
        adjustmentService.reject(planId, TEST_USER, Long.valueOf(proposal.getAdjustmentId()), "算了");

        assertEquals(before, version(planId), "拒绝不该产生新版本");
        assertEquals(noteBefore, note(ids.get(0)));
        assertEquals(PlanAdjustment.REJECTED,
                jdbc.queryForObject("select status from t_plan_adjustment where id = ?", String.class,
                        proposal.getAdjustmentId()));
    }

    @Test
    @DisplayName("草稿不走调整建议：直接编辑即可，绕一圈 AI 只会多一次出错机会")
    void draftCannotAdjust() {
        long planId = generatePlan();
        BizException e = assertThrows(BizException.class, () ->
                adjustmentService.propose(planId, TEST_USER, "改一下", ""));
        assertEquals(ErrorCode.PLAN_STATE_INVALID, e.getErrorCode());
    }

    @Test
    @DisplayName("拿另一个计划的 itemId 出建议：拒绝，不能改到别人的行程")
    void foreignItemIdRejected() {
        long mine = startedPlan();
        long another = startedPlan();
        long foreignItem = itemIds(another).get(0);
        assertNotEquals(foreignItem, itemIds(mine).get(0));

        stubAdjust("改别人的", """
                [{"op":"UPDATE_ITEM","itemId":"%d","changes":[{"field":"note","after":"x"}]}]
                """.formatted(foreignItem));

        BizException e = assertThrows(BizException.class, () ->
                adjustmentService.propose(mine, TEST_USER, "改一下", ""));
        assertEquals(ErrorCode.ADJUSTMENT_INVALID, e.getErrorCode());
    }

    @Test
    @DisplayName("建议不归你：确认被拒，不泄露「这个 ID 存在」")
    void otherUserCannotConfirm() {
        long planId = startedPlan();
        List<Long> ids = itemIds(planId);
        stubAdjust("改备注", """
                [{"op":"UPDATE_ITEM","itemId":"%d","changes":[{"field":"note","after":"x"}]}]
                """.formatted(ids.get(0)));
        PlanAdjustmentVO proposal = adjustmentService.propose(planId, TEST_USER, "改备注", "");

        BizException e = assertThrows(BizException.class, () ->
                adjustmentService.confirm(planId, OTHER_USER, Long.valueOf(proposal.getAdjustmentId())));
        assertEquals(ErrorCode.FORBIDDEN, e.getErrorCode());
    }

    @Test
    @DisplayName("确认后写一份「确认调整」快照，回滚能回到调整前")
    void confirmWritesSnapshot() {
        long planId = startedPlan();
        List<Long> ids = itemIds(planId);
        stubAdjust("改备注", """
                [{"op":"UPDATE_ITEM","itemId":"%d","changes":[{"field":"note","after":"调整后的备注"}]}]
                """.formatted(ids.get(0)));
        PlanAdjustmentVO proposal = adjustmentService.propose(planId, TEST_USER, "改备注", "");
        adjustmentService.confirm(planId, TEST_USER, Long.valueOf(proposal.getAdjustmentId()));

        int latest = version(planId);
        String summary = jdbc.queryForObject(
                "select change_summary from t_plan_version where plan_id = ? and version_no = ?",
                String.class, planId, latest);
        assertTrue(summary != null && summary.startsWith("确认调整："), "实际摘要：" + summary);
    }

    // ---------------- helpers ----------------

    private long generatePlan() {
        JsonNode draft = PlanMutants.validDraft();
        Map<String, Object> payload = Map.of("code", 0, "data", Map.of(
                "draft", Map.of("plan", draft),
                "done", Map.of("provider", "mock", "model", "mock-model")));
        when(aiServiceClient.generatePlan(anyList(), any()))
                .thenReturn((JsonNode) MAPPER.valueToTree(payload));
        return planService.generate(TEST_USER, messages()).path("planId").asLong();
    }

    /** 生成 → 确认 → 开始，得到可调整的计划 */
    private long startedPlan() {
        long planId = generatePlan();
        planService.confirm(planId, TEST_USER);
        planService.start(planId, TEST_USER, null);
        return planId;
    }

    private void adjustOrder(long planId, List<Long> ordered) {
        stubAdjust("调整顺序", """
                [{"op":"REORDER_ITEM","dayIndex":1,"orderedItemIds":["%d","%d"]}]
                """.formatted(ordered.get(0), ordered.get(1)));
        PlanAdjustmentVO proposal = adjustmentService.propose(planId, TEST_USER, "把这两个换个顺序", "");
        adjustmentService.confirm(planId, TEST_USER, Long.valueOf(proposal.getAdjustmentId()));
    }

    private void stubAdjust(String summary, String opsJson) {
        ObjectNode data = MAPPER.createObjectNode();
        data.put("summary", summary);
        try {
            data.set("ops", MAPPER.readTree(opsJson));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
        when(aiServiceClient.adjustPlan(any(), any(), any())).thenReturn(data);
    }

    private String firstChangeBefore(PlanAdjustmentVO vo) {
        Object changes = vo.getOps().get(0).get("changes");
        return String.valueOf(((List<?>) changes).get(0) instanceof Map<?, ?> m ? m.get("before") : null);
    }

    private String note(long itemId) {
        return jdbc.queryForObject("select note from t_travel_item where id = ?", String.class, itemId);
    }

    private int version(long planId) {
        Integer v = jdbc.queryForObject("select version from t_travel_plan where id = ?", Integer.class, planId);
        return v == null ? 0 : v;
    }

    private List<Long> itemIds(long planId) {
        return jdbc.queryForList("select i.id from t_travel_item i join t_travel_day d on d.id = i.day_id "
                + "where d.plan_id = ? and i.deleted = 0 and d.deleted = 0 "
                + "order by d.day_index, i.item_order", Long.class, planId);
    }

    private List<Map<String, String>> messages() {
        return List.of(Map.of("role", "user", "content", "我想去成都玩三天，预算三千，喜欢美食"));
    }

    private int count(String sql, Object... args) {
        Integer value = jdbc.queryForObject(sql, Integer.class, args);
        return value == null ? 0 : value;
    }

    private void cleanUp() {
        jdbc.update("delete from t_plan_adjustment where plan_id in "
                + "(select id from t_travel_plan where user_id in (?, ?))", TEST_USER, OTHER_USER);
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
