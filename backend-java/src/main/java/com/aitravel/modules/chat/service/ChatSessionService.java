package com.aitravel.modules.chat.service;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.chat.dto.ChatTurn;
import com.aitravel.modules.chat.dto.MessageVO;
import com.aitravel.modules.chat.dto.SendMessageRequest;
import com.aitravel.modules.chat.dto.SessionVO;
import com.aitravel.modules.chat.entity.ChatMessage;
import com.aitravel.modules.chat.entity.ChatSession;
import com.aitravel.modules.chat.mapper.ChatMessageMapper;
import com.aitravel.modules.chat.mapper.ChatSessionMapper;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Objects;

@Slf4j
@Service
@RequiredArgsConstructor
public class ChatSessionService {

    /** 每次带给模型的最近轮次上限，防止上下文无限膨胀 */
    private static final int CONTEXT_LIMIT = 20;

    private final ChatSessionMapper sessionMapper;
    private final ChatMessageMapper messageMapper;

    public List<SessionVO> listSessions(Long userId) {
        return sessionMapper.selectList(Wrappers.<ChatSession>lambdaQuery()
                        .eq(ChatSession::getUserId, userId)
                        .orderByDesc(ChatSession::getUpdateTime))
                .stream().map(SessionVO::from).toList();
    }

    /** 校验会话归属，越权一律 403（总纲 8.1） */
    public ChatSession requireOwnedSession(Long sessionId, Long userId) {
        ChatSession session = sessionMapper.selectById(sessionId);
        if (session == null) {
            throw new BizException(ErrorCode.SESSION_NOT_FOUND);
        }
        if (!Objects.equals(session.getUserId(), userId)) {
            throw new BizException(ErrorCode.FORBIDDEN);
        }
        return session;
    }

    @Transactional(rollbackFor = Exception.class)
    public ChatSession createSession(Long userId, String firstUserContent) {
        ChatSession session = new ChatSession();
        session.setUserId(userId);
        session.setTitle(defaultTitle(firstUserContent));
        session.setMessageCount(0);
        sessionMapper.insert(session);
        return session;
    }

    public void renameSession(Long sessionId, Long userId, String title) {
        ChatSession session = requireOwnedSession(sessionId, userId);
        ChatSession patch = new ChatSession();
        patch.setId(session.getId());
        patch.setTitle(title);
        sessionMapper.updateById(patch);
    }

    public void deleteSession(Long sessionId, Long userId) {
        requireOwnedSession(sessionId, userId);
        sessionMapper.deleteById(sessionId);
        messageMapper.delete(Wrappers.<ChatMessage>lambdaQuery().eq(ChatMessage::getSessionId, sessionId));
    }

    public List<MessageVO> listMessages(Long sessionId, Long userId) {
        requireOwnedSession(sessionId, userId);
        return messageMapper.selectList(Wrappers.<ChatMessage>lambdaQuery()
                        .eq(ChatMessage::getSessionId, sessionId)
                        .orderByAsc(ChatMessage::getId))
                .stream().map(MessageVO::from).toList();
    }

    public List<ChatTurn> buildContext(Long sessionId) {
        List<ChatMessage> recent = messageMapper.selectList(Wrappers.<ChatMessage>lambdaQuery()
                .eq(ChatMessage::getSessionId, sessionId)
                .eq(ChatMessage::getStatus, "SUCCESS")
                .orderByDesc(ChatMessage::getId)
                .last("LIMIT " + CONTEXT_LIMIT));
        // 查询是倒序的，还原成时间正序
        return recent.stream().sorted((a, b) -> Long.compare(a.getId(), b.getId()))
                .map(m -> new ChatTurn(m.getRole(), m.getContent()))
                .toList();
    }

    public ChatMessage saveUserMessage(Long sessionId, Long userId, String content) {
        ChatMessage msg = new ChatMessage();
        msg.setSessionId(sessionId);
        msg.setUserId(userId);
        msg.setRole("user");
        msg.setContent(content);
        msg.setStatus("SUCCESS");
        messageMapper.insert(msg);
        touchSession(sessionId, summarize(content));
        return msg;
    }

    public ChatMessage saveAssistantMessage(Long sessionId, Long userId, String content, String model,
                                            long latencyMs, String status, String errorMessage) {
        ChatMessage msg = new ChatMessage();
        msg.setSessionId(sessionId);
        msg.setUserId(userId);
        msg.setRole("assistant");
        msg.setContent(content == null ? "" : content);
        msg.setStatus(status);
        msg.setModel(model);
        msg.setLatencyMs(latencyMs);
        msg.setErrorMessage(errorMessage);
        messageMapper.insert(msg);
        if ("SUCCESS".equals(status) && content != null && !content.isBlank()) {
            touchSession(sessionId, summarize(content));
        }
        return msg;
    }

    /** 请求参数解析：sessionId 为空则新建会话 */
    @Transactional(rollbackFor = Exception.class)
    public ChatSession resolveSession(Long userId, SendMessageRequest req) {
        if (req.getSessionId() == null) {
            return createSession(userId, req.getContent());
        }
        return requireOwnedSession(req.getSessionId(), userId);
    }

    private void touchSession(Long sessionId, String preview) {
        ChatSession session = sessionMapper.selectById(sessionId);
        if (session == null) {
            return;
        }
        ChatSession patch = new ChatSession();
        patch.setId(sessionId);
        patch.setLastMessagePreview(preview);
        patch.setMessageCount((session.getMessageCount() == null ? 0 : session.getMessageCount()) + 1);
        sessionMapper.updateById(patch);
    }

    private String summarize(String content) {
        if (content == null) {
            return "";
        }
        String flat = content.replaceAll("\\s+", " ").trim();
        return flat.length() > 60 ? flat.substring(0, 60) : flat;
    }

    private String defaultTitle(String content) {
        String flat = summarize(content);
        return flat.isEmpty() ? "新会话" : (flat.length() > 10 ? flat.substring(0, 10) : flat);
    }
}
