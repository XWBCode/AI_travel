package com.aitravel.modules.plan.dto;

/**
 * PATCH 语义下的「字段是否出现」。
 * <p>
 * 部分更新必须区分三种情况：<b>不传</b>（不改）、<b>传 null</b>（清空）、<b>传值</b>（改成新值）。
 * Jackson 把前两者都反序列化成 {@code null}，用普通 DTO 字段无法区分——
 * 于是「把出发地清空」会被误解成「不改出发地」，用户点了保存却什么都没发生。
 * 因此这里用 {@code present} 显式承载「这个 key 出现在请求体里了吗」。
 *
 * @param present 请求体中是否出现该字段
 * @param value   字段值，{@code present=true} 时允许为 null（表示清空）
 */
public record Patch<T>(boolean present, T value) {

    public static <T> Patch<T> absent() {
        return new Patch<>(false, null);
    }

    public static <T> Patch<T> of(T value) {
        return new Patch<>(true, value);
    }

    /** 出现了且值非 null */
    public boolean hasValue() {
        return present && value != null;
    }

    /** 出现了且显式要求清空 */
    public boolean isExplicitNull() {
        return present && value == null;
    }
}
