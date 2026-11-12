package com.aitravel.modules.plan.service;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.plan.dto.PlanProgressVO;
import com.aitravel.modules.plan.dto.PlanTodayVO;
import com.aitravel.modules.plan.entity.TravelDay;
import com.aitravel.modules.plan.entity.TravelItem;
import com.aitravel.modules.plan.entity.TravelPlan;
import com.aitravel.modules.plan.entity.TravelProgress;
import com.aitravel.modules.plan.mapper.TravelDayMapper;
import com.aitravel.modules.plan.mapper.TravelItemMapper;
import com.aitravel.modules.plan.mapper.TravelProgressMapper;
import com.aitravel.modules.plan.validation.PlanStatus;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 当日执行视图（M2，R-21）：把「今天」翻译成「第几天」。
 *
 * <p>核心不是渲染，而是<b>把相对天数钉到绝对日期</b>。天序号 {@code dayIndex} 是相对的，
 * 唯一的时间锚是 {@code plan.startDate}；它为 null 时（AI 契约允许「只有天数没有出发日」）
 * 今天与任何一天都不存在对应关系，此时返回 {@code UNANCHORED} 让前端引导补日期，
 * <b>绝不猜一个「那就当第 1 天吧」</b>——猜错的表现是用户按第 3 天的安排跑到了第 1 天的景点。
 *
 * <p>三条边界：
 * <ul>
 *   <li>越界不报错：未出发 = {@code BEFORE}（预览第 1 天）、超期 = {@code AFTER}（停在最后一天），
 *       空页或 500 会让用户以为计划没了；</li>
 *   <li>终态不提供「今天」：已完成/已取消的行程再谈今天没有意义，直接拒绝而非返回脏数据；</li>
 *   <li>进度只统计<b>仍存在的行程项</b>：改天数会删尾部整天，万一残留进度行，
 *       按 plan_id 直接 count 会让「完成了 12 项」超过总数。</li>
 * </ul>
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class PlanTodayService {

    private final PlanStoreService store;
    private final TravelDayMapper dayMapper;
    private final TravelItemMapper itemMapper;
    private final TravelProgressMapper progressMapper;

    /**
     * @param date 前端本地日期；null 时用服务端东八区当天（见 {@link PlanDates}）
     */
    public PlanTodayVO today(Long planId, Long userId, LocalDate date) {
        TravelPlan plan = store.loadOwned(planId, userId);
        PlanStatus status = PlanStatus.parse(plan.getStatus());
        if (status == PlanStatus.COMPLETED || status == PlanStatus.CANCELLED) {
            throw new BizException(ErrorCode.PLAN_STATE_INVALID,
                    "行程已" + status.getLabel() + "，没有「今天」可执行；需要继续安排请新建计划");
        }

        LocalDate today = date == null ? PlanDates.today() : date;
        List<TravelDay> days = dayMapper.selectList(Wrappers.<TravelDay>lambdaQuery()
                .eq(TravelDay::getPlanId, planId)
                .orderByAsc(TravelDay::getDayIndex));
        Map<Integer, TravelDay> dayByIndex = new HashMap<>();
        for (TravelDay day : days) {
            dayByIndex.put(day.getDayIndex(), day);
        }

        // 逐项统计一次，当日进度与整趟进度都从这里取，避免两次查询之间数据漂移
        Map<Long, List<TravelItem>> itemsByDay = loadItems(days);
        Map<Long, LocalDateTime> completedAt = loadProgress(planId, itemsByDay);

        PlanTodayVO vo = new PlanTodayVO();
        vo.setPlanId(String.valueOf(planId));
        vo.setStatus(plan.getStatus());
        vo.setTitle(plan.getTitle());
        vo.setDate(today);
        vo.setTotalDays(resolveTotalDays(plan, days.size()));

        fillOverall(vo, itemsByDay, completedAt);
        fillPhase(vo, plan, today, dayByIndex, itemsByDay, completedAt);
        return vo;
    }

    /** 只认仍存在的行程项：孤儿进度行不参与统计（见类注释第 3 条） */
    private void fillOverall(PlanTodayVO vo, Map<Long, List<TravelItem>> itemsByDay,
                             Map<Long, LocalDateTime> completedAt) {
        int total = 0;
        int done = 0;
        for (List<TravelItem> items : itemsByDay.values()) {
            for (TravelItem item : items) {
                total++;
                if (completedAt.get(item.getId()) != null) {
                    done++;
                }
            }
        }
        vo.setTotal(total);
        vo.setCompleted(done);
        vo.setPercent(total == 0 ? 0 : (int) Math.round(done * 100.0 / total));
    }

    private void fillPhase(PlanTodayVO vo, TravelPlan plan, LocalDate today,
                           Map<Integer, TravelDay> dayByIndex,
                           Map<Long, List<TravelItem>> itemsByDay,
                           Map<Long, LocalDateTime> completedAt) {
        LocalDate start = plan.getStartDate();
        if (start == null) {
            // 没有锚点：不猜。前端据此显示「补充出发日期 / 直接开始旅行」
            vo.setPhase("UNANCHORED");
            return;
        }

        int totalDays = vo.getTotalDays() == null ? 0 : vo.getTotalDays();
        long delta = ChronoUnit.DAYS.between(start, today);
        int dayIndex;
        if (delta < 0) {
            vo.setPhase("BEFORE");
            vo.setDaysUntilStart((int) -delta);
            dayIndex = 1;
        } else if (totalDays > 0 && delta >= totalDays) {
            vo.setPhase("AFTER");
            vo.setDaysSinceEnd((int) (delta - (totalDays - 1L)));
            dayIndex = totalDays;
        } else {
            vo.setPhase("TODAY");
            dayIndex = (int) delta + 1;
        }
        vo.setDayIndex(dayIndex);

        TravelDay day = dayByIndex.get(dayIndex);
        if (day == null) {
            // 天表比计划天数少（例如导航到一份未生成完的天表）：给一个空卡片而不是 null，
            // 前端就能统一按「今天没有安排」渲染
            PlanProgressVO.DayProgress empty = new PlanProgressVO.DayProgress();
            empty.setDayIndex(dayIndex);
            empty.setDate(start.plusDays(dayIndex - 1L));
            empty.setItems(List.of());
            vo.setDay(empty);
            return;
        }
        vo.setDay(dayCard(day, itemsByDay.get(day.getId()), completedAt));
    }

    private PlanProgressVO.DayProgress dayCard(TravelDay day, List<TravelItem> items,
                                               Map<Long, LocalDateTime> completedAt) {
        PlanProgressVO.DayProgress card = new PlanProgressVO.DayProgress();
        card.setDayIndex(day.getDayIndex());
        card.setDate(day.getDayDate());
        card.setCity(day.getCity());
        card.setTheme(day.getTheme());

        List<TravelItem> sorted = items == null ? List.of() : items.stream()
                .sorted(Comparator.comparing(TravelItem::getItemOrder,
                        Comparator.nullsLast(Comparator.naturalOrder())))
                .toList();

        List<PlanProgressVO.ItemProgress> rows = new ArrayList<>();
        boolean nextAssigned = false;
        int done = 0;
        for (TravelItem item : sorted) {
            PlanProgressVO.ItemProgress row = PlanProgressService.project(item, completedAt.get(item.getId()));
            if (!row.isCompleted() && !nextAssigned) {
                // 当日视图的 next 只在「今天」里选：整趟的下一个可能在三天后，对今天没用
                row.setNext(true);
                nextAssigned = true;
            }
            if (row.isCompleted()) {
                done++;
            }
            rows.add(row);
        }
        card.setItems(rows);
        card.setTotal(rows.size());
        card.setCompleted(done);
        return card;
    }

    private int resolveTotalDays(TravelPlan plan, int dayRows) {
        if (plan.getDays() != null && plan.getDays() > 0) {
            return plan.getDays();
        }
        return dayRows;
    }

    private Map<Long, List<TravelItem>> loadItems(List<TravelDay> days) {
        Map<Long, List<TravelItem>> byDay = new HashMap<>();
        if (days.isEmpty()) {
            return byDay;
        }
        List<Long> dayIds = days.stream().map(TravelDay::getId).toList();
        for (TravelItem item : itemMapper.selectList(Wrappers.<TravelItem>lambdaQuery()
                .in(TravelItem::getDayId, dayIds))) {
            byDay.computeIfAbsent(item.getDayId(), k -> new ArrayList<>()).add(item);
        }
        return byDay;
    }

    private Map<Long, LocalDateTime> loadProgress(Long planId, Map<Long, List<TravelItem>> itemsByDay) {
        Map<Long, LocalDateTime> completed = new HashMap<>();
        for (TravelProgress row : progressMapper.selectList(Wrappers.<TravelProgress>lambdaQuery()
                .eq(TravelProgress::getPlanId, planId))) {
            completed.put(row.getItemId(), row.getCompletedAt());
        }
        return completed;
    }
}
