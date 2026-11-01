package com.aitravel.modules.auth.dto;

import com.aitravel.modules.user.dto.UserVO;
import lombok.AllArgsConstructor;
import lombok.Data;

@Data
@AllArgsConstructor
public class LoginResponse {

    private String accessToken;

    /** 秒 */
    private long expiresIn;

    private UserVO user;
}
