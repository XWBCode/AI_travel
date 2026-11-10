package com.aitravel.common.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.AllArgsConstructor;
import lombok.Data;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.http.converter.json.Jackson2ObjectMapperBuilder;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 长整型 ID 的 JSON 序列化回归。
 * <p>
 * 雪花 ID 是 19 位整数（约 2.1e18），超出 JS 的 Number.MAX_SAFE_INTEGER
 * (9007199254740991)。若按数字下发，浏览器 JSON.parse 会把它四舍五入成
 * 2119988130910269400，再回传就被后端判为「会话不存在」，用户侧表现为
 * 一个空 body 的 500。这个用例锁住「包装 Long 一律输出字符串」这条约定。
 */
class LongAsStringTest {

    @Data
    @AllArgsConstructor
    static class Payload {
        /** 主键：包装类型 Long，必须序列化为字符串 */
        private Long id;
        /** 耗时：基本类型 long，保持数字，避免前端做算术时还要 parse */
        private long latencyMs;
    }

    private final ObjectMapper mapper = buildMapper();

    private static ObjectMapper buildMapper() {
        Jackson2ObjectMapperBuilder builder = new Jackson2ObjectMapperBuilder();
        new JacksonConfig().longAsStringCustomizer().customize(builder);
        return builder.build();
    }

    @Test
    @DisplayName("19 位雪花 ID 序列化为字符串，JS 端可无损往返")
    void snowflakeIdSerializedAsString() throws Exception {
        long id = 2119988130910269442L;
        String json = mapper.writeValueAsString(new Payload(id, 1248L));

        assertTrue(json.contains("\"id\":\"" + id + "\""), "Long 字段应带引号输出，实际: " + json);
        // 带引号后前端拿到的是字符串，再回传时不会丢精度
        assertEquals(String.valueOf(id), mapper.readTree(json).get("id").asText());
    }

    @Test
    @DisplayName("基本类型 long 仍是数字，不受影响")
    void primitiveLongStaysNumber() throws Exception {
        String json = mapper.writeValueAsString(new Payload(1L, 1248L));
        assertTrue(json.contains("\"latencyMs\":1248"), "long 字段应保持数字，实际: " + json);
    }
}
