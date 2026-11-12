package com.aitravel.modules.plan.service;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.api.PageResult;
import com.aitravel.common.api.TraceIdHolder;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.chat.service.AiServiceClient;
import com.aitravel.modules.plan.dto.PlanDraftDto;
import com.aitravel.modules.plan.dto.PlanSummaryVO;
import com.aitravel.modules.plan.dto.PlanUpdateCommand;
import com.aitravel.modules.plan.entity.PlanVersion;
import com.aitravel.modules.plan.entity.TravelPlan;
import com.aitravel.modules.plan.mapper.TravelPlanMapper;
import com.aitravel.modules.plan.validation.PlanSchemaValidator;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.fasterxml.jackson.databind.JsonNode;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

/**
 * 计划编排层：调 AI、校验、留痕、读写编排。
 * <p>
 * 把「调外部服务」与「写数据库」分成两层是刻意的：
 * {@link PlanStoreService} 的事务里不允许出现网络调用，否则一个 3 秒的模型请求
 * 会把数据库连接一起占住（连接池只有 10 个）。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class PlanService {

    private final PlanSchemaValidator validator;
    private final PlanStoreService store;
    private final PlanAssembler assembler;
    private final GenerationLogWriter logWriter;
    private final AiServiceClient aiServiceClient;
    private final TravelPlanMapper planMapper;

    /**
     * 生成计划：AI 生成 → 契约校验 → 事务落库 → 留痕。
     * <p>
     * 校验失败时<b>不落业务表</b>，只写 {@code t_ai_generation_log}（AC-07）。
     */
    public JsonNode generate(Long userId, List<Map<String, String>> messages) {
        return generate(userId, messages, null, null);
    }

    /**
     * 生成计划并记录来源会话。
     *
     * @param sessionId       来源会话，脱离对话生成时为 null
     * @param sourceMessageId 触发本次生成的用户消息
     */
    public JsonNode generate(Long userId, List<Map<String, String>> messages,
                             Long sessionId, Long sourceMessageId) {
        String traceId = TraceIdHolder.get();
        long startedAt = System.currentTimeMillis();

        JsonNode response = aiServiceClient.generatePlan(messages, traceId);
        JsonNode data = response.path("data");
        String rawResponse = data.toString();
        String provider = data.path("done").path("provider").asText(null);
        String model = data.path("done").path("model").asText(null);

        JsonNode draft = data.path("draft").path("plan");
        if (draft.isMissingNode() || draft.isNull()) {
            String skipped = data.path("skipped").asText(null);
            String message = response.path("message").asText("计划生成失败");
            JsonNode missing = data.path("missingFields");
            logWriter.write(userId, null, traceId, provider, model, rawResponse, false,
                    "NO_DRAFT skipped=" + skipped + " message=" + message, elapsed(startedAt));

            if ("NEED_MORE_INFO".equals(skipped)) {
                throw new BizException(ErrorCode.BAD_REQUEST,
                        "信息不足，需要先补充：" + joinText(missing) + "。请继续描述需求后重试");
            }
            throw new BizException(ErrorCode.AI_SERVICE_UNAVAILABLE, message);
        }

        try {
            validator.validateOrThrow(draft);
        } catch (BizException e) {
            // 关键：模型输出必须留痕，否则「为什么被拒」无从复查
            logWriter.write(userId, null, traceId, provider, model, rawResponse, false,
                    e.getMessage(), elapsed(startedAt));
            throw e;
        }

        PlanDraftDto dto = validator.toDto(draft);
        // 不采信模型自报的数量，一律以递归重算为准（S-5 已比对，这里是写入值的来源）
        dto.setUnverifiedFieldCount(validator.countUnverifiedFactFields(draft));

        Long planId = store.createFromDraft(userId, draft, dto, sessionId, sourceMessageId);
        logWriter.write(userId, planId, traceId, provider, model, rawResponse, true, null,
                elapsed(startedAt));
        return detail(planId, userId);
    }

    public JsonNode detail(Long planId, Long userId) {
        TravelPlan plan = store.loadOwned(planId, userId);
        return assembler.assemble(plan);
    }

    /**
     * 编辑草稿基础信息（PATCH）。解析失败与规则冲突都在这里挡住，Store 只负责落库。
     * <p>
     * 没有实际变更时也会返回当前详情：对调用方来说「保存成功」的语义应当一致，
     * 只是不会产生新版本。
     */
    public JsonNode update(Long planId, Long userId, JsonNode body) {
        PlanUpdateCommand cmd = PlanUpdateCommand.parse(body);
        store.updateBasic(planId, userId, cmd);
        return detail(planId, userId);
    }

    public PageResult<PlanSummaryVO> list(Long userId, long page, long size) {
        return list(userId, page, size, null);
    }

    /**
     * @param status 可选状态过滤（DRAFT/CONFIRMED/...）。空或非法值都按「全部」处理——
     *               列表页加个筛选不该因为拼错参数就报错
     */
    public PageResult<PlanSummaryVO> list(Long userId, long page, long size, String status) {
        long safePage = Math.max(page, 1);
        // 05 §5：size ≤ 50，超出按 50 处理而不是报错，减少前端一个小坑
        long safeSize = Math.min(Math.max(size, 1), 50);

        var query = Wrappers.<TravelPlan>lambdaQuery()
                .eq(TravelPlan::getUserId, userId)
                .orderByDesc(TravelPlan::getUpdateTime);
        if (status != null && !status.isBlank()) {
            query.eq(TravelPlan::getStatus, status.trim().toUpperCase());
        }

        IPage<TravelPlan> result = planMapper.selectPage(new Page<>(safePage, safeSize), query);
        List<PlanSummaryVO> records = result.getRecords().stream().map(this::toSummary).toList();
        return PageResult.of(records, result.getTotal(), safePage, safeSize);
    }

    public void confirm(Long planId, Long userId) {
        store.confirm(planId, userId);
    }

    public void start(Long planId, Long userId) {
        store.start(planId, userId, null);
    }

    /**
     * 开始旅行，并显式指定出发日（当日执行页的锚点，05 §5.7）。
     *
     * @param startDate 用户确认的出发日；null 表示沿用计划已有出发日，没有则取服务器当天
     */
    public void start(Long planId, Long userId, LocalDate startDate) {
        store.start(planId, userId, startDate);
    }

    public void complete(Long planId, Long userId) {
        store.complete(planId, userId);
    }

    public void cancel(Long planId, Long userId) {
        store.cancel(planId, userId);
    }

    /**
     * 回滚到指定版本，返回回滚后的详情。
     * <p>
     * 直接把新详情一起返回，前端不用「回滚成功再拉一次详情」——两次请求之间
     * 页面会短暂显示旧内容，看起来像回滚没生效。
     */
    public RollbackVO rollback(Long planId, Long userId, int versionNo) {
        PlanStoreService.RollbackOutcome outcome = store.rollback(planId, userId, versionNo);
        RollbackVO vo = new RollbackVO();
        vo.setPlan(detail(planId, userId));
        vo.setRestoredFrom(outcome.restoredFrom());
        vo.setVersion(outcome.plan().getVersion());
        vo.setClearedProgress((int) outcome.clearedProgress());
        return vo;
    }

    public List<VersionVO> versions(Long planId, Long userId) {
        return store.listVersions(planId, userId).stream().map(this::toVersion).toList();
    }

    private PlanSummaryVO toSummary(TravelPlan plan) {
        PlanSummaryVO vo = new PlanSummaryVO();
        vo.setId(plan.getId());
        vo.setTitle(plan.getTitle());
        vo.setDestination(plan.getDestination());
        vo.setStatus(plan.getStatus());
        vo.setStartDate(plan.getStartDate());
        vo.setEndDate(plan.getEndDate());
        vo.setDays(plan.getDays());
        vo.setTravelers(plan.getTravelers());
        vo.setUnverifiedFieldCount(plan.getUnverifiedFieldCount());
        vo.setVersion(plan.getVersion());
        vo.setUpdateTime(plan.getUpdateTime());
        return vo;
    }

    private VersionVO toVersion(PlanVersion version) {
        VersionVO vo = new VersionVO();
        vo.setId(version.getId());
        vo.setVersionNo(version.getVersionNo());
        vo.setChangeSummary(version.getChangeSummary());
        vo.setOperatorId(version.getOperatorId());
        vo.setCreateTime(version.getCreateTime());
        return vo;
    }

    private long elapsed(long startedAt) {
        return System.currentTimeMillis() - startedAt;
    }

    private String joinText(JsonNode array) {
        if (array == null || !array.isArray() || array.isEmpty()) {
            return "目的地、天数或日期";
        }
        StringBuilder sb = new StringBuilder();
        array.forEach(n -> sb.append(sb.isEmpty() ? "" : "、").append(n.asText()));
        return sb.toString();
    }

    /** 版本列表视图：不返回 snapshotJson，避免一次列表把整计划拖出来 */
    @lombok.Data
    public static class VersionVO {
        private Long id;
        private Integer versionNo;
        private String changeSummary;
        private Long operatorId;
        private LocalDateTime createTime;
    }

    /**
     * 回滚结果。
     * <p>
     * {@code clearedProgress} 单独返回，是为了让前端能明确告诉用户「你之前打过的 N 个勾没了」。
     * 悄悄重置进度比多一个字段更糟——用户会以为是自己点错了。
     * <p>
     * 注意类型是 {@code Integer} 而不是 {@code Long}：全局 Jackson 配置把包装类型 Long
     * 一律序列化成字符串（防雪花 ID 丢精度），这里是个数量，被转成 {@code "2"} 之后
     * 前端 {@code cleared > 0} 恒为 false，提示就永远不出现。
     */
    @lombok.Data
    public static class RollbackVO {
        private JsonNode plan;
        private Integer restoredFrom;
        private Integer version;
        private Integer clearedProgress;
    }
}
