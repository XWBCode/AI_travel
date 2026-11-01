package com.aitravel.common.api;

import com.fasterxml.jackson.annotation.JsonInclude;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

/**
 * 统一响应体。
 * <p>
 * 规范：所有 Controller 一律返回 Result，HTTP 状态码只表达传输层语义，
 * 业务错误通过 code 表达，避免前端到处判断 response.ok。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
@JsonInclude(JsonInclude.Include.ALWAYS)
public class Result<T> {

    /** 业务码，0 表示成功 */
    private int code;
    private String message;
    private T data;
    /** 请求追踪 ID，便于排查问题时关联日志 */
    private String traceId;

    public static <T> Result<T> ok(T data) {
        return new Result<>(ErrorCode.OK.getCode(), "ok", data, TraceIdHolder.get());
    }

    public static <T> Result<T> ok() {
        return ok(null);
    }

    public static <T> Result<T> fail(ErrorCode code) {
        return new Result<>(code.getCode(), code.getMessage(), null, TraceIdHolder.get());
    }

    public static <T> Result<T> fail(ErrorCode code, String message) {
        return new Result<>(code.getCode(), message, null, TraceIdHolder.get());
    }

    public static <T> Result<T> fail(int code, String message) {
        return new Result<>(code, message, null, TraceIdHolder.get());
    }
}
