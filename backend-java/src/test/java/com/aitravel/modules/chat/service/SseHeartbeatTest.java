package com.aitravel.modules.chat.service;

import com.aitravel.common.config.AiServiceProperties;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.web.servlet.mvc.method.annotation.ResponseBodyEmitter;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * SSE 心跳（R7）。
 * <p>
 * 断言的都是「行为」而不是「实现」：间隔到了必须发、发不出去必须自己停、关掉必须一条都不发。
 * 时间用 50ms 级而不是 15s，避免用例跑成分钟级；被缩短的只是间隔，判断逻辑与线上同一份代码。
 */
class SseHeartbeatTest {

    /** 记录每次 send 的载荷；可切换成「客户端已断开」 */
    private static class RecordingEmitter extends SseEmitter {
        private final List<String> payloads = new CopyOnWriteArrayList<>();
        private volatile boolean broken = false;

        RecordingEmitter() {
            super(5_000L);
        }

        @Override
        public void send(SseEventBuilder builder) throws IOException {
            if (broken) {
                throw new IOException("客户端已断开");
            }
            for (ResponseBodyEmitter.DataWithMediaType item : builder.build()) {
                payloads.add(String.valueOf(item.getData()));
            }
        }
    }

    private static AiServiceProperties props(boolean enabled, long intervalMs) {
        AiServiceProperties p = new AiServiceProperties();
        p.setSseHeartbeatEnabled(enabled);
        p.setSseHeartbeatIntervalMs(intervalMs);
        return p;
    }

    @Test
    @DisplayName("间隔到了就发注释行，且载荷是 SSE 注释（不是业务事件）")
    void sendsCommentPingOnInterval() throws Exception {
        SseHeartbeat heartbeat = new SseHeartbeat(props(true, 50L));
        RecordingEmitter emitter = new RecordingEmitter();
        heartbeat.start(emitter);

        Thread.sleep(220L);

        heartbeat.stop(emitter);
        assertTrue(emitter.payloads.size() >= 2,
                () -> "220ms / 50ms 至少应发出 2 次心跳，实际：" + emitter.payloads);
        // 注释行必须以 ":" 开头：前端只认 event:/data:，注释不会变成一次事件
        assertTrue(emitter.payloads.stream().allMatch(p -> p.startsWith(":")),
                () -> "心跳必须是 SSE 注释行，实际：" + emitter.payloads);
        assertTrue(emitter.payloads.stream().anyMatch(p -> p.contains("ping")),
                () -> "心跳内容应可辨认（: ping），便于在 nginx / tcpdump 里区分，实际：" + emitter.payloads);
    }

    @Test
    @DisplayName("客户端断开后自动停发，不往业务里抛异常")
    void stopsWhenClientDisconnects() throws Exception {
        SseHeartbeat heartbeat = new SseHeartbeat(props(true, 40L));
        RecordingEmitter emitter = new RecordingEmitter();
        emitter.broken = true;
        heartbeat.start(emitter);

        Thread.sleep(150L);

        // 第一次 ping 失败就应该摘掉任务，之后不再重试
        assertEquals(0, heartbeat.activeCount(),
                "send 失败后必须把任务摘掉，否则连接已断还在发，等于拖着一个永不结束的定时任务");
        emitter.broken = false;
        emitter.payloads.clear();
        Thread.sleep(120L);
        assertTrue(emitter.payloads.isEmpty(), "已经停掉的心跳不应该继续发");
    }

    @Test
    @DisplayName("关掉开关后一条都不发（R7 对照组就靠这个）")
    void sendsNothingWhenDisabled() throws Exception {
        SseHeartbeat heartbeat = new SseHeartbeat(props(false, 20L));
        RecordingEmitter emitter = new RecordingEmitter();
        heartbeat.start(emitter);

        Thread.sleep(120L);

        assertTrue(emitter.payloads.isEmpty(), () -> "开关关闭时不得发送任何心跳：" + emitter.payloads);
        assertEquals(0, heartbeat.activeCount());
    }

    @Test
    @DisplayName("stop() 之后不再发，且可重复调用")
    void stopIsIdempotent() throws Exception {
        SseHeartbeat heartbeat = new SseHeartbeat(props(true, 30L));
        RecordingEmitter emitter = new RecordingEmitter();
        heartbeat.start(emitter);
        Thread.sleep(100L);

        heartbeat.stop(emitter);
        heartbeat.stop(emitter);
        // cancel(false) 不会打断「已经开始的那一次 send」，所以先给在途的那一次落地的时间，
        // 再取基准值。否则这条用例会因为一个毫秒级的竞态随机变红（第一版就踩了）。
        Thread.sleep(80L);
        int settled = emitter.payloads.size();

        Thread.sleep(150L);

        assertTrue(settled > 0, "停止前应已发过心跳");
        assertEquals(settled, emitter.payloads.size(), "stop() 之后不应再有新的心跳");
        assertEquals(0, heartbeat.activeCount());
    }

    @Test
    @DisplayName("默认配置：开启，15s 间隔")
    void defaultsAreSafe() {
        AiServiceProperties defaults = new AiServiceProperties();
        assertTrue(defaults.isSseHeartbeatEnabled(), "默认必须开启，否则线上挂反代就会在 60s 处被掐断");
        assertEquals(15_000L, defaults.getSseHeartbeatIntervalMs(),
                "15s 是 60s 默认 proxy_read_timeout 的 1/4，留了 3 次容错");
        assertFalse(defaults.getSseHeartbeatIntervalMs() >= 60_000L,
                "间隔不得大于反代默认空闲超时，否则心跳等于没用");
    }
}
