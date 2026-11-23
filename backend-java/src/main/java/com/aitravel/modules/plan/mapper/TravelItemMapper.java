package com.aitravel.modules.plan.mapper;

import com.aitravel.modules.plan.entity.TravelItem;
import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Param;

public interface TravelItemMapper extends BaseMapper<TravelItem> {

    /**
     * 物理删除一条行程项。
     * <p>
     * 不能用 {@code deleteById}：{@code TravelItem} 带 {@code @TableLogic}，走的是逻辑删除。
     * 而 {@code uk_day_item(day_id, item_order, deleted)} 把 {@code deleted} 放进了唯一键——
     * 同一位置删第二次就会撞上第一次留下的 {@code (day, 2, 1)}，报 Duplicate entry。
     * 回滚（{@link PlanRollbackMapper}）踩过同一个坑，这里用同一套解法。
     */
    @Delete("DELETE FROM t_travel_item WHERE id = #{itemId}")
    int deletePhysically(@Param("itemId") Long itemId);
}
