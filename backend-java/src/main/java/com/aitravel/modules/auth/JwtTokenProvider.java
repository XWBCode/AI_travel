package com.aitravel.modules.auth;

import com.aitravel.modules.user.entity.User;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import org.springframework.stereotype.Component;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Date;

@Component
public class JwtTokenProvider {

    private final JwtProperties properties;
    private final SecretKey key;

    public JwtTokenProvider(JwtProperties properties) {
        this.properties = properties;
        // HS256 要求密钥 >= 256 bit，启动时校验，避免运行期才炸
        byte[] bytes = properties.getSecret().getBytes(StandardCharsets.UTF_8);
        if (bytes.length < 32) {
            throw new IllegalStateException("app.jwt.secret 长度必须不少于 32 字节");
        }
        this.key = Keys.hmacShaKeyFor(bytes);
    }

    public String createToken(User user) {
        Date now = new Date();
        Date expiry = new Date(now.getTime() + Duration.ofMinutes(properties.getAccessTokenMinutes()).toMillis());
        return Jwts.builder()
                .subject(String.valueOf(user.getId()))
                .claim("username", user.getUsername())
                .claim("role", user.getRole())
                .issuedAt(now)
                .expiration(expiry)
                .signWith(key)
                .compact();
    }

    public Claims parse(String token) {
        return Jwts.parser()
                .verifyWith(key)
                .build()
                .parseSignedClaims(token)
                .getPayload();
    }

    public Long getUserId(Claims claims) {
        return Long.valueOf(claims.getSubject());
    }

    public long getExpiresInSeconds() {
        return Duration.ofMinutes(properties.getAccessTokenMinutes()).toSeconds();
    }
}
