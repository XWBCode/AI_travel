package com.aitravel.common.config;

import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

import java.util.Arrays;
import java.util.List;
import java.util.Locale;

/**
 * 天气数据源的合规闸门：生产环境禁止 {@code WEATHER_PROVIDER=open-meteo}。
 *
 * <p>这不是代码风格问题，是许可条款问题。Open-Meteo 免费端点明确禁止商业使用，
 * 数据以 CC BY 4.0 发布（须署名）。仓库里保留它只是为了 CI / 本地无凭据联调。</p>
 *
 * <p>为什么必须让进程<strong>起不来</strong>：这类违规的特点是<strong>没有任何用户可见的异常</strong>。
 * 天气照常显示、温度看起来也对，只有我们知道在用无授权的免费端点。
 * 告警会被忽略、日志会被跳过，只有启动失败不会被忽略。</p>
 *
 * <p>判定规则与 Python 侧 {@code app/tools/weather_policy.py} 保持逐条一致：
 * <strong>只有显式处于 dev / test 才允许 open-meteo</strong>；
 * 其余一切取值（prod、拼错的 prd、其他自定义 profile）一律拒绝。
 * 不做「看不懂就当 dev」——默认放行的开关迟早会被绕过。</p>
 *
 * <p>已知边界（写出来而不是假装没有）：{@code application.yml} 里
 * {@code spring.profiles.active} 默认是 {@code dev}，所以「生产部署忘了设
 * {@code SPRING_PROFILES_ACTIVE=prod}」时本闸门不会触发。这种情况下
 * {@code application-dev.yml} 会生效，它会去连本机 MySQL 并使用开发默认值，
 * 在生产机器上会直接连不上库而启动失败——失败点不同，但同样是起不来。
 * 真正兜底的是发布检查清单，不是这一行判断。</p>
 */
@Component
public class WeatherProviderGuard {

    /** 允许 Open-Meteo 的环境，必须显式处于其中 */
    static final List<String> ENVS_ALLOWING_OPEN_METEO = List.of("dev", "test");

    /** provider 的不同写法统一到注册表用的那个，防止换个拼法绕过检查 */
    private static final List<String> OPEN_METEO_ALIASES =
            List.of("open-meteo", "open_meteo", "openmeteo", "open meteo");

    private static final String PROD_PROVIDER_HINT = "qweather";

    public WeatherProviderGuard(WeatherProperties properties, Environment environment) {
        // 构造期校验：Bean 创建失败即上下文启动失败，不会出现「服务已起来、只是天气不对」
        assertAllowed(activeProfiles(environment), properties.getProvider());
    }

    /**
     * 取当前生效的全部 profile。
     *
     * <p>这里取<strong>全部</strong>而不是第一个，是因为 {@code SPRING_PROFILES_ACTIVE}
     * 允许逗号分隔多值。若只取第一个，{@code SPRING_PROFILES_ACTIVE=metrics,prod}
     * 会被判成 metrics 从而放行——这是个真实可用的绕过路径。</p>
     */
    static List<String> activeProfiles(Environment environment) {
        String[] profiles = environment.getActiveProfiles();
        if (profiles == null || profiles.length == 0) {
            return List.of();
        }
        return Arrays.stream(profiles)
                .map(WeatherProviderGuard::normalize)
                .filter(p -> !p.isEmpty())
                .toList();
    }

    static String normalize(String value) {
        return value == null ? "" : value.trim().toLowerCase(Locale.ROOT);
    }

    static String canonicalProvider(String provider) {
        return normalize(provider);
    }

    static boolean isOpenMeteo(String provider) {
        return OPEN_METEO_ALIASES.contains(canonicalProvider(provider));
    }

    /**
     * 是否允许 Open-Meteo：至少有一个 profile，且<strong>全部</strong>落白名单内。
     *
     * <p>「全部」而不是「任一」是刻意的：只要混进一个 prod，这台机器的身份就不干净了，
     * 不该因为同时还写着 dev 就放行。（逗号分隔的多 profile 是真实写法，
     * 只取第一个就能绕过。）</p>
     *
     * <p>入参在这里自行归一化，不依赖调用方先 trim/lower——这个方法对外可调用，
     * 「调用方一定会先归一化」这种约定迟早会被某个新调用点打破。</p>
     */
    static boolean openMeteoAllowed(List<String> profiles) {
        if (profiles == null || profiles.isEmpty()) {
            return false;
        }
        List<String> normalized = profiles.stream()
                .map(WeatherProviderGuard::normalize)
                .filter(p -> !p.isEmpty())
                .toList();
        return !normalized.isEmpty() && normalized.stream().allMatch(ENVS_ALLOWING_OPEN_METEO::contains);
    }

    static void assertAllowed(List<String> profiles, String provider) {
        if (!isOpenMeteo(provider)) {
            return;
        }
        if (openMeteoAllowed(profiles)) {
            return;
        }
        throw new IllegalStateException(policyMessage(profiles, provider));
    }

    static String policyMessage(List<String> profiles, String provider) {
        String where = (profiles == null || profiles.isEmpty())
                ? "未声明(SPRING_PROFILES_ACTIVE 为空)"
                : String.join(",", profiles);
        return "配置被拒绝：天气数据源 \"" + provider + "\" 只能在 "
                + String.join(" / ", ENVS_ALLOWING_OPEN_METEO) + " 环境使用。"
                + "\n  当前环境：" + where
                + "\n  原因：Open-Meteo 免费端点禁止商业使用（数据 CC BY 4.0，须署名），"
                + "生产使用会违反其条款；且这种违规不会产生任何用户可见的异常，"
                + "所以只能在启动期阻断。"
                + "\n  生产请设 WEATHER_PROVIDER=" + PROD_PROVIDER_HINT + "（和风天气）。"
                + "\n  本地联调请显式设 SPRING_PROFILES_ACTIVE=dev 或 test。";
    }

    /** 仅用于自检：当前配置下是否会被本闸门拒绝 */
    public static boolean wouldReject(List<String> profiles, String provider) {
        return isOpenMeteo(provider) && !openMeteoAllowed(profiles);
    }

    /** 便于日志与运维排查时确认判定依据 */
    public static List<String> allowedEnvs() {
        return ENVS_ALLOWING_OPEN_METEO;
    }
}
