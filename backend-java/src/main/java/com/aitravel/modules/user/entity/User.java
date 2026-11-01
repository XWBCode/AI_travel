package com.aitravel.modules.user.entity;

import com.aitravel.common.mybatis.BaseEntity;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalDateTime;

@Data
@EqualsAndHashCode(callSuper = true)
@TableName("t_user")
public class User extends BaseEntity {

    private String username;

    /** BCrypt 加密后的密码，任何情况下不得返回给前端 */
    private String passwordHash;

    private String nickname;

    private String avatarUrl;

    private String bio;

    /** USER / ADMIN */
    private String role;

    /** 0=正常 1=禁用 */
    private Integer status;

    private LocalDateTime lastLoginTime;
}
