package com.aitravel.common.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.annotation.Configuration;

/**
 * app.ai.* 配置：Java 调用 Python AI 服务的参数。
 */
@Data
@Configuration
@ConfigurationProperties(prefix = "app.ai")
public class AiServiceProperties {

    private String baseUrl = "http://localhost:8000";

    private int connectTimeoutMs = 5000;

    /**
     * AI 流式响应可能较慢，读超时需要给足，但不能无限。
     * 实测：DeepSeek 直连 3 天计划约 14s；交融网关的 v4-pro / v4-flash 约 85–100s，
     * 且天上界随天数线性上升。180s 对 7 天以上的行程不够，默认放到 300s，可用环境变量覆盖。
     */
    private int readTimeoutMs = 300000;

    /**
     * 单次计划生成（非流式 /v1/plan/generate）的上限。
     * 与 readTimeoutMs 分开是因为它的历史语义不同：原来硬编码 180s，
     * 在交融网关上 7 天行程要 130–160s，长尾直接顶到 180s 被判超时——
     * 这类失败会被记成「AI 服务连接失败」，看起来像宕机，其实是等得不够。
     */
    private int planTimeoutMs = 300000;

    /** 调整建议（/v1/plan/adjust）上限：输出远小于整份计划，但仍给推理模型留余量 */
    private int adjustTimeoutMs = 180000;

    /**
     * 流式对话线程数。
     * <p>
     * 实测（2026-11-23，v4-pro）：8 并发全部成功，P50 132.7s，比串行的 127.4s 只多 4%；
     * 12 并发也全部成功，但最后 4 个最长等了 298.8s——**不是被拒绝，是排队**。
     * 也就是说这个池子不是「上限」，而是「并行度」，超出的请求在无界队列里静默等待。
     */
    private int streamPoolSize = 8;

    /**
     * 流式对话的排队上限。默认 0：**不排队，满了直接拒**。
     * <p>
     * 这个 0 是量出来的，不是拍的。20 并发实测（8 并行 + 8 排队）：
     * 15 成功、4 个 10429，还有 1 个在 **330s 的 SSE 连接上限**处断开——
     * 它排在第二批，等 150s 再生成 180s，正好把连接耗死。而计划此时可能已经落库，
     * 于是又回到 BUG-012 那种「用户看到失败、数据其实在」的假失败。
     * <p>
     * 结论：只要队列不为空，最坏耗时就是「排队 + 生成」，一定会吃掉 SSE 预算。
     * 因此把队列压到 0：并行度就是硬上限（8），第 9 个立刻收到 10429 并能马上重试。
     * **要提并发就提 {@code streamPoolSize}（线程），不要提这个值。**
     */
    private int streamQueueCapacity = 0;

    /**
     * SSE 心跳开关。默认开。
     * <p>
     * 存在的唯一理由是「对照组」：R7 要证明的是「心跳让长连接不被反代掐断」，
     * 那就必须有一个同样配置、只是关掉心跳的分支在 60s 处收到 504。
     * 生产环境没有关掉它的理由。
     */
    private boolean sseHeartbeatEnabled = true;

    /**
     * 心跳间隔。默认 15s。
     * <p>
     * 取 15s 的依据是反代的默认空闲超时：Nginx {@code proxy_read_timeout} 默认 60s，
     * 心跳必须显著小于它（15s 给了 3 次容错），否则一次 GC 停顿就足以把连接判死。
     * 心跳本身是 SSE 注释行 {@code : ping}，不产生前端事件，也不消耗模型额度。
     */
    private long sseHeartbeatIntervalMs = 15000L;
}
