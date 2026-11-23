package com.aitravel.support;

import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import org.apache.ibatis.builder.MapperBuilderAssistant;

/**
 * 给纯 Mockito 单测补上 MyBatis-Plus 的实体元信息（BUG-017）。
 *
 * <p><b>为什么需要它</b>：{@code Wrappers.<X>lambdaUpdate()} 这类 Lambda 条件构造器要靠
 * {@code TableInfo} 缓存才能把方法引用翻译成列名。这份缓存平时由「扫到 mapper 的 Spring
 * 上下文启动」建立；而纯单测（{@code mock(XxxMapper.class)}）不会启动上下文，于是抛：
 *
 * <pre>
 * MybatisPlusException: can not find lambda cache for this entity [...]
 * </pre>
 *
 * <p>症状很有欺骗性：**单跑必红，全跑可能绿**——只要先跑过任何会启动上下文的测试类，
 * 静态缓存就被建好了。这正是 BUG-015「本地红 / CI 绿」的同一形态，
 * 所以这里显式初始化，让每个测试类都能独立运行，不再依赖执行顺序。
 *
 * <p>用法：在测试类里
 * <pre>
 * &#64;BeforeAll
 * static void initTableInfo() {
 *     MybatisTableInfo.init(IdempotencyRecord.class);
 * }
 * </pre>
 */
public final class MybatisTableInfo {

    private MybatisTableInfo() {
    }

    /**
     * 初始化实体的表元信息。重复调用是幂等的（{@code TableInfoHelper} 自带缓存，
     * 同实体多次初始化只会覆盖同一个 key）。
     */
    public static void init(Class<?>... entities) {
        MapperBuilderAssistant assistant = new MapperBuilderAssistant(new MybatisConfiguration(), "");
        for (Class<?> entity : entities) {
            TableInfoHelper.initTableInfo(assistant, entity);
        }
    }
}
