package com.aitravel.modules.notification.entity;

import com.aitravel.common.mybatis.BaseEntity;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalDateTime;

/**
 * 站内通知（R-30 / V7）。
 *
 * <p>它只影响展示，不参与任何状态判断：删掉通知不会让计划变得不可操作。
 * 把「用户看没看过某条消息」混进业务表，迟早会写出「因为通知没读所以改不了计划」这种东西。
 *
 * <p>收不收一条通知，判据只有一条：<b>这件事有没有让计划本身发生变化</b>。
 * 所以状态迁移和「确认调整」会进，勾选行程项、生成建议不会——理由写在 V7 迁移脚本头部。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("t_notification")
public class Notification extends BaseEntity {

    public static final String PLAN_CONFIRMED = "PLAN_CONFIRMED";
    public static final String PLAN_STARTED = "PLAN_STARTED";
    public static final String PLAN_COMPLETED = "PLAN_COMPLETED";
    public static final String PLAN_CANCELLED = "PLAN_CANCELLED";
    public static final String ADJUSTMENT_CONFIRMED = "ADJUSTMENT_CONFIRMED";

    private Long userId;

    private Long planId;

    /** 见上面的常量 */
    private String type;

    private String title;

    private String body;

    /** 关联对象 ID，例如调整建议 ID；用于点击后跳转 */
    private Long refId;

    /** 已读时间；null 表示未读。用时间戳而不是布尔，将来统计「多久看到」不用加列 */
    private LocalDateTime readAt;
}
