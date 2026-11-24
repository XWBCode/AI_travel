package com.aitravel.modules.plan.service;

import com.aitravel.common.config.WeatherProperties;
import com.aitravel.modules.chat.service.AiServiceClient;
import com.aitravel.modules.plan.dto.PlanWeatherVO;
import com.aitravel.modules.plan.entity.TravelPlan;
import com.fasterxml.jackson.databind.JsonNode;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeParseException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 计划天气展示（M3-1）：编排 + 缓存 + 降级。
 *
 * <p>这一层<b>不碰生成链路</b>——不改提示词、不改校验器、不改状态机、不占 SSE 线程池。
 * 天气是计划落库之后的附加展示，所以它的失败必须被限制在一个接口里。
 *
 * <p>三条设计决定，都不是随手写的：
 *
 * <ol>
 *   <li><b>降级 = 200 + available=false</b>，不用 4xx/5xx。天气缺失不是请求错误，
 *       用错误码会让前端把整页当失败，也会污染错误率监控。</li>
 *   <li><b>没有出发日期就不查</b>。计划里 {@code basic.startDate} 经常为 null，
 *       此时拿「今天」顶替就等于给用户看了一个月后的错觉天气——那是看起来最像真数据的假信息。
 *       所以直接 {@code NO_START_DATE}，让前端引导补日期。</li>
 *   <li><b>过期先给旧值</b>。缓存过期且上游这一刻不可用时，返回上次成功的数据并标
 *       {@code stale=true}，而不是把已经能显示的内容换成一句「暂不可用」。</li>
 * </ol>
 *
 * <p>缓存是进程内的，所以它是<b>单机状态</b>：多实例部署时每台各存一份，命中率下降但不会出错
 * （与 R7 §6.3 同一类问题，本切片不解决，也不为此引入 Redis）。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class PlanWeatherService {

    /** 预报窗口上限，与 Python 侧、前端文案三处一致；超出的日期只能等（和风天气官方 10 天） */
    private static final int MAX_FORECAST_DAYS = 10;

    /** 跨天失效边界：次日 00:10。官方缓存文档点名"跨天不刷新会整体错一天" */
    private static final LocalTime CROSS_DAY_CUTOFF = LocalTime.of(0, 10);

    /**
     * 缓存键的自然日按哪个时区算。
     * <p>
     * 必须与 Python 侧（固定 +08:00）一致，不能取服务器默认时区：否则一台 UTC 的机器
     * 会在这里提前/推迟 8 小时换 key，跨天表现和本地开发完全不一样。
     */
    private static final ZoneId CN_ZONE = ZoneId.of("Asia/Shanghai");

    private final PlanStoreService store;
    private final AiServiceClient aiServiceClient;
    private final WeatherProperties properties;

    /**
     * LRU：访问顺序 + 上限淘汰，避免长期运行内存随计划数增长。
     * <p>
     * 匿名类只在 {@link #putCached} 时才读 {@code properties}，而那时依赖已注入完毕；
     * 不要把「读上限」搬进字段初始化阶段，否则会 NPE。
     */
    private final Map<String, Cached> cache = new LinkedHashMap<>(64, 0.75f, true) {
        @Override
        protected boolean removeEldestEntry(Map.Entry<String, Cached> eldest) {
            return size() > properties.getCacheMaxEntries();
        }
    };

    /** 同 key 并发合并：用户狂点刷新时只打一次上游，不消耗别人给的免费额度 */
    private final Map<String, CompletableFuture<PlanWeatherVO>> inFlight = new ConcurrentHashMap<>();

    private record Cached(PlanWeatherVO value, Instant expiresAt) {
    }

    /**
     * 查询某个计划的天气。
     *
     * @throws com.aitravel.common.exception.BizException 计划不存在（30101）或不属于当前用户（403）
     */
    public PlanWeatherVO weather(Long planId, Long userId, String traceId) {
        // 归属校验放在最前面：越权请求不能拿到任何天气数据，也不能因为「配置关了」而返回不同结果
        TravelPlan plan = store.loadOwned(planId, userId);

        if (!properties.isEnabled()) {
            return PlanWeatherVO.unavailable("PROVIDER_DISABLED", "天气功能未开启");
        }
        if (plan.getStartDate() == null) {
            // 刻意不兜底成「今天」（设计稿 §4.3 方案 C）
            return PlanWeatherVO.unavailable("NO_START_DATE", "补充出发日期后可查看天气");
        }
        String city = plan.getDestination();
        if (city == null || city.isBlank()) {
            return PlanWeatherVO.unavailable("NO_CITY", "计划里还没有城市，无法查询天气");
        }

        int days = clamp(plan.getDays() == null ? 1 : plan.getDays(), 1, MAX_FORECAST_DAYS);
        // key 带日期、天数与「请求时的自然日」：计划改了日期或天数自动失效，
        // 跨过零点也会换 key —— 这样 23:50 缓存的「明天起 3 天」在次日不会再被当成最新
        String key = cacheKey(planId, plan.getStartDate(), days, city, LocalDate.now(CN_ZONE));

        Cached cached = getCached(key);
        if (cached != null && cached.expiresAt().isAfter(Instant.now())) {
            log.debug("天气缓存命中 planId={} key={}", planId, key);
            return cached.value();
        }

        PlanWeatherVO fresh = loadWithSingleFlight(key, plan, city, days, traceId);
        if (fresh.isAvailable()) {
            putCached(key, fresh);
            return fresh;
        }
        if (cached != null) {
            // 上游这次没成，但手上有旧值：给旧值 + 标记，比给一句「暂不可用」有用
            log.info("天气刷新失败，回退到旧值 planId={} reason={}", planId, fresh.getReason());
            return staleOf(cached.value(), fresh);
        }
        return fresh;
    }

    private PlanWeatherVO loadWithSingleFlight(String key, TravelPlan plan, String city, int days, String traceId) {
        CompletableFuture<PlanWeatherVO> mine = new CompletableFuture<>();
        CompletableFuture<PlanWeatherVO> running = inFlight.putIfAbsent(key, mine);
        if (running != null) {
            try {
                return running.join();
            } catch (CompletionException e) {
                // 领跑者异常了，自己也走一遍：降级路径很便宜，比把异常抛给用户好
                log.debug("合并的天气请求失败，改为自行查询: {}", e.getMessage());
            }
        }
        try {
            PlanWeatherVO vo = load(plan, city, days, traceId);
            mine.complete(vo);
            return vo;
        } catch (RuntimeException e) {
            mine.completeExceptionally(e);
            throw e;
        } finally {
            inFlight.remove(key, mine);
        }
    }

    private PlanWeatherVO load(TravelPlan plan, String city, int days, String traceId) {
        String startDate = plan.getStartDate() == null ? null : plan.getStartDate().toString();
        JsonNode root = aiServiceClient.fetchWeather(city, startDate, days, traceId);
        if (root == null) {
            // 网络层失败（连不上/超时/非 200）：对用户而言就是"暂不可用"，原因写日志
            log.warn("天气服务不可用 planId={}", plan.getId());
            return PlanWeatherVO.unavailable("UPSTREAM_ERROR", "天气暂不可用");
        }
        if (!root.path("ok").asBoolean(false)) {
            return PlanWeatherVO.unavailable(
                    text(root, "reason", "UPSTREAM_ERROR"),
                    text(root, "message", "天气暂不可用"));
        }
        return toVo(root);
    }

    private PlanWeatherVO toVo(JsonNode root) {
        PlanWeatherVO vo = new PlanWeatherVO();
        vo.setAvailable(true);
        // 兜底值写 NONE 而不是某个 provider 名：默认值写死 "OPEN_METEO"，
        // 换源之后那句默认值就变成一句假话（D-03 实施清单第 8 条）
        vo.setSource(text(root, "source", "NONE"));
        vo.setAttribution(text(root, "attribution", ""));
        vo.setFetchedAt(text(root, "fetchedAt", null));
        vo.setExpiresAt(text(root, "expiresAt", null));
        vo.setNote(text(root, "note", null));
        for (JsonNode node : root.path("days")) {
            PlanWeatherVO.WeatherDayVO day = new PlanWeatherVO.WeatherDayVO();
            day.setDayIndex(node.path("dayIndex").asInt());
            day.setDate(text(node, "date", null));
            day.setCity(text(node, "city", null));
            day.setResolvedCity(text(node, "resolvedCity", null));
            day.setWeather(field(node, "weather"));
            day.setTemperature(field(node, "temperature"));
            day.setPrecipitation(field(node, "precipitation"));
            day.setWind(field(node, "wind"));
            vo.getDays().add(day);
        }
        return vo;
    }

    /** 旧值 + 说明。数据保留（含原始 fetchedAt），只把"这是旧值"这件事说清楚 */
    private PlanWeatherVO staleOf(PlanWeatherVO old, PlanWeatherVO fresh) {
        PlanWeatherVO vo = new PlanWeatherVO();
        vo.setAvailable(true);
        vo.setStale(true);
        vo.setSource(old.getSource());
        vo.setAttribution(old.getAttribution());
        vo.setFetchedAt(old.getFetchedAt());
        vo.setExpiresAt(old.getExpiresAt());
        vo.setDays(old.getDays());
        String reason = "数据为上次成功获取的结果（本次刷新失败：" + fresh.getReason() + "）";
        vo.setNote(old.getNote() == null || old.getNote().isBlank() ? reason : old.getNote() + "；" + reason);
        return vo;
    }

    private JsonNode field(JsonNode node, String name) {
        JsonNode value = node.get(name);
        return value == null || value.isNull() ? null : value;
    }

    private static String text(JsonNode node, String name, String fallback) {
        JsonNode value = node.get(name);
        if (value == null || value.isNull()) {
            return fallback;
        }
        String text = value.asText();
        return text == null || text.isBlank() ? fallback : text;
    }

    private static int clamp(int value, int min, int max) {
        return Math.max(min, Math.min(max, value));
    }

    private Cached getCached(String key) {
        synchronized (cache) {
            return cache.get(key);
        }
    }

    private void putCached(String key, PlanWeatherVO vo) {
        Instant expiresAt = parseExpiry(vo.getExpiresAt());
        if (expiresAt == null) {
            expiresAt = Instant.now().plusSeconds(properties.getCacheTtlMinutes() * 60L);
        }
        // 硬边界：不管上游/配置给多久，跨过次日 00:10 就必须失效。
        // 这一层兜底不信任 Python——版本错配、灰度期间新旧服务混跑都可能让 expiresAt 变长，
        // 而"错一天的天气"在界面上看不出任何异常。
        Instant cutoff = nextDayCutoff();
        if (expiresAt.isAfter(cutoff)) {
            expiresAt = cutoff;
        }
        synchronized (cache) {
            cache.put(key, new Cached(vo, expiresAt));
        }
    }

    /** 下一个"次日 00:10"（东八区）。包级可见是为了让测试直接断言边界，不用等下一天。 */
    static Instant nextDayCutoff() {
        LocalDate tomorrow = LocalDate.now(CN_ZONE).plusDays(1);
        return tomorrow.atTime(CROSS_DAY_CUTOFF).atZone(CN_ZONE).toInstant();
    }

    /** 优先用 Python 给的 expiresAt（TTL 只有一个来源），解析不了才退回本地配置 */
    private static Instant parseExpiry(String text) {
        if (text == null || text.isBlank()) {
            return null;
        }
        try {
            return OffsetDateTime.parse(text).toInstant();
        } catch (DateTimeParseException e) {
            return null;
        }
    }

    /** 仅用于测试/排障：当前缓存条数 */
    int cacheSize() {
        synchronized (cache) {
            return cache.size();
        }
    }

    /** 仅用于测试：清空缓存，避免用例之间互相影响 */
    void clearCache() {
        synchronized (cache) {
            cache.clear();
        }
    }

    /** 仅用于测试：直接注入一条过期缓存，构造"过期 + 上游挂掉"的场景 */
    void seedExpired(PlanWeatherVO vo, Long planId, LocalDate startDate, int days, String city) {
        synchronized (cache) {
            cache.put(
                    cacheKey(planId, startDate, days, city, LocalDate.now(CN_ZONE)),
                    new Cached(vo, Instant.now().minusSeconds(60)));
        }
    }

    /**
     * 缓存键。日期、天数、**请求时的自然日**都进 key，是刻意的：
     * <ul>
     *   <li>计划一改日期，旧值自动不再被命中，不需要在写路径上到处记得清缓存；</li>
     *   <li>跨过零点换 key，这样「23:50 缓存的明天起 3 天」在次日不会被当成最新
     *       （和风天气官方缓存文档点名的坑：整体错一天）。</li>
     * </ul>
     */
    static String cacheKey(Long planId, LocalDate startDate, int days, String city, LocalDate requestDate) {
        return planId + "|" + startDate + "|" + days + "|" + city + "|" + requestDate;
    }

    /**
     * 供控制器/测试复用的降级原因清单，防止前后端文案对不上。
     *
     * <p>{@code UPSTREAM_ERROR} 与 {@code CIRCUIT_OPEN} 是两件事，不能合并：前者是这次真的发了请求、
     * 上游失败；后者是熔断器开着、这次根本没发请求，属于主动拒绝。判据只有「有没有出网」，
     * 不能用耗时判断——两者都是毫秒级返回，从外部完全同形。
     */
    public static List<String> reasons() {
        return List.of("PROVIDER_DISABLED", "NO_START_DATE", "NO_CITY", "CITY_UNKNOWN",
                "DATE_OUT_OF_RANGE", "UPSTREAM_TIMEOUT", "UPSTREAM_ERROR", "CIRCUIT_OPEN");
    }
}
