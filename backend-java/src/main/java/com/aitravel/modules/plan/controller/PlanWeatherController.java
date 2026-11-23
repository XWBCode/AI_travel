package com.aitravel.modules.plan.controller;

import com.aitravel.common.api.Result;
import com.aitravel.common.web.CurrentUser;
import com.aitravel.modules.plan.dto.PlanWeatherVO;
import com.aitravel.modules.plan.service.PlanWeatherService;
import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 计划天气（M3-1）。
 *
 * <p>路径挂在 {@code /api/plans} 下与计划详情同级，因为它的权限语义完全一样
 * （必须是计划所有者）。单独一个控制器而不是塞进 {@link PlanController}：
 * 后者的注释已声明「不负责天气」，且天气是可整体删除的附属功能，
 * 独立出来才方便关掉。
 *
 * <p><b>永远返回 200</b>（除鉴权/归属失败）。天气不可用时是
 * {@code available=false + reason}，不是错误码——理由见 {@link PlanWeatherService}。
 */
@RestController
@RequestMapping("/api/plans")
@RequiredArgsConstructor
public class PlanWeatherController {

    private final PlanWeatherService weatherService;

    @GetMapping("/{id}/weather")
    public Result<PlanWeatherVO> weather(@PathVariable Long id, HttpServletRequest http) {
        return Result.ok(weatherService.weather(id, CurrentUser.requireUserId(),
                http.getHeader("X-Trace-Id")));
    }
}
