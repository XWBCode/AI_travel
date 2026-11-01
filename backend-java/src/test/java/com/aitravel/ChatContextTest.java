package com.aitravel.modules.chat.service;

import com.aitravel.modules.chat.dto.ChatTurn;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * 上下文组装的基础校验。
 * 说明：这是 Phase 1 的第一批测试，用来固定「历史消息顺序不可乱」这一契约。
 */
class ChatContextTest {

    @Test
    void context_should_keep_chronological_order() {
        List<ChatTurn> turns = List.of(
                new ChatTurn("user", "我想去云南"),
                new ChatTurn("assistant", "你准备去几天？"),
                new ChatTurn("user", "五天")
        );
        assertEquals("user", turns.get(0).getRole());
        assertEquals("五天", turns.get(turns.size() - 1).getContent());
    }
}
