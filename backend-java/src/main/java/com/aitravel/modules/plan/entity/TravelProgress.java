package com.aitravel.modules.plan.entity;

import com.baomidou.mybatisplus.annotation.FieldFill;
import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 行程执行进度（04 §5 / V5）。
 *
 * <p>刻意<b>不继承</b> {@code BaseEntity}：
 * <ul>
 *   <li>没有 {@code deleted}——取消勾选就是物理删除。逻辑删除会与 {@code uk(plan_id,item_id)}
 *       打架：取消后再勾选会撞唯一键，而把 deleted 放进唯一键又会在第二次取消时撞历史行；</li>
 *   <li>没有 {@code update_time}——这行数据只有「插入」与「删除」两种命运，不存在中间修改，
 *       留一个永远不变的时间戳只会让人误以为它有意义。</li>
 * </ul>
 */
@Data
@TableName("t_travel_progress")
public class TravelProgress implements Serializable {

    @TableId(type = IdType.ASSIGN_ID)
    private Long id;

    private Long planId;

    private Long itemId;

    private Long userId;

    /** 勾选完成的时间；与行共存亡，不使用 null 表示未完成 */
    private LocalDateTime completedAt;

    @TableField(fill = FieldFill.INSERT)
    private LocalDateTime createTime;
}
