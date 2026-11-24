"""故障注入替身：把和风天气的**唯一出网点**换成本地可控响应。

这不是产品功能，也不改产品代码——`run_fault_server.py` 在启动 uvicorn 之前
把 `app.tools.weather_providers.qweather._get` 换掉，进程结束后一切复原。

为什么用替身而不是真实账号：
PM 明确要求「不要通过真实账号故意制造 429、403」。而且真实故障不可控——
我们没法让上游准时在 T+10:00 恢复，也没法让它连续失败正好 10 分钟。

## 为什么必须记日志

"熔断后没有请求风暴"这句话，只能靠**上游调用次数不再增长**来证明。
耗时快、界面正常都不能证明——一个安静的无限重试循环同样"看起来很快"。
所以每次被调用都追加一行到 JSONL，驱动脚本只认这个计数。
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.tools.weather_providers import qweather
from app.tools.weather_providers.base import ProviderFailure
from app.tools.weather_reasons import R_UPSTREAM_ERROR, R_UPSTREAM_TIMEOUT

TZ = timezone(timedelta(hours=8), "Asia/Shanghai")

# 控制文件与调用日志的路径由驱动脚本通过环境变量给出
STATE_FILE = Path(os.getenv("FAULT_STATE_FILE", "fault_state.json"))
LOG_FILE = Path(os.getenv("FAULT_LOG_FILE", "fault_calls.jsonl"))

VALID_MODES = ("ok", "http_500", "timeout", "http_403")


def read_mode() -> str:
    """读当前故障模式。文件不存在或写坏了都按 ok 处理——替身自己不能成为故障源。"""
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "ok"
    mode = str(raw.get("mode", "ok")).strip()
    return mode if mode in VALID_MODES else "ok"


def _append_log(entry: dict) -> None:
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def canned_payload(days: int = 10, today: date | None = None) -> dict:
    """造一份形状与官方响应同构的数据，日期从**本地今天**开始。

    日期必须相对今天生成：provider 会做「forecastStartTime 转 +08:00」与
    「今天 + 序号」的双轨校验，写死某个日期会直接触发那条 warning 分支。
    """
    today = today or datetime.now(TZ).date()
    items = []
    for i in range(days):
        # 官方示例里"某一天"的 forecastStartTime 是本地当天 06:00（UTC 前一天 22:00）
        start_local = datetime.combine(today + timedelta(days=i), datetime.min.time(), tzinfo=TZ) + timedelta(hours=6)
        utc = start_local.astimezone(timezone.utc)
        items.append(
            {
                "forecastStartTime": utc.strftime("%Y-%m-%dT%H:%MZ"),
                "forecastEndTime": (utc + timedelta(days=1)).strftime("%Y-%m-%dT%H:%MZ"),
                "temperatureMax": {"value": 16.0, "unit": "°C"},
                "temperatureMin": {"value": 11.0, "unit": "°C"},
                "temperatureAvg": {"value": 13.5, "unit": "°C"},
                "uvIndexMax": 5,
                "daytime": {
                    "condition": {"text": "多云", "code": "101"},
                    "wind": {"speed": {"value": 5.0, "unit": "m/s"}, "scale": 2},
                    "precipitation": {"amount": {"value": 0.0, "unit": "mm"}, "probability": 0.3},
                },
                "nighttime": {
                    "condition": {"text": "晴", "code": "100"},
                    "wind": {"speed": {"value": 2.3, "unit": "m/s"}, "scale": 2},
                    "precipitation": {"amount": {"value": 0, "unit": "mm"}, "probability": 0},
                },
            }
        )
    return {
        "metadata": {"tag": "fault-injection", "attributions": ["https://www.qweather.com"]},
        "days": items,
    }


async def fake_get(client, url: str, params: dict, token: str) -> tuple[dict, int]:
    """替换 `qweather._get`：签名与行为契约和真身一致（返回 (payload, status) 或抛 ProviderFailure）。"""
    mode = read_mode()
    _append_log(
        {
            "ts": time.time(),
            "mode": mode,
            "url": url,
            "days": (params or {}).get("days"),
            "auth_len": len(token or ""),
        }
    )

    if mode == "ok":
        wanted = int((params or {}).get("days") or 3)
        return canned_payload(days=max(1, min(10, wanted))), 200
    if mode == "timeout":
        raise ProviderFailure(R_UPSTREAM_TIMEOUT)
    if mode == "http_403":
        # INVALID HOST / NO CREDIT 一类：hard，一次都不该重试
        raise ProviderFailure(R_UPSTREAM_ERROR, hard=True)
    # http_500 及未知：soft，连续 N 次才熔断
    raise ProviderFailure(R_UPSTREAM_ERROR)


def install() -> None:
    qweather._get = fake_get  # noqa: SLF001 —— 替身就是靠替换私有出网点实现的
