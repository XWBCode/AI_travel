package com.aitravel.modules.plan.service;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.chat.service.AiServiceClient;
import com.aitravel.modules.notification.service.NotificationService;
import com.aitravel.modules.plan.dto.PlanAdjustmentVO;
import com.aitravel.modules.plan.entity.PlanAdjustment;
import com.aitravel.modules.plan.entity.TravelDay;
import com.aitravel.modules.plan.entity.TravelItem;
import com.aitravel.modules.plan.entity.TravelPlan;
import com.aitravel.modules.plan.mapper.PlanAdjustmentMapper;
import com.aitravel.modules.plan.mapper.TravelDayMapper;
import com.aitravel.modules.plan.mapper.TravelItemMapper;
import com.aitravel.modules.plan.mapper.TravelPlanMapper;
import com.aitravel.modules.plan.validation.PlanStatus;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.LocalDateTime;
import java.time.LocalTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * AI 调整建议的完整流程（R-23 / R-24 / R-25）。
 *
 * <pre>
 * 用户说「下午不去了」
 *   → 组装当前计划的可编辑视图（带 itemId）
 *   → AI 只产出增量 ops（不碰正式表）
 *   → Java 校验 + 回填 before（AI 不可能编造「原值」）
 *   → 落 t_plan_adjustment，状态 PENDING
 *   → 用户确认 → 同一事务内应用 ops + version+1 + 写快照 → CONFIRMED
 * </pre>
 *
 * <p><b>三个刻意的决定：</b>
 * <ol>
 *   <li><b>确认不是「再调一次 AI」</b>：ops 在生成时就固化进库了。若确认时让模型重新算，
 *       用户看到的 diff 和真正落下去的东西可能不是同一份——这是最不该发生的一类不一致。</li>
 *   <li><b>base_version 不匹配就作废</b>：用户收到建议后自己改了一处，再点确认时，
 *       这份建议会静默覆盖掉那次修改，且没有任何报错。所以确认前必须比对版本。</li>
 *   <li><b>过期是惰性的</b>：没有定时任务。确认时发现版本不符或状态已变，就地转 EXPIRED。
 *       一条用户从没点开的建议，没人关心它几点过期，为它养一个调度器不值得。</li>
 * </ol>
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class PlanAdjustmentService {

    private static final DateTimeFormatter HH_MM = DateTimeFormatter.ofPattern("HH:mm");
    private static final int MAX_INSTRUCTION = 500;
    private static final int RECENT_LIMIT = 20;

    private final PlanStoreService store;
    private final PlanAdjustmentMapper adjustmentMapper;
    private final TravelPlanMapper planMapper;
    private final TravelDayMapper dayMapper;
    private final TravelItemMapper itemMapper;
    private final AdjustmentApplier applier;
    private final AiServiceClient aiServiceClient;
    private final ObjectMapper objectMapper;
    private final PlatformTransactionManager txManager;
    /** 确认后记一条「行程已调整」；生成建议本身不通知（计划没变） */
    private final NotificationService notificationService;

    /**
     * 生成一条建议。
     * <p>
     * 不加 {@code @Transactional}：中间要等模型（可能 30s+），把 HTTP 等待包在事务里会白占连接。
     * 只有最后落库那一条 insert，本身就是一个原子操作。
     */
    public PlanAdjustmentVO propose(Long planId, Long userId, String instruction, String traceId) {
        String text = instruction == null ? "" : instruction.trim();
        if (text.isEmpty()) {
            throw new BizException(ErrorCode.BAD_REQUEST, "请先说要调整什么，比如「下午不去了」");
        }
        if (text.length() > MAX_INSTRUCTION) {
            throw new BizException(ErrorCode.BAD_REQUEST, "描述太长了，请控制在 " + MAX_INSTRUCTION + " 字以内");
        }

        TravelPlan plan = store.loadOwned(planId, userId);
        requireAdjustable(plan);

        Snapshot snapshot = loadSnapshot(plan);
        JsonNode data = aiServiceClient.adjustPlan(snapshot.aiView(), text, traceId);
        String summary = text(data.path("summary"), "行程调整建议");

        List<AdjustmentOps.Op> ops;
        try {
            ops = AdjustmentOps.normalize(data.path("ops"), snapshot.view());
        } catch (AdjustmentOps.Invalid e) {
            // 模型给的东西不合规，这条建议不该进库——存下来只会让用户在确认时撞一个错
            throw new BizException(ErrorCode.ADJUSTMENT_INVALID, e.getMessage());
        }
        if (ops.isEmpty()) {
            throw new BizException(ErrorCode.ADJUSTMENT_NOOP,
                    "这次要求没有可以调整的内容。想改天数、日期或预算的话，请走行程编辑。");
        }

        PlanAdjustment adjustment = new PlanAdjustment();
        adjustment.setPlanId(planId);
        adjustment.setUserId(userId);
        // 用视图对应的版本，不是调用前的旧值：两者不一致说明读到一半被改了，loadSnapshot 已重试
        adjustment.setBaseVersion(snapshot.version());
        adjustment.setStatus(PlanAdjustment.PENDING);
        adjustment.setInstruction(text);
        adjustment.setSummary(truncate(summary, 200));
        adjustment.setOpsJson(json(AdjustmentOps.toPayload(ops)));
        adjustmentMapper.insert(adjustment);

        log.info("调整建议已生成 planId={} adjustmentId={} baseVersion={} ops={}",
                planId, adjustment.getId(), snapshot.version(), ops.size());
        return toVO(adjustment, plan.getVersion());
    }

    /**
     * 确认建议：应用增量操作，版本 +1，写快照。
     * <p>
     * 应用 ops 用的是**当前库里的真实数据**，不是生成时的视图。所以 itemId 仍然有效，
     * before 也不会过期——但这也意味着 base_version 的比对必须严格，否则会盖掉期间的修改。
     *
     * <p><b>「标记过期」必须发生在事务之外。</b>如果把它写进带 {@code @Transactional} 的方法，
     * 紧接着抛出业务异常，Spring 会把这次状态更新一起回滚——用户下次点确认，看到的还是
     * PENDING，于是永远点不动。所以这里分两段：先在事务外判版本、就地标过期，再进事务应用变更。
     * 事务内仍保留一次版本比对，防止进入事务的瞬间被写。
     */
    public ConfirmResult confirm(Long planId, Long userId, Long adjustmentId) {
        TravelPlan plan = store.loadOwned(planId, userId);
        PlanAdjustment adjustment = requirePending(planId, userId, adjustmentId);

        if (!adjustment.getBaseVersion().equals(plan.getVersion())) {
            expire(adjustment, "计划在建议生成后被修改过");
            throw new BizException(ErrorCode.ADJUSTMENT_STALE);
        }

        TransactionTemplate template = new TransactionTemplate(txManager);
        return template.execute(status -> applyConfirmed(planId, userId, adjustmentId));
    }

    /** 真正的落库段。必须由 {@link #confirm} 通过事务模板调用——自调用会让 @Transactional 失效。 */
    private ConfirmResult applyConfirmed(Long planId, Long userId, Long adjustmentId) {
        TravelPlan plan = store.loadOwned(planId, userId);
        PlanAdjustment adjustment = requirePending(planId, userId, adjustmentId);

        if (!adjustment.getBaseVersion().equals(plan.getVersion())) {
            // 极端竞态：进门时版本又变了。这里不标过期（同样会被回滚），下次确认会再报一次
            throw new BizException(ErrorCode.ADJUSTMENT_STALE);
        }
        requireAdjustable(plan);

        Snapshot snapshot = loadSnapshot(plan);
        List<AdjustmentOps.Op> ops;
        try {
            ops = AdjustmentOps.normalize(readTree(adjustment.getOpsJson()), snapshot.view());
        } catch (AdjustmentOps.Invalid e) {
            throw new BizException(ErrorCode.ADJUSTMENT_INVALID, e.getMessage());
        }
        if (ops.isEmpty()) {
            throw new BizException(ErrorCode.ADJUSTMENT_NOOP);
        }

        AdjustmentApplier.Result result = applier.apply(plan, ops);

        int unverified = Math.max(0,
                (plan.getUnverifiedFieldCount() == null ? 0 : plan.getUnverifiedFieldCount())
                        + result.factFieldDelta());
        int rows = planMapper.update(null, Wrappers.<TravelPlan>lambdaUpdate()
                .eq(TravelPlan::getId, planId)
                .eq(TravelPlan::getVersion, plan.getVersion())
                .eq(TravelPlan::getStatus, plan.getStatus())
                .setSql("version = version + 1")
                .set(TravelPlan::getUnverifiedFieldCount, unverified));
        if (rows == 0) {
            throw new BizException(ErrorCode.PLAN_VERSION_CONFLICT);
        }

        TravelPlan latest = planMapper.selectById(planId);
        store.writeSnapshotIfAbsent(latest, latest.getVersion(),
                "确认调整：" + adjustment.getSummary(), userId);

        adjustment.setStatus(PlanAdjustment.CONFIRMED);
        adjustment.setDecidedAt(LocalDateTime.now());
        adjustment.setReason(null);
        adjustmentMapper.updateById(adjustment);

        notificationService.adjustmentConfirmed(latest, adjustmentId, adjustment.getSummary(),
                latest.getVersion());

        log.info("调整已确认 planId={} adjustmentId={} version={} clearedProgress={}",
                planId, adjustmentId, latest.getVersion(), result.clearedProgress());
        return new ConfirmResult(String.valueOf(adjustmentId), latest.getVersion(),
                result.clearedProgress(), adjustment.getSummary());
    }

    /** 拒绝建议。不产生版本，也不动正式计划——这正是「AI 的建议而已」该有的形态。 */
    @Transactional
    public void reject(Long planId, Long userId, Long adjustmentId, String reason) {
        store.loadOwned(planId, userId);
        PlanAdjustment adjustment = requirePending(planId, userId, adjustmentId);
        adjustment.setStatus(PlanAdjustment.REJECTED);
        adjustment.setDecidedAt(LocalDateTime.now());
        adjustment.setReason(truncate(reason == null || reason.isBlank() ? "用户放弃该建议" : reason.trim(), 200));
        adjustmentMapper.updateById(adjustment);
        log.info("调整建议被拒绝 planId={} adjustmentId={}", planId, adjustmentId);
    }

    /** 最近若干条建议，含已决策的：用户回看「上次那条我为什么拒了」时需要。 */
    public List<PlanAdjustmentVO> list(Long planId, Long userId) {
        TravelPlan plan = store.loadOwned(planId, userId);
        List<PlanAdjustment> rows = adjustmentMapper.selectList(Wrappers.<PlanAdjustment>lambdaQuery()
                .eq(PlanAdjustment::getPlanId, planId)
                .orderByDesc(PlanAdjustment::getCreateTime)
                .last("LIMIT " + RECENT_LIMIT));
        List<PlanAdjustmentVO> result = new ArrayList<>();
        for (PlanAdjustment row : rows) {
            result.add(toVO(row, plan.getVersion()));
        }
        return result;
    }

    /**
     * 只允许「已确认、进行中」的计划调整。
     * 草稿直接编辑就行，不必绕一圈 AI；终态没有调整的意义。
     */
    private void requireAdjustable(TravelPlan plan) {
        PlanStatus status = PlanStatus.parse(plan.getStatus());
        if (status == PlanStatus.DRAFT) {
            throw new BizException(ErrorCode.PLAN_STATE_INVALID,
                    "草稿阶段直接编辑就好，不用走调整建议");
        }
        if (status != PlanStatus.CONFIRMED && status != PlanStatus.IN_PROGRESS) {
            throw new BizException(ErrorCode.PLAN_STATE_INVALID,
                    "这趟行程已经「" + status.getLabel() + "」，不能再调整了");
        }
    }

    private PlanAdjustment requirePending(Long planId, Long userId, Long adjustmentId) {
        PlanAdjustment adjustment = adjustmentMapper.selectById(adjustmentId);
        // 不区分「不存在」与「不是你的」：能枚举出别人的建议 ID 本身就不该被允许
        if (adjustment == null || !planId.equals(adjustment.getPlanId()) || !userId.equals(adjustment.getUserId())) {
            throw new BizException(ErrorCode.ADJUSTMENT_NOT_FOUND);
        }
        if (!PlanAdjustment.PENDING.equals(adjustment.getStatus())) {
            throw new BizException(ErrorCode.ADJUSTMENT_NOT_FOUND, "这条建议已经处理过了");
        }
        return adjustment;
    }

    private void expire(PlanAdjustment adjustment, String reason) {
        adjustment.setStatus(PlanAdjustment.EXPIRED);
        adjustment.setDecidedAt(LocalDateTime.now());
        adjustment.setReason(truncate(reason, 200));
        adjustmentMapper.updateById(adjustment);
        log.info("调整建议已过期 adjustmentId={} reason={}", adjustment.getId(), reason);
    }

    /**
     * 读取一份「版本与内容自洽」的计划视图。
     * <p>
     * 先记版本、再读子表、最后再读一次版本：不一致说明期间有人写过（另一个标签页、回滚、打勾），
     * 此时视图里的 itemId 可能对应已经不存在或已变化的行。重试一次就够——调整是低频操作，
     * 而这层检查挡掉的是「AI 对着过期数据出建议、确认时才发现」这种最难排查的错。
     */
    private Snapshot loadSnapshot(TravelPlan plan) {
        for (int attempt = 0; attempt < 2; attempt++) {
            TravelPlan before = planMapper.selectById(plan.getId());
            int version = before.getVersion() == null ? 0 : before.getVersion();

            List<TravelDay> days = dayMapper.selectList(Wrappers.<TravelDay>lambdaQuery()
                    .eq(TravelDay::getPlanId, plan.getId())
                    .orderByAsc(TravelDay::getDayIndex));
            List<Long> dayIds = days.stream().map(TravelDay::getId).toList();
            List<TravelItem> items = dayIds.isEmpty() ? List.of()
                    : itemMapper.selectList(Wrappers.<TravelItem>lambdaQuery()
                    .in(TravelItem::getDayId, dayIds)
                    .orderByAsc(TravelItem::getItemOrder));

            TravelPlan after = planMapper.selectById(plan.getId());
            int afterVersion = after.getVersion() == null ? 0 : after.getVersion();
            if (version != afterVersion) {
                log.info("读取计划视图期间版本变化，重试 planId={} {} → {}", plan.getId(), version, afterVersion);
                continue;
            }
            return build(days, items, version, before);
        }
        throw new BizException(ErrorCode.PLAN_VERSION_CONFLICT);
    }

    private Snapshot build(List<TravelDay> days, List<TravelItem> items, int version, TravelPlan plan) {
        Map<Long, TravelDay> dayById = new LinkedHashMap<>();
        days.forEach(d -> dayById.put(d.getId(), d));

        Map<Integer, AdjustmentOps.DayView> dayViews = new LinkedHashMap<>();
        for (TravelDay day : days) {
            dayViews.put(day.getDayIndex(), new AdjustmentOps.DayView(
                    day.getDayIndex(), day.getId(), day.getCity(), day.getTheme()));
        }

        Map<Long, AdjustmentOps.ItemView> itemViews = new LinkedHashMap<>();
        Map<Long, List<TravelItem>> itemsByDay = new LinkedHashMap<>();
        for (TravelItem item : items) {
            TravelDay day = dayById.get(item.getDayId());
            int dayIndex = day == null ? 0 : day.getDayIndex();
            itemViews.put(item.getId(), new AdjustmentOps.ItemView(
                    item.getId(), dayIndex, item.getItemOrder(), item.getTitle(),
                    formatTime(item.getStartTime()), formatTime(item.getEndTime()), item.getNote()));
            itemsByDay.computeIfAbsent(item.getDayId(), k -> new ArrayList<>()).add(item);
        }

        // 给模型的视图刻意做窄：只留下「能改的」和「用来定位的」。
        // 事实字段、预算、偏好对调整决策是噪声，多给反而更容易被顺手改掉。
        ObjectNode aiView = objectMapper.createObjectNode();
        aiView.put("title", plan.getTitle());
        aiView.put("totalDays", days.size());
        aiView.put("startDate", plan.getStartDate() == null ? null : plan.getStartDate().toString());
        aiView.put("status", plan.getStatus());
        ArrayNode dayArray = aiView.putArray("days");
        for (TravelDay day : days) {
            ObjectNode dayNode = dayArray.addObject();
            dayNode.put("dayIndex", day.getDayIndex());
            dayNode.put("date", day.getDayDate() == null ? null : day.getDayDate().toString());
            dayNode.put("city", day.getCity());
            dayNode.put("theme", day.getTheme());
            ArrayNode itemArray = dayNode.putArray("items");
            for (TravelItem item : itemsByDay.getOrDefault(day.getId(), List.of())) {
                ObjectNode itemNode = itemArray.addObject();
                itemNode.put("itemId", String.valueOf(item.getId()));
                itemNode.put("itemOrder", item.getItemOrder());
                itemNode.put("title", item.getTitle());
                itemNode.put("startTime", formatTime(item.getStartTime()));
                itemNode.put("endTime", formatTime(item.getEndTime()));
                itemNode.put("note", item.getNote());
            }
        }

        return new Snapshot(version,
                new AdjustmentOps.PlanView(dayViews, itemViews), aiView);
    }

    /** 视图 + 它对应的计划版本，两者必须成对出现，否则 base_version 就失去意义 */
    private record Snapshot(int version, AdjustmentOps.PlanView view, ObjectNode aiView) {
    }

    public record ConfirmResult(String adjustmentId, int version, long clearedProgress, String summary) {
    }

    private PlanAdjustmentVO toVO(PlanAdjustment adjustment, Integer planVersion) {
        PlanAdjustmentVO vo = new PlanAdjustmentVO();
        vo.setAdjustmentId(String.valueOf(adjustment.getId()));
        vo.setPlanId(String.valueOf(adjustment.getPlanId()));
        vo.setBaseVersion(adjustment.getBaseVersion());
        vo.setStatus(adjustment.getStatus());
        vo.setInstruction(adjustment.getInstruction());
        vo.setSummary(adjustment.getSummary());
        vo.setOps(readOps(adjustment.getOpsJson()));
        vo.setPlanVersion(planVersion);
        vo.setCreateTime(adjustment.getCreateTime());
        return vo;
    }

    private List<Map<String, Object>> readOps(String raw) {
        if (raw == null || raw.isBlank()) {
            return List.of();
        }
        try {
            return objectMapper.readValue(raw, new TypeReference<List<Map<String, Object>>>() {
            });
        } catch (Exception e) {
            log.error("调整建议 ops 解析失败 adjustment={}", raw, e);
            return List.of();
        }
    }

    private JsonNode readTree(String raw) {
        try {
            return objectMapper.readTree(raw == null ? "[]" : raw);
        } catch (Exception e) {
            throw new BizException(ErrorCode.ADJUSTMENT_INVALID, "建议内容已损坏");
        }
    }

    private String json(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception e) {
            throw new BizException(ErrorCode.INTERNAL_ERROR, "建议序列化失败");
        }
    }

    private String formatTime(LocalTime time) {
        return time == null ? null : time.format(HH_MM);
    }

    private String text(JsonNode node, String fallback) {
        String value = node == null || node.isNull() ? "" : node.asText("");
        return value.isBlank() ? fallback : value.trim();
    }

    private String truncate(String text, int max) {
        if (text == null) {
            return null;
        }
        return text.length() <= max ? text : text.substring(0, max);
    }
}
