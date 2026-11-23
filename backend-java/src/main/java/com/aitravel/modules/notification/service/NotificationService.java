package com.aitravel.modules.notification.service;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.notification.dto.NotificationPageVO;
import com.aitravel.modules.notification.dto.NotificationVO;
import com.aitravel.modules.notification.entity.Notification;
import com.aitravel.modules.notification.mapper.NotificationMapper;
import com.aitravel.modules.plan.entity.TravelPlan;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;

/**
 * 站内通知（R-30 / V7）。
 *
 * <p><b>写入是「尽力而为」，读取才是契约。</b>业务代码在状态迁移的事务里顺手调
 * {@link #planEvent}，通知写失败只打日志、不抛出——一条通知没记上，不该让「确认计划」
 * 这种用户已经点下去的动作失败。注意这里<b>不加 {@code REQUIRES_NEW}</b>：通知跟着业务
 * 事务一起提交，计划回滚了通知也一起消失，否则会出现「收到『已确认』但计划还是草稿」。
 *
 * <p><b>文案集中在这里。</b>五个事件的中文说法只有这一处，想改口吻不必翻业务代码；
 * 测试也只需要对着这一个方法断言。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class NotificationService {

    /** 05 §5：列表 size ≤ 50，超出按 50 处理 */
    private static final long MAX_PAGE_SIZE = 50;

    private static final DateTimeFormatter MONTH_DAY = DateTimeFormatter.ofPattern("M 月 d 日");

    private final NotificationMapper notificationMapper;

    // ---------------- 写入 ----------------

    /**
     * 计划状态迁移的通知（确认 / 开始 / 完成 / 取消）。
     *
     * @param plan 迁移后的计划，用于取标题与日期
     */
    public void planEvent(TravelPlan plan, String type) {
        if (plan == null) {
            return;
        }
        String name = "《" + safeTitle(plan) + "》";
        String title;
        String body;
        switch (type) {
            case Notification.PLAN_CONFIRMED -> {
                title = "行程已确认";
                body = name + "确认好了，准备好就可以点「开始旅行」。";
            }
            case Notification.PLAN_STARTED -> {
                title = "旅行开始了";
                body = plan.getStartDate() == null
                        ? name + "已开始，打开「当日执行」看看今天怎么安排。"
                        : name + "已开始，第 1 天是 " + MONTH_DAY.format(plan.getStartDate()) + "。";
            }
            case Notification.PLAN_COMPLETED -> {
                title = "旅行已结束";
                body = name + "已标记完成，随时可以回来看看行程和花费。";
            }
            case Notification.PLAN_CANCELLED -> {
                title = "行程已取消";
                body = name + "已取消，历史版本都还在，需要时可以回滚。";
            }
            default -> {
                log.warn("未知的计划通知类型，已忽略 type={}", type);
                return;
            }
        }
        write(plan.getUserId(), plan.getId(), type, title, body, null);
    }

    /**
     * 确认调整建议的通知。
     *
     * <p>生成建议时<b>不</b>通知：那一刻计划没有任何变化，用户也正盯着确认弹窗。
     * 变的是「确认之后」——所以只有这一条。
     *
     * @param adjustmentId 建议 ID，前端据此跳到对应计划
     * @param version      调整后计划的版本号，写进正文便于对账
     */
    public void adjustmentConfirmed(TravelPlan plan, Long adjustmentId, String summary, int version) {
        if (plan == null) {
            return;
        }
        String name = "《" + safeTitle(plan) + "》";
        String text = summary == null || summary.isBlank() ? "行程已按你的要求调整。" : summary + "。";
        write(plan.getUserId(), plan.getId(), Notification.ADJUSTMENT_CONFIRMED,
                "行程已调整", name + text + "（第 " + version + " 版）", adjustmentId);
    }

    /**
     * 落库。所有异常就地吞掉——见类注释：通知失败不能反过来毁掉业务操作。
     */
    private void write(Long userId, Long planId, String type, String title, String body, Long refId) {
        if (userId == null) {
            return;
        }
        try {
            Notification n = new Notification();
            n.setUserId(userId);
            n.setPlanId(planId);
            n.setType(type);
            n.setTitle(truncate(title, 120));
            n.setBody(truncate(body, 300));
            n.setRefId(refId);
            n.setReadAt(null);
            notificationMapper.insert(n);
        } catch (RuntimeException e) {
            log.warn("写通知失败，已忽略 type={} planId={} err={}", type, planId, e.toString());
        }
    }

    // ---------------- 读取 ----------------

    /**
     * 通知列表 + 未读数。
     *
     * <p>两者放同一个响应里，是因为红点和列表是同一个界面上同时要的两样东西。
     * 拆成两个接口就会出现「红点说 3 条未读，点开列表只有 2 条」这种瞬时不一致——
     * 用户只会觉得这功能坏了。
     *
     * @param unreadOnly true 时只返回未读，用于「只看未读」筛选
     */
    @Transactional(readOnly = true)
    public NotificationPageVO list(Long userId, long page, long size, boolean unreadOnly) {
        long safePage = Math.max(page, 1);
        long safeSize = Math.min(Math.max(size, 1), MAX_PAGE_SIZE);

        var query = Wrappers.<Notification>lambdaQuery()
                .eq(Notification::getUserId, userId)
                .orderByDesc(Notification::getCreateTime)
                .orderByDesc(Notification::getId);
        if (unreadOnly) {
            query.isNull(Notification::getReadAt);
        }

        IPage<Notification> result = notificationMapper.selectPage(new Page<>(safePage, safeSize), query);
        List<NotificationVO> records = result.getRecords().stream().map(NotificationVO::of).toList();
        return new NotificationPageVO(records, result.getTotal(), safePage, safeSize, unreadCount(userId));
    }

    /**
     * 未读数。
     *
     * <p>返回 {@code int} 而不是 {@code Long}，不是随手写的：全局 Jackson 把
     * {@code Long} 一律序列化成字符串（为了雪花 ID 不丢精度），若这里用包装类型，
     * 前端收到的是 {@code "1"}，`unread === 0` 就永远为 false——按钮该禁用时不禁用，
     * 红点该消失时不消失。计数不是 ID，别享受 ID 的待遇。
     */
    @Transactional(readOnly = true)
    public int unreadCount(Long userId) {
        Long count = notificationMapper.selectCount(Wrappers.<Notification>lambdaQuery()
                .eq(Notification::getUserId, userId)
                .isNull(Notification::getReadAt));
        return count == null ? 0 : count.intValue();
    }

    // ---------------- 已读 ----------------

    /**
     * 标记一条已读。
     *
     * <p>已经读过的再点一次不报错、也不改 {@code read_at}——「第一次看到的时间」
     * 比「最后一次点它的时间」有用，覆盖掉就再也算不出「多久才看到」。
     *
     * <p><b>动别人的通知是 403，不是「静默无事发生」。</b>AC-12 要求越权读写通知 100% 返回 403；
     * 静默放过虽然不泄露数据，但会让「越权被拒绝」这件事在监控里彻底不可见。
     *
     * @return 操作后的未读数
     */
    @Transactional
    public int markRead(Long userId, Long notificationId) {
        Notification n = notificationMapper.selectById(notificationId);
        if (n == null) {
            throw new BizException(ErrorCode.NOTIFICATION_NOT_FOUND);
        }
        if (!n.getUserId().equals(userId)) {
            throw new BizException(ErrorCode.FORBIDDEN, "无权操作该通知");
        }
        if (n.getReadAt() == null) {
            n.setReadAt(LocalDateTime.now());
            notificationMapper.updateById(n);
        }
        return unreadCount(userId);
    }

    /**
     * 全部标记已读。
     *
     * @return 本次实际标记的条数（已是已读的不算）
     */
    @Transactional
    public int markAllRead(Long userId) {
        int rows = notificationMapper.update(null, Wrappers.<Notification>lambdaUpdate()
                .eq(Notification::getUserId, userId)
                .isNull(Notification::getReadAt)
                .set(Notification::getReadAt, LocalDateTime.now()));
        log.info("通知全部标记已读 userId={} rows={}", userId, rows);
        return rows;
    }

    private String safeTitle(TravelPlan plan) {
        return plan.getTitle() == null || plan.getTitle().isBlank() ? "未命名行程" : plan.getTitle();
    }

    private String truncate(String text, int max) {
        if (text == null) {
            return null;
        }
        return text.length() <= max ? text : text.substring(0, max - 1) + "…";
    }
}
