package com.aitravel.modules.plan.service;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.plan.dto.PlanDraftDto;
import com.aitravel.modules.plan.entity.Accommodation;
import com.aitravel.modules.plan.entity.BudgetItem;
import com.aitravel.modules.plan.entity.PlanVersion;
import com.aitravel.modules.plan.entity.TravelDay;
import com.aitravel.modules.plan.entity.TravelItem;
import com.aitravel.modules.plan.entity.TravelPlan;
import com.aitravel.modules.plan.entity.TransportSegment;
import com.aitravel.modules.plan.mapper.AccommodationMapper;
import com.aitravel.modules.plan.mapper.BudgetItemMapper;
import com.aitravel.modules.plan.mapper.PlanVersionMapper;
import com.aitravel.modules.plan.mapper.TravelDayMapper;
import com.aitravel.modules.plan.mapper.TravelItemMapper;
import com.aitravel.modules.plan.mapper.TravelPlanMapper;
import com.aitravel.modules.plan.mapper.TransportSegmentMapper;
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
import java.util.HashMap;
import java.util.List;
import java.util.Map;

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
    private final PlanAssembler assembler;
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
        log.info("计划落库完成 planId={} userId={} days={}", planId, userId, nullSafe(dto.getDays()).size());
        return planId;
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
        PlanVersion snapshot = new PlanVersion();
        snapshot.setPlanId(planId);
        snapshot.setVersionNo(latest.getVersion());
        snapshot.setSnapshotJson(assembler.assemble(latest).toString());
        snapshot.setChangeSummary("用户确认计划");
        snapshot.setOperatorId(userId);
        versionMapper.insert(snapshot);
        log.info("计划已确认 planId={} version={}", planId, latest.getVersion());
    }

    @Transactional
    public void start(Long planId, Long userId) {
        transit(planId, userId, PlanStatus.IN_PROGRESS, TravelPlan::getStartedAt);
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
