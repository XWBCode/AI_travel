package com.aitravel;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@MapperScan("com.aitravel.**.mapper")
// 目前只有幂等记录清理一个定时任务，但 @Scheduled 不开启就静默不跑——不报错、不告警，
// 只能是表悄悄涨到千万级时才被发现。所以显式打开
@EnableScheduling
public class AiTravelApplication {

    public static void main(String[] args) {
        SpringApplication.run(AiTravelApplication.class, args);
    }
}
