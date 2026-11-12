package com.aitravel.modules.plan.mapper;

import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

/**
 * 回滚专用：按 plan_id <b>物理删除</b>计划内容（9 张表里的 5 张子表）。
 *
 * <p>为什么不走 MyBatis-Plus 的逻辑删除（{@code deleted=1}）：
 * 子表唯一键把 {@code deleted} 放进了索引，{@code uk_plan_day(plan_id, day_index, deleted)}。
 * 逻辑删除后「同一计划同一天」最多只能删一次——第二次回滚会出现两行
 * {@code (plan_id, day_index, 1)}，直接撞唯一键，回滚失败。
 *
 * <p>物理删除在这里是安全的：回滚前会把「当前内容」写成一份快照，
 * 内容不会真的丢失；而且回滚的语义就是「整体替换」，留一堆 deleted=1 的历史行
 * 只会让详情查询多一层过滤，没有任何收益。
 *
 * <p>行程项没有 plan_id，只能 join 天表删——单独一条 SQL，避免先查 dayIds 再拼 in 列表。
 */
@Mapper
public interface PlanRollbackMapper {

    @Delete("DELETE i FROM t_travel_item i JOIN t_travel_day d ON i.day_id = d.id "
            + "WHERE d.plan_id = #{planId}")
    int deleteItems(@Param("planId") Long planId);

    @Delete("DELETE FROM t_transport_segment WHERE plan_id = #{planId}")
    int deleteSegments(@Param("planId") Long planId);

    @Delete("DELETE FROM t_accommodation WHERE plan_id = #{planId}")
    int deleteAccommodations(@Param("planId") Long planId);

    @Delete("DELETE FROM t_travel_day WHERE plan_id = #{planId}")
    int deleteDays(@Param("planId") Long planId);

    @Delete("DELETE FROM t_budget_item WHERE plan_id = #{planId}")
    int deleteBudgetItems(@Param("planId") Long planId);
}
