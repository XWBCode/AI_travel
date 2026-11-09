package com.aitravel.modules.plan.entity;

import com.aitravel.common.mybatis.BaseEntity;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 交通段（04 §4.4）。
 * <p>
 * Q-04：用 {@code scope} 区分两类，两组字段互斥，约束由 Service 层保证：
 * <ul>
 *   <li>INTRA_DAY：{@code dayId} + {@code fromItemId/toItemId} 非空，跨城四字段为 null</li>
 *   <li>INTER_CITY：{@code fromDayIndex/toDayIndex/fromCity/toCity} 非空，其余为 null</li>
 * </ul>
 * {@code duration / distance} 在 L0 恒为 null，这是防止 AI 编造「车程 30 分钟」的技术落点。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("t_transport_segment")
public class TransportSegment extends BaseEntity {

    private Long planId;

    /** INTRA_DAY 时非空 */
    private Long dayId;

    /** INTRA_DAY / INTER_CITY */
    private String scope;

    private Long fromItemId;

    private Long toItemId;

    private Integer fromDayIndex;

    private Integer toDayIndex;

    private String fromCity;

    private String toCity;

    /** 步行 / 公交 / 城际铁路等一般性建议 */
    private String mode;

    /** JSON：FactField&lt;number&gt;，L0 恒为 null */
    private String duration;

    /** JSON：FactField&lt;number&gt;，L0 恒为 null */
    private String distance;

    /** 不得含具体数值 */
    private String advice;
}
