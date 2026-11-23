package com.aitravel.modules.notification.controller;

import com.aitravel.common.api.Result;
import com.aitravel.common.web.CurrentUser;
import com.aitravel.modules.notification.dto.NotificationPageVO;
import com.aitravel.modules.notification.service.NotificationService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 站内通知（R-30）。
 *
 * <p>三个接口，各有各的用途，故意不合并：
 * <ul>
 *   <li>{@code GET /api/notifications} —— 列表，顺带带出未读数（红点和列表必须同源）；</li>
 *   <li>{@code POST /{id}/read} —— 点开某一条；</li>
 *   <li>{@code POST /read-all} —— 「全部已读」。</li>
 * </ul>
 * 「全部已读」单独成接口而不是「传空 ids 向量」，是因为后者在参数缺失时会静默变成
 * 「把所有通知标成已读」——一个拼错的请求就能清空红点，代价太大。
 */
@RestController
@RequestMapping("/api/notifications")
@RequiredArgsConstructor
public class NotificationController {

    private final NotificationService notificationService;

    @GetMapping
    public Result<NotificationPageVO> list(@RequestParam(defaultValue = "1") long page,
                                           @RequestParam(defaultValue = "20") long size,
                                           @RequestParam(defaultValue = "false") boolean unreadOnly) {
        return Result.ok(notificationService.list(CurrentUser.requireUserId(), page, size, unreadOnly));
    }

    /**
     * @return 标记后的未读数，前端直接拿去更新红点，不必再查一次。
     *         <b>必须是 {@code Integer} 而不是 {@code Long}</b>：全局序列化把 Long 写成字符串，
     *         前端拿到 "1" 之后 `=== 0` 判断就全错了。
     */
    @PostMapping("/{id}/read")
    public Result<Integer> markRead(@PathVariable Long id) {
        return Result.ok(notificationService.markRead(CurrentUser.requireUserId(), id));
    }

    @PostMapping("/read-all")
    public Result<Integer> markAllRead() {
        Long userId = CurrentUser.requireUserId();
        notificationService.markAllRead(userId);
        return Result.ok(notificationService.unreadCount(userId));
    }
}
