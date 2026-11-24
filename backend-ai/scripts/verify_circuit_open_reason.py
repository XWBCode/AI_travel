"""CIRCUIT_OPEN 跨层验证：Python 返回 → Java 透传 → Vue 展示。

    # 1) 起一个"上游永远 500"的天气服务实例
    FAULT_STATE_FILE=.tools/fault/state.json FAULT_LOG_FILE=.tools/fault/calls.jsonl \
    .venv/Scripts/python.exe scripts/run_fault_server.py --port 8100
    # 2) 起一台指向它的 Java（另开端口，不动正在跑的 8080）
    AI_SERVICE_URL=http://127.0.0.1:8100 WEATHER_PROVIDER=qweather \
    mvn -o spring-boot:run -Dspring-boot.run.arguments=--server.port=8081
    # 3) 跑本脚本
    .venv/Scripts/python.exe scripts/verify_circuit_open_reason.py

## 这个脚本要证明什么

只有一件事：**「这次真的打了上游」和「这次根本没打」能被人区分出来**。

A 项验证时这两件事在外部完全同形——都是 `available=false`、都是毫秒级返回、
`reason` 都是 `UPSTREAM_ERROR`，于是运维看到"上游错误"会去查一个本轮压根没被碰过的接口。
所以判据只能落在**上游调用计数有没有增长**上，不能落在耗时上
（注入的 500 在内存里直接抛，一样是毫秒级）。

顺序是刻意排的：先让 Java 真失败 3 次把熔断打开，第 4 次才是熔断拦截。
这样「同样是毫秒级、同样是 available=false，但 reason 和文案不同」是同一个进程、
同一条链路上跑出来的，不是两个场景拼出来的对比。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT.parent / ".tools" / "fault"
STATE_FILE = TOOLS / "state.json"
LOG_FILE = TOOLS / "calls.jsonl"

JAVA = "http://127.0.0.1:8081"
PY = "http://127.0.0.1:8100"
PLAN_ID = "2124887242789089282"
CREDENTIAL = {"username": "wx95447477", "password": "weather12345"}

RESULTS: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str) -> bool:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} —— {detail}", flush=True)
    return ok


def http_json(url: str, token: str | None = None, timeout: float = 20.0,
              method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode()), resp.status


def timed(url: str, token: str | None = None, timeout: float = 20.0):
    start = time.monotonic()
    try:
        payload, status = http_json(url, token, timeout=timeout)
        return payload, status, (time.monotonic() - start) * 1000.0, None
    except urllib.error.HTTPError as e:
        return None, e.code, (time.monotonic() - start) * 1000.0, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return None, None, (time.monotonic() - start) * 1000.0, type(e).__name__


def call_count() -> int:
    """上游被真正调用的次数。这是「有没有出网」的唯一硬证据。"""
    try:
        return sum(1 for _ in LOG_FILE.open(encoding="utf-8"))
    except OSError:
        return 0


def set_mode(mode: str) -> None:
    TOOLS.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"mode": mode}), encoding="utf-8")


def login() -> str:
    payload, _ = http_json(f"{JAVA}/api/auth/login", None, method="POST", body=CREDENTIAL)
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
    return payload["accessToken"]


def weather_java(token: str):
    """Java 统一信封 {code,message,data,traceId}：断言必须落在 data 上，否则读到的是 None。"""
    vo, status, ms, err = timed(f"{JAVA}/api/plans/{PLAN_ID}/weather", token)
    if isinstance(vo, dict) and "data" in vo:
        vo = vo["data"]
    return vo, status, ms, err


def weather_py(days: int = 3):
    start = (date.today() + timedelta(days=1)).isoformat()
    vo, status, ms, err = timed(f"{PY}/v1/weather?city=%E6%88%90%E9%83%BD&start_date={start}&days={days}")
    return vo, status, ms, err


def main() -> int:
    if not LOG_FILE.exists():
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.touch()

    # 每场从「熔断关闭 + 空调用日志」开始：熔断器是进程级单例，
    # 上一场留下的 open 会让本场第 1 次请求就被拦住，测到的是上一场的状态。
    set_mode("http_500")
    LOG_FILE.write_text("", encoding="utf-8")

    token = login()
    base = call_count()

    print("\n=== 阶段 1：上游真的坏了（3 次真失败，第 3 次打开熔断）===", flush=True)
    first_vo = None
    first_text = None
    for i in range(1, 4):
        vo, status, ms, err = weather_java(token)
        calls = call_count() - base
        detail = (f"HTTP {status} available={vo.get('available')} reason={vo.get('reason')} "
                  f"days={len(vo.get('days') or [])} 耗时={ms:.0f}ms 上游调用={calls}")
        record(f"1.{i} 真失败：200 + available=false + UPSTREAM_ERROR", 
               status == 200 and vo.get("available") is False and vo.get("reason") == "UPSTREAM_ERROR",
               detail + (f" err={err}" if err else ""))
        record(f"1.{i} 这次确实出了网", calls == i, f"累计上游调用={calls}，期望={i}")
        if i == 1:
            first_vo, first_text = vo, (vo.get("message") or "")

    print("\n=== 阶段 2：熔断已打开，第 4 次请求应该没出网 ===", flush=True)
    calls_before = call_count() - base
    blocked_vo, status, ms, err = weather_java(token)
    calls_after = call_count() - base
    blocked_text = (blocked_vo.get("message") or "") if blocked_vo else ""
    record("2.1 熔断拦截：200 + available=false + CIRCUIT_OPEN",
           status == 200 and blocked_vo is not None
           and blocked_vo.get("available") is False and blocked_vo.get("reason") == "CIRCUIT_OPEN",
           f"HTTP {status} available={blocked_vo.get('available') if blocked_vo else None} "
           f"reason={blocked_vo.get('reason') if blocked_vo else None} 耗时={ms:.0f}ms"
           + (f" err={err}" if err else ""))
    record("2.2 熔断期间没有出网（调用计数不增长）", calls_after == calls_before,
           f"拦截前={calls_before} 拦截后={calls_after}")
    record("2.3 原因是 Java 从 Python 透传的，没有被改写成 UPSTREAM_ERROR",
           blocked_vo is not None and blocked_vo.get("reason") != "UPSTREAM_ERROR",
           f"Java 拿到的 reason={blocked_vo.get('reason') if blocked_vo else None}")
    record("2.4 两种原因给用户不同的话", first_text != blocked_text,
           f"真失败=「{first_text}」 / 熔断=「{blocked_text}」")

    print("\n=== 阶段 3：绕过 Java 直连 Python，确认枚举在源头就是对的 ===", flush=True)
    py_vo, py_status, py_ms, py_err = weather_py()
    record("3.1 Python 直连也返回 CIRCUIT_OPEN",
           py_status == 200 and py_vo is not None and py_vo.get("reason") == "CIRCUIT_OPEN",
           f"HTTP {py_status} ok={py_vo.get('ok') if py_vo else None} "
           f"reason={py_vo.get('reason') if py_vo else None} 耗时={py_ms:.0f}ms"
           + (f" err={py_err}" if py_err else ""))
    record("3.2 直连同样没有出网", call_count() - base == calls_after,
           f"累计上游调用={call_count() - base}，与阶段 2 相同")
    record("3.3 两端 reason 逐字一致（Java 没有二次翻译）",
           py_vo is not None and blocked_vo is not None
           and py_vo.get("reason") == blocked_vo.get("reason"),
           f"Python={py_vo.get('reason') if py_vo else None} "
           f"Java={blocked_vo.get('reason') if blocked_vo else None}")

    print("\n=== 汇总 ===", flush=True)
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r[1] == "PASS")
    failed = total - passed
    print(f"共 {total} 项：PASS {passed} / FAIL {failed}", flush=True)
    print(f"整场 4 次请求只产生 {call_count() - base} 次上游调用（阈值 3），熔断后 0 次新增", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
