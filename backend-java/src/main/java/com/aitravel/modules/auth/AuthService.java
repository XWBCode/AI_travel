package com.aitravel.modules.auth;

import com.aitravel.common.api.ErrorCode;
import com.aitravel.common.exception.BizException;
import com.aitravel.modules.auth.dto.LoginRequest;
import com.aitravel.modules.auth.dto.LoginResponse;
import com.aitravel.modules.auth.dto.RegisterRequest;
import com.aitravel.modules.user.dto.UserVO;
import com.aitravel.modules.user.entity.User;
import com.aitravel.modules.user.mapper.UserMapper;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;

@Slf4j
@Service
@RequiredArgsConstructor
public class AuthService {

    private final UserMapper userMapper;
    private final PasswordEncoder passwordEncoder;
    private final JwtTokenProvider jwtTokenProvider;

    @Transactional(rollbackFor = Exception.class)
    public UserVO register(RegisterRequest req) {
        Long exists = userMapper.selectCount(Wrappers.<User>lambdaQuery().eq(User::getUsername, req.getUsername()));
        if (exists != null && exists > 0) {
            throw new BizException(ErrorCode.USERNAME_EXISTS);
        }
        User user = new User();
        user.setUsername(req.getUsername());
        user.setPasswordHash(passwordEncoder.encode(req.getPassword()));
        user.setNickname(req.getNickname() == null || req.getNickname().isBlank()
                ? req.getUsername() : req.getNickname());
        user.setRole("USER");
        user.setStatus(0);
        try {
            userMapper.insert(user);
        } catch (DuplicateKeyException e) {
            // 并发注册时唯一索引兜底
            throw new BizException(ErrorCode.USERNAME_EXISTS);
        }
        log.info("用户注册成功 userId={} username={}", user.getId(), user.getUsername());
        return toVO(user);
    }

    public LoginResponse login(LoginRequest req) {
        User user = userMapper.selectOne(Wrappers.<User>lambdaQuery().eq(User::getUsername, req.getUsername()));
        // 用户不存在与密码错误返回同一错误码，防止用户名枚举
        if (user == null || !passwordEncoder.matches(req.getPassword(), user.getPasswordHash())) {
            throw new BizException(ErrorCode.BAD_CREDENTIALS);
        }
        if (user.getStatus() != null && user.getStatus() == 1) {
            throw new BizException(ErrorCode.ACCOUNT_DISABLED);
        }
        User patch = new User();
        patch.setId(user.getId());
        patch.setLastLoginTime(LocalDateTime.now());
        userMapper.updateById(patch);

        String token = jwtTokenProvider.createToken(user);
        return new LoginResponse(token, jwtTokenProvider.getExpiresInSeconds(), toVO(user));
    }

    public UserVO getById(Long userId) {
        User user = userMapper.selectById(userId);
        if (user == null) {
            throw new BizException(ErrorCode.NOT_FOUND, "用户不存在");
        }
        return toVO(user);
    }

    private UserVO toVO(User user) {
        UserVO vo = new UserVO();
        vo.setId(user.getId());
        vo.setUsername(user.getUsername());
        vo.setNickname(user.getNickname());
        vo.setAvatarUrl(user.getAvatarUrl());
        vo.setBio(user.getBio());
        vo.setRole(user.getRole());
        vo.setCreateTime(user.getCreateTime());
        return vo;
    }
}
