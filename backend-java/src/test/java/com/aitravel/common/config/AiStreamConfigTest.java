package com.aitravel.common.config;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertThrows;


/**
 * 流式线程池的容量语义。
 *
 * 这条测试的意义不在「算术」，而在于钉住一个曾经吃亏的行为：
 * {@code Executors.newFixedThreadPool} 用的是无界队列，永远不拒绝，
 * 于是 12 并发时后 4 个请求的墙钟从 132s 变成 298s，用户只看到转圈。
 * 现在改成有界队列 + 拒绝，必须有测试证明「真的会拒绝」。
 */
class AiStreamConfigTest {

    @Test
    @DisplayName("池子与队列都满时必须拒绝，而不是无限排队")
    void rejectsWhenSaturated() throws Exception {
        AiServiceProperties props = new AiServiceProperties();
        props.setStreamPoolSize(1);
        props.setStreamQueueCapacity(1);

        ExecutorService pool = new AiStreamConfig().aiStreamExecutor(props);
        CountDownLatch blocker = new CountDownLatch(1);
        try {
            pool.execute(() -> await(blocker));   // 占住唯一的线程
            pool.execute(() -> await(blocker));   // 进队列
            // 第三个：队列已满，必须立刻抛，而不是等前两个跑完
            assertThrows(RejectedExecutionException.class, () -> pool.execute(() -> {}));
        } finally {
            blocker.countDown();
            pool.shutdownNow();
        }
    }

    @Test
    @DisplayName("默认不排队：8 个并行可接受，第 9 个立刻被拒")
    void defaultCapacityHasNoQueue() throws Exception {
        AiServiceProperties props = new AiServiceProperties();
        ExecutorService pool = new AiStreamConfig().aiStreamExecutor(props);
        CountDownLatch blocker = new CountDownLatch(1);
        try {
            // 队列为 0：前 8 个占满线程，第 9 个必须立刻拒绝（不能排队吃掉 SSE 预算）
            for (int i = 0; i < 8; i++) {
                pool.execute(() -> await(blocker));
            }
            assertThrows(RejectedExecutionException.class, () -> pool.execute(() -> {}));
        } finally {
            blocker.countDown();
            pool.shutdownNow();
        }
    }

    private static void await(CountDownLatch latch) {
        try {
            latch.await(5, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
