"""天气降级原因枚举 + 文案。

单独成一个模块，是因为它被三处引用：`weather.py`（编排）、各 provider（抛 `ProviderFailure`）、
以及测试。放在 provider 包里会反过来让 `weather.py` 依赖 provider，形成环。

**枚举是契约的一部分**：`PlanWeatherVO.reason` 在 `contracts/travel-plan/` 里是封闭枚举，
前端按它映射文案与"能不能自己修"。所以这里只允许两种改动：
新增取值（契约同步）或改文案。**不允许**为了表达一个更细腻的状态而偷偷换成另一个取值。
"""

from __future__ import annotations

from typing import Any

# 没有出发日期。**不要**用"今天"顶替——那是看起来最像真数据的假信息
R_NO_START_DATE = "NO_START_DATE"
# 城市解析不出来（不在自带坐标表里）
R_CITY_UNKNOWN = "CITY_UNKNOWN"
# 行程日期整段落在预报窗口之外
R_DATE_OUT_OF_RANGE = "DATE_OUT_OF_RANGE"
# 上游超时
R_UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
# 上游返回错误 / 结构异常 / 熔断打开
R_UPSTREAM_ERROR = "UPSTREAM_ERROR"
# 配置层关闭，或凭据没配齐
R_PROVIDER_DISABLED = "PROVIDER_DISABLED"
# 计划里没有城市
R_NO_CITY = "NO_CITY"

MESSAGES: dict[str, str] = {
    R_NO_START_DATE: "补充出发日期后可查看天气",
    R_CITY_UNKNOWN: "该地点暂无天气数据",
    R_DATE_OUT_OF_RANGE: "出发日期超出预报范围，临近再看",
    R_UPSTREAM_TIMEOUT: "天气服务响应超时",
    R_UPSTREAM_ERROR: "天气服务暂时不可用",
    R_PROVIDER_DISABLED: "天气功能未开启",
    R_NO_CITY: "计划里还没有城市，无法查询天气",
}

ALL_REASONS = tuple(MESSAGES.keys())


def fail(reason: str, message: str | None = None) -> dict[str, Any]:
    """构造统一的失败返回。**失败一律 200 + ok=false**，不用 4xx/5xx（理由见 03 文档 §4.1）。"""
    return {"ok": False, "reason": reason, "message": message or MESSAGES.get(reason, "天气暂不可用")}
