package com.aitravel.common.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.SynchronousQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 流式对话线程池。
 * <p>
 * 为什么必须异步：Spring MVC 的 {@code SseEmitter} 只有在控制器方法<b>返回之后</b>才会被初始化，
 * 方法内同步推送的事件会先进入 earlySendAttempts 缓冲（且是 Set 语义，重复内容会被去重），
 * 等到方法返回时一次性刷出——用户看到的将是「转圈很久，然后整段文字突然出现」，等于没有流式。
 * 因此把读取 AI 流的动作交给独立线程，控制器立刻返回 emitter。
 * <p>
 * 为什么不是 {@code Executors.newFixedThreadPool}：那个工厂方法用的是<b>无界</b>队列，
 * 「满了」这件事永远不会发生，只会悄悄排队。实测 12 并发时最后 4 个请求的墙钟从 132s
 * 变成 298s——没有报错，只是慢了一倍多，用户完全不知道发生了什么。
 * 这里改成有界队列 + 拒绝策略：满了立刻返回 10429，让前端能说「现在请求过多」。
 */
@Configuration
public class AiStreamConfig {

    @Bean(name = "aiStreamExecutor", destroyMethod = "shutdown")
    public ExecutorService aiStreamExecutor(AiServiceProperties properties) {
        AtomicInteger seq = new AtomicInteger(1);
        ThreadFactory factory = r -> {
            Thread t = new Thread(r, "ai-stream-" + seq.getAndIncrement());
            t.setDaemon(true);
            return t;
        };
        int poolSize = Math.max(1, properties.getStreamPoolSize());
        int queueCapacity = Math.max(0, properties.getStreamQueueCapacity());
        // ArrayBlockingQueue 不接受容量 0，所以「不排队」要用 SynchronousQueue 表达：
        // 它没有容量，交接必须当场完成，接不住就交给拒绝策略。
        BlockingQueue<Runnable> queue = queueCapacity == 0
                ? new SynchronousQueue<>()
                : new ArrayBlockingQueue<>(queueCapacity);
        // AbortPolicy 是刻意的：拒绝要发生在「进入队列之前」，这样调用方还能给用户一句人话。
        // 用 CallerRunsPolicy 会把慢任务甩回 Tomcat 线程，表现成整个请求线程被占住。
        return new ThreadPoolExecutor(poolSize, poolSize, 0L, TimeUnit.MILLISECONDS,
                queue, factory, new ThreadPoolExecutor.AbortPolicy());
    }
}
