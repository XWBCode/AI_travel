package com.aitravel.modules.notification;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.chat.service.AiServiceClient;
import com.aitravel.modules.notification.dto.NotificationPageVO;
import com.aitravel.modules.notification.dto.NotificationVO;
import com.aitravel.modules.notification.entity.Notification;
import com.aitravel.modules.notification.service.NotificationService;
import com.aitravel.modules.plan.dto.PlanAdjustmentVO;
import com.aitravel.modules.plan.service.PlanAdjustmentService;
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

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.Mockito.when;

/**
 * 站内通知（R-30）的落库级证据。
 *
 * <p>通知最容易出的三类问题，只有连真库才验得出来：
 * <ul>
 *   <li><b>该不写的时候写了</b>：状态迁移被状态机拒掉时，如果通知先写了，
 *       用户会收到一条「旅行已结束」，而计划还是草稿。这里用非法迁移来证明顺序。</li>
 *   <li><b>不该写的时候写了</b>：生成建议没有改变计划，不该进时间线；
 *       确认之后才该进。两者的区别正是「计划本身变没变」。</li>
 *   <li><b>已读时间被覆盖</b>：重复点开一条通知如果刷新 {@code read_at}，
 *       「多久才看到」就永远算不出来了。</li>
 * </ul>
 */
@SpringBootTest
@ActiveProfiles("test")
@EnabledIfEnvironmentVariable(named = "MYSQL_PASSWORD", matches = ".+")
class NotificationTest {

    private static final long TEST_USER = 9_000_401L;
    private static final long OTHER_USER = 9_000_402L;

    private static final ObjectMapper MAPPER = new ObjectMapper();

    @Autowired
    private PlanService planService;

    @Autowired
    private PlanAdjustmentService adjustmentService;

    @Autowired
    private NotificationService notificationService;

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
    @DisplayName("状态迁移：确认/开始/完成各写一条通知，且都是未读")
    void lifecycleWritesOnePerTransition() {
        long planId = generatePlan(TEST_USER);
        planService.confirm(planId, TEST_USER);
        planService.start(planId, TEST_USER, null);
        planService.complete(planId, TEST_USER);

        assertEquals(List.of(Notification.PLAN_CONFIRMED, Notification.PLAN_STARTED,
                        Notification.PLAN_COMPLETED),
                types(TEST_USER), "顺序应与迁移顺序一致");
        assertEquals(3, notificationService.unreadCount(TEST_USER));

        NotificationVO started = list(TEST_USER, false).getRecords().get(1);
        assertTrue(started.getBody().contains("第 1 天是"), "开始那条要带上锚定的出发日：" + started.getBody());
        assertEquals(planId, Long.parseLong(started.getPlanId()));
    }

    @Test
    @DisplayName("迁移被状态机拒掉：一条通知都不写（先判状态再通知）")
    void failedTransitionWritesNothing() {
        long planId = generatePlan(TEST_USER);
        assertEquals(0, count(TEST_USER));

        // 草稿不能直接完成，只有 CONFIRMED/IN_PROGRESS 才能
        BizException e = assertThrows(BizException.class, () -> planService.complete(planId, TEST_USER));
        assertEquals(ErrorCode.PLAN_STATE_INVALID, e.getErrorCode());
        assertEquals(0, count(TEST_USER), "失败的动作不该留下任何痕迹");

        // 重复取消同理：第二次进入终态校验就抛，不会写第二条
        planService.cancel(planId, TEST_USER);
        assertEquals(1, count(TEST_USER));
        assertThrows(BizException.class, () -> planService.cancel(planId, TEST_USER));
        assertEquals(1, count(TEST_USER), "重复操作不能产生重复通知");
    }

    @Test
    @DisplayName("草稿直接取消也算一次状态迁移，要通知")
    void cancelFromDraftNotifies() {
        long planId = generatePlan(TEST_USER);
        planService.cancel(planId, TEST_USER);
        assertEquals(List.of(Notification.PLAN_CANCELLED), types(TEST_USER));
    }

    @Test
    @DisplayName("生成建议不通知，确认才通知：判据是「计划本身有没有变」")
    void proposeSilentConfirmNotifies() {
        long planId = startedPlan();
        int base = count(TEST_USER);
        assertEquals(2, base, "确认 + 开始，两条");

        List<Long> ids = itemIds(planId);
        stubAdjust("把第一项的备注补上", """
                [{"op":"UPDATE_ITEM","itemId":"%d","changes":[{"field":"note","after":"从北门进"}]}]
                """.formatted(ids.get(0)));

        PlanAdjustmentVO proposal = adjustmentService.propose(planId, TEST_USER, "第一项从北门进", "");
        assertEquals(base, count(TEST_USER), "生成建议没改计划，不该进时间线");

        adjustmentService.confirm(planId, TEST_USER, Long.valueOf(proposal.getAdjustmentId()));

        assertEquals(base + 1, count(TEST_USER));
        NotificationVO latest = list(TEST_USER, false).getRecords().get(0);
        assertEquals(Notification.ADJUSTMENT_CONFIRMED, latest.getType());
        // refId 指向建议本身，将来点击跳转要用
        assertEquals(proposal.getAdjustmentId(), latest.getRefId());
        assertTrue(latest.getBody().contains("第 " + version(planId) + " 版"), "正文要带版本：" + latest.getBody());
    }

    @Test
    @DisplayName("重复点开同一条：read_at 保持第一次的时间，不刷新")
    void markReadKeepsFirstReadTime() {
        long planId = generatePlan(TEST_USER);
        planService.confirm(planId, TEST_USER);

        List<NotificationVO> before = list(TEST_USER, false).getRecords();
        assertEquals(1, before.size());
        assertTrue(!before.get(0).isRead());

        long id = Long.parseLong(before.get(0).getNotificationId());
        assertEquals(0, notificationService.markRead(TEST_USER, id));

        LocalDateTime first = readAt(id);
        assertNotNull(first);
        notificationService.markRead(TEST_USER, id);
        assertEquals(first, readAt(id), "第二次点开不该覆盖第一次的时间");
    }

    @Test
    @DisplayName("别人的通知标不了已读（403），未读数也只算自己的")
    void otherUserCannotMarkMine() {
        long mine = generatePlan(TEST_USER);
        planService.confirm(mine, TEST_USER);
        long other = generatePlan(OTHER_USER);
        planService.confirm(other, OTHER_USER);

        long myNotification = Long.parseLong(list(TEST_USER, false).getRecords().get(0).getNotificationId());

        // 用别人的身份去标：接口层拿的是自己的 userId，这里直接调服务层模拟越权
        BizException e = assertThrows(BizException.class,
                () -> notificationService.markRead(OTHER_USER, myNotification));
        // AC-12：越权读写通知 100% 返回 403，不能静默当成功
        assertEquals(ErrorCode.FORBIDDEN, e.getErrorCode());
        assertNull(readAt(myNotification), "不是自己的通知不能改");
        assertEquals(1, notificationService.unreadCount(TEST_USER));

        // 「全部已读」也只清自己的
        notificationService.markAllRead(OTHER_USER);
        assertEquals(1, notificationService.unreadCount(TEST_USER));
        assertEquals(0, notificationService.unreadCount(OTHER_USER));
    }

    @Test
    @DisplayName("通知不存在：明确报 30111，而不是悄悄成功")
    void missingNotificationRejected() {
        BizException e = assertThrows(BizException.class,
                () -> notificationService.markRead(TEST_USER, 1L));
        assertEquals(ErrorCode.NOTIFICATION_NOT_FOUND, e.getErrorCode());
    }

    @Test
    @DisplayName("列表：unreadOnly 只筛未读，但 unreadCount 始终是全部未读")
    void listFiltersButCountDoesNot() {
        long planId = generatePlan(TEST_USER);
        planService.confirm(planId, TEST_USER);
        planService.start(planId, TEST_USER, null);

        long first = Long.parseLong(list(TEST_USER, false).getRecords().get(0).getNotificationId());
        notificationService.markRead(TEST_USER, first);

        NotificationPageVO all = list(TEST_USER, false);
        assertEquals(2, all.getTotal());
        assertEquals(1, all.getUnreadCount());

        NotificationPageVO unread = list(TEST_USER, true);
        assertEquals(1, unread.getTotal(), "只看未读时列表变短");
        assertEquals(1, unread.getUnreadCount(), "红点数字不受筛选影响");
    }

    // ---------------- helpers ----------------

    private NotificationPageVO list(long userId, boolean unreadOnly) {
        return notificationService.list(userId, 1, 20, unreadOnly);
    }

    private List<String> types(long userId) {
        return jdbc.queryForList("select type from t_notification where user_id = ? "
                + "and deleted = 0 order by create_time, id", String.class, userId);
    }

    private int count(long userId) {
        Integer value = jdbc.queryForObject(
                "select count(*) from t_notification where user_id = ? and deleted = 0", Integer.class, userId);
        return value == null ? 0 : value;
    }

    /** 直接读库，绕过实体（实体上的 readAt 是对象状态，不是库里的值） */
    private LocalDateTime readAt(long notificationId) {
        return jdbc.queryForObject("select read_at from t_notification where id = ?",
                LocalDateTime.class, notificationId);
    }

    private long generatePlan(long userId) {
        JsonNode draft = PlanMutants.validDraft();
        Map<String, Object> payload = Map.of("code", 0, "data", Map.of(
                "draft", Map.of("plan", draft),
                "done", Map.of("provider", "mock", "model", "mock-model")));
        when(aiServiceClient.generatePlan(anyList(), any()))
                .thenReturn((JsonNode) MAPPER.valueToTree(payload));
        return planService.generate(userId, messages()).path("planId").asLong();
    }

    private long startedPlan() {
        long planId = generatePlan(TEST_USER);
        planService.confirm(planId, TEST_USER);
        planService.start(planId, TEST_USER, null);
        return planId;
    }

    private void stubAdjust(String summary, String opsJson) {
        ObjectNode data = MAPPER.createObjectNode();
        data.put("summary", summary);
        try {
            data.set("ops", MAPPER.readTree(opsJson));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
        when(aiServiceClient.adjustPlan(any(), any(), any(), any())).thenReturn(data);
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

    private void cleanUp() {
        jdbc.update("delete from t_notification where user_id in (?, ?)", TEST_USER, OTHER_USER);
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
