package com.aitravel.modules.notification.dto;

import com.aitravel.modules.notification.entity.Notification;
import lombok.Data;

import java.time.LocalDateTime;

/**
 * 通知的下发形态。
 *
 * <p>{@code read} 与 {@code readAt} 都给：前者让前端少写一个判空，后者备着将来做
 * 「多久才看到」的统计。两个字段同源，不会打架。
 */
@Data
public class NotificationVO {

    /** 雪花 ID 一律字符串下发，避免 JS 丢精度 */
    private String notificationId;

    private String planId;

    /** 见 Notification 上的常量 */
    private String type;

    private String title;

    private String body;

    /** 关联对象 ID（例如调整建议 ID），可空 */
    private String refId;

    private boolean read;

    private LocalDateTime readAt;

    private LocalDateTime createTime;

    public static NotificationVO of(Notification n) {
        NotificationVO vo = new NotificationVO();
        vo.setNotificationId(n.getId() == null ? null : String.valueOf(n.getId()));
        vo.setPlanId(n.getPlanId() == null ? null : String.valueOf(n.getPlanId()));
        vo.setType(n.getType());
        vo.setTitle(n.getTitle());
        vo.setBody(n.getBody());
        vo.setRefId(n.getRefId() == null ? null : String.valueOf(n.getRefId()));
        vo.setRead(n.getReadAt() != null);
        vo.setReadAt(n.getReadAt());
        vo.setCreateTime(n.getCreateTime());
        return vo;
    }
}
