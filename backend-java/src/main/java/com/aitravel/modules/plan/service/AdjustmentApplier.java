package com.aitravel.modules.plan.service;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.plan.entity.TravelDay;
import com.aitravel.modules.plan.entity.TravelItem;
import com.aitravel.modules.plan.entity.TravelPlan;
import com.aitravel.modules.plan.mapper.TravelDayMapper;
import com.aitravel.modules.plan.mapper.TravelItemMapper;
import com.aitravel.modules.plan.mapper.TravelProgressMapper;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalTime;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 把校验通过的调整操作落到 {@code t_travel_item} / {@code t_travel_day}。
 *
 * <p><b>增量，不重建。</b>只有被 op 点名的行会被改动，其余行程项保持原行、原 {@code itemId}。
 * 这是「用户已经打的勾不会因为一次下午取消而蒸发」的唯一实现方式——如果照搬回滚的
 * 整份替换，进度必然清零。
 *
 * <p>本类只负责改数据，不碰 plan 的 {@code version} 与版本快照：那属于「一次变更的边界」，
 * 由 {@code PlanAdjustmentService} 在同一个事务里统一处理，免得两处各写一遍 version+1。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class AdjustmentApplier {

    /**
     * 新增行程项的事实字段默认值，与 AI 生成契约里的七键形态完全一致。
     * 模型在调整场景不允许碰事实字段，这里由服务端填空值，等将来接入实时数据源再回填。
     */
    private static final String FACT_FIELD_JSON =
            "{\"value\":null,\"display\":\"待核实\",\"source\":\"NONE\",\"verified\":false,"
                    + "\"fetchedAt\":null,\"expiresAt\":null,\"note\":\"未接入实时数据源\"}";

    /** 每条行程项固定带三个待核实事实字段（时长 / 开放时间 / 票价），用于推算计数变化 */
    private static final int FACT_FIELDS_PER_ITEM = 3;

    /**
     * 重排时的临时偏移量。{@code uk_day_item(day_id, item_order, deleted)} 是真唯一键，
     * 直接把 A 的位置写成 B 会立刻撞键，所以先把整天的序号平移到负区间腾出 1..n。
     * 用「减固定值」而不是「取负」：取负会翻转相对顺序，导致重排结果整体倒序。
     */
    private static final int ORDER_SHIFT = 1_000_000;

    private final TravelDayMapper dayMapper;
    private final TravelItemMapper itemMapper;
    private final TravelProgressMapper progressMapper;

    /**
     * @param factFieldDelta 事实字段数量的净变化，调用方据此更新 {@code unverifiedFieldCount}
     */
    public record Result(int added, int removed, int updated, int moved, int reordered, int dayUpdated,
                         long clearedProgress, int factFieldDelta) {
    }

    @Transactional
    public Result apply(TravelPlan plan, List<AdjustmentOps.Op> ops) {
        Map<Integer, TravelDay> days = dayMapper.selectList(Wrappers.<TravelDay>lambdaQuery()
                        .eq(TravelDay::getPlanId, plan.getId()))
                .stream().collect(Collectors.toMap(TravelDay::getDayIndex, d -> d));

        Set<Long> affected = new LinkedHashSet<>();
        int added = 0;
        int removed = 0;
        int updated = 0;
        int moved = 0;
        int reordered = 0;
        int dayUpdated = 0;
        long cleared = 0;

        for (AdjustmentOps.Op op : ops) {
            switch (op.type()) {
                case ADD_ITEM -> {
                    TravelDay day = days.get(op.dayIndex());
                    TravelItem item = new TravelItem();
                    item.setDayId(day.getId());
                    item.setAttractionId(null);
                    item.setItemOrder(nextOrder(day.getId()));
                    item.setTitle(op.title());
                    item.setStartTime(parseTime(op.startTime()));
                    item.setEndTime(parseTime(op.endTime()));
                    item.setDuration(FACT_FIELD_JSON);
                    item.setOpenHours(FACT_FIELD_JSON);
                    item.setTicketPrice(FACT_FIELD_JSON);
                    item.setNote(op.note());
                    itemMapper.insert(item);
                    affected.add(day.getId());
                    added++;
                }
                case REMOVE_ITEM -> {
                    TravelItem item = requireItem(op.itemId());
                    // 物理删除：逻辑删除会与 uk_day_item 里的 deleted 一起构成「同一位置只能删一次」
                    itemMapper.deletePhysically(item.getId());
                    // 进度跟着走：行程项都没了还留着勾选行，统计会算出「完成数 > 总数」
                    cleared += progressMapper.remove(plan.getId(), item.getId());
                    affected.add(item.getDayId());
                    removed++;
                }
                case UPDATE_ITEM -> {
                    TravelItem item = requireItem(op.itemId());
                    for (AdjustmentOps.Change change : op.changes()) {
                        switch (change.field()) {
                            case "title" -> item.setTitle(change.after());
                            case "startTime" -> item.setStartTime(parseTime(change.after()));
                            case "endTime" -> item.setEndTime(parseTime(change.after()));
                            case "note" -> item.setNote(change.after());
                            default -> throw new BizException(ErrorCode.ADJUSTMENT_INVALID,
                                    "不支持修改字段：" + change.field());
                        }
                    }
                    itemMapper.updateById(item);
                    affected.add(item.getDayId());
                    updated++;
                }
                case MOVE_ITEM -> {
                    TravelItem item = requireItem(op.itemId());
                    TravelDay target = days.get(op.toDayIndex());
                    Long sourceDayId = item.getDayId();
                    item.setDayId(target.getId());
                    item.setItemOrder(nextOrder(target.getId()));
                    itemMapper.updateById(item);
                    // 源天少了这一项、目标天多了一项，两边都要重排
                    affected.add(sourceDayId);
                    affected.add(target.getId());
                    moved++;
                }
                case REORDER_ITEM -> {
                    TravelDay day = days.get(op.dayIndex());
                    reindexTo(day.getId(), op.orderedItemIds());
                    affected.add(day.getId());
                    reordered++;
                }
                case UPDATE_DAY -> {
                    TravelDay day = days.get(op.dayIndex());
                    for (AdjustmentOps.Change change : op.changes()) {
                        if ("theme".equals(change.field())) {
                            day.setTheme(change.after());
                        } else if ("city".equals(change.field())) {
                            day.setCity(change.after());
                        }
                    }
                    dayMapper.updateById(day);
                    dayUpdated++;
                }
            }
        }

        // 增删移都会让日内序号出现空洞或越界，统一重排；重排过的天再走一遍是幂等的
        for (Long dayId : affected) {
            reindex(dayId);
        }
        for (Long dayId : affected) {
            assertDayConsistent(dayId);
        }

        int delta = added * FACT_FIELDS_PER_ITEM - removed * FACT_FIELDS_PER_ITEM;
        log.info("调整落地 planId={} add={} remove={} update={} move={} reorder={} day={} clearedProgress={}",
                plan.getId(), added, removed, updated, moved, reordered, dayUpdated, cleared);
        return new Result(added, removed, updated, moved, reordered, dayUpdated, cleared, delta);
    }

    /** 按当前库里的顺序重排：先读出来，再统一写回，避免「边读边改」读到中间态 */
    private void reindex(Long dayId) {
        List<Long> ids = itemMapper.selectList(Wrappers.<TravelItem>lambdaQuery()
                        .eq(TravelItem::getDayId, dayId)
                        .orderByAsc(TravelItem::getItemOrder))
                .stream().map(TravelItem::getId).toList();
        reindexTo(dayId, ids);
    }

    /**
     * 按给定顺序重写日内序号。
     * <p>
     * 两步走是必须的：第一步把整天的序号平移到负数区间，腾出 1..n；第二步按目标顺序逐个写回。
     * 一步到位会与 {@code uk_day_item(day_id, item_order, deleted)} 撞键。
     */
    private void reindexTo(Long dayId, List<Long> orderedIds) {
        if (orderedIds.isEmpty()) {
            return;
        }
        itemMapper.update(null, Wrappers.<TravelItem>lambdaUpdate()
                .eq(TravelItem::getDayId, dayId)
                .setSql("item_order = item_order - " + ORDER_SHIFT));
        for (int i = 0; i < orderedIds.size(); i++) {
            itemMapper.update(null, Wrappers.<TravelItem>lambdaUpdate()
                    .eq(TravelItem::getId, orderedIds.get(i))
                    .set(TravelItem::getItemOrder, i + 1));
        }
    }

    private int nextOrder(Long dayId) {
        List<TravelItem> items = itemMapper.selectList(Wrappers.<TravelItem>lambdaQuery()
                .eq(TravelItem::getDayId, dayId));
        return items.stream().mapToInt(i -> i.getItemOrder() == null ? 0 : i.getItemOrder()).max().orElse(0) + 1;
    }

    private TravelItem requireItem(Long itemId) {
        TravelItem item = itemMapper.selectById(itemId);
        if (item == null) {
            throw new BizException(ErrorCode.PLAN_ITEM_NOT_FOUND);
        }
        return item;
    }

    /**
     * 应用后的一致性检查。
     * <p>
     * 校验器只看得见「单条 op」，看不见组合后的结果：把开始时间提前到 08:00、结束时间保持在 07:30，
     * 两条 op 各自合法，合起来却是一条时间倒流的行程。所以必须在写完之后整体看一遍，
     * 不通过就整批回滚——宁可让用户重说一遍，也不能把矛盾数据落进正式计划。
     */
    private void assertDayConsistent(Long dayId) {
        List<TravelItem> items = itemMapper.selectList(Wrappers.<TravelItem>lambdaQuery()
                .eq(TravelItem::getDayId, dayId)
                .orderByAsc(TravelItem::getItemOrder));
        List<String> problems = new ArrayList<>();
        for (TravelItem item : items) {
            if (item.getTitle() == null || item.getTitle().isBlank()) {
                problems.add("有一条行程项没有标题");
            }
            LocalTime start = item.getStartTime();
            LocalTime end = item.getEndTime();
            if (start != null && end != null && !start.isBefore(end)) {
                problems.add("「" + item.getTitle() + "」的开始时间不早于结束时间");
            }
        }
        if (!problems.isEmpty()) {
            throw new BizException(ErrorCode.ADJUSTMENT_INVALID, String.join("；", problems));
        }
    }

    private LocalTime parseTime(String text) {
        if (text == null || text.isBlank()) {
            return null;
        }
        return LocalTime.parse(text.length() == 5 ? text : text.substring(0, 5));
    }
}
