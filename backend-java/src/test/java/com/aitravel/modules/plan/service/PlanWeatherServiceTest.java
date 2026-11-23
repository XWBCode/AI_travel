package com.aitravel.modules.plan.service;

import com.aitravel.common.config.WeatherProperties;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.chat.service.AiServiceClient;
import com.aitravel.modules.plan.dto.PlanWeatherVO;
import com.aitravel.modules.plan.entity.TravelPlan;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDate;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * 天气编排层测试（M3-1，W-2 / W-3 / W-4 / W-5 / W-6）。
 *
 * <p>这里盯的是<b>降级行为</b>而不是「能不能拿到天气」——后者只能靠真实上游验证，
 * 而前者是上线后才最容易出错的部分：
 * <ul>
 *   <li>缓存命中时上游调用次数必须是 0（用 verify(times(1)) 断言，不看日志）；</li>
 *   <li>没有出发日期时一次请求都不该发（否则就是拿别的日期查了天气）；</li>
 *   <li>过期 + 上游挂掉时要给旧值并标 stale，而不是把能显示的内容换成"暂不可用"；</li>
 *   <li>越权请求在任何分支之前就被挡掉，且不触发上游调用。</li>
 * </ul>
 */
@ExtendWith(MockitoExtension.class)
class PlanWeatherServiceTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final Long USER_ID = 7L;
    private static final Long PLAN_ID = 1001L;
    private static final LocalDate START = LocalDate.of(2026, 11, 24);

    @Mock
    private PlanStoreService store;

    @Mock
    private AiServiceClient aiServiceClient;

    private WeatherProperties properties;
    private PlanWeatherService service;

    @BeforeEach
    void setUp() {
        properties = new WeatherProperties();
        properties.setProvider("open-meteo");
        properties.setCacheTtlMinutes(30);
        // 不注入 @InjectMocks：WeatherProperties 是真实对象，走构造器更直观
        service = new PlanWeatherService(store, aiServiceClient, properties);
    }

    private static TravelPlan plan(LocalDate startDate, String destination, Integer days) {
        TravelPlan plan = new TravelPlan();
        plan.setId(PLAN_ID);
        plan.setUserId(USER_ID);
        plan.setTitle("成都三日");
        plan.setDestination(destination);
        plan.setStartDate(startDate);
        plan.setDays(days);
        return plan;
    }

    private JsonNode okJson() throws Exception {
        return MAPPER.readTree("""
                {
                  "ok": true, "source": "OPEN_METEO",
                  "attribution": "Weather data by Open-Meteo.com (CC BY 4.0)",
                  "fetchedAt": "2026-11-23T22:40:00+08:00",
                  "expiresAt": "2099-01-01T00:00:00+08:00",
                  "days": [
                    {"dayIndex": 1, "date": "2026-11-24", "city": "成都", "resolvedCity": "成都",
                     "weather": {"value": "多云", "display": "多云", "source": "TOOL", "verified": true,
                                 "fetchedAt": "2026-11-23T22:40:00+08:00", "expiresAt": "2099-01-01T00:00:00+08:00", "note": ""},
                     "temperature": {"value": 14.0, "display": "12–16℃", "source": "TOOL", "verified": true,
                                 "fetchedAt": "2026-11-23T22:40:00+08:00", "expiresAt": "2099-01-01T00:00:00+08:00", "note": ""},
                     "precipitation": {"value": 6, "display": "降水概率 6%", "source": "TOOL", "verified": true,
                                 "fetchedAt": "2026-11-23T22:40:00+08:00", "expiresAt": "2099-01-01T00:00:00+08:00", "note": ""},
                     "wind": {"value": 18.0, "display": "3级风", "source": "TOOL", "verified": true,
                                 "fetchedAt": "2026-11-23T22:40:00+08:00", "expiresAt": "2099-01-01T00:00:00+08:00", "note": ""}}
                  ]
                }
                """);
    }

    @Test
    @DisplayName("有日期有城市时正常返回，且每天四组天气原样透传")
    void weather_mapsAvailablePayload() throws Exception {
        when(store.loadOwned(PLAN_ID, USER_ID)).thenReturn(plan(START, "成都", 3));
        when(aiServiceClient.fetchWeather(eq("成都"), eq("2026-11-24"), eq(3), any()))
                .thenReturn(okJson());

        PlanWeatherVO vo = service.weather(PLAN_ID, USER_ID, "t-1");

        assertTrue(vo.isAvailable());
        assertFalse(vo.isStale());
        assertEquals("OPEN_METEO", vo.getSource());
        // 署名是 CC BY 4.0 的硬要求，不能因为前端"忘了展示"就从响应里消失
        assertTrue(vo.getAttribution().contains("Open-Meteo"));
        assertEquals(1, vo.getDays().size());
        PlanWeatherVO.WeatherDayVO day = vo.getDays().get(0);
        assertEquals(1, day.getDayIndex());
        assertEquals("12–16℃", day.getTemperature().path("display").asText());
        assertTrue(day.getTemperature().path("verified").asBoolean());
        assertEquals("多云", day.getWeather().path("display").asText());
    }

    @Test
    @DisplayName("30 分钟内二次请求不再打上游（W-4）")
    void weather_servesFromCache() throws Exception {
        when(store.loadOwned(PLAN_ID, USER_ID)).thenReturn(plan(START, "成都", 3));
        when(aiServiceClient.fetchWeather(anyString(), anyString(), anyInt(), any()))
                .thenReturn(okJson());

        service.weather(PLAN_ID, USER_ID, "t-1");
        service.weather(PLAN_ID, USER_ID, "t-2");
        service.weather(PLAN_ID, USER_ID, "t-3");

        verify(aiServiceClient, times(1)).fetchWeather(anyString(), anyString(), anyInt(), any());
    }

    @Test
    @DisplayName("计划改了日期后缓存自动失效（key 带日期）")
    void weather_cacheKeyIncludesDate() throws Exception {
        when(store.loadOwned(PLAN_ID, USER_ID)).thenReturn(plan(START, "成都", 3));
        when(aiServiceClient.fetchWeather(anyString(), anyString(), anyInt(), any()))
                .thenReturn(okJson());
        service.weather(PLAN_ID, USER_ID, "t-1");

        // 用户把出发日期改成 12-01
        when(store.loadOwned(PLAN_ID, USER_ID))
                .thenReturn(plan(LocalDate.of(2026, 12, 1), "成都", 3));
        service.weather(PLAN_ID, USER_ID, "t-2");

        verify(aiServiceClient, times(2)).fetchWeather(anyString(), anyString(), anyInt(), any());
        verify(aiServiceClient).fetchWeather("成都", "2026-12-01", 3, "t-2");
    }

    @Test
    @DisplayName("没有出发日期：直接降级且一次上游请求都不发（W-3）")
    void weather_withoutStartDateNeverCallsUpstream() {
        when(store.loadOwned(PLAN_ID, USER_ID)).thenReturn(plan(null, "成都", 3));

        PlanWeatherVO vo = service.weather(PLAN_ID, USER_ID, "t-1");

        assertFalse(vo.isAvailable());
        assertEquals("NO_START_DATE", vo.getReason());
        // 兜底成"今天"就是给用户看错月份的天气，属于假信息；这里锁死"不许发请求"
        verifyNoInteractions(aiServiceClient);
    }

    @Test
    @DisplayName("provider=none 时全部降级（W-8）")
    void weather_providerDisabledDegrades() {
        properties.setProvider("none");
        when(store.loadOwned(PLAN_ID, USER_ID)).thenReturn(plan(START, "成都", 3));

        PlanWeatherVO vo = service.weather(PLAN_ID, USER_ID, "t-1");

        assertFalse(vo.isAvailable());
        assertEquals("PROVIDER_DISABLED", vo.getReason());
        verifyNoInteractions(aiServiceClient);
    }

    @Test
    @DisplayName("计划没有城市：降级为 NO_CITY，不发请求")
    void weather_withoutCityDegrades() {
        when(store.loadOwned(PLAN_ID, USER_ID)).thenReturn(plan(START, " ", 3));

        PlanWeatherVO vo = service.weather(PLAN_ID, USER_ID, "t-1");

        assertFalse(vo.isAvailable());
        assertEquals("NO_CITY", vo.getReason());
        verifyNoInteractions(aiServiceClient);
    }

    @Test
    @DisplayName("上游连不上：降级为 UPSTREAM_ERROR，不抛异常（W-2）")
    void weather_upstreamUnavailableDegrades() {
        when(store.loadOwned(PLAN_ID, USER_ID)).thenReturn(plan(START, "成都", 3));
        when(aiServiceClient.fetchWeather(anyString(), anyString(), anyInt(), any())).thenReturn(null);

        PlanWeatherVO vo = service.weather(PLAN_ID, USER_ID, "t-1");

        assertFalse(vo.isAvailable());
        assertEquals("UPSTREAM_ERROR", vo.getReason());
        assertNotNull(vo.getMessage());
    }

    @Test
    @DisplayName("Python 给的降级原因原样透传，不在 Java 重新翻译")
    void weather_reasonPassesThrough() throws Exception {
        when(store.loadOwned(PLAN_ID, USER_ID)).thenReturn(plan(START, "成都", 3));
        when(aiServiceClient.fetchWeather(anyString(), anyString(), anyInt(), any()))
                .thenReturn(MAPPER.readTree("""
                        {"ok": false, "reason": "DATE_OUT_OF_RANGE", "message": "出发日期超出预报范围，临近再看"}
                        """));

        PlanWeatherVO vo = service.weather(PLAN_ID, USER_ID, "t-1");

        assertFalse(vo.isAvailable());
        assertEquals("DATE_OUT_OF_RANGE", vo.getReason());
        assertEquals("出发日期超出预报范围，临近再看", vo.getMessage());
    }

    @Test
    @DisplayName("缓存过期且上游挂掉：返回旧值并标 stale（W-5）")
    void weather_servesStaleValueWhenRefreshFails() throws Exception {
        when(store.loadOwned(PLAN_ID, USER_ID)).thenReturn(plan(START, "成都", 3));
        when(aiServiceClient.fetchWeather(anyString(), anyString(), anyInt(), any()))
                .thenReturn(okJson());
        PlanWeatherVO cachedCopy = service.weather(PLAN_ID, USER_ID, "t-1");

        // 手工把缓存改成"已过期"，再让上游挂掉
        service.seedExpired(cachedCopy, PLAN_ID, START, 3, "成都");
        when(aiServiceClient.fetchWeather(anyString(), anyString(), anyInt(), any())).thenReturn(null);

        PlanWeatherVO vo = service.weather(PLAN_ID, USER_ID, "t-2");

        assertTrue(vo.isAvailable(), "有旧值就别退化成「暂不可用」");
        assertTrue(vo.isStale(), "旧值必须标出来，否则就是拿旧数据冒充当前数据");
        assertEquals(1, vo.getDays().size());
        assertEquals("12–16℃", vo.getDays().get(0).getTemperature().path("display").asText());
        assertTrue(vo.getNote().contains("上次"), "note 要说明这是上次成功获取的结果：" + vo.getNote());
    }

    @Test
    @DisplayName("天数超过预报窗口时按上限截断，不把 30 天原样发给上游")
    void weather_clampsDaysToForecastWindow() throws Exception {
        when(store.loadOwned(PLAN_ID, USER_ID)).thenReturn(plan(START, "成都", 30));
        when(aiServiceClient.fetchWeather(anyString(), anyString(), anyInt(), any()))
                .thenReturn(okJson());

        service.weather(PLAN_ID, USER_ID, "t-1");

        // D-03 之后上限是 10（和风天气官方窗口），不再是 Open-Meteo 的 16。
        // 这个数字必须与 Python 侧 FORECAST_WINDOW_DAYS、前端文案三处一致。
        verify(aiServiceClient).fetchWeather("成都", "2026-11-24", 10, "t-1");
    }

    @Test
    @DisplayName("越权：别人的计划在发起任何查询前就被拒（W-6）")
    void weather_forbidsOtherUsersPlan() {
        when(store.loadOwned(PLAN_ID, 999L))
                .thenThrow(new BizException(com.aitravel.common.api.ErrorCode.FORBIDDEN, "无权访问该计划"));

        assertThrows(BizException.class, () -> service.weather(PLAN_ID, 999L, "t-1"));
        verify(aiServiceClient, never()).fetchWeather(any(), any(), anyInt(), any());
    }

    @Test
    @DisplayName("缓存 key 含请求自然日：跨过零点必须换 key，否则次日仍读「明天起 3 天」")
    void weather_cacheKeyIncludesRequestDate() {
        LocalDate day1 = LocalDate.of(2026, 11, 23);
        LocalDate day2 = LocalDate.of(2026, 11, 24);

        String k1 = PlanWeatherService.cacheKey(PLAN_ID, START, 3, "成都", day1);
        String k2 = PlanWeatherService.cacheKey(PLAN_ID, START, 3, "成都", day2);

        assertNotEquals(k1, k2, "同一计划跨天后必须变成另一个 key，否则会整体错一天的天气");
        // 同一自然日、其余条件相同 → 必须命中同一条缓存，不能每次刷新都打上游
        assertEquals(k1, PlanWeatherService.cacheKey(PLAN_ID, START, 3, "成都", day1));
    }

    @Test
    @DisplayName("跨天失效边界是「次日 00:10」（东八区），不是服务器的本地零点")
    void weather_nextDayCutoffUsesChinaZone() {
        java.time.ZonedDateTime cutoff = PlanWeatherService.nextDayCutoff().atZone(java.time.ZoneId.of("Asia/Shanghai"));

        assertEquals(java.time.LocalTime.of(0, 10), cutoff.toLocalTime());
        assertEquals(LocalDate.now(java.time.ZoneId.of("Asia/Shanghai")).plusDays(1), cutoff.toLocalDate());
    }

    @Test
    @DisplayName("上游没给 source 时兜底 NONE，不再写死某个 provider 名")
    void weather_sourceFallsBackToNone() throws Exception {
        // D-03 实施清单第 8 条：默认值写死 "OPEN_METEO"，换源之后那句默认值就变成假话
        JsonNode noSource = MAPPER.readTree("""
                {"ok": true, "attribution": "", "fetchedAt": "2026-11-23T22:40:00+08:00", "days": []}
                """);
        when(store.loadOwned(PLAN_ID, USER_ID)).thenReturn(plan(START, "成都", 3));
        when(aiServiceClient.fetchWeather(anyString(), anyString(), anyInt(), any()))
                .thenReturn(noSource);

        PlanWeatherVO vo = service.weather(PLAN_ID, USER_ID, "t-1");

        assertEquals("NONE", vo.getSource());
    }
}
