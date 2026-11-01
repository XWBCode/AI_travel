package com.aitravel.modules.chat.entity;

import com.aitravel.common.mybatis.BaseEntity;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * AI 消息（总纲 2.2.1）。
 * <p>
 * MySQL 承担持久化职责，Redis 仅做活跃上下文缓存。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("t_chat_message")
public class ChatMessage extends BaseEntity {

    private Long sessionId;

    private Long userId;

    /** user / assistant / tool / system */
    private String role;

    private String content;

    /** SUCCESS / STREAMING / FAILED / CANCELED */
    private String status;

    /** 失败原因，便于排查，不直接展示给用户 */
    private String errorMessage;

    /** 本轮使用的模型名 */
    private String model;

    /** token 用量，能获取时记录 */
    private Integer promptTokens;

    private Integer completionTokens;

    /** 耗时（毫秒） */
    private Long latencyMs;
}
