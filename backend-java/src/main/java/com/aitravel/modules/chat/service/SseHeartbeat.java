package com.aitravel.modules.chat.service;

import com.aitravel.common.config.AiServiceProperties;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

/**
 * SSE 心跳：在计划生成期间每 15s 发一条注释行 {@code : ping}，防止反代把「静默」当成空闲连接掐掉。
 * <p>
 * <b>为什么需要</b>（R7）：一次真实生成要 90–170s，期间服务端可能一个字节都不产出。
 * 直连 8080 没人管；一旦前面挂 Nginx，默认 {@code proxy_read_timeout 60s} 就会在第 60 秒
 * 回一个 504 并断开——用户看到「一直转圈然后失败」，服务端却已经把计划存进库了。
 * 这和 BUG-012（连接上限短于 AI 超时）是同一类假失败，只是发生在更外面一层。
 * <p>
 * <b>为什么是注释行而不是事件</b>：{@code : ping} 属于 SSE 规范的注释，浏览器原生 EventSource
 * 与我们的前端解析器（只认 {@code event:}/{@code data:}，没有 data 就丢弃）都不会把它当消息，
 * 所以不改变任何业务语义，也不需要前端配合。它也不会产生模型调用。
 * <p>
 * <b>并发安全</b>：心跳在调度线程里 send，业务在流式线程里 send。Spring 的
 * {@code ResponseBodyEmitter#send} 内部对同一实例串行化，因此两者不会写坏响应体；
 * 顺序上心跳只是插在业务事件之间，不参与业务协议。
 * <p>
 * <b>失败处理</b>：send 抛异常基本只有一种原因——客户端已断开。此时立即取消该 emitter 的任务，
 * 既能停止无意义的写入，也能避免「连接没了还在发」把线程池拖住。
 */
@Slf4j
@Component
public class SseHeartbeat {

    private final boolean enabled;
    private final long intervalMs;
    private final ScheduledExecutorService scheduler;
    private final Map<SseEmitter, ScheduledFuture<?>> tasks = new ConcurrentHashMap<>();

    public SseHeartbeat(AiServiceProperties properties) {
        this.enabled = properties.isSseHeartbeatEnabled();
        this.intervalMs = properties.getSseHeartbeatIntervalMs();
        // 单线程足够：一次 ping 只是往 socket 写几个字节，串行反而不会和业务线程抢锁
        this.scheduler = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "sse-heartbeat");
            t.setDaemon(true);
            return t;
        });
        if (!enabled) {
            log.warn("SSE 心跳已关闭（app.ai.sse-heartbeat-enabled=false）。"
                    + "仅在 R7 对照组中使用；生产环境关闭会让反代在 60s 空闲处掐断长连接");
        }
    }

    /** 开流后立刻注册：第一个 ping 在一整个间隔之后发出，避免和 meta 事件挤在一起 */
    public void start(SseEmitter emitter) {
        if (!enabled || emitter == null) {
            return;
        }
        ScheduledFuture<?> future = scheduler.scheduleAtFixedRate(
                () -> ping(emitter), intervalMs, intervalMs, TimeUnit.MILLISECONDS);
        ScheduledFuture<?> previous = tasks.put(emitter, future);
        if (previous != null) {
            previous.cancel(false);
        }
    }

    /** 成功、失败、超时、取消都要调用，否则任务会一直跑（emitter 已被回收，等于泄漏一个定时任务） */
    public void stop(SseEmitter emitter) {
        if (emitter == null) {
            return;
        }
        ScheduledFuture<?> future = tasks.remove(emitter);
        if (future != null) {
            future.cancel(false);
        }
    }

    /** 当前正在维持的连接数，仅用于测试与排查 */
    public int activeCount() {
        return tasks.size();
    }

    private void ping(SseEmitter emitter) {
        try {
            emitter.send(SseEmitter.event().comment("ping"));
        } catch (Exception e) {
            // 客户端断开、或 emitter 已经 complete：停掉自己，不往业务里抛
            stop(emitter);
            log.debug("SSE 心跳停止（连接已结束）：{}", e.getMessage());
        }
    }
}
