package com.aitravel.modules.user.dto;

import lombok.Data;

import java.time.LocalDateTime;

/** 用户对外视图，绝不包含 passwordHash */
@Data
public class UserVO {

    private Long id;
    private String username;
    private String nickname;
    private String avatarUrl;
    private String bio;
    private String role;
    private LocalDateTime createTime;
}
