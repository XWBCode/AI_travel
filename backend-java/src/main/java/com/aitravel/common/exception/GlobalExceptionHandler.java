package com.aitravel.common.exception;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.api.Result;
import jakarta.servlet.http.HttpServletRequest;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.NoHandlerFoundException;

import java.util.stream.Collectors;

/**
 * 全局异常处理。
 * <p>
 * 原则：异常绝不吞掉后返回成功（项目总纲 9.3）。所有异常必须落日志并返回明确错误码。
 * <p>
 * 另外所有错误响应都显式指定 {@code application/json}：
 * SSE 接口的请求头只有 {@code Accept: text/event-stream}，若交给内容协商去挑
 * 转换器，会因为没有能写 text/event-stream 的转换器而抛 406，最终被 Tomcat 兜成
 * 一个「500 + 空 body」。前端于是只看到「请求失败(500)」，真正的原因（例如
 * 「会话不存在」「登录凭证无效」）被彻底吞掉。
 */
@Slf4j
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(BizException.class)
    public ResponseEntity<Result<Void>> handleBiz(BizException e, HttpServletRequest req) {
        log.warn("业务异常 uri={} code={} msg={}", req.getRequestURI(), e.getErrorCode().getCode(), e.getMessage());
        int status = e.getHttpStatus() <= 0 ? 200 : e.getHttpStatus();
        return json(status, Result.fail(e.getErrorCode(), e.getMessage()));
    }

    /**
     * 参数校验失败必须是 4xx，不能回 200。
     * <p>
     * 原因：/api/chat/stream 走 SSE，前端只要拿到 2xx 就当流开始解析。
     * 若这里回 200 + JSON，前端解析不出任何事件，最后留下一个空气泡，
     * 用户既看不到回复也看不到错误。
     */
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<Result<Void>> handleValidation(MethodArgumentNotValidException e) {
        String detail = e.getBindingResult().getFieldErrors().stream()
                .map(FieldError::getDefaultMessage)
                .collect(Collectors.joining("; "));
        log.warn("参数校验失败: {}", detail);
        return json(HttpStatus.BAD_REQUEST.value(), Result.fail(ErrorCode.VALIDATION_FAILED, detail));
    }

    @ExceptionHandler(MissingServletRequestParameterException.class)
    public ResponseEntity<Result<Void>> handleMissingParam(MissingServletRequestParameterException e) {
        return json(HttpStatus.BAD_REQUEST.value(),
                Result.fail(ErrorCode.BAD_REQUEST, "缺少必填参数: " + e.getParameterName()));
    }

    /**
     * 请求体不是合法 JSON（含「声明了 application/json 但 body 为空」）。
     * <p>
     * 不接住会落到兜底的 Exception 分支变成 500，前端只会看到「系统内部错误」，
     * 而真实原因是它自己发了个空 body。
     */
    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<Result<Void>> handleUnreadableBody(HttpMessageNotReadableException e) {
        log.warn("请求体无法解析: {}", e.getMessage());
        return json(HttpStatus.BAD_REQUEST.value(),
                Result.fail(ErrorCode.BAD_REQUEST, "请求体不是合法的 JSON"));
    }

    @ExceptionHandler(NoHandlerFoundException.class)
    public ResponseEntity<Result<Void>> handleNoHandler(NoHandlerFoundException e) {
        return json(HttpStatus.NOT_FOUND.value(), Result.fail(ErrorCode.NOT_FOUND));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<Result<Void>> handleOther(Exception e, HttpServletRequest req) {
        log.error("未捕获异常 uri={}", req.getRequestURI(), e);
        return json(HttpStatus.INTERNAL_SERVER_ERROR.value(), Result.fail(ErrorCode.INTERNAL_ERROR));
    }

    /** 统一显式声明 application/json，绕开 Accept 内容协商（见类注释）。 */
    private ResponseEntity<Result<Void>> json(int status, Result<Void> body) {
        return ResponseEntity.status(status)
                .contentType(MediaType.APPLICATION_JSON)
                .body(body);
    }
}
