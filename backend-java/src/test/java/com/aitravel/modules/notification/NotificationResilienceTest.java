package com.aitravel.modules.notification;

import com.aitravel.modules.notification.entity.Notification;
import com.aitravel.modules.notification.mapper.NotificationMapper;
import com.aitravel.modules.notification.service.NotificationService;
import com.aitravel.modules.plan.entity.TravelPlan;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 通知写不进去时，业务必须照常成功。
 *
 * <p>这是<b>故意</b>的设计，不是宽容：用户点了「确认计划」，计划已经落库了，
 * 此时因为一条附带的展示数据插不进去而把整个动作回滚，是本末倒置。
 * 反过来说，如果哪天有人把 {@code try/catch} 去掉，这里会红。
 *
 * <p>不开 Spring 上下文：要验的只有「异常没往上抛」这一件事，真库验证在
 * {@link NotificationTest} 里做。
 */
class NotificationResilienceTest {

    @Test
    @DisplayName("insert 抛异常：只有一条 warn 日志，调用方毫发无伤")
    void insertFailureIsSwallowed() {
        NotificationMapper mapper = mock(NotificationMapper.class);
        when(mapper.insert(any(Notification.class))).thenThrow(new RuntimeException("db down"));
        NotificationService service = new NotificationService(mapper);

        TravelPlan plan = new TravelPlan();
        plan.setId(1L);
        plan.setUserId(2L);
        plan.setTitle("成都三日");

        assertDoesNotThrow(() -> service.planEvent(plan, Notification.PLAN_CONFIRMED));
        assertDoesNotThrow(() -> service.adjustmentConfirmed(plan, 3L, "把下午那项删了", 5));
    }

    @Test
    @DisplayName("未知类型不落库：宁可少一条，也不要写进一个前端不认识的事件")
    void unknownTypeIgnored() {
        NotificationMapper mapper = mock(NotificationMapper.class);
        NotificationService service = new NotificationService(mapper);

        TravelPlan plan = new TravelPlan();
        plan.setId(1L);
        plan.setUserId(2L);
        plan.setTitle("成都三日");

        service.planEvent(plan, "SOMETHING_NEW");
        verify(mapper, never()).insert(any(Notification.class));
    }

    @Test
    @DisplayName("没有 userId 的计划不写：通知总要有个收件人")
    void nullUserSkipped() {
        NotificationMapper mapper = mock(NotificationMapper.class);
        NotificationService service = new NotificationService(mapper);

        service.planEvent(new TravelPlan(), Notification.PLAN_CONFIRMED);
        service.planEvent(null, Notification.PLAN_CONFIRMED);
        verify(mapper, never()).insert(any(Notification.class));
    }
}
