package com.aitravel.modules.auth;

import com.aitravel.common.api.Result;
import com.aitravel.common.web.CurrentUser;
import com.aitravel.modules.auth.dto.LoginRequest;
import com.aitravel.modules.auth.dto.LoginResponse;
import com.aitravel.modules.auth.dto.RegisterRequest;
import com.aitravel.modules.user.dto.UserVO;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/auth")
@RequiredArgsConstructor
public class AuthController {

    private final AuthService authService;

    @PostMapping("/register")
    public Result<UserVO> register(@Valid @RequestBody RegisterRequest req) {
        return Result.ok(authService.register(req));
    }

    @PostMapping("/login")
    public Result<LoginResponse> login(@Valid @RequestBody LoginRequest req) {
        return Result.ok(authService.login(req));
    }

    /** 需要携带 token，用于前端刷新页面后校验登录态 */
    @GetMapping("/me")
    public Result<UserVO> me() {
        return Result.ok(authService.getById(CurrentUser.requireUserId()));
    }
}
