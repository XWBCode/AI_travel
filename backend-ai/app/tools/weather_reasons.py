"""天气降级原因枚举 + 文案。

单独成一个模块，是因为它被三处引用：`weather.py`（编排）、各 provider（抛 `ProviderFailure`）、
以及测试。放在 provider 包里会反过来让 `weather.py` 依赖 provider，形成环。

**枚举是契约的一部分**：`PlanWeatherVO.reason` 在 `contracts/travel-plan/` 里是封闭枚举，
前端按它映射文案与"能不能自己修"。所以这里只允许两种改动：
新增取值（契约同步）或改文案。**不允许**为了表达一个更细腻的状态而偷偷换成另一个取值。

## `UPSTREAM_ERROR` 与 `CIRCUIT_OPEN` 为什么必须分开

两者在外部表现上几乎一样：`available=false`、耗时都是毫秒级、用户都无从下手。
但**成因和责任方完全不同**，混用会同时误导两类人：

- `UPSTREAM_ERROR` = 这次真的发了请求，上游返回了失败。说明**链路是通的**，
  该去查上游状态页 / 凭据 / 配额。
- `CIRCUIT_OPEN` = 熔断器开着，这次**根本没发请求**（`weather_circuit` 主动拒绝）。
  说明我们**已经知道了**上游在坏，正在保护它不被继续打。此时上游可能已经在恢复，
  再去查凭据是白费功夫；反过来，把熔断说成"上游错误"会让人以为上游又被打了新一轮。

所以判据只有一个、且可机检：**本次请求有没有出网**。出网并失败 → `UPSTREAM_ERROR`；
没出网就被拦住 → `CIRCUIT_OPEN`。不要用"耗时短"来判——注入的失败同样是毫秒级，
拆分之前两者从外部完全同形（A 项验证时就是这么误判了一整轮）。
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
# 上游真的被访问了，但它失败了（5xx / 结构异常 / 凭据错误）
R_UPSTREAM_ERROR = "UPSTREAM_ERROR"
# 熔断器已打开，**本次请求没有访问上游**，是主动拒绝
R_CIRCUIT_OPEN = "CIRCUIT_OPEN"
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
    # 刻意不说"不可用"：熔断是**我们**主动停的，而且会自己恢复，
    # 说成"不可用"用户会以为要去修什么，也区分不出"这次压根没打上游"
    R_CIRCUIT_OPEN: "天气服务连续异常，已暂停刷新，稍后会自动恢复",
    R_PROVIDER_DISABLED: "天气功能未开启",
    R_NO_CITY: "计划里还没有城市，无法查询天气",
}

ALL_REASONS = tuple(MESSAGES.keys())


def fail(reason: str, message: str | None = None) -> dict[str, Any]:
    """构造统一的失败返回。**失败一律 200 + ok=false**，不用 4xx/5xx（理由见 03 文档 §4.1）。"""
    return {"ok": False, "reason": reason, "message": message or MESSAGES.get(reason, "天气暂不可用")}
