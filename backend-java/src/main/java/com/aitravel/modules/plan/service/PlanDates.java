package com.aitravel.modules.plan.service;

import java.time.LocalDate;
import java.time.ZoneId;

/**
 * 计划里「今天」的唯一来源。
 *
 * <p>为什么不是随手一个 {@code LocalDate.now()}：服务端时区决定了「今天」是哪一天，
 * 而用户可能已经在目的地（东京比东八区早一小时）。同一份计划在 23:30 打开，
 * 浏览器认为今天是 12 号、服务器认为还是 11 号，页面就会显示昨天的行程——
 * 这种 bug 只在深夜复现，排查成本极高。
 *
 * <p>约定（05 §5.7）：<b>前端把本地日期作为 {@code date} 参数传上来</b>，
 * 服务端只在参数缺失时兜底。兜底用东八区而不是 {@code ZoneId.systemDefault()}：
 * 部署机器时区是不可控的（容器里常常是 UTC），跟着机器走等于让「今天」变成部署细节。
 */
public final class PlanDates {

    /** 服务端兜底时区。要改就改这里，不要在调用点各写一个 now() */
    public static final ZoneId ZONE = ZoneId.of("Asia/Shanghai");

    private PlanDates() {
    }

    public static LocalDate today() {
        return LocalDate.now(ZONE);
    }
}
