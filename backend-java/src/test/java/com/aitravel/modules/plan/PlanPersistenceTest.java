package com.aitravel.modules.plan;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.chat.service.AiServiceClient;
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
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.Mockito.when;

/**
 * AC-07 / AC-09 的落库级证据（连真实 MySQL，不用 H2）。
 * <p>
 * 与单测的分工：
 * <ul>
 *   <li>{@code PlanSchemaValidatorTest} 证明「校验器认得出畸形」；</li>
 *   <li>本测试证明「畸形一条也没进库」——校验通过与否是判断，落库数才是事实。</li>
 * </ul>
 * AI 服务用 Mock 替换：本测试要验的是 Java 的校验与落库，不需要真实模型参与。
 * 未设置 {@code MYSQL_PASSWORD} 时整类跳过（不是失败），避免无库环境红一片。
 */
@SpringBootTest
@ActiveProfiles("test")
@EnabledIfEnvironmentVariable(named = "MYSQL_PASSWORD", matches = ".+")
class PlanPersistenceTest {

    private static final long TEST_USER = 9_000_001L;
    private static final long OTHER_USER = 9_000_002L;

    @Autowired
    private PlanService planService;

    @Autowired
    private JdbcTemplate jdbc;

    @Autowired
    private ObjectMapper objectMapper;

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
    @DisplayName("AC-07：50 组畸形样例全部被拒，业务表落库数 = 0")
    void malformedDraftsNeverReachDatabase() {
        List<PlanMutants.Mutant> mutants = PlanMutants.all();
        assertEquals(50, mutants.size());

        int rejected = 0;
        for (PlanMutants.Mutant mutant : mutants) {
            JsonNode draft = PlanMutants.validDraft();
            mutant.mutator().accept((ObjectNode) draft);
            stubAiReturns(draft);

            BizException ex = assertThrows(BizException.class,
                    () -> planService.generate(TEST_USER, messages()),
                    "畸形样例未被拒绝：" + mutant.name());
            assertEquals(ErrorCode.PLAN_SCHEMA_VIOLATION, ex.getErrorCode(),
                    "拒绝原因应是契约校验失败：" + mutant.name());
            rejected++;
        }
        assertEquals(50, rejected);

        assertEquals(0, count("select count(*) from t_travel_plan where user_id = ?", TEST_USER),
                "AC-07：畸形样例的落库数必须为 0");
        assertEquals(0, count("select count(*) from t_travel_day where plan_id in "
                + "(select id from t_travel_plan where user_id = ?)", TEST_USER));
        // 失败也必须留痕，否则「模型吐了什么」无从追溯（R-14）
        assertEquals(50, count("select count(*) from t_ai_generation_log "
                + "where user_id = ? and validate_result = 'FAIL'", TEST_USER),
                "每次被拒都要写入生成留痕");
    }

    @Test
    @DisplayName("合规草稿落库：9 张表按结构写入，JSON 列原样保存")
    void validDraftIsPersisted() {
        stubAiReturns(PlanMutants.validDraft());
        JsonNode detail = planService.generate(TEST_USER, messages());
        long planId = detail.path("planId").asLong();

        assertTrue(planId > 0);
        assertEquals("DRAFT", detail.path("status").asText());
        assertEquals(0, detail.path("version").asInt());
        assertEquals(3, detail.path("days").size());
        // 24 个 FactField，全部 verified=false，落库后数量不变
        assertEquals(24, detail.path("unverifiedFieldCount").asInt());

        assertEquals(3, count("select count(*) from t_travel_day where plan_id = ?", planId));
        assertEquals(4, count("select count(*) from t_travel_item where day_id in "
                + "(select id from t_travel_day where plan_id = ?)", planId));
        // 样例共 3 段：day1 一段日内，day2/day3 各一段跨城
        assertEquals(3, count("select count(*) from t_transport_segment where plan_id = ?", planId));
        assertEquals(3, count("select count(*) from t_budget_item where plan_id = ?", planId));
        // 跨城段的 day_id 必须为空（Q-04：跨城段不属于任何一天）
        assertEquals(2, count("select count(*) from t_transport_segment "
                + "where plan_id = ? and scope = 'INTER_CITY' and day_id is null", planId));
        // 落库的 JSON 必须仍是「待核实」，不能被序列化改写成别的形态
        assertEquals(1, count("select count(*) from t_travel_plan "
                + "where id = ? and json_extract(budget, '$.total.display') = '待核实'", planId));
        assertEquals(1, count("select count(*) from t_travel_plan "
                + "where id = ? and json_length(destinations) = 3", planId));
    }

    @Test
    @DisplayName("AC-09：未确认不得开始；确认后写快照；终态不可迁移")
    void confirmationIsMandatoryBeforeStart() {
        stubAiReturns(PlanMutants.validDraft());
        long planId = planService.generate(TEST_USER, messages()).path("planId").asLong();

        // 1) 草稿直接开始 → 拒绝，且状态不变
        BizException illegal = assertThrows(BizException.class,
                () -> planService.start(planId, TEST_USER));
        assertEquals(ErrorCode.PLAN_STATE_INVALID, illegal.getErrorCode());
        assertEquals("DRAFT", statusOf(planId), "被拒绝的迁移不得改动状态");

        // 2) 确认 → CONFIRMED，version +1，且落一条版本快照
        planService.confirm(planId, TEST_USER);
        assertEquals("CONFIRMED", statusOf(planId));
        assertEquals(1, versionOf(planId), "确认应把 version 从 0 推到 1");
        assertEquals(1, count("select count(*) from t_plan_version where plan_id = ?", planId));
        assertEquals(1, count("select count(*) from t_plan_version "
                + "where plan_id = ? and json_extract(snapshot_json, '$.status') = 'CONFIRMED'", planId));

        // 3) 重复确认 → 拒绝
        BizException again = assertThrows(BizException.class,
                () -> planService.confirm(planId, TEST_USER));
        assertEquals(ErrorCode.PLAN_STATE_INVALID, again.getErrorCode());

        // 4) 正常推进
        planService.start(planId, TEST_USER);
        assertEquals("IN_PROGRESS", statusOf(planId));
        planService.complete(planId, TEST_USER);
        assertEquals("COMPLETED", statusOf(planId));

        // 5) 终态不可再取消
        BizException terminal = assertThrows(BizException.class,
                () -> planService.cancel(planId, TEST_USER));
        assertEquals(ErrorCode.PLAN_STATE_INVALID, terminal.getErrorCode());
    }

    @Test
    @DisplayName("越权：他人计划一律拒绝，且不暴露是否存在")
    void otherUsersPlanIsForbidden() {
        stubAiReturns(PlanMutants.validDraft());
        long planId = planService.generate(TEST_USER, messages()).path("planId").asLong();

        BizException ex = assertThrows(BizException.class,
                () -> planService.detail(planId, OTHER_USER));
        assertEquals(ErrorCode.FORBIDDEN, ex.getErrorCode());
    }

    @Test
    @DisplayName("M1 P2-4：编辑草稿——字段落库、version+1、改前改后各一份快照、天数同步到每日行程")
    void draftCanBeEdited() {
        stubAiReturns(PlanMutants.validDraft());
        long planId = planService.generate(TEST_USER, messages()).path("planId").asLong();
        assertEquals("成都乐山三日游", titleOf(planId));

        JsonNode updated = planService.update(planId, TEST_USER,
                body("{\"title\":\"成都四日美食\",\"days\":4,\"travelers\":3}"));

        assertEquals("成都四日美食", updated.path("title").asText());
        assertEquals(4, updated.path("basic").path("days").asInt());
        assertEquals(3, updated.path("basic").path("travelers").asInt());
        // 天数改了，结束日期必须跟着走：11-20 起 4 天 → 11-23
        assertEquals("2026-11-20", updated.path("basic").path("startDate").asText());
        assertEquals("2026-11-23", updated.path("basic").path("endDate").asText());
        assertEquals(1, updated.path("version").asInt());

        // 天数变多要补出第 4 天，否则会出现「头部 4 天、下面只列 3 天」
        assertEquals(4, count("select count(*) from t_travel_day where plan_id = ?", planId));
        assertEquals(1, count("select count(*) from t_travel_day "
                + "where plan_id = ? and day_index = 4 and day_date = '2026-11-23'", planId));
        // 改前（v0）+ 改后（v1）各一份快照，M2 回滚才有原稿可回
        assertEquals(2, count("select count(*) from t_plan_version where plan_id = ?", planId));
        assertEquals(1, count("select count(*) from t_plan_version "
                + "where plan_id = ? and version_no = 0 and change_summary = '编辑前自动快照'", planId));

        // 天数改少：尾部整天连同其行程项一起删掉（逻辑删除，统计要带 deleted = 0）
        JsonNode shrunk = planService.update(planId, TEST_USER, body("{\"days\":2}"));
        assertEquals(2, shrunk.path("basic").path("days").asInt());
        assertEquals(2, count("select count(*) from t_travel_day "
                + "where plan_id = ? and deleted = 0", planId));
        assertEquals(3, count("select count(*) from t_travel_item where deleted = 0 and day_id in "
                + "(select id from t_travel_day where plan_id = ? and deleted = 0)", planId),
                "第 3 天的行程项应随该天一起删除（原样例共 4 项）");
        // 统计值随删天重算：24 个 FactField 中至少少了第 3 天那一份
        assertTrue(shrunk.path("unverifiedFieldCount").asInt() < 24,
                "删掉整天后待核实字段数必须重算，不能沿用旧值");
    }

    @Test
    @DisplayName("M1 P2-4：版本冲突与状态保护——过期版本改不动，已确认的不能改")
    void editIsGuardedByVersionAndStatus() {
        stubAiReturns(PlanMutants.validDraft());
        long planId = planService.generate(TEST_USER, messages()).path("planId").asLong();

        planService.update(planId, TEST_USER, body("{\"title\":\"改过一次\"}"));
        assertEquals(1, versionOf(planId));

        // 带着过期的 baseVersion 再提交：必须拒绝，且不能覆盖别人的修改
        BizException conflict = assertThrows(BizException.class,
                () -> planService.update(planId, TEST_USER,
                        body("{\"title\":\"旧版本提交\",\"baseVersion\":0}")));
        assertEquals(ErrorCode.PLAN_VERSION_CONFLICT, conflict.getErrorCode());
        assertEquals("改过一次", titleOf(planId), "冲突的请求不得改动任何数据");

        // 确认之后进入只读：编辑会让「确认」这个动作失去意义
        planService.confirm(planId, TEST_USER);
        BizException locked = assertThrows(BizException.class,
                () -> planService.update(planId, TEST_USER, body("{\"title\":\"确认后再改\"}")));
        assertEquals(ErrorCode.PLAN_STATE_INVALID, locked.getErrorCode());
        assertEquals("改过一次", titleOf(planId));
    }

    @Test
    @DisplayName("M1 P2-4：内容没变时不产生新版本（幂等保存）")
    void noOpEditDoesNotCreateVersion() {
        stubAiReturns(PlanMutants.validDraft());
        long planId = planService.generate(TEST_USER, messages()).path("planId").asLong();

        planService.update(planId, TEST_USER, body("{\"title\":\"成都乐山三日游\",\"days\":3}"));
        assertEquals(0, versionOf(planId), "值与原来一致时不该推版本");
        assertEquals(0, count("select count(*) from t_plan_version where plan_id = ?", planId));
    }

    // ---------------- helpers ----------------

    private void stubAiReturns(JsonNode draft) {
        ObjectNode response = objectMapper.createObjectNode();
        response.put("code", 0);
        response.put("message", "ok");
        ObjectNode data = response.putObject("data");
        ObjectNode done = data.putObject("done");
        done.put("provider", "mock");
        done.put("model", "mock-model");
        data.putObject("draft").set("plan", draft);
        when(aiServiceClient.generatePlan(anyList(), any())).thenReturn(response);
    }

    private List<Map<String, String>> messages() {
        return List.of(Map.of("role", "user", "content", "我想去成都玩三天，预算三千，喜欢美食"));
    }

    private JsonNode body(String json) {
        try {
            return objectMapper.readTree(json);
        } catch (Exception e) {
            throw new IllegalStateException("测试请求体不是合法 JSON：" + json, e);
        }
    }

    private String titleOf(long planId) {
        return jdbc.queryForObject("select title from t_travel_plan where id = ?", String.class, planId);
    }

    private String statusOf(long planId) {
        return jdbc.queryForObject("select status from t_travel_plan where id = ?", String.class, planId);
    }

    private int versionOf(long planId) {
        Integer value = jdbc.queryForObject("select version from t_travel_plan where id = ?",
                Integer.class, planId);
        return value == null ? -1 : value;
    }

    private int count(String sql, Object... args) {
        Integer value = jdbc.queryForObject(sql, Integer.class, args);
        return value == null ? 0 : value;
    }

    private void cleanUp() {
        // 物理删除：逻辑删除会让「落库数」无法统计，测试需要看到真实行数
        jdbc.update("delete from t_travel_item where day_id in (select id from t_travel_day "
                + "where plan_id in (select id from t_travel_plan where user_id = ?))", TEST_USER);
        jdbc.update("delete from t_transport_segment where plan_id in "
                + "(select id from t_travel_plan where user_id = ?)", TEST_USER);
        jdbc.update("delete from t_budget_item where plan_id in "
                + "(select id from t_travel_plan where user_id = ?)", TEST_USER);
        jdbc.update("delete from t_accommodation where plan_id in "
                + "(select id from t_travel_plan where user_id = ?)", TEST_USER);
        jdbc.update("delete from t_plan_version where plan_id in "
                + "(select id from t_travel_plan where user_id = ?)", TEST_USER);
        jdbc.update("delete from t_travel_day where plan_id in "
                + "(select id from t_travel_plan where user_id = ?)", TEST_USER);
        jdbc.update("delete from t_travel_plan where user_id in (?, ?)", TEST_USER, OTHER_USER);
        jdbc.update("delete from t_ai_generation_log where user_id in (?, ?)", TEST_USER, OTHER_USER);
    }
}
