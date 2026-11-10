package com.aitravel.modules.plan.validation;

/**
 * 一条校验违规记录。
 *
 * @param code  规则编号：{@code SCHEMA_VIOLATION} 或 {@code S-1}~{@code S-6}
 * @param path  出错位置（JSON Pointer 风格，如 {@code /days/2/items/0/duration/value}）
 * @param detail 人类可读说明，直接透给用户/日志
 */
public record PlanViolation(String code, String path, String detail) {

    @Override
    public String toString() {
        return code + " " + (path == null || path.isEmpty() ? "(root)" : path) + " : " + detail;
    }
}
