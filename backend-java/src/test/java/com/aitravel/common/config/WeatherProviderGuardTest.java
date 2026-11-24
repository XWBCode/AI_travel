package com.aitravel.common.config;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.mock.env.MockEnvironment;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 数据源合规闸门：环境 × provider 的完整组合。
 *
 * <p>这类测试的价值不在"跑通"，而在于<strong>锁住不允许的组合</strong>。
 * 如果哪天有人为了让本地方便，把白名单改成"永远放行"，这里必须红。</p>
 *
 * <p>每条"应当拒绝"的用例都断言了消息关键词，而不是只断言"抛了异常"——
 * 只断言抛异常的话，任何空指针也会算通过，那就等于没测。</p>
 */
class WeatherProviderGuardTest {

    private static WeatherProperties props(String provider) {
        WeatherProperties p = new WeatherProperties();
        p.setProvider(provider);
        return p;
    }

    /** 用真实 Spring Environment 语义构造闸门（而不是手工传字符串），确认 profile 识别链路是通的 */
    private static WeatherProviderGuard guard(String... activeProfiles) {
        MockEnvironment env = new MockEnvironment();
        env.setActiveProfiles(activeProfiles);
        return new WeatherProviderGuard(props("open-meteo"), env);
    }

    private static List<String> profiles(String... values) {
        return List.of(values);
    }

    @Nested
    @DisplayName("允许的组合：必须能启动")
    class Allowed {

        @ParameterizedTest(name = "环境 {0} 允许 Open-Meteo")
        @ValueSource(strings = {"dev", "test"})
        void openMeteoAllowedInDevAndTest(String profile) {
            assertDoesNotThrow(() -> guard(profile));
        }

        @ParameterizedTest(name = "环境 {0} 下 open-meteo 的各种写法都认")
        @ValueSource(strings = {"open-meteo", "OPEN_METEO", "Open-Meteo", "openmeteo", " open-meteo "})
        void aliasVariantsAllowedInDev(String provider) {
            assertDoesNotThrow(() -> WeatherProviderGuard.assertAllowed(profiles("dev"), provider));
        }

        @Test
        @DisplayName("dev 与 test 同时激活也允许（两者都在白名单内）")
        void devAndTestTogetherAllowed() {
            assertDoesNotThrow(() -> WeatherProviderGuard.assertAllowed(profiles("dev", "test"), "open-meteo"));
        }

        @ParameterizedTest(name = "环境 [{0}] 下 qweather 必须能启动")
        @ValueSource(strings = {"dev", "test", "prod", "staging"})
        void qweatherNeverBlocked(String env) {
            assertDoesNotThrow(() -> WeatherProviderGuard.assertAllowed(profiles(env), "qweather"));
        }

        @ParameterizedTest(name = "环境 [{0}] 下 none 必须能启动")
        @ValueSource(strings = {"dev", "test", "prod", "staging"})
        void noneNeverBlocked(String env) {
            assertDoesNotThrow(() -> WeatherProviderGuard.assertAllowed(profiles(env), "none"));
        }

        @Test
        @DisplayName("provider 为空/null 视为未启用，不阻断启动")
        void blankProviderNotBlocked() {
            assertDoesNotThrow(() -> WeatherProviderGuard.assertAllowed(profiles("prod"), ""));
            assertDoesNotThrow(() -> WeatherProviderGuard.assertAllowed(profiles("prod"), null));
        }
    }

    @Nested
    @DisplayName("拒绝的组合：必须起不来")
    class Rejected {

        @ParameterizedTest(name = "环境 [{0}] 禁止 Open-Meteo")
        @ValueSource(strings = {"prod", "production", "PRD", "prd", "staging", "uat"})
        void openMeteoRejectedOutsideDevAndTest(String env) {
            IllegalStateException ex = assertThrows(IllegalStateException.class,
                    () -> WeatherProviderGuard.assertAllowed(profiles(env), "open-meteo"));
            assertTrue(ex.getMessage().contains("Open-Meteo"), "消息必须点出被拒的数据源");
            assertTrue(ex.getMessage().contains("dev / test"), "消息必须点出允许的环境");
        }

        @ParameterizedTest(name = "环境 prod 下写法 [{0}] 也拦得住")
        @ValueSource(strings = {"open-meteo", "OPEN_METEO", "Open-Meteo", "openmeteo", " open meteo "})
        void aliasVariantsRejectedInProd(String provider) {
            assertThrows(IllegalStateException.class,
                    () -> WeatherProviderGuard.assertAllowed(profiles("prod"), provider));
        }

        @Test
        @DisplayName("真实 Spring 上下文：profile=prod + open-meteo → 构造即失败")
        void prodProfileFailsConstruction() {
            IllegalStateException ex = assertThrows(IllegalStateException.class, () -> guard("prod"));
            assertTrue(ex.getMessage().contains("prod"));
        }

        @Test
        @DisplayName("多 profile 混入 prod 也不能放行（metrics,prod 这类写法是真实绕过路径）")
        void mixedProfilesWithProdRejected() {
            assertThrows(IllegalStateException.class, () -> guard("metrics", "prod"));
            assertThrows(IllegalStateException.class, () -> guard("dev", "prod"));
            assertFalse(WeatherProviderGuard.openMeteoAllowed(profiles("dev", "prod")));
        }

        @Test
        @DisplayName("报错带出实际读到的环境值，运维不用猜")
        void messageCarriesActualEnv() {
            IllegalStateException ex = assertThrows(IllegalStateException.class,
                    () -> WeatherProviderGuard.assertAllowed(profiles("prd"), "open-meteo"));
            assertTrue(ex.getMessage().contains("prd"), "必须显示拼错的值本身，而不是只说不允许");
        }

        @Test
        @DisplayName("没有激活 profile 时按未声明处理，不放行")
        void noActiveProfileIsNotAllowed() {
            assertFalse(WeatherProviderGuard.openMeteoAllowed(List.of()));
            assertThrows(IllegalStateException.class, () -> guard());
        }
    }

    @Nested
    @DisplayName("判定依据本身")
    class Policy {

        @Test
        @DisplayName("fail-closed：默认不放行")
        void failClosedByDefault() {
            assertFalse(WeatherProviderGuard.openMeteoAllowed(null));
            assertFalse(WeatherProviderGuard.openMeteoAllowed(List.of()));
            assertTrue(WeatherProviderGuard.openMeteoAllowed(profiles("dev")));
        }

        @Test
        @DisplayName("大小写与空白被归一化")
        void profileNormalized() {
            assertTrue(WeatherProviderGuard.openMeteoAllowed(profiles(" DEV ")));
        }

        @Test
        @DisplayName("activeProfiles 收集全部而不只是第一个")
        void activeProfilesCollectsAll() {
            MockEnvironment env = new MockEnvironment();
            env.setActiveProfiles("dev", "metrics");
            assertEquals(List.of("dev", "metrics"), WeatherProviderGuard.activeProfiles(env));

            MockEnvironment none = new MockEnvironment();
            assertEquals(List.of(), WeatherProviderGuard.activeProfiles(none), "未激活时返回空列表而不是默认 dev");
        }

        @Test
        @DisplayName("未知 provider 不在这里被猜成已知源")
        void unknownProviderNotGuessed() {
            assertEquals("qweather-v2", WeatherProviderGuard.canonicalProvider("QWeather-V2"));
            assertFalse(WeatherProviderGuard.isOpenMeteo("qweather-v2"));
        }
    }
}
