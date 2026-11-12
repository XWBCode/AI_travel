package com.aitravel.modules.plan.service;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.plan.dto.PlanProgressVO;
import com.aitravel.modules.plan.entity.TravelDay;
import com.aitravel.modules.plan.entity.TravelItem;
import com.aitravel.modules.plan.entity.TravelPlan;
import com.aitravel.modules.plan.entity.TravelProgress;
import com.aitravel.modules.plan.mapper.TravelDayMapper;
import com.aitravel.modules.plan.mapper.TravelItemMapper;
import com.aitravel.modules.plan.mapper.TravelProgressMapper;
import com.aitravel.modules.plan.validation.PlanStatus;
import com.baomidou.mybatisplus.core.toolkit.IdWorker;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 行程执行进度（M2）。
 *
 * <p>三条边界：
 * <ul>
 *   <li><b>只有进行中能打勾</b>：草稿阶段还没出发，勾「已完成」没有意义；
 *       已完成的计划再改会污染「这次旅行做到了多少」的统计。</li>
 *   <li><b>行程项必须属于该计划</b>：只用 itemId 落库，不校验归属的话，
 *       拿到别人的 itemId 就能在自己计划里打勾（或反过来暴露他人行程结构）。</li>
 *   <li><b>幂等由数据库保证</b>：重复点击走 ON DUPLICATE KEY UPDATE，
 *       不靠应用层「先查再插」，并发下不会 500。</li>
 * </ul>
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class PlanProgressService {

    private static final DateTimeFormatter HHMM = DateTimeFormatter.ofPattern("HH:mm");

    private final PlanStoreService store;
    private final TravelDayMapper dayMapper;
    private final TravelItemMapper itemMapper;
    private final TravelProgressMapper progressMapper;

    /**
     * 读进度。任何状态都能读（草稿时全是 0），前端据此决定是否显示勾选框。
     */
    public PlanProgressVO progress(Long planId, Long userId) {
        TravelPlan plan = store.loadOwned(planId, userId);
        return build(plan);
    }

    /**
     * 勾选 / 取消勾选某个行程项，返回最新进度。
     *
     * @param completed true 勾选完成，false 取消勾选
     */
    @Transactional
    public PlanProgressVO mark(Long planId, Long userId, Long itemId, boolean completed) {
        TravelPlan plan = store.loadOwned(planId, userId);
        PlanStatus current = PlanStatus.parse(plan.getStatus());
        if (current != PlanStatus.IN_PROGRESS) {
            throw new BizException(ErrorCode.PLAN_STATE_INVALID,
                    "只有「进行中」的行程可以打勾，当前状态是「" + current.getLabel() + "」");
        }

        requireItemInPlan(planId, itemId);

        if (completed) {
            TravelProgress row = new TravelProgress();
            row.setId(IdWorker.getId());
            row.setPlanId(planId);
            row.setItemId(itemId);
            row.setUserId(userId);
            row.setCompletedAt(LocalDateTime.now());
            progressMapper.upsert(row);
        } else {
            progressMapper.remove(planId, itemId);
        }
        log.info("进度更新 planId={} itemId={} completed={}", planId, itemId, completed);
        return build(plan);
    }

    /** 归属校验：item → day → plan，任一层对不上都按「不存在」处理，不暴露他人数据结构 */
    private void requireItemInPlan(Long planId, Long itemId) {
        TravelItem item = itemId == null ? null : itemMapper.selectById(itemId);
        if (item == null) {
            throw new BizException(ErrorCode.PLAN_ITEM_NOT_FOUND);
        }
        TravelDay day = dayMapper.selectById(item.getDayId());
        if (day == null || !planId.equals(day.getPlanId())) {
            throw new BizException(ErrorCode.PLAN_ITEM_NOT_FOUND);
        }
    }

    private PlanProgressVO build(TravelPlan plan) {
        PlanProgressVO vo = new PlanProgressVO();
        vo.setPlanId(String.valueOf(plan.getId()));
        vo.setStatus(plan.getStatus());

        List<TravelDay> days = dayMapper.selectList(Wrappers.<TravelDay>lambdaQuery()
                .eq(TravelDay::getPlanId, plan.getId())
                .orderByAsc(TravelDay::getDayIndex));
        if (days.isEmpty()) {
            vo.setDays(List.of());
            return vo;
        }

        List<Long> dayIds = days.stream().map(TravelDay::getId).toList();
        List<TravelItem> items = itemMapper.selectList(Wrappers.<TravelItem>lambdaQuery()
                .in(TravelItem::getDayId, dayIds)
                .orderByAsc(TravelItem::getItemOrder));

        Map<Long, LocalDateTime> completedAt = new HashMap<>();
        for (TravelProgress row : progressMapper.selectList(Wrappers.<TravelProgress>lambdaQuery()
                .eq(TravelProgress::getPlanId, plan.getId()))) {
            completedAt.put(row.getItemId(), row.getCompletedAt());
        }

        Map<Long, List<TravelItem>> itemsByDay = new HashMap<>();
        for (TravelItem item : items) {
            itemsByDay.computeIfAbsent(item.getDayId(), k -> new ArrayList<>()).add(item);
        }

        int total = 0;
        int done = 0;
        boolean nextAssigned = false;
        List<PlanProgressVO.DayProgress> dayVOs = new ArrayList<>();

        for (TravelDay day : days) {
            PlanProgressVO.DayProgress dv = new PlanProgressVO.DayProgress();
            dv.setDayIndex(day.getDayIndex());
            dv.setDate(day.getDayDate());
            dv.setCity(day.getCity());

            List<TravelItem> dayItems = itemsByDay.getOrDefault(day.getId(), List.of()).stream()
                    .sorted(Comparator.comparing(TravelItem::getItemOrder,
                            Comparator.nullsLast(Comparator.naturalOrder())))
                    .toList();

            List<PlanProgressVO.ItemProgress> itemVOs = new ArrayList<>();
            for (TravelItem item : dayItems) {
                PlanProgressVO.ItemProgress iv = new PlanProgressVO.ItemProgress();
                iv.setItemId(String.valueOf(item.getId()));
                iv.setItemOrder(item.getItemOrder());
                iv.setTitle(item.getTitle());
                iv.setStartTime(item.getStartTime() == null ? null : item.getStartTime().format(HHMM));
                iv.setEndTime(item.getEndTime() == null ? null : item.getEndTime().format(HHMM));
                iv.setNote(item.getNote());

                LocalDateTime at = completedAt.get(item.getId());
                iv.setCompleted(at != null);
                iv.setCompletedAt(at);
                if (at == null && !nextAssigned) {
                    // 只有第一个未完成项标 next：「接下来做哪件」只能有一个答案
                    iv.setNext(true);
                    nextAssigned = true;
                }

                total++;
                if (at != null) {
                    done++;
                }
                itemVOs.add(iv);
            }

            dv.setItems(itemVOs);
            dv.setTotal(itemVOs.size());
            dv.setCompleted((int) itemVOs.stream().filter(PlanProgressVO.ItemProgress::isCompleted).count());
            dayVOs.add(dv);
        }

        vo.setDays(dayVOs);
        vo.setTotal(total);
        vo.setCompleted(done);
        vo.setPercent(total == 0 ? 0 : (int) Math.round(done * 100.0 / total));
        return vo;
    }
}
