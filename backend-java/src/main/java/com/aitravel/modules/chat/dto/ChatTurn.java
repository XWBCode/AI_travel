package com.aitravel.modules.chat.dto;

import lombok.AllArgsConstructor;
import lombok.Data;

/** 发给 Python AI 服务的历史消息 */
@Data
@AllArgsConstructor
public class ChatTurn {

    private String role;

    private String content;
}
