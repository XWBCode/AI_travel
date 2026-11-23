package com.aitravel.modules.idempotency;

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

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.Mockito.when;

/**
 * BUG-007 的并发证据：连真实 MySQL，靠唯一约束而不是靠锁。
 *
 * <p>PM 要求的场景在这里落地：连续 10 次、并发 10 次、成功后重提、同 ID 不同内容、
 * 处理中重提、失败后重试、两个用户同 ID。
 *
 * <p>「业务数据只产生一次」用 SQL count 证明，不看接口返回——接口返回 200 不代表只写了一份。
 * 未设置 {@code MYSQL_PASSWORD} 时整类跳过（不是失败）。
 */
@SpringBootTest
@ActiveProfiles("test")
@EnabledIfEnvironmentVariable(named = "MYSQL_PASSWORD", matches = ".+")
class IdempotencyDbTest {

    private static final long USER = 9_100_001L;
    private static final long OTHER_USER = 9_100_002L;

    @Autowired
    private PlanService planService;

    @Autowired
    private JdbcTemplate jdbc;

    @Autowired
    private com.aitravel.modules.idempotency.job.IdempotencyCleanupJob cleanupJob;

    @Autowired
    private ObjectMapper objectMapper;

    @MockBean
    private AiServiceClient aiServiceClient;

    private final List<Map<String, String>> messages = List.of(
            Map.of("role", "user", "content", "帮我规划成都三天，两个人，喜欢美食"));

    @BeforeEach
    void setUp() {
        clean();
        when(aiServiceClient.generatePlan(anyList(), any())).thenAnswer(inv -> aiResponse());
    }

    @AfterEach
    void tearDown() {
        clean();
    }

    private void clean() {
        jdbc.update("DELETE FROM t_travel_plan WHERE user_id IN (?,?)", USER, OTHER_USER);
        jdbc.update("DELETE FROM t_idempotency WHERE user_id IN (?,?)", USER, OTHER_USER);
    }

    /** 造一份能过契约校验的 AI 响应；每次返回新的 JsonNode，避免被下游改写 */
    private JsonNode aiResponse() {
        ObjectNode root = objectMapper.createObjectNode();
        root.put("code", 0);
        ObjectNode data = root.putObject("data");
        data.putObject("draft").set("plan", PlanMutants.validDraft());
        ObjectNode done = data.putObject("done");
        done.put("provider", "test");
        done.put("model", "unit-test");
        return root;
    }

    private int planCount() {
        Integer n = jdbc.queryForObject("SELECT COUNT(*) FROM t_travel_plan WHERE user_id=?", Integer.class, USER);
        return n == null ? 0 : n;
    }

    private int idemCount() {
        Integer n = jdbc.queryForObject("SELECT COUNT(*) FROM t_idempotency WHERE user_id=?", Integer.class, USER);
        return n == null ? 0 : n;
    }

    @Test
    @DisplayName("同一 ID 连续提交 10 次：只产生 1 份计划，10 次拿到同一个 planId")
    void sequentialRetriesCreateOnePlan() {
        List<String> ids = new ArrayList<>();
        for (int i = 0; i < 10; i++) {
            JsonNode plan = planService.generate(USER, messages, "req-seq");
            ids.add(plan.path("planId").asText());
        }
        assertEquals(1, planCount(), "连续提交 10 次只应有 1 份计划");
        assertEquals(1, ids.stream().distinct().count(), "10 次返回的 planId 必须一致");
        assertEquals(1, idemCount());
    }

    @Test
    @DisplayName("同一 ID 并发 10 次：唯一约束兜住，业务数据仍只有 1 份")
    void concurrentRetriesCreateOnePlan() throws Exception {
        int threads = 10;
        ExecutorService pool = Executors.newFixedThreadPool(threads);
        CountDownLatch ready = new CountDownLatch(threads);
        CountDownLatch go = new CountDownLatch(1);
        AtomicInteger ok = new AtomicInteger();
        AtomicInteger processing = new AtomicInteger();
        List<String> planIds = new ArrayList<>();

        for (int i = 0; i < threads; i++) {
            pool.submit(() -> {
                ready.countDown();
                try {
                    go.await(5, TimeUnit.SECONDS);
                    JsonNode plan = planService.generate(USER, messages, "req-race");
                    synchronized (planIds) {
                        planIds.add(plan.path("planId").asText());
                    }
                    ok.incrementAndGet();
                } catch (BizException e) {
                    if (e.getErrorCode() == ErrorCode.IDEMPOTENCY_PROCESSING) {
                        processing.incrementAndGet();
                    }
                } catch (Exception ignored) {
                    // 其他异常交给断言里的数量体现，不在工作线程里抛
                }
            });
        }
        assertTrue(ready.await(5, TimeUnit.SECONDS));
        go.countDown();
        pool.shutdown();
        assertTrue(pool.awaitTermination(60, TimeUnit.SECONDS));

        assertEquals(1, planCount(), "并发 10 次只能有 1 份计划");
        assertEquals(1, idemCount(), "幂等记录也只能有 1 条");
        assertEquals(threads, ok.get() + processing.get(), "每个请求要么回放结果，要么被明确告知处理中");
        assertEquals(1, planIds.stream().distinct().count(), "成功的请求必须指向同一份计划");
    }

    @Test
    @DisplayName("成功后重提：返回首次计划，不新建")
    void replayAfterSuccess() {
        JsonNode first = planService.generate(USER, messages, "req-replay");
        JsonNode again = planService.generate(USER, messages, "req-replay");
        assertEquals(first.path("planId").asText(), again.path("planId").asText());
        assertEquals(1, planCount());
    }

    @Test
    @DisplayName("同 ID 不同内容：30112，且不产生第二份计划")
    void sameIdDifferentPayloadIsRejected() {
        planService.generate(USER, messages, "req-conflict");
        List<Map<String, String>> other = List.of(
                Map.of("role", "user", "content", "帮我规划西安四天"));

        BizException e = assertThrows(BizException.class,
                () -> planService.generate(USER, other, "req-conflict"));
        assertEquals(ErrorCode.IDEMPOTENCY_CONFLICT, e.getErrorCode());
        assertEquals(1, planCount());
    }

    @Test
    @DisplayName("首次失败后同 ID 重试：允许执行并最终成功")
    void retryAfterFailureSucceeds() {
        when(aiServiceClient.generatePlan(anyList(), any()))
                .thenThrow(new BizException(ErrorCode.AI_SERVICE_UNAVAILABLE, "模型超时"))
                .thenAnswer(inv -> aiResponse());

        assertThrows(BizException.class, () -> planService.generate(USER, messages, "req-retry"));
        String status = jdbc.queryForObject(
                "SELECT status FROM t_idempotency WHERE user_id=? AND request_id=?", String.class, USER, "req-retry");
        assertEquals("FAILED", status, "失败必须记下来，否则重试会被当成并发请求挡掉");

        JsonNode plan = planService.generate(USER, messages, "req-retry");
        assertNotNull(plan.path("planId").asText(null));
        assertEquals(1, planCount());
        assertEquals("SUCCESS", jdbc.queryForObject(
                "SELECT status FROM t_idempotency WHERE user_id=? AND request_id=?", String.class, USER, "req-retry"));
    }

    @Test
    @DisplayName("两个用户用同一个请求 ID：互不影响，各自生成")
    void sameRequestIdAcrossUsersIsIsolated() {
        planService.generate(USER, messages, "shared-id");
        planService.generate(OTHER_USER, messages, "shared-id");

        assertEquals(1, planCount());
        Integer other = jdbc.queryForObject(
                "SELECT COUNT(*) FROM t_travel_plan WHERE user_id=?", Integer.class, OTHER_USER);
        assertEquals(1, other, "幂等域必须按用户隔离，不能把 B 的请求当成 A 的重复");
    }

    @Test
    @DisplayName("过期清理：只删保留期外的终态行，PROCESSING 与近期记录一条不动")
    void purgeRemovesOnlyOldTerminalRows() {
        insertIdem("old-success", "SUCCESS", 31);
        insertIdem("old-processing", "PROCESSING", 31);
        insertIdem("fresh-success", "SUCCESS", 1);

        int deleted = cleanupJob.purge(30);

        assertEquals(1, deleted, "两条 31 天前的记录里只有 SUCCESS 该被删");
        assertEquals(0, countIdem("old-success"));
        assertEquals(1, countIdem("old-processing"), "卡死的 PROCESSING 留着：删掉会让重复请求重新执行");
        assertEquals(1, countIdem("fresh-success"), "保留期内的一条都不能动");
    }

    @Test
    @DisplayName("清理是真删：逻辑删除会留下唯一键占位，等于没清")
    void purgeIsPhysicalNotLogical() {
        insertIdem("old-success", "SUCCESS", 31);
        cleanupJob.purge(30);

        // 逻辑删除的行会以 deleted=1 留在表里，这里连这行都不该存在
        Integer rows = jdbc.queryForObject(
                "SELECT COUNT(*) FROM t_idempotency WHERE user_id=? AND request_id=?", Integer.class,
                USER, "old-success");
        assertEquals(0, rows == null ? 0 : rows);

        // 唯一键已释放：同一个 ID 可以重新写入，说明清理对索引确实生效了
        assertEquals(1, insertIdem("old-success", "SUCCESS", 0));
    }

    private int insertIdem(String requestId, String status, int daysAgo) {
        long id = System.nanoTime();
        return jdbc.update("INSERT INTO t_idempotency "
                        + "(id, user_id, request_id, action, request_hash, status, create_time, update_time, deleted) "
                        + "VALUES (?, ?, ?, 'PLAN_GENERATE', REPEAT('a', 64), ?, "
                        + "DATE_SUB(NOW(), INTERVAL ? DAY), DATE_SUB(NOW(), INTERVAL ? DAY), 0)",
                id, USER, requestId, status, daysAgo, daysAgo);
    }

    private int countIdem(String requestId) {
        Integer n = jdbc.queryForObject("SELECT COUNT(*) FROM t_idempotency WHERE user_id=? AND request_id=?",
                Integer.class, USER, requestId);
        return n == null ? 0 : n;
    }
}
