package com.aitravel.modules.idempotency.job;

import com.aitravel.modules.idempotency.mapper.IdempotencyMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;

/**
 * 幂等记录的过期清理（V8 / 发布条件第 3 条）。
 *
 * <p>为什么要清：{@code (user_id, request_id)} 是唯一键，行只增不减。
 * 每个用户每发一条消息就写一行，一年下来是千万级，唯一键索引会越来越厚，
 * 每次插入的代价都在涨——而其中 99.9% 的行永远不会再被查询（前端不会拿一个月前的 ID 重试）。
 *
 * <p>为什么是 30 天：幂等窗口只需要覆盖「用户可能重试」的时间，最极端的情况是断网一天后重开页面。
 * 30 天留了两个数量级的余量，同时又保证表不会无限长。
 *
 * <p>为什么只删 SUCCESS / FAILED：PROCESSING 理论上不该停留几周，真出现了说明有卡死的请求，
 * 那种行删掉会让一次本该被拦住的重复请求重新执行。宁可留一条脏数据，也不放开这个口子。
 *
 * <p>多实例并行执行是安全的：DELETE ... WHERE create_time &lt; ? 本身幂等，
 * 两台机器同时跑最多是其中一台删 0 行。所以不需要分布式锁，也不该为一个日更任务引入调度中心。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class IdempotencyCleanupJob {

    /** 保留窗口：30 天 */
    static final int RETENTION_DAYS = 30;

    private final IdempotencyMapper mapper;

    /** 每天 03:30 清一次（业务低峰，且避开整点可能存在的其它任务） */
    @Scheduled(cron = "0 30 3 * * ?")
    public void cleanup() {
        purge(RETENTION_DAYS);
    }

    /** 拆出来是为了让测试能直接调（不必等到凌晨三点，也不必操纵系统时钟） */
    public int purge(int retentionDays) {
        LocalDateTime before = LocalDateTime.now().minusDays(retentionDays);
        int deleted = mapper.purgeBefore(before);
        if (deleted > 0) {
            log.info("幂等记录清理完成 before={} deleted={}", before, deleted);
        }
        return deleted;
    }
}
