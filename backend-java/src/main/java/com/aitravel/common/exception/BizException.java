package com.aitravel.common.exception;

import com.aitravel.common.api.ErrorCode;
import lombok.Getter;

@Getter
public class BizException extends RuntimeException {

    private final ErrorCode errorCode;
    private final int httpStatus;

    public BizException(ErrorCode errorCode) {
        super(errorCode.getMessage());
        this.errorCode = errorCode;
        this.httpStatus = defaultStatus(errorCode);
    }

    public BizException(ErrorCode errorCode, String message) {
        super(message);
        this.errorCode = errorCode;
        this.httpStatus = defaultStatus(errorCode);
    }

    public static BizException of(ErrorCode errorCode) {
        return new BizException(errorCode);
    }

    /**
     * 传输层状态码也一并返回，前端拦截器可据此做统一跳转（例如 401 跳登录）。
     */
    private static int defaultStatus(ErrorCode errorCode) {
        return switch (errorCode) {
            case UNAUTHORIZED, TOKEN_INVALID -> 401;
            case FORBIDDEN -> 403;
            case NOT_FOUND, PLAN_NOT_FOUND, SESSION_NOT_FOUND -> 404;
            case RATE_LIMITED -> 429;
            case AI_SERVICE_UNAVAILABLE, EXTERNAL_API_ERROR -> 502;
            default -> 200;
        };
    }
}
