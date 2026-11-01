package com.aitravel.common.web;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import jakarta.servlet.http.HttpServletRequest;

/**
 * 当前登录用户上下文。由 AuthInterceptor 写入，Controller/Service 通过此工具读取。
 */
public final class CurrentUser {

    private static final ThreadLocal<Long> HOLDER = new ThreadLocal<>();
    private static final ThreadLocal<String> NAME_HOLDER = new ThreadLocal<>();
    private static final String ATTR_USER_ID = "CURRENT_USER_ID";
    private static final String ATTR_USERNAME = "CURRENT_USERNAME";

    private CurrentUser() {
    }

    public static void bind(HttpServletRequest request, Long userId, String username) {
        HOLDER.set(userId);
        NAME_HOLDER.set(username);
        request.setAttribute(ATTR_USER_ID, userId);
    }

    public static void unbind() {
        HOLDER.remove();
        NAME_HOLDER.remove();
    }

    /** 未登录时抛 401，业务代码无需判空 */
    public static Long requireUserId() {
        Long id = HOLDER.get();
        if (id == null) {
            throw new BizException(ErrorCode.UNAUTHORIZED);
        }
        return id;
    }

    public static Long userIdOrNull() {
        return HOLDER.get();
    }

    public static String usernameOrNull() {
        return NAME_HOLDER.get();
    }
}
