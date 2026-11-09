package com.aitravel.modules.chat.service;

import com.fasterxml.jackson.databind.JsonNode;
import org.junit.jupiter.api.Test;

import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

/**
 * SSE 解析契约测试。
 * <p>
 * 固定住一条曾经踩过的坑：事件名在 {@code event:} 行，不在 data 体里。
 * 早期实现只读 data.type，导致 delta/done/error 全部被丢弃（详见 docs/plan 第 3 节）。
 */
class AiServiceClientSseTest {

    private static InputStream sse(String text) {
        return new ByteArrayInputStream(text.getBytes(StandardCharsets.UTF_8));
    }

    @Test
    void should_dispatch_by_event_line() {
        String raw = """
                event: delta
                data: {"content":"你"}

                event: delta
                data: {"content":"好"}

                event: done
                data: {"model":"mock-model","latencyMs":12}

                """;

        List<String> deltas = new ArrayList<>();
        AtomicReference<String> doneModel = new AtomicReference<>();
        AtomicReference<String> error = new AtomicReference<>();

        AiServiceClient.parseSse(sse(raw), deltas::add,
                node -> doneModel.set(node.path("model").asText("")), error::set);

        assertEquals(List.of("你", "好"), deltas);
        assertEquals("mock-model", doneModel.get());
        assertNull(error.get());
    }

    @Test
    void should_keep_duplicated_deltas() {
        // 重复内容不能被去重：中文里连续两片一样的字很常见
        String raw = """
                event: delta
                data: {"content":"的"}

                event: delta
                data: {"content":"的"}

                """;

        List<String> deltas = new ArrayList<>();
        AiServiceClient.parseSse(sse(raw), deltas::add, node -> {
        }, msg -> {
        });

        assertEquals(List.of("的", "的"), deltas);
    }

    @Test
    void should_still_accept_legacy_type_field() {
        // 兼容早期约定：事件名写在 data.type 里
        String raw = """
                data: {"type":"delta","content":"旧协议"}

                """;

        List<String> deltas = new ArrayList<>();
        AiServiceClient.parseSse(sse(raw), deltas::add, node -> {
        }, msg -> {
        });

        assertEquals(List.of("旧协议"), deltas);
    }

    @Test
    void should_report_error_event() {
        String raw = """
                event: error
                data: {"code":50201,"message":"模型超时"}

                """;

        AtomicReference<String> error = new AtomicReference<>();
        AiServiceClient.parseSse(sse(raw), content -> fail(), node -> fail(), error::set);

        assertEquals("模型超时", error.get());
    }

    @Test
    void should_ignore_comment_heartbeat_and_stop_on_done_marker() {
        String raw = """
                : keep-alive

                event: delta
                data: {"content":"a"}

                data: [DONE]

                event: delta
                data: {"content":"不该被读到"}

                """;

        List<String> deltas = new ArrayList<>();
        AiServiceClient.parseSse(sse(raw), deltas::add, node -> {
        }, msg -> {
        });

        assertEquals(List.of("a"), deltas);
    }

    @Test
    void abort_from_callback_should_stop_without_error() {
        String raw = """
                event: delta
                data: {"content":"a"}

                event: delta
                data: {"content":"b"}

                """;

        List<String> deltas = new ArrayList<>();
        AtomicReference<String> error = new AtomicReference<>();

        // 客户端断开时，调用方在回调里抛中止异常，解析器应安静退出，不当成错误
        assertDoesNotThrow(() ->
                AiServiceClient.parseSse(sse(raw), content -> {
                    deltas.add(content);
                    throw new AiServiceClient.StreamAbortedException("客户端已断开");
                }, node -> {
                }, error::set));

        assertEquals(List.of("a"), deltas);
        assertNull(error.get());
    }

    private static void fail() {
        throw new AssertionError("不应触发该回调");
    }
}
