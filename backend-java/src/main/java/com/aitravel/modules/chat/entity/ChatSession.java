package com.aitravel.modules.chat.entity;

import com.aitravel.common.mybatis.BaseEntity;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;

/** AI 会话（总纲 2.2.1） */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("t_chat_session")
public class ChatSession extends BaseEntity {

    private Long userId;

    /** 会话标题，首轮对话后由 AI 生成，不超过 10 个汉字 */
    private String title;

    /** 最近一条消息摘要，用于会话列表展示 */
    private String lastMessagePreview;

    private Integer messageCount;
}
