package com.aitravel.modules.plan;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.chat.service.AiServiceClient;
import com.aitravel.modules.plan.dto.PlanProgressVO;
import com.aitravel.modules.plan.service.PlanProgressService;
import com.aitravel.modules.plan.service.PlanService;
import com.aitravel.support.PlanMutants;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
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
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.Mockito.when;

/**
 * M2 版本回滚的落库级证据。
 *
 * <p>回滚最容易出错的不是「读快照」这一步，而是三个副作用：
 * <ul>
 *   <li>子表是<b>物理删除</b>后再插入——若改成逻辑删除，第二次回滚会撞
 *       {@code uk_plan_day(plan_id, day_index, deleted)}，所以「连续回滚两次」必须有用例；</li>
 *   <li>行程项会重建、itemId 全变——不打勾的计划看不出问题，只有带进度时才暴露；</li>
 *   <li>回滚本身要留下「回滚前的快照」，否则回滚错了没法回头。</li>
 * </ul>
 * 这三条都只有连真库才验得出来，Mock 掉 Mapper 会全部假通过。
 */
@SpringBootTest
@ActiveProfiles("test")
@EnabledIfEnvironmentVariable(named = "MYSQL_PASSWORD", matches = ".+")
class PlanRollbackTest {

    private static final long TEST_USER = 9_000_201L;
    private static final long OTHER_USER = 9_000_202L;

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
    @DisplayName("回滚到 v0：标题、天数、天表一起回到原样，version 继续递增")
    void rollbackRestoresContent() {
        long planId = generatePlan();
        String originalTitle = title(planId);
        assertEquals(3, days(planId));

        // 编辑成 5 天：version 0 → 1，同时写下 v0（改前）与 v1（改后）两份快照
        planService.update(planId, TEST_USER, body("{\"days\":5,\"title\":\"改过的标题\"}"));
        assertEquals(5, days(planId));
        assertEquals("改过的标题", title(planId));
        assertEquals(1, version(planId));

        PlanService.RollbackVO result = planService.rollback(planId, TEST_USER, 0);

        assertEquals(0, result.getRestoredFrom());
        assertEquals(2, result.getVersion(), "回滚也要占用一个新版本号，历史只能向前写");
        assertEquals(originalTitle, result.getPlan().path("title").asText());
        assertEquals(3, result.getPlan().path("basic").path("days").asInt());
        assertEquals(3, days(planId), "天表必须跟着回滚，否则出现「头部 3 天、下面 5 天」");
        assertEquals(0, result.getClearedProgress(), "草稿阶段没有进度可清");
        assertEquals(4, count("select count(*) from t_travel_item i join t_travel_day d "
                + "on d.id = i.day_id where d.plan_id = ?", planId),
                "原样例 3 天共 4 项，回滚到 v0 后必须一项不少");
    }

    @Test
    @DisplayName("连续回滚两次不撞唯一键（子表必须是物理删除）")
    void rollbackTwiceDoesNotCollideOnUniqueKey() {
        long planId = generatePlan();
        planService.update(planId, TEST_USER, body("{\"days\":5}"));

        planService.rollback(planId, TEST_USER, 0);
        // 第二次回滚同一版本：逻辑删除方案会在这里抛出 Duplicate entry '...' for key 'uk_plan_day'
        PlanService.RollbackVO second = planService.rollback(planId, TEST_USER, 0);

        assertEquals(3, second.getPlan().path("basic").path("days").asInt());
        assertEquals(3, days(planId));
        assertEquals(0, count("select count(*) from t_travel_day where plan_id = ? and deleted = 1",
                planId), "回滚路径不应留下逻辑删除的天");
    }

    @Test
    @DisplayName("回滚前会留快照：能再回滚到「回滚前」的样子")
    void rollbackKeepsPreRollbackSnapshot() {
        long planId = generatePlan();
        planService.update(planId, TEST_USER, body("{\"days\":5,\"title\":\"五天的版本\"}"));
        int editedVersion = version(planId);

        planService.rollback(planId, TEST_USER, 0);
        assertEquals(3, days(planId));

        // editedVersion 的快照在编辑时已写过（内容 = 5 天），回滚到它应当恢复 5 天
        PlanService.RollbackVO back = planService.rollback(planId, TEST_USER, editedVersion);
        assertEquals(5, back.getPlan().path("basic").path("days").asInt());
        assertEquals("五天的版本", back.getPlan().path("title").asText());
        assertEquals(5, days(planId));
    }

    @Test
    @DisplayName("回滚后的快照有可读的变更说明，版本列表能看出发生了什么")
    void rollbackWritesVersionSummary() {
        long planId = generatePlan();
        planService.update(planId, TEST_USER, body("{\"days\":5}"));
        PlanService.RollbackVO result = planService.rollback(planId, TEST_USER, 0);

        String summary = jdbc.queryForObject("select change_summary from t_plan_version "
                + "where plan_id = ? and version_no = ?", String.class, planId, result.getVersion());
        assertEquals("回滚到 v0", summary);

        List<String> all = jdbc.queryForList("select change_summary from t_plan_version "
                + "where plan_id = ? order by version_no", String.class, planId);
        assertEquals("AI 生成初稿", all.get(0), "v0 由生成时写入，是回滚的兜底目标");
        assertTrue(all.contains("回滚到 v0"));
        assertTrue(all.stream().anyMatch(s -> s != null && s.startsWith("编辑：")),
                "编辑的变更说明要留在版本列表里，否则看不出中间发生过什么");
    }

    @Test
    @DisplayName("进行中回滚：清空打勾记录并如实报告条数")
    void rollbackInProgressClearsProgress() {
        long planId = generatePlan();
        planService.confirm(planId, TEST_USER);   // v1，写确认快照
        planService.start(planId, TEST_USER);     // v2，不写快照
        int confirmedVersion = 1;

        List<Long> itemIds = itemIds(planId);
        progressService.mark(planId, TEST_USER, itemIds.get(0), true);
        progressService.mark(planId, TEST_USER, itemIds.get(1), true);
        assertEquals(2, count("select count(*) from t_travel_progress where plan_id = ?", planId));

        PlanService.RollbackVO result = planService.rollback(planId, TEST_USER, confirmedVersion);

        assertEquals(2, result.getClearedProgress(), "用户打过几个勾就要报几个，不能悄悄归零");
        assertEquals(0, count("select count(*) from t_travel_progress where plan_id = ?", planId),
                "行程项已重建，旧 itemId 的进度行必须清掉，否则进度条会指向不存在的项");

        // 回滚后状态不变（仍是进行中），进度从 0 重新开始
        PlanProgressVO progress = progressService.progress(planId, TEST_USER);
        assertEquals("IN_PROGRESS", progress.getStatus());
        assertEquals(0, progress.getCompleted());
        assertEquals(itemIds.size(), progress.getTotal());

        // v2 是 start 之后的版本，启动不写快照；回滚前必须先补一份，否则这次回滚不可撤销
        assertTrue(jdbc.queryForList("select change_summary from t_plan_version where plan_id = ?",
                        String.class, planId).contains("回滚前自动快照"),
                "进行中回滚前必须补写当前内容的快照");
    }

    @Test
    @DisplayName("回滚不改状态：进行中的计划回滚后还是进行中")
    void rollbackKeepsStatus() {
        long planId = generatePlan();
        planService.confirm(planId, TEST_USER);
        planService.start(planId, TEST_USER);

        planService.rollback(planId, TEST_USER, 0);

        String status = jdbc.queryForObject("select status from t_travel_plan where id = ?",
                String.class, planId);
        assertEquals("IN_PROGRESS", status, "回滚的是内容，不是时间线");
    }

    @Test
    @DisplayName("终态不可回滚：已完成 / 已取消一律拒绝")
    void terminalStateRejectsRollback() {
        long completed = generatePlan();
        planService.confirm(completed, TEST_USER);
        planService.start(completed, TEST_USER);
        planService.complete(completed, TEST_USER);
        BizException done = assertThrows(BizException.class,
                () -> planService.rollback(completed, TEST_USER, 0));
        assertEquals(ErrorCode.PLAN_STATE_INVALID, done.getErrorCode());

        long cancelled = generatePlan();
        planService.cancel(cancelled, TEST_USER);
        BizException cancel = assertThrows(BizException.class,
                () -> planService.rollback(cancelled, TEST_USER, 0));
        assertEquals(ErrorCode.PLAN_STATE_INVALID, cancel.getErrorCode());
    }

    @Test
    @DisplayName("目标版本不存在 / 就是当前版本：都拒绝，且不改动内容")
    void invalidTargetVersionRejected() {
        long planId = generatePlan();
        int current = version(planId);

        BizException missing = assertThrows(BizException.class,
                () -> planService.rollback(planId, TEST_USER, 99));
        assertEquals(ErrorCode.PLAN_VERSION_NOT_FOUND, missing.getErrorCode());

        BizException same = assertThrows(BizException.class,
                () -> planService.rollback(planId, TEST_USER, current));
        assertEquals(ErrorCode.BAD_REQUEST, same.getErrorCode());

        assertEquals(current, version(planId), "被拒绝的请求不得改动 version");
    }

    @Test
    @DisplayName("越权：回滚别人的计划一律拒绝，且不暴露是否存在")
    void otherUsersPlanIsForbidden() {
        long planId = generatePlan();
        BizException ex = assertThrows(BizException.class,
                () -> planService.rollback(planId, OTHER_USER, 0));
        assertEquals(ErrorCode.FORBIDDEN, ex.getErrorCode());
    }

    // ---------------- helpers ----------------

    private long generatePlan() {
        stubAiReturns(PlanMutants.validDraft());
        return planService.generate(TEST_USER, messages()).path("planId").asLong();
    }

    private String title(long planId) {
        return jdbc.queryForObject("select title from t_travel_plan where id = ?", String.class, planId);
    }

    private int days(long planId) {
        return count("select count(*) from t_travel_day where plan_id = ? and deleted = 0", planId);
    }

    private int version(long planId) {
        Integer v = jdbc.queryForObject("select version from t_travel_plan where id = ?",
                Integer.class, planId);
        return v == null ? 0 : v;
    }

    private List<Long> itemIds(long planId) {
        return jdbc.queryForList("select i.id from t_travel_item i "
                + "join t_travel_day d on d.id = i.day_id where d.plan_id = ? "
                + "order by d.day_index, i.item_order", Long.class, planId);
    }

    private JsonNode body(String json) {
        try {
            return new ObjectMapper().readTree(json);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    private void stubAiReturns(JsonNode draft) {
        Map<String, Object> payload = Map.of("code", 0, "data", Map.of(
                "draft", Map.of("plan", draft),
                "done", Map.of("provider", "mock", "model", "mock-model")));
        when(aiServiceClient.generatePlan(anyList(), any()))
                .thenReturn((JsonNode) new ObjectMapper().valueToTree(payload));
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
