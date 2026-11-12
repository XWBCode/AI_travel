package com.aitravel.modules.plan.service;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.plan.dto.PlanDraftDto;
import com.aitravel.modules.plan.dto.PlanUpdateCommand;
import com.aitravel.modules.plan.entity.Accommodation;
import com.aitravel.modules.plan.entity.BudgetItem;
import com.aitravel.modules.plan.entity.PlanVersion;
import com.aitravel.modules.plan.entity.TravelDay;
import com.aitravel.modules.plan.entity.TravelItem;
import com.aitravel.modules.plan.entity.TravelPlan;
import com.aitravel.modules.plan.entity.TravelProgress;
import com.aitravel.modules.plan.entity.TransportSegment;
import com.aitravel.modules.plan.mapper.AccommodationMapper;
import com.aitravel.modules.plan.mapper.BudgetItemMapper;
import com.aitravel.modules.plan.mapper.PlanRollbackMapper;
import com.aitravel.modules.plan.mapper.PlanVersionMapper;
import com.aitravel.modules.plan.mapper.TravelDayMapper;
import com.aitravel.modules.plan.mapper.TravelItemMapper;
import com.aitravel.modules.plan.mapper.TravelPlanMapper;
import com.aitravel.modules.plan.mapper.TravelProgressMapper;
import com.aitravel.modules.plan.mapper.TransportSegmentMapper;
import com.aitravel.modules.plan.validation.PlanSchemaValidator;
import com.aitravel.modules.plan.validation.PlanStatus;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * 计划持久化与状态迁移。<b>所有写操作都在事务内</b>，且不调用任何外部服务。
 *
 * <p>三条硬约束：
 * <ul>
 *   <li>落库前必须已通过 {@code PlanSchemaValidator}——本类不做校验，也不提供「跳过校验」的入口；</li>
 *   <li>状态迁移走乐观锁条件更新（{@code WHERE id=? AND status=? AND version=?}），并发下不会出现两个请求都成功；</li>
 *   <li>归属校验在此完成，DAO 层不暴露按 id 直查给控制器用。</li>
 * </ul>
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class PlanStoreService {

    private final TravelPlanMapper planMapper;
    private final TravelDayMapper dayMapper;
    private final TravelItemMapper itemMapper;
    private final TransportSegmentMapper segmentMapper;
    private final BudgetItemMapper budgetItemMapper;
    private final AccommodationMapper accommodationMapper;
    private final PlanVersionMapper versionMapper;
    private final PlanRollbackMapper rollbackMapper;
    private final TravelProgressMapper progressMapper;
    private final PlanAssembler assembler;
    /** 只用于「删天之后重算待核实统计」，不承担放行校验的职责（校验仍在 PlanService 里做） */
    private final PlanSchemaValidator validator;
    private final ObjectMapper objectMapper;

    /**
     * 事务落库：plan + day + item + transport + budget + accommodation。
     *
     * @param draftNode 已通过校验的原始草稿（用于原样保存 JSON 列）
     */
    @Transactional
    public Long createFromDraft(Long userId, JsonNode draftNode, PlanDraftDto dto) {
        return createFromDraft(userId, draftNode, dto, null, null);
    }

    /**
     * 事务落库，并记录来源会话。
     * <p>
     * 会话与消息均可空：`/api/plans/generate` 仍允许脱离对话直接生成。
     */
    @Transactional
    public Long createFromDraft(Long userId, JsonNode draftNode, PlanDraftDto dto,
                                Long sessionId, Long sourceMessageId) {
        PlanDraftDto.Basic basic = dto.getBasic();

        TravelPlan plan = new TravelPlan();
        plan.setUserId(userId);
        plan.setSessionId(sessionId);
        plan.setSourceMessageId(sourceMessageId);
        plan.setTitle(dto.getTitle());
        plan.setStatus(PlanStatus.DRAFT.name());
        plan.setOriginCity(basic.getOriginCity());
        plan.setDestination(basic.getDestination());
        plan.setDestinations(json(draftNode.path("basic").path("destinations")));
        plan.setMissingFields(json(draftNode.path("basic").path("missingFields")));
        plan.setStartDate(parseDate(basic.getStartDate()));
        plan.setEndDate(parseDate(basic.getEndDate()));
        plan.setDays(basic.getDays());
        plan.setTravelers(basic.getTravelers());
        plan.setBudget(json(draftNode.path("budget")));
        plan.setPreferences(json(draftNode.path("preferences")));
        plan.setRisks(json(draftNode.path("risks")));
        plan.setAiNote(dto.getAiNote());
        plan.setVersion(0);
        // 不信任模型自报值，这里用递归重算的结果（S-5 已比对过，重算是二次保险）
        plan.setUnverifiedFieldCount(dto.getUnverifiedFieldCount());
        planMapper.insert(plan);
        Long planId = plan.getId();

        writeChildren(planId, dto);
        // 生成即写 v0 快照：否则「最初那一版」在版本列表里点不得（没有快照可回），
        // 而用户最想回到的往往正是 AI 刚生成、还没被自己改过的样子
        writeSnapshotIfAbsent(plan, 0, "AI 生成初稿", userId);
        log.info("计划落库完成 planId={} userId={} days={}", planId, userId, nullSafe(dto.getDays()).size());
        return planId;
    }

    /**
     * 写入全部子表：天、行程项、交通段、住宿、预算条目。
     * <p>
     * 生成与回滚<b>共用</b>这一段：回滚是按快照重建，如果另写一份实现，
     * 「跨城段的 day_id 留空」「itemOrder → itemId 的映射」这类规则迟早会漂移，
     * 回滚出来的计划就会和原计划长得不一样。
     */
    private void writeChildren(Long planId, PlanDraftDto dto) {
        for (PlanDraftDto.Day dayDto : nullSafe(dto.getDays())) {
            TravelDay day = new TravelDay();
            day.setPlanId(planId);
            day.setDayIndex(dayDto.getDayIndex());
            day.setDayDate(parseDate(dayDto.getDate()));
            day.setCity(dayDto.getCity());
            day.setTheme(dayDto.getTheme());
            dayMapper.insert(day);

            Map<Integer, Long> itemIdByOrder = new HashMap<>();
            for (PlanDraftDto.Item itemDto : nullSafe(dayDto.getItems())) {
                TravelItem item = new TravelItem();
                item.setDayId(day.getId());
                item.setAttractionId(itemDto.getAttractionId());
                item.setItemOrder(itemDto.getItemOrder());
                item.setTitle(itemDto.getTitle());
                item.setStartTime(parseTime(itemDto.getStartTime()));
                item.setEndTime(parseTime(itemDto.getEndTime()));
                item.setDuration(json(itemDto.getDuration()));
                item.setOpenHours(json(itemDto.getOpenHours()));
                item.setTicketPrice(json(itemDto.getTicketPrice()));
                item.setNote(itemDto.getNote());
                itemMapper.insert(item);
                itemIdByOrder.put(itemDto.getItemOrder(), item.getId());
            }

            for (PlanDraftDto.TransportSegment segDto : nullSafe(dayDto.getTransportSegments())) {
                boolean interCity = "INTER_CITY".equals(segDto.getScope());
                TransportSegment seg = new TransportSegment();
                seg.setPlanId(planId);
                // 跨城段不属于任何一天：只靠 day_id 归不了属，必须留空
                seg.setDayId(interCity ? null : day.getId());
                seg.setScope(segDto.getScope());
                seg.setFromItemId(interCity ? null : itemIdByOrder.get(segDto.getFromItemOrder()));
                seg.setToItemId(interCity ? null : itemIdByOrder.get(segDto.getToItemOrder()));
                seg.setFromDayIndex(segDto.getFromDayIndex());
                seg.setToDayIndex(segDto.getToDayIndex());
                seg.setFromCity(segDto.getFromCity());
                seg.setToCity(segDto.getToCity());
                seg.setMode(segDto.getMode());
                seg.setDuration(json(segDto.getDuration()));
                seg.setDistance(json(segDto.getDistance()));
                seg.setAdvice(segDto.getAdvice());
                segmentMapper.insert(seg);
            }

            if (dayDto.getAccommodation() != null) {
                Accommodation acc = new Accommodation();
                acc.setPlanId(planId);
                acc.setDayId(day.getId());
                acc.setAreaName(dayDto.getAccommodation().getAreaName());
                acc.setSuggestion(dayDto.getAccommodation().getSuggestion());
                acc.setPriceRange(json(dayDto.getAccommodation().getPriceRange()));
                acc.setSource(dayDto.getAccommodation().getPriceRange() == null ? null
                        : dayDto.getAccommodation().getPriceRange().getSource());
                accommodationMapper.insert(acc);
            }
        }

        if (dto.getBudget() != null) {
            for (PlanDraftDto.BudgetItem itemDto : nullSafe(dto.getBudget().getItems())) {
                BudgetItem item = new BudgetItem();
                item.setPlanId(planId);
                item.setCategory(itemDto.getCategory());
                item.setLabel(itemDto.getLabel());
                item.setAmount(json(itemDto.getAmount()));
                item.setNote(itemDto.getNote());
                item.setSource(itemDto.getAmount() == null ? null : itemDto.getAmount().getSource());
                budgetItemMapper.insert(item);
            }
        }
    }

    @Transactional(readOnly = true)
    public TravelPlan loadOwned(Long planId, Long userId) {
        TravelPlan plan = planMapper.selectById(planId);
        if (plan == null) {
            throw new BizException(ErrorCode.PLAN_NOT_FOUND);
        }
        if (!plan.getUserId().equals(userId)) {
            // 不区分「不存在」与「不是你的」，避免枚举他人计划 ID
            throw new BizException(ErrorCode.FORBIDDEN, "无权访问该计划");
        }
        return plan;
    }

    /**
     * 确认计划：DRAFT → CONFIRMED，并写整计划快照。
     * <p>
     * AC-09 的落点：起点必须是 DRAFT，且状态更新带乐观锁条件；
     * 任何「跳过确认直接开始」的调用都会在 {@link PlanStatus#requireTransitionTo} 被拒。
     */
    @Transactional
    public void confirm(Long planId, Long userId) {
        TravelPlan plan = loadOwned(planId, userId);
        PlanStatus current = PlanStatus.parse(plan.getStatus());
        current.requireTransitionTo(PlanStatus.CONFIRMED);

        int rows = planMapper.update(null, Wrappers.<TravelPlan>lambdaUpdate()
                .eq(TravelPlan::getId, planId)
                .eq(TravelPlan::getStatus, current.name())
                .eq(TravelPlan::getVersion, plan.getVersion())
                .set(TravelPlan::getStatus, PlanStatus.CONFIRMED.name())
                .set(TravelPlan::getConfirmedAt, LocalDateTime.now())
                .setSql("version = version + 1"));
        if (rows == 0) {
            throw conflict(planId);
        }

        // 快照用「加锁后的最新状态」重建，避免把旧 version 写进版本表
        TravelPlan latest = planMapper.selectById(planId);
        writeSnapshotIfAbsent(latest, latest.getVersion(), "用户确认计划", userId);
        log.info("计划已确认 planId={} version={}", planId, latest.getVersion());
    }

    /**
     * 编辑草稿的基础信息（M1 P2-4）。
     *
     * <p>三重防线，缺一不可：
     * <ol>
     *   <li><b>只有 DRAFT 可改</b>：已确认的行程再改，会让「确认」这个动作失去意义；</li>
     *   <li><b>乐观锁</b>：{@code WHERE id=? AND status='DRAFT' AND version=?}，
     *       两个标签页同时保存时只有先到的那个成功，后到的收到明确的版本冲突；</li>
     *   <li><b>改前快照</b>：先把当前状态存成 {@code version_no = 旧 version} 的快照，
     *       再更新。否则一次编辑就把原稿覆盖掉，M2 做回滚时无版本可回。</li>
     * </ol>
     *
     * @return true 表示确实写入了变更；false 表示请求体没有带来任何变化（幂等保存，不产生新版本）
     */
    @Transactional
    public boolean updateBasic(Long planId, Long userId, PlanUpdateCommand cmd) {
        TravelPlan plan = loadOwned(planId, userId);
        PlanStatus current = PlanStatus.parse(plan.getStatus());
        if (current != PlanStatus.DRAFT) {
            throw new BizException(ErrorCode.PLAN_STATE_INVALID,
                    "只有草稿可以修改，当前状态是「" + current.getLabel() + "」");
        }

        int baseVersion = plan.getVersion() == null ? 0 : plan.getVersion();
        // 客户端带了 version 就先比一次：能早失败就不要等到 UPDATE 影响行数为 0 再猜原因
        if (cmd.baseVersion() != null && cmd.baseVersion() != baseVersion) {
            throw conflict(planId);
        }

        PlanUpdateCommand.Resolved resolved = cmd.resolve(toCurrent(plan));
        if (!resolved.changed()) {
            log.info("计划编辑无实际变更，跳过写库 planId={} version={}", planId, baseVersion);
            return false;
        }

        Integer baseDays = plan.getDays();
        boolean destinationChanged = !Objects.equals(resolved.destination(), plan.getDestination());

        // 改前快照必须在 UPDATE 之前取，此时 assemble 读到的还是旧值
        writeSnapshotIfAbsent(plan, baseVersion, "编辑前自动快照", userId);

        int rows = planMapper.update(null, Wrappers.<TravelPlan>lambdaUpdate()
                .eq(TravelPlan::getId, planId)
                .eq(TravelPlan::getStatus, PlanStatus.DRAFT.name())
                .eq(TravelPlan::getVersion, baseVersion)
                .set(TravelPlan::getTitle, resolved.title())
                .set(TravelPlan::getOriginCity, resolved.originCity())
                .set(TravelPlan::getDestination, resolved.destination())
                .set(TravelPlan::getDestinations, json(resolved.destinations()))
                .set(TravelPlan::getMissingFields, json(resolved.missingFields()))
                .set(TravelPlan::getStartDate, resolved.startDate())
                .set(TravelPlan::getEndDate, resolved.endDate())
                .set(TravelPlan::getDays, resolved.days())
                .set(TravelPlan::getTravelers, resolved.travelers())
                .setSql("version = version + 1"));
        if (rows == 0) {
            throw conflict(planId);
        }

        TravelPlan latest = planMapper.selectById(planId);
        // 天数变化意味着「整天行程」的增删，必须同步到 t_travel_day，
        // 否则会出现「头部写 4 天、下面只有 3 天安排」这种自相矛盾的展示
        boolean daysChanged = !Objects.equals(resolved.days(), baseDays);
        syncCalendar(latest, resolved, destinationChanged, baseDays);
        if (daysChanged) {
            // 删掉的天里带着 FactField（时长/门票等），统计值必须跟着重算
            int count = validator.countUnverifiedFactFields(assembler.assemble(latest));
            planMapper.update(null, Wrappers.<TravelPlan>lambdaUpdate()
                    .eq(TravelPlan::getId, planId)
                    .set(TravelPlan::getUnverifiedFieldCount, count));
            latest.setUnverifiedFieldCount(count);
        }
        writeSnapshotIfAbsent(latest, latest.getVersion(), resolved.summary(), userId);
        log.info("计划已编辑 planId={} version {} -> {} 变更：{}",
                planId, baseVersion, latest.getVersion(), resolved.summary());
        return true;
    }

    /**
     * 回滚到指定版本（M2-2）。
     *
     * <p>语义是「<b>把内容换成那一刻的样子</b>」，不是「把整行记录改成那一刻」：
     * 状态、确认时间、会话来源都不回退——用户回滚的是行程内容，不是时间线。
     * 因此回滚后 version 仍然<b>递增</b>，历史只向前写，不会被改写。
     *
     * <p>四个必须做的动作，少一个都会留下不可解释的状态：
     * <ol>
     *   <li><b>回滚前存快照</b>：不然这次回滚本身不可撤销，而「回滚错了想回去」
     *       恰恰是回滚功能最常遇到的第二次操作；</li>
     *   <li><b>清空进度</b>：行程项会被删掉重建，itemId 全变。不清就会出现
     *       「进度条说完成了 3 项，列表里却一项都没打勾」；</li>
     *   <li><b>重建子表</b>：走与生成同一段逻辑，保证跨城段/住宿/预算的映射规则不漂移；</li>
     *   <li><b>乐观锁</b>：{@code WHERE status=? AND version=?}，与编辑、状态迁移同一套条件。</li>
     * </ol>
     *
     * <p>允许回滚的状态：DRAFT / CONFIRMED / IN_PROGRESS；COMPLETED、CANCELLED 是终态，
     * 历史内容只读，否则「这次旅行最终是什么样」会随回滚变化。
     */
    @Transactional
    public RollbackOutcome rollback(Long planId, Long userId, int versionNo) {
        TravelPlan plan = loadOwned(planId, userId);
        PlanStatus current = PlanStatus.parse(plan.getStatus());
        if (current == PlanStatus.COMPLETED || current == PlanStatus.CANCELLED) {
            throw new BizException(ErrorCode.PLAN_STATE_INVALID,
                    "「" + current.getLabel() + "」的计划不能再回滚");
        }

        int baseVersion = plan.getVersion() == null ? 0 : plan.getVersion();
        if (versionNo == baseVersion) {
            throw new BizException(ErrorCode.BAD_REQUEST,
                    "当前内容就是 v" + versionNo + "，无需回滚");
        }

        PlanVersion snapshot = versionMapper.selectOne(Wrappers.<PlanVersion>lambdaQuery()
                .eq(PlanVersion::getPlanId, planId)
                .eq(PlanVersion::getVersionNo, versionNo)
                .orderByDesc(PlanVersion::getId)
                .last("LIMIT 1"));
        if (snapshot == null) {
            throw new BizException(ErrorCode.PLAN_VERSION_NOT_FOUND,
                    "v" + versionNo + " 没有快照，无法回滚到该版本");
        }

        JsonNode root;
        try {
            root = objectMapper.readTree(snapshot.getSnapshotJson());
        } catch (Exception e) {
            log.error("版本快照解析失败 planId={} versionNo={}", planId, versionNo, e);
            throw new BizException(ErrorCode.INTERNAL_ERROR, "版本快照已损坏，无法回滚");
        }
        // 快照是 assemble 的产物，结构与 draft 契约一致，可直接复用 DTO 映射重建子表
        PlanDraftDto dto = validator.toDto(root);
        PlanDraftDto.Basic basic = dto.getBasic();

        writeSnapshotIfAbsent(plan, baseVersion, "回滚前自动快照", userId);

        long clearedProgress = deleteChildren(planId);
        writeChildren(planId, dto);

        int unverified = validator.countUnverifiedFactFields(root);
        int rows = planMapper.update(null, Wrappers.<TravelPlan>lambdaUpdate()
                .eq(TravelPlan::getId, planId)
                .eq(TravelPlan::getStatus, current.name())
                .eq(TravelPlan::getVersion, baseVersion)
                .set(TravelPlan::getTitle, dto.getTitle())
                .set(TravelPlan::getOriginCity, basic == null ? null : basic.getOriginCity())
                .set(TravelPlan::getDestination, basic == null ? null : basic.getDestination())
                .set(TravelPlan::getDestinations, basic == null ? null : json(basic.getDestinations()))
                .set(TravelPlan::getMissingFields, basic == null ? null : json(basic.getMissingFields()))
                .set(TravelPlan::getStartDate, basic == null ? null : parseDate(basic.getStartDate()))
                .set(TravelPlan::getEndDate, basic == null ? null : parseDate(basic.getEndDate()))
                .set(TravelPlan::getDays, basic == null ? null : basic.getDays())
                .set(TravelPlan::getTravelers, basic == null ? null : basic.getTravelers())
                .set(TravelPlan::getBudget, json(root.path("budget")))
                .set(TravelPlan::getPreferences, json(root.path("preferences")))
                .set(TravelPlan::getRisks, json(root.path("risks")))
                .set(TravelPlan::getAiNote, dto.getAiNote())
                .set(TravelPlan::getUnverifiedFieldCount, unverified)
                .setSql("version = version + 1"));
        if (rows == 0) {
            throw conflict(planId);
        }

        TravelPlan latest = planMapper.selectById(planId);
        writeSnapshotIfAbsent(latest, latest.getVersion(), "回滚到 v" + versionNo, userId);
        log.info("计划已回滚 planId={} v{} -> v{}（新 version {}），清空进度 {} 条",
                planId, baseVersion, versionNo, latest.getVersion(), clearedProgress);
        return new RollbackOutcome(latest, versionNo, clearedProgress);
    }

    /**
     * 清空计划内容（物理删除，原因见 {@link PlanRollbackMapper}）。
     *
     * @return 被清掉的打勾记录数，用于在响应里如实告知用户「进度已重置」
     */
    private long deleteChildren(Long planId) {
        long progressRows = progressMapper.selectCount(Wrappers.<TravelProgress>lambdaQuery()
                .eq(TravelProgress::getPlanId, planId));
        if (progressRows > 0) {
            progressMapper.delete(Wrappers.<TravelProgress>lambdaQuery()
                    .eq(TravelProgress::getPlanId, planId));
        }
        rollbackMapper.deleteItems(planId);
        rollbackMapper.deleteSegments(planId);
        rollbackMapper.deleteAccommodations(planId);
        rollbackMapper.deleteDays(planId);
        rollbackMapper.deleteBudgetItems(planId);
        return progressRows;
    }

    /** 回滚结果：{@code clearedProgress} 表示被打勾记录被清掉的条数 */
    public record RollbackOutcome(TravelPlan plan, int restoredFrom, long clearedProgress) {
    }

    /**
     * 把天数与日期的变化同步到「按天」表。
     *
     * <p>三条规则：
     * <ul>
     *   <li>天数变多 → 补空白天（只有日期和城市，没有行程项），用户后续可以往里填；</li>
     *   <li>天数变少 → 删掉尾部整天（含日内交通、住宿、行程项）。这些内容在改前快照里留有备份；</li>
     *   <li>出发日期变了 → 所有天按 {@code startDate + (dayIndex-1)} 重算日期，保持第 1 天就是出发日。</li>
     * </ul>
     * 天数不变、日期也没变时整段是空操作（不做任何 update）。
     */
    private void syncCalendar(TravelPlan plan, PlanUpdateCommand.Resolved resolved,
                              boolean destinationChanged, Integer oldDays) {
        boolean calendarChanged = !Objects.equals(resolved.days(), oldDays)
                || !Objects.equals(resolved.startDate(), plan.getStartDate());
        if (!calendarChanged) {
            return;
        }

        List<TravelDay> existing = dayMapper.selectList(Wrappers.<TravelDay>lambdaQuery()
                .eq(TravelDay::getPlanId, plan.getId())
                .orderByAsc(TravelDay::getDayIndex));

        Integer target = resolved.days();
        if (target == null) {
            // 天数改成「未知」：清掉日期，免留一组对不上的日期
            for (TravelDay day : existing) {
                if (day.getDayDate() != null) {
                    day.setDayDate(null);
                    dayMapper.updateById(day);
                }
            }
            return;
        }

        List<Long> removedDayIds = existing.stream()
                .filter(d -> d.getDayIndex() > target)
                .map(TravelDay::getId)
                .toList();
        if (!removedDayIds.isEmpty()) {
            // 先按 itemId 清进度：行程项一删，progress 行就失去了归属，
            // 留着的孤儿行不会报错，但会让「完成了多少项」这类按 plan 直接 count 的查询虚高
            List<Long> removedItemIds = itemMapper.selectList(Wrappers.<TravelItem>lambdaQuery()
                            .in(TravelItem::getDayId, removedDayIds))
                    .stream().map(TravelItem::getId).toList();
            if (!removedItemIds.isEmpty()) {
                progressMapper.delete(Wrappers.<TravelProgress>lambdaQuery()
                        .in(TravelProgress::getItemId, removedItemIds));
            }
            itemMapper.delete(Wrappers.<TravelItem>lambdaQuery().in(TravelItem::getDayId, removedDayIds));
            segmentMapper.delete(Wrappers.<TransportSegment>lambdaQuery()
                    .in(TransportSegment::getDayId, removedDayIds));
            accommodationMapper.delete(Wrappers.<Accommodation>lambdaQuery()
                    .in(Accommodation::getDayId, removedDayIds));
            dayMapper.deleteBatchIds(removedDayIds);
        }
        // 跨城段不挂 day_id，得按天数索引单独清，否则会留下指向已删除天的段
        segmentMapper.delete(Wrappers.<TransportSegment>lambdaQuery()
                .eq(TransportSegment::getPlanId, plan.getId())
                .and(w -> w.gt(TransportSegment::getFromDayIndex, target)
                        .or().gt(TransportSegment::getToDayIndex, target)));

        Set<Integer> existingIndexes = new HashSet<>();
        for (TravelDay day : existing) {
            existingIndexes.add(day.getDayIndex());
            if (day.getDayIndex() > target) {
                continue;
            }
            boolean dirty = false;
            LocalDate date = resolved.startDate() == null
                    ? null : resolved.startDate().plusDays(day.getDayIndex() - 1L);
            if (!Objects.equals(day.getDayDate(), date)) {
                day.setDayDate(date);
                dirty = true;
            }
            // 只有目的地真的改了才覆盖城市，否则会把多目的地计划的城市统一掉
            if (destinationChanged && resolved.destination() != null
                    && !Objects.equals(day.getCity(), resolved.destination())) {
                day.setCity(resolved.destination());
                dirty = true;
            }
            if (dirty) {
                dayMapper.updateById(day);
            }
        }

        for (int index = 1; index <= target; index++) {
            if (existingIndexes.contains(index)) {
                continue;
            }
            if (resolved.destination() == null) {
                throw new BizException(ErrorCode.BAD_REQUEST,
                        "缺少目的地，无法把行程调整到 " + target + " 天，请先填写目的地");
            }
            TravelDay day = new TravelDay();
            day.setPlanId(plan.getId());
            day.setDayIndex(index);
            day.setDayDate(resolved.startDate() == null
                    ? null : resolved.startDate().plusDays(index - 1L));
            day.setCity(resolved.destination());
            dayMapper.insert(day);
        }
    }

    /**
     * 开始旅行，并确定「第 1 天到底是哪一天」。
     *
     * <p>「开始」是唯一能把相对天数钉到绝对日期的时机：计划可以只有天数没有出发日
     * （用户说「东京玩五天」就能生成），此时 {@code t_travel_day.day_date} 全是 null，
     * 当日执行页算不出「今天对应第几天」。所以这里做三件事：
     * <ol>
     *   <li>锚点优先级：请求显式传入 &gt; 计划已有出发日 &gt; 东八区当天；</li>
     *   <li>出发日与结束日一起回填，并按 {@code 出发日 + (dayIndex-1)} 重算全部 day_date；</li>
     *   <li>锚点确实变了就写一份版本快照——改日期是改内容，回滚要能回得去。</li>
     * </ol>
     *
     * <p>只改日期、不动天序号与行程项：锚定是「给已有内容贴上日期」，不是重排行程。
     *
     * @param requestedStart 用户确认的出发日，可为 null（沿用计划原值或服务器当天）
     */
    @Transactional
    public void start(Long planId, Long userId, LocalDate requestedStart) {
        TravelPlan plan = loadOwned(planId, userId);
        PlanStatus current = PlanStatus.parse(plan.getStatus());
        current.requireTransitionTo(PlanStatus.IN_PROGRESS);

        LocalDate anchor = requestedStart != null ? requestedStart : plan.getStartDate();
        if (anchor == null) {
            anchor = PlanDates.today();
        }
        boolean anchorChanged = !anchor.equals(plan.getStartDate());
        int totalDays = resolveTotalDays(plan);
        LocalDate endDate = totalDays > 0 ? anchor.plusDays(totalDays - 1L) : null;

        var update = Wrappers.<TravelPlan>lambdaUpdate()
                .eq(TravelPlan::getId, planId)
                .eq(TravelPlan::getStatus, current.name())
                .eq(TravelPlan::getVersion, plan.getVersion())
                .set(TravelPlan::getStatus, PlanStatus.IN_PROGRESS.name())
                .set(TravelPlan::getStartedAt, LocalDateTime.now())
                .set(TravelPlan::getStartDate, anchor)
                .set(TravelPlan::getEndDate, endDate)
                .setSql("version = version + 1");
        if (planMapper.update(null, update) == 0) {
            throw conflict(planId);
        }

        if (anchorChanged) {
            reindexDayDates(planId, anchor);
            TravelPlan after = planMapper.selectById(planId);
            writeSnapshotIfAbsent(after, after.getVersion(),
                    "开始旅行并锚定出发日期 " + anchor, userId);
        }
        log.info("开始旅行 planId={} anchor={} anchorChanged={} totalDays={}",
                planId, anchor, anchorChanged, totalDays);
    }

    /** 天数以计划字段为准；字段被清空时退回天表行数，避免算出「0 天」的结束日 */
    private int resolveTotalDays(TravelPlan plan) {
        if (plan.getDays() != null && plan.getDays() > 0) {
            return plan.getDays();
        }
        Long rows = dayMapper.selectCount(Wrappers.<TravelDay>lambdaQuery()
                .eq(TravelDay::getPlanId, plan.getId()));
        return rows == null ? 0 : rows.intValue();
    }

    /** 把出发日重算到每一天；日期已经对上就不写库，避免无意义的 update */
    private void reindexDayDates(Long planId, LocalDate startDate) {
        List<TravelDay> days = dayMapper.selectList(Wrappers.<TravelDay>lambdaQuery()
                .eq(TravelDay::getPlanId, planId));
        for (TravelDay day : days) {
            LocalDate expected = startDate.plusDays(day.getDayIndex() - 1L);
            if (!Objects.equals(day.getDayDate(), expected)) {
                day.setDayDate(expected);
                dayMapper.updateById(day);
            }
        }
    }

    @Transactional
    public void complete(Long planId, Long userId) {
        transit(planId, userId, PlanStatus.COMPLETED, TravelPlan::getCompletedAt);
    }

    /** 取消：DRAFT / CONFIRMED / IN_PROGRESS 均可，COMPLETED 之后不可 */
    @Transactional
    public void cancel(Long planId, Long userId) {
        transit(planId, userId, PlanStatus.CANCELLED, null);
    }

    private void transit(Long planId, Long userId, PlanStatus target,
                         com.baomidou.mybatisplus.core.toolkit.support.SFunction<TravelPlan, ?> timestampField) {
        TravelPlan plan = loadOwned(planId, userId);
        PlanStatus current = PlanStatus.parse(plan.getStatus());
        current.requireTransitionTo(target);

        var update = Wrappers.<TravelPlan>lambdaUpdate()
                .eq(TravelPlan::getId, planId)
                .eq(TravelPlan::getStatus, current.name())
                .eq(TravelPlan::getVersion, plan.getVersion())
                .set(TravelPlan::getStatus, target.name())
                .setSql("version = version + 1");
        if (timestampField != null) {
            update.set(timestampField, LocalDateTime.now());
        }
        if (planMapper.update(null, update) == 0) {
            throw conflict(planId);
        }
        log.info("计划状态变更 planId={} {} -> {}", planId, current, target);
    }

    private BizException conflict(Long planId) {
        TravelPlan latest = planMapper.selectById(planId);
        if (latest == null) {
            return new BizException(ErrorCode.PLAN_NOT_FOUND);
        }
        // 状态被别的请求改过，优先报状态错误（信息更准确），否则报版本冲突
        return new BizException(ErrorCode.PLAN_VERSION_CONFLICT,
                "计划已被修改（当前状态 " + latest.getStatus() + "，version "
                        + latest.getVersion() + "），请刷新后重试");
    }

    @Transactional(readOnly = true)
    public List<PlanVersion> listVersions(Long planId, Long userId) {
        loadOwned(planId, userId);
        return versionMapper.selectList(Wrappers.<PlanVersion>lambdaQuery()
                .eq(PlanVersion::getPlanId, planId)
                .orderByDesc(PlanVersion::getVersionNo));
    }

    /**
     * 同一个 version_no 只写一份快照。
     * <p>
     * 唯一索引 {@code uk_plan_version(plan_id, version_no, deleted)} 会拦重复插入，
     * 但那是抛异常；先查再写能让「编辑 + 确认」这类连续操作自然共存。
     */
    private void writeSnapshotIfAbsent(TravelPlan plan, int versionNo, String changeSummary, Long userId) {
        Long existing = versionMapper.selectCount(Wrappers.<PlanVersion>lambdaQuery()
                .eq(PlanVersion::getPlanId, plan.getId())
                .eq(PlanVersion::getVersionNo, versionNo));
        if (existing != null && existing > 0) {
            return;
        }
        PlanVersion snapshot = new PlanVersion();
        snapshot.setPlanId(plan.getId());
        snapshot.setVersionNo(versionNo);
        snapshot.setSnapshotJson(assembler.assemble(plan).toString());
        snapshot.setChangeSummary(truncate(changeSummary, 200));
        snapshot.setOperatorId(userId);
        versionMapper.insert(snapshot);
    }

    private PlanUpdateCommand.Current toCurrent(TravelPlan plan) {
        return new PlanUpdateCommand.Current(
                plan.getTitle(),
                plan.getOriginCity(),
                plan.getDestination(),
                readStringList(plan.getDestinations()),
                plan.getStartDate(),
                plan.getEndDate(),
                plan.getDays(),
                plan.getTravelers(),
                readStringList(plan.getMissingFields()));
    }

    private List<String> readStringList(String raw) {
        if (raw == null || raw.isBlank()) {
            return List.of();
        }
        try {
            JsonNode node = objectMapper.readTree(raw);
            if (!node.isArray()) {
                return List.of();
            }
            List<String> out = new ArrayList<>();
            node.forEach(n -> out.add(n.asText()));
            return out;
        } catch (Exception e) {
            log.error("JSON 数组列解析失败，按空处理: {}", raw, e);
            return List.of();
        }
    }

    private String truncate(String text, int max) {
        if (text == null) {
            return null;
        }
        return text.length() <= max ? text : text.substring(0, max - 3) + "...";
    }

    private String json(Object value) {
        if (value == null) {
            return null;
        }
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception e) {
            throw new BizException(ErrorCode.PLAN_SCHEMA_VIOLATION, "计划字段序列化失败：" + e.getMessage());
        }
    }

    private LocalDate parseDate(String text) {
        return text == null || text.isBlank() ? null : LocalDate.parse(text);
    }

    private LocalTime parseTime(String text) {
        return text == null || text.isBlank() ? null : LocalTime.parse(text);
    }

    private <T> List<T> nullSafe(List<T> list) {
        return list == null ? List.of() : list;
    }
}
