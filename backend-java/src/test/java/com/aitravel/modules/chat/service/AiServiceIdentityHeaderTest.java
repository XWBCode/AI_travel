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

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * M3-3 · S5：{@code /plan/adjust} 的灰度身份头。
 * <p>
 * 这是 Java 与 Python 之间唯一新增的跨语言协议，所以用**真 HTTP**（JDK 自带 HttpServer）
 * 把实际发出的请求头抓下来断言，而不是 mock 掉 {@code HttpClient}：
 * mock 只能证明"我调用了我自己写的方法"，证明不了"网线上真的有这两个头、值是对的"。
 * <p>
 * 更要紧的是**签名算法跨语言一致**：Python 侧算不出同一个 HMAC，灰度就会静默地对所有人关闭
 * （不报错、只是不注入）。这里的期望值在测试里用独立的实现再算一遍，等于把算法钉在用例里。
 */
class AiServiceIdentityHeaderTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final String SECRET = "test-secret-请勿用于生产";

    /**
     * 同一密钥与载荷下 Python 侧 {@code inject_gate.sign} 的输出。
     * 把字面量钉在这里，等于把"跨语言协议"写进用例：任何一方改了算法（编码、大小写、分隔符）
     * 这个用例会立刻红，而不会变成"灰度悄悄不生效"。
     */
    private static final String EXPECTED_SIG_42_T9 =
            "38373c73cd9f91a55fabfed9cbdc336652e6b808296703ed9251368aef5088bc";

    private HttpServer server;
    private final AtomicReference<Headers> capturedHeaders = new AtomicReference<>();
    private final AtomicReference<String> capturedBody = new AtomicReference<>();

    @BeforeEach
    void startServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/v1/plan/adjust", exchange -> {
            capturedHeaders.set(exchange.getRequestHeaders());
            capturedBody.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            byte[] body = "{\"code\":0,\"message\":\"ok\",\"data\":{\"summary\":\"s\",\"ops\":[]}}"
                    .getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, body.length);
            exchange.getResponseBody().write(body);
            exchange.close();
        });
        server.start();
    }

    @AfterEach
    void stopServer() {
        server.stop(0);
    }

    private AiServiceClient client(String secret) {
        AiServiceProperties props = new AiServiceProperties();
        props.setBaseUrl("http://127.0.0.1:" + server.getAddress().getPort());
        props.setInternalSecret(secret);
        return new AiServiceClient(props);
    }

    /** 独立的 HMAC 实现：与产品代码不同路径，用来钉住跨语言协议。 */
    private static String expectedSignature(String secret, String payload) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        byte[] digest = mac.doFinal(payload.getBytes(StandardCharsets.UTF_8));
        StringBuilder hex = new StringBuilder();
        for (byte b : digest) {
            hex.append(String.format("%02x", b));
        }
        return hex.toString();
    }

    @Test
    @DisplayName("配了密钥且有 userId：两个身份头成对出现，签名与独立算法一致")
    void sendsSignedIdentity() throws Exception {
        JsonNode data = client(SECRET).adjustPlan(MAPPER.readTree("{\"totalDays\":1}"), "改到三点", "t-9", 42L);

        assertNotNull(data);
        Headers headers = capturedHeaders.get();
        assertEquals("42", headers.getFirst("X-User-Id"));
        assertEquals(expectedSignature(SECRET, "42|t-9"), headers.getFirst("X-Identity-Sig"));
        // 再对一次 Python 侧算出的常量：算法一致才算跨语言通了
        assertEquals(EXPECTED_SIG_42_T9, headers.getFirst("X-Identity-Sig"));
        // 签名必须是小写十六进制 64 位，否则 Python 侧 compare_digest 必然失败
        assertEquals(64, headers.getFirst("X-Identity-Sig").length());
    }

    @Test
    @DisplayName("userId 不进入请求体：身份只走头，不污染模型输入")
    void userIdNeverLeaksIntoBody() throws Exception {
        client(SECRET).adjustPlan(MAPPER.readTree("{\"totalDays\":1}"), "改到三点", "t-9", 42L);
        String body = capturedBody.get();
        assertNotNull(body);
        assertFalse(body.contains("\"userId\""), "请求体不该出现 userId 字段");
        assertTrue(body.contains("改到三点"), "用户原话照旧在请求体里");
    }

    @Test
    @DisplayName("没配密钥：一个身份头都不发（Python 会判为不可信，灰度不生效）")
    void noSecretMeansNoIdentityHeaders() throws Exception {
        client("").adjustPlan(MAPPER.readTree("{}"), "改到三点", "t-9", 42L);
        Headers headers = capturedHeaders.get();
        assertNull(headers.getFirst("X-User-Id"));
        assertNull(headers.getFirst("X-Identity-Sig"));
    }

    @Test
    @DisplayName("没有 userId：不发身份头，但请求照常成功")
    void nullUserMeansNoIdentityHeaders() throws Exception {
        JsonNode data = client(SECRET).adjustPlan(MAPPER.readTree("{}"), "改到三点", "t-9", null);
        assertNotNull(data);
        assertNull(capturedHeaders.get().getFirst("X-User-Id"));
        assertNull(capturedHeaders.get().getFirst("X-Identity-Sig"));
    }

    @Test
    @DisplayName("traceId 不同则签名不同：签名绑定单次请求，不能跨请求搬运")
    void signatureIsBoundToTrace() throws Exception {
        client(SECRET).adjustPlan(MAPPER.readTree("{}"), "改到三点", "t-1", 42L);
        String first = capturedHeaders.get().getFirst("X-Identity-Sig");

        client(SECRET).adjustPlan(MAPPER.readTree("{}"), "改到三点", "t-2", 42L);
        String second = capturedHeaders.get().getFirst("X-Identity-Sig");

        assertFalse(first.equals(second));
        assertEquals(expectedSignature(SECRET, "42|t-2"), second);
    }
}
