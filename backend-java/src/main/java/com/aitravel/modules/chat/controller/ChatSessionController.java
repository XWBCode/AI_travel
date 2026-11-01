package com.aitravel.modules.chat.controller;

import com.aitravel.common.api.Result;
import com.aitravel.common.web.CurrentUser;
import com.aitravel.modules.chat.dto.MessageVO;
import com.aitravel.modules.chat.dto.SessionVO;
import com.aitravel.modules.chat.service.ChatSessionService;
import lombok.Data;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/chat/sessions")
@RequiredArgsConstructor
public class ChatSessionController {

    private final ChatSessionService chatSessionService;

    @GetMapping
    public Result<List<SessionVO>> list() {
        return Result.ok(chatSessionService.listSessions(CurrentUser.requireUserId()));
    }

    @GetMapping("/{sessionId}/messages")
    public Result<List<MessageVO>> messages(@PathVariable Long sessionId) {
        return Result.ok(chatSessionService.listMessages(sessionId, CurrentUser.requireUserId()));
    }

    @PatchMapping("/{sessionId}")
    public Result<Void> rename(@PathVariable Long sessionId, @RequestBody RenameRequest req) {
        chatSessionService.renameSession(sessionId, CurrentUser.requireUserId(), req.getTitle());
        return Result.ok();
    }

    @DeleteMapping("/{sessionId}")
    public Result<Void> delete(@PathVariable Long sessionId) {
        chatSessionService.deleteSession(sessionId, CurrentUser.requireUserId());
        return Result.ok();
    }

    @Data
    public static class RenameRequest {
        private String title;
    }
}
