package com.aitravel.modules.plan.mapper;

import com.aitravel.modules.plan.entity.TravelProgress;
import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Param;

public interface TravelProgressMapper extends BaseMapper<TravelProgress> {

    /**
     * 幂等勾选：重复点击由 {@code uk(plan_id,item_id)} 吸收，转成一次 UPDATE。
     * <p>
     * 不用「先 select 再 insert」：两个并发请求会同时判定为「不存在」，
     * 然后一个成功、一个撞唯一键报 500。幂等要在数据库层落地，不在应用层猜。
     *
     * @return 1 表示新勾选，2 表示重复勾选（MySQL 对 ON DUPLICATE 的返回值约定）
     */
    @Insert("INSERT INTO t_travel_progress (id, plan_id, item_id, user_id, completed_at, create_time) "
            + "VALUES (#{id}, #{planId}, #{itemId}, #{userId}, #{completedAt}, NOW()) "
            + "ON DUPLICATE KEY UPDATE completed_at = VALUES(completed_at), user_id = VALUES(user_id)")
    int upsert(TravelProgress progress);

    /** 取消勾选：物理删除这一行（见 V5 注释，逻辑删除会与唯一键冲突） */
    @Delete("DELETE FROM t_travel_progress WHERE plan_id = #{planId} AND item_id = #{itemId}")
    int remove(@Param("planId") Long planId, @Param("itemId") Long itemId);
}
