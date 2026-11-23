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
    /**
     * 失败原因。历史里 FAILED 且 content 为空时，前端只有拿到它才不至于显示一个空气泡。
     * 之前只存库没外传，刷新页面后用户看到的是一条空白回复，等于把错误丢了。
     */
    private String errorMessage;
    private String model;
    private LocalDateTime createTime;

    public static MessageVO from(ChatMessage m) {
        MessageVO vo = new MessageVO();
        vo.setId(m.getId());
        vo.setSessionId(m.getSessionId());
        vo.setRole(m.getRole());
        vo.setContent(m.getContent());
        vo.setStatus(m.getStatus());
        vo.setErrorMessage(m.getErrorMessage());
        vo.setModel(m.getModel());
        vo.setCreateTime(m.getCreateTime());
        return vo;
    }
}
