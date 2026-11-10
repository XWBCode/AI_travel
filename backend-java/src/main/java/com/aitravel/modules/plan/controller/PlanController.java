package com.aitravel.modules.plan.controller;

import com.aitravel.common.api.PageResult;
import com.aitravel.common.api.Result;
import com.aitravel.common.web.CurrentUser;
import com.aitravel.modules.plan.dto.PlanSummaryVO;
import com.aitravel.modules.plan.service.PlanService;
import com.fasterxml.jackson.databind.JsonNode;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import lombok.Data;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * 计划接口（05 §5）。
 * <p>
 * M1 范围：生成、列表、详情、确认与状态迁移、版本列表。
 * 未实现（已写入开发计划，不得声称已完成）：PATCH 修改草稿、执行进度、调整建议、通知。
 * <p>
 * 权限一律由 {@link CurrentUser} + Service 层的归属校验保证；控制器不接收 userId 参数，
 * 避免越权（AC-14）。
 */
@RestController
@RequestMapping("/api/plans")
@RequiredArgsConstructor
public class PlanController {

    private final PlanService planService;

    /** 触发生成；返回落库后的计划详情（stored 模型） */
    @PostMapping("/generate")
    public Result<JsonNode> generate(@Valid @RequestBody GenerateRequest req) {
        List<Map<String, String>> messages = req.getMessages().stream()
                .map(m -> Map.of("role", m.getRole(), "content", m.getContent()))
                .toList();
        return Result.ok(planService.generate(CurrentUser.requireUserId(), messages));
    }

    @GetMapping
    public Result<PageResult<PlanSummaryVO>> list(
            @RequestParam(defaultValue = "1") long page,
            @RequestParam(defaultValue = "20") long size) {
        return Result.ok(planService.list(CurrentUser.requireUserId(), page, size));
    }

    @GetMapping("/{id}")
    public Result<JsonNode> detail(@PathVariable Long id) {
        return Result.ok(planService.detail(id, CurrentUser.requireUserId()));
    }

    /** 确认计划：DRAFT → CONFIRMED，并生成版本快照（AC-09） */
    @PostMapping("/{id}/confirm")
    public Result<Void> confirm(@PathVariable Long id) {
        planService.confirm(id, CurrentUser.requireUserId());
        return Result.ok();
    }

    @PostMapping("/{id}/start")
    public Result<Void> start(@PathVariable Long id) {
        planService.start(id, CurrentUser.requireUserId());
        return Result.ok();
    }

    @PostMapping("/{id}/complete")
    public Result<Void> complete(@PathVariable Long id) {
        planService.complete(id, CurrentUser.requireUserId());
        return Result.ok();
    }

    @PostMapping("/{id}/cancel")
    public Result<Void> cancel(@PathVariable Long id) {
        planService.cancel(id, CurrentUser.requireUserId());
        return Result.ok();
    }

    @GetMapping("/{id}/versions")
    public Result<List<PlanService.VersionVO>> versions(@PathVariable Long id) {
        return Result.ok(planService.versions(id, CurrentUser.requireUserId()));
    }

    @Data
    public static class GenerateRequest {

        @NotEmpty(message = "消息列表不能为空")
        @Size(max = 40, message = "消息条数不得超过 40")
        private List<@Valid Message> messages;
    }

    @Data
    public static class Message {

        @NotBlank(message = "role 不能为空")
        @Pattern(regexp = "^(user|assistant|system)$", message = "role 只能是 user/assistant/system")
        private String role;

        @NotEmpty(message = "消息内容不能为空")
        @Size(max = 4000, message = "单条消息不得超过 4000 字")
        private String content;
    }
}
