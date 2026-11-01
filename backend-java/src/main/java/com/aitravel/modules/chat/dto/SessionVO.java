package com.aitravel.modules.chat.dto;

import com.aitravel.modules.chat.entity.ChatSession;
import lombok.Data;

import java.time.LocalDateTime;

@Data
public class SessionVO {

    private Long id;
    private String title;
    private String lastMessagePreview;
    private Integer messageCount;
    private LocalDateTime createTime;
    private LocalDateTime updateTime;

    public static SessionVO from(ChatSession s) {
        SessionVO vo = new SessionVO();
        vo.setId(s.getId());
        vo.setTitle(s.getTitle());
        vo.setLastMessagePreview(s.getLastMessagePreview());
        vo.setMessageCount(s.getMessageCount());
        vo.setCreateTime(s.getCreateTime());
        vo.setUpdateTime(s.getUpdateTime());
        return vo;
    }
}
