package com.aitravel.common.api;

import lombok.Getter;

/**
 * 业务错误码。
 * <p>
 * 约定：
 * - 0 成功
 * - 1xxxx 通用/参数
 * - 2xxxx 认证与权限
 * - 3xxxx 业务实体
 * - 5xxxx 系统与外部依赖
 */
@Getter
public enum ErrorCode {

    OK(0, "ok"),

    BAD_REQUEST(10001, "请求参数不合法"),
    VALIDATION_FAILED(10002, "参数校验失败"),
    NOT_FOUND(10004, "资源不存在"),
    METHOD_NOT_ALLOWED(10005, "请求方法不支持"),
    RATE_LIMITED(10429, "请求过于频繁，请稍后再试"),

    UNAUTHORIZED(20001, "未登录或登录已过期"),
    FORBIDDEN(20003, "无权访问该资源"),
    TOKEN_INVALID(20002, "登录凭证无效"),
    USERNAME_EXISTS(20101, "用户名已被占用"),
    BAD_CREDENTIALS(20102, "用户名或密码错误"),
    ACCOUNT_DISABLED(20103, "账号已被禁用"),

    PLAN_NOT_FOUND(30101, "旅行计划不存在"),
    /** 计划未通过契约/语义校验，一律不落库（AC-07） */
    PLAN_SCHEMA_VIOLATION(30102, "计划未通过校验"),
    /** 状态机非法迁移，例如未确认直接开始（AC-09） */
    PLAN_STATE_INVALID(30103, "当前计划状态不允许该操作"),
    /** 乐观锁冲突：version 已过期 */
    PLAN_VERSION_CONFLICT(30104, "计划已被修改，请刷新后重试"),
    /** 行程项不属于该计划（防止拿别人的 itemId 打勾） */
    PLAN_ITEM_NOT_FOUND(30105, "行程项不存在或不属于该计划"),
    /** 回滚目标版本不存在（只保留「内容变化」的版本，不是每个序号都有快照） */
    PLAN_VERSION_NOT_FOUND(30106, "该版本快照不存在"),
    /** 调整建议不存在或不属于该计划 */
    ADJUSTMENT_NOT_FOUND(30107, "调整建议不存在或已失效"),
    /**
     * 调整建议已过期：生成后计划又被改过（手改、回滚、另一次调整）。
     * 单独一个码而不是复用 30104，是因为前端要说的话不一样：
     * 这里要引导用户「重新生成建议」，而不是「刷新页面重试」。
     */
    ADJUSTMENT_STALE(30108, "计划已变更，该建议已失效，请重新生成"),
    /** 调整建议内容非法：ops 引用了不存在的行程项、字段不在白名单、应用后违反契约等 */
    ADJUSTMENT_INVALID(30109, "调整建议内容不合法"),
    /** 建议里没有任何实际变更（模型返回了空操作或等价改写），不占用一个版本 */
    ADJUSTMENT_NOOP(30110, "这条建议没有带来任何变化"),
    /** 通知不存在：正常前端只会标自己刚列出来的那几条，出现即说明请求是拼的或已过期 */
    NOTIFICATION_NOT_FOUND(30111, "通知不存在"),
    SESSION_NOT_FOUND(30201, "会话不存在"),

    AI_SERVICE_UNAVAILABLE(50201, "AI 服务暂时不可用"),
    EXTERNAL_API_ERROR(50202, "外部数据源调用失败"),
    INTERNAL_ERROR(50000, "系统内部错误");

    private final int code;
    private final String message;

    ErrorCode(int code, String message) {
        this.code = code;
        this.message = message;
    }
}
