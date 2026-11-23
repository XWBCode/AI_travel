package com.aitravel.modules.idempotency.mapper;

import com.aitravel.modules.idempotency.entity.IdempotencyRecord;
import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Param;

import java.time.LocalDateTime;

public interface IdempotencyMapper extends BaseMapper<IdempotencyRecord> {

    /**
     * 物理删除过期记录。
     *
     * <p><b>不能用 {@code mapper.delete(...)}</b>：{@link IdempotencyRecord} 继承了带
     * {@code @TableLogic} 的基类，那会走逻辑删除，只把 {@code deleted} 置 1。
     * 而唯一键 {@code uk_idem_user_request(user_id, request_id)} 不包含 {@code deleted}——
     * 行还在，唯一键就还占着，清理等于没做，表照涨不误。
     *
     * <p>幂等记录是纯技术表，没有审计价值（失败原因已进日志），物理删除才是正确语义。
     * 只删终态（SUCCESS / FAILED）：PROCESSING 停留超过保留期说明有请求卡死，
     * 删掉会让一次本该被拦住的重复请求重新执行。
     */
    @Delete("DELETE FROM t_idempotency WHERE create_time < #{before} AND status IN ('SUCCESS','FAILED')")
    int purgeBefore(@Param("before") LocalDateTime before);
}
