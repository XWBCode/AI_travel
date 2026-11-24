package com.aitravel.modules.chat.service;

import com.aitravel.common.config.AiServiceProperties;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;

/**
 * TD-02：发往 AI 服务的请求必须带内部令牌。
 * <p>
 * 为什么用真 HTTP（JDK HttpServer）而不是 mock 掉 {@code HttpClient}：
 * mock 只能证明"我调用了自己写的方法"，证明不了"网线上真的有这个头"。
 * 这个头一旦没发出去，Python 侧会对生产环境直接 403——是一个只在运行态才暴露的故障。
 * <p>
 * 同时反向钉一条：<b>没配密钥时不能发空头</b>。发空串会让 Python 的
 * "未配密钥"分支和"配了但令牌不对"分支混淆，两种情况的排障动作完全不同。
 */
class AiServiceInternalTokenHeaderTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final String SECRET = "td02-header-test-secret";
    private static final String TOKEN_HEADER = "X-Internal-Token";

    private HttpServer server;
    private final AtomicReference<Headers> captured = new AtomicReference<>();

    @BeforeEach
    void startServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        // 用 /v1 前缀一次覆盖所有端点：测的就是"每条路径都带上了头"
        server.createContext("/v1", exchange -> {
            captured.set(exchange.getRequestHeaders());
            String path = exchange.getRequestURI().getPath();
            String body = path.endsWith("/weather")
                    ? "{\"ok\":true,\"source\":\"TEST\",\"days\":[]}"
                    : "{\"code\":0,\"message\":\"ok\",\"data\":{\"summary\":\"s\",\"ops\":[]}}";
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
    }

    @AfterEach
    void stopServer() {
        server.stop(0);
    }

    private AiServiceClient client() {
        return client(SECRET);
    }

    private AiServiceClient client(String secret) {
        AiServiceProperties props = new AiServiceProperties();
        props.setBaseUrl("http://127.0.0.1:" + server.getAddress().getPort());
        props.setInternalSecret(secret);
        return new AiServiceClient(props);
    }

    private static JsonNode planView() throws Exception {
        return MAPPER.readTree("{\"title\":\"t\",\"days\":3,\"items\":[]}");
    }

    @Test
    @DisplayName("adjust 请求带 X-Internal-Token")
    void adjustCarriesToken() throws Exception {
        JsonNode result = client().adjustPlan(planView(), "下午不去了", "t-1", 42L);
        assertNotNull(result, "服务端返回了合法 JSON，不应为 null");
        assertEquals(SECRET, captured.get().getFirst(TOKEN_HEADER));
    }

    @Test
    @DisplayName("weather 请求带 X-Internal-Token")
    void weatherCarriesToken() {
        client().fetchWeather("成都", "2026-11-25", 1, "t-2");
        assertEquals(SECRET, captured.get().getFirst(TOKEN_HEADER));
    }

    @Test
    @DisplayName("intent 请求带 X-Internal-Token")
    void intentCarriesToken() {
        client().analyzeIntent(List.of(Map.of("role", "user", "content", "我想去成都")), "t-3");
        assertEquals(SECRET, captured.get().getFirst(TOKEN_HEADER));
    }

    @Test
    @DisplayName("没配密钥时不发令牌头（而不是发空串）")
    void noSecretSendsNoHeader() throws Exception {
        client("").adjustPlan(planView(), "下午不去了", "t-4", 42L);
        assertNull(captured.get().getFirst(TOKEN_HEADER),
                "没配密钥时应完全不发这个头，让服务端走来源判定分支");
    }

    @Test
    @DisplayName("非 ASCII 密钥不发非法头，也不让请求崩掉")
    void nonAsciiSecretDoesNotBreakTheCall() throws Exception {
        // 这个坑是实测撞出来的：中文密钥直接放进 header 会抛
        // IllegalArgumentException: invalid header value，整条链路断掉。
        // 身份签名那边没事（HMAC 输出 hex），所以同一密钥可以继续签名，只是不能当令牌。
        JsonNode result = client("test-secret-请勿用于生产")
                .adjustPlan(planView(), "下午不去了", "t-5", 42L);
        assertNotNull(result, "非 ASCII 密钥不能让请求失败");
        assertNull(captured.get().getFirst(TOKEN_HEADER), "非 ASCII 不得放进 HTTP 头");
    }
}
