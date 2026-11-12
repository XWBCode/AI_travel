package com.aitravel.modules.plan.controller;

import com.aitravel.common.api.PageResult;
import com.aitravel.common.api.Result;
import com.aitravel.common.web.CurrentUser;
import com.aitravel.modules.plan.dto.PlanProgressVO;
import com.aitravel.modules.plan.dto.PlanSummaryVO;
import com.aitravel.modules.plan.dto.PlanTodayVO;
import com.aitravel.modules.plan.service.PlanProgressService;
import com.aitravel.modules.plan.service.PlanService;
import com.aitravel.modules.plan.service.PlanTodayService;
import com.fasterxml.jackson.databind.JsonNode;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import lombok.Data;
import lombok.RequiredArgsConstructor;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;

/**
 * 计划接口（05 §5）。
 * <p>
 * 已实现：生成、列表、详情、编辑草稿、确认与状态迁移、版本列表与回滚、执行进度（M2）。
 * 未实现（已写入开发计划，不得声称已完成）：行程条目增删、调整建议、通知。
 * <p>
 * 权限一律由 {@link CurrentUser} + Service 层的归属校验保证；控制器不接收 userId 参数，
 * 避免越权（AC-14）。
 */
@RestController
@RequestMapping("/api/plans")
@RequiredArgsConstructor
public class PlanController {

    private final PlanService planService;
    private final PlanProgressService progressService;
    private final PlanTodayService todayService;

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
            @RequestParam(defaultValue = "20") long size,
            @RequestParam(required = false) String status) {
        return Result.ok(planService.list(CurrentUser.requireUserId(), page, size, status));
    }

    @GetMapping("/{id}")
    public Result<JsonNode> detail(@PathVariable Long id) {
        return Result.ok(planService.detail(id, CurrentUser.requireUserId()));
    }

    /**
     * 编辑草稿的基础信息（标题/出发地/目的地/天数/人数/起止日期）。
     * <p>
     * PATCH 语义：只改请求体里出现的字段；传 {@code null} 表示清空该项。
     * 并发安全由 {@code status + version} 双条件更新保证——同一版本只有一个请求能改成功，
     * 其余返回 {@code 30104} 版本冲突。
     */
    @PatchMapping("/{id}")
    public Result<JsonNode> update(@PathVariable Long id,
                                   @RequestBody(required = false) JsonNode body) {
        return Result.ok(planService.update(id, CurrentUser.requireUserId(), body));
    }

    /** 确认计划：DRAFT → CONFIRMED，并生成版本快照（AC-09） */
    @PostMapping("/{id}/confirm")
    public Result<Void> confirm(@PathVariable Long id) {
        planService.confirm(id, CurrentUser.requireUserId());
        return Result.ok();
    }

    /**
     * 开始行程：CONFIRMED → IN_PROGRESS。
     * <p>
     * 请求体可选。带 {@code startDate} 表示用户确认了出发日——计划没有出发日时，
     * 这是唯一能把「第几天」钉到真实日期的入口（05 §5.7）；不带则沿用计划原值，
     * 仍为空就取服务器当天（东八区）。
     */
    @PostMapping("/{id}/start")
    public Result<Void> start(@PathVariable Long id,
                              @RequestBody(required = false) StartRequest req) {
        planService.start(id, CurrentUser.requireUserId(), req == null ? null : req.getStartDate());
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

    /**
     * 回滚到指定版本（M2-2）。
     * <p>
     * 回滚只替换<b>内容</b>，不回退状态：v3 的草稿回滚到 v1 之后仍是草稿，
     * version 继续递增到 v4。历史只向前写，不回改写。
     */
    @PostMapping("/{id}/versions/{versionNo}/rollback")
    public Result<PlanService.RollbackVO> rollback(@PathVariable Long id,
                                                   @PathVariable int versionNo) {
        return Result.ok(planService.rollback(id, CurrentUser.requireUserId(), versionNo));
    }

    /**
     * 当日执行视图（M2，R-21）。
     * <p>
     * {@code date} 是<b>用户本地的今天</b>，由前端传上来：服务端时区与用户所在时区可能差一天，
     * 拿服务器时间算会让跨时区用户在深夜看到昨天的行程。缺省时按服务端东八区当天兜底。
     * <p>
     * 越界不报错：未出发返回 {@code BEFORE} + 第 1 天预览，超期返回 {@code AFTER} + 最后一天；
     * 计划没有出发日返回 {@code UNANCHORED}，由前端引导补日期或直接开始旅行。
     */
    @GetMapping("/{id}/today")
    public Result<PlanTodayVO> today(
            @PathVariable Long id,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate date) {
        return Result.ok(todayService.today(id, CurrentUser.requireUserId(), date));
    }

    /**
     * 执行进度（M2）。任何状态都可读：草稿阶段返回全 0，前端据此决定是否渲染勾选框。
     */
    @GetMapping("/{id}/progress")
    public Result<PlanProgressVO> progress(@PathVariable Long id) {
        return Result.ok(progressService.progress(id, CurrentUser.requireUserId()));
    }

    /**
     * 勾选 / 取消勾选行程项，返回最新进度。
     * <p>
     * 幂等：同一项重复勾选不报错、不产生重复记录（05 §5「幂等」）。取消勾选用
     * {@code completed=false} 而不是单独的 DELETE——前端只需要一个「切换」动作，
     * 多一个接口就多一处状态不一致的机会。
     */
    @PostMapping("/{id}/progress")
    public Result<PlanProgressVO> markProgress(@PathVariable Long id,
                                               @Valid @RequestBody ProgressRequest req) {
        return Result.ok(progressService.mark(id, CurrentUser.requireUserId(),
                req.getItemId(), req.getCompleted() == null || req.getCompleted()));
    }

    @Data
    public static class StartRequest {

        /**
         * 用户确认的出发日；缺省表示沿用计划已有出发日，没有则用服务器当天。
         * 不做「不得早于今天」这类校验：用户可能昨天就出发了，今天才点开页面。
         */
        private LocalDate startDate;
    }

    @Data
    public static class ProgressRequest {

        @NotNull(message = "itemId 不能为空")
        private Long itemId;

        /** 缺省 true；传 false 表示取消勾选 */
        private Boolean completed;
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
