package com.aitravel.common.config;

import com.fasterxml.jackson.databind.ser.std.ToStringSerializer;
import org.springframework.boot.autoconfigure.jackson.Jackson2ObjectMapperBuilderCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * JSON 序列化约定。
 * <p>
 * 主键使用雪花算法（19 位，约 2.1e18），远超 JS 的 {@code Number.MAX_SAFE_INTEGER}
 * （9007199254740991）。如果按数字下发，浏览器 {@code JSON.parse} 会直接四舍五入：
 * <pre>
 * 2119988130910269442 -> 2119988130910269400
 * </pre>
 * 前端再把这个值传回来就是「会话不存在」「计划不存在」这类看起来莫名其妙的错误。
 * <p>
 * 因此所有包装类型 {@link Long} 一律序列化为字符串。基本类型 {@code long}
 * （例如耗时 latencyMs）保持数字，避免把非 ID 的数值也变成字符串。
 *
 * @see <a href="https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Number/MAX_SAFE_INTEGER">MAX_SAFE_INTEGER</a>
 */
@Configuration
public class JacksonConfig {

    @Bean
    public Jackson2ObjectMapperBuilderCustomizer longAsStringCustomizer() {
        return builder -> builder.serializerByType(Long.class, ToStringSerializer.instance);
    }
}
