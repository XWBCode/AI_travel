package com.aitravel.modules.chat.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import lombok.Data;

@Data
public class SendMessageRequest {

    /** 为空时表示新建会话 */
    private Long sessionId;

    @NotBlank(message = "消息内容不能为空")
    @Size(max = 4000, message = "单条消息不能超过 4000 字")
    private String content;

    /**
     * 幂等键（05 §5 / BUG-007）：同一条消息的重试必须复用同一个值。
     * 前端每次「发送」生成一个 UUID；网络超时重发时沿用，而不是新生成一个。
     */
    @Size(max = 64, message = "clientRequestId 不得超过 64 个字符")
    private String clientRequestId;
}
