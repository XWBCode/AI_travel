package com.aitravel.modules.notification.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

/**
 * 通知列表的一页。<b>没复用 {@code PageResult}，只因为多带了一个 {@code unreadCount}。</b>
 *
 * <p>红点和列表是同一个界面上同时要的东西。拆成「列表接口 + 未读数接口」两个请求，
 * 就会出现「红点说 3 条未读，点开列表里只有 2 条」——两个请求之间刚好又写完了一条。
 * 一次查询带出来，两端永远一致。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class NotificationPageVO {

    private List<NotificationVO> records;

    private long total;

    private long page;

    private long size;

    /**
     * 未读总数，与 unreadOnly 筛选无关：红点显示的永远是全部未读。
     * 用 {@code int} 而非 {@code Long}——后者会被全局规则序列化成字符串。
     */
    private int unreadCount;
}
