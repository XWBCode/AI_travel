package com.aitravel.common.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 流式对话线程池。
 * <p>
 * 为什么必须异步：Spring MVC 的 {@code SseEmitter} 只有在控制器方法<b>返回之后</b>才会被初始化，
 * 方法内同步推送的事件会先进入 earlySendAttempts 缓冲（且是 Set 语义，重复内容会被去重），
 * 等到方法返回时一次性刷出——用户看到的将是「转圈很久，然后整段文字突然出现」，等于没有流式。
 * 因此把读取 AI 流的动作交给独立线程，控制器立刻返回 emitter。
 */
@Configuration
public class AiStreamConfig {

    @Bean(name = "aiStreamExecutor", destroyMethod = "shutdown")
    public ExecutorService aiStreamExecutor() {
        AtomicInteger seq = new AtomicInteger(1);
        ThreadFactory factory = r -> {
            Thread t = new Thread(r, "ai-stream-" + seq.getAndIncrement());
            t.setDaemon(true);
            return t;
        };
        // 生成类请求耗时长（最长 180s），池子给小一点，避免被慢请求打满；并发上限后续由限流组件接管
        return Executors.newFixedThreadPool(8, factory);
    }
}
