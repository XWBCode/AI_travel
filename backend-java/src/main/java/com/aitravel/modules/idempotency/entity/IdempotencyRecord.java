package com.aitravel.modules.idempotency.entity;

import com.aitravel.common.mybatis.BaseEntity;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 写操作幂等记录（V8 / BUG-007）。
 *
 * <p>它存在的唯一目的：同一用户用同一个 requestId 重复提交时，第二次不再执行业务。
 * 它<b>不参与</b>业务状态判断——删掉这张表，计划本身仍然自洽，只是重试不再安全。
 *
 * <p>为什么要落库而不是用内存 Map：多实例部署下内存 Map 各存各的，等于没做；
 * 而且进程重启后重试就会真的重复执行（真实模型一次 30-45 秒，重启窗口不算小）。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("t_idempotency")
public class IdempotencyRecord extends BaseEntity {

    public static final String PROCESSING = "PROCESSING";
    public static final String SUCCESS = "SUCCESS";
    public static final String FAILED = "FAILED";

    private Long userId;

    /** 客户端生成；同一次提交的所有重试必须复用同一个值 */
    private String requestId;

    /** 操作类型，避免「生成」和「调整」的 ID 撞车 */
    private String action;

    /** 规范化请求体的 SHA-256；同 ID 不同内容必须拒绝，而不是返回旧结果 */
    private String requestHash;

    private String status;

    /** 成功时的结果引用（计划 ID / 建议 ID） */
    private String resultRef;

    private Integer errorCode;
}
