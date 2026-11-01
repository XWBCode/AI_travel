package com.aitravel.common.api;

/**
 * 当前请求的 traceId 持有者（ThreadLocal）。
 * 由 TraceIdFilter 写入与清理，Result 构造时读取。
 */
public final class TraceIdHolder {

    private static final ThreadLocal<String> HOLDER = new ThreadLocal<>();

    private TraceIdHolder() {
    }

    public static void set(String traceId) {
        HOLDER.set(traceId);
    }

    public static String get() {
        return HOLDER.get();
    }

    public static void clear() {
        HOLDER.remove();
    }
}
