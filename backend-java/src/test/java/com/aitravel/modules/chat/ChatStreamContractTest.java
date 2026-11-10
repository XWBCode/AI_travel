package com.aitravel.modules.chat;

import com.aitravel.modules.chat.dto.SessionVO;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 接口层的两个「坑」回归。
 *
 * <p><b>坑 1：SSE 请求的错误体被 Accept 吞掉。</b>
 * {@code /api/chat/stream} 的请求头只有 {@code Accept: text/event-stream}。
 * 若异常响应交给内容协商去挑转换器，会因为没有能写 text/event-stream 的转换器而
 * 抛 406，最终变成「500 + 空 body」——前端只能显示「请求失败(500)」，真正原因
 * （会话不存在 / 登录凭证无效）全部丢失。修复后错误体固定为 JSON。
 *
 * <p><b>坑 2：雪花 ID 超出 JS 安全整数范围。</b>
 * 19 位 ID 按数字下发会被浏览器四舍五入（…442 → …400），回传后就查不到记录。
 * 修复后所有 Long 字段以字符串下发。
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
@EnabledIfEnvironmentVariable(named = "MYSQL_PASSWORD", matches = ".+")
class ChatStreamContractTest {

    @Autowired
    private MockMvc mvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Test
    @DisplayName("SSE 请求遇到业务异常时返回 JSON 404，而不是空 body 的 500")
    void sseErrorIsJsonNotBlank500() throws Exception {
        String token = login();

        MvcResult result = mvc.perform(post("/api/chat/stream")
                        .header("Authorization", "Bearer " + token)
                        .accept(MediaType.TEXT_EVENT_STREAM)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"content\":\"你好\",\"sessionId\":\"1\"}"))
                .andExpect(status().isNotFound())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.message").value("会话不存在"))
                .andReturn();

        // 关键：响应体不能是空的，否则前端拿不到原因
        assertTrue(result.getResponse().getContentAsString().length() > 0, "错误响应体不应为空");
    }

    @Test
    @DisplayName("雪花 ID 以字符串下发，避免 JS 丢精度")
    void snowflakeIdIsSerializedAsString() throws Exception {
        SessionVO vo = new SessionVO();
        vo.setId(2119988130910269442L);
        vo.setTitle("你好");

        String json = objectMapper.writeValueAsString(vo);
        assertEquals("2119988130910269442", objectMapper.readTree(json).get("id").asText(),
                "id 必须原样保留 19 位精度，实际: " + json);
        assertEquals(true, objectMapper.readTree(json).get("id").isTextual(),
                "id 必须是字符串类型，实际: " + json);
    }

    /** 注册并登录一个测试账号，拿到可用的 access token。 */
    private String login() throws Exception {
        String username = "ct" + System.nanoTime() % 1_000_000_000L;
        String body = "{\"username\":\"" + username + "\",\"password\":\"Contract123456\",\"nickname\":\"契约测试\"}";

        mvc.perform(post("/api/auth/register").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isOk());

        MvcResult login = mvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"" + username + "\",\"password\":\"Contract123456\"}"))
                .andExpect(status().isOk())
                .andReturn();

        return objectMapper.readTree(login.getResponse().getContentAsString())
                .path("data").path("accessToken").asText();
    }
}
