package com.aitravel.modules.chat.dto;

import com.aitravel.modules.chat.entity.ChatMessage;
import lombok.Data;

import java.time.LocalDateTime;

@Data
public class MessageVO {

    private Long id;
    private Long sessionId;
    private String role;
    private String content;
    private String status;
    private String model;
    private LocalDateTime createTime;

    public static MessageVO from(ChatMessage m) {
        MessageVO vo = new MessageVO();
        vo.setId(m.getId());
        vo.setSessionId(m.getSessionId());
        vo.setRole(m.getRole());
        vo.setContent(m.getContent());
        vo.setStatus(m.getStatus());
        vo.setModel(m.getModel());
        vo.setCreateTime(m.getCreateTime());
        return vo;
    }
}
