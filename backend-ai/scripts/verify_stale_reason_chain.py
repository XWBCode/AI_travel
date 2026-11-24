"""旧值（stale）+ 刷新失败原因 的端到端验证：Python → Java → 前端文案。

    # 1) 故障天气服务实例（上游可被人为改坏）
    FAULT_STATE_FILE=.tools/fault/state.json FAULT_LOG_FILE=.tools/fault/calls.jsonl \
    WEATHER_PROVIDER=qweather WEATHER_CACHE_TTL_MINUTES=1 \
    .venv/Scripts/python.exe scripts/run_fault_server.py --port 8100
    # 2) 一台指向它的 Java（TTL 也压到 1 分钟，否则要等 60 分钟）
    AI_SERVICE_URL=http://127.0.0.1:8100 WEATHER_PROVIDER=qweather \
    WEATHER_CACHE_TTL_MINUTES=1 CORS_ORIGINS=http://localhost:5173,http://localhost:5174 \
    mvn -o spring-boot:run -Dspring-boot.run.arguments=--server.port=8081
    # 3) 跑本脚本（约 80 秒，其中 70 秒在等缓存过期）
    .venv/Scripts/python.exe scripts/verify_stale_reason_chain.py

## 这个脚本要证明什么

PM 的一句话：「用户看到旧天气数据时，仍然无法判断它为什么没有更新。」

所以这里要证明的不是"能拿到天气"，而是**旧值场景下原因能到达界面**：
- `available=true` 且 `stale=true`（旧值还在，不是"没有天气"）
- `reason` 有值，且 `CIRCUIT_OPEN` 与 `UPSTREAM_ERROR` 不同
- 全程上游调用次数不超过熔断阈值（不因为刷新失败而打风暴）

## 为什么必须真等缓存过期

Java 的缓存过期时间是 Python 给的 `expiresAt`（TTL 只有一个来源）。想造 stale，
只有两条路：改代码绕过去（假证据），或者真等它过期。这里选后者，
把 TTL 压到 1 分钟，等 70 秒——慢，但拿到的是真链路的行为。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT.parent / ".tools" / "fault"
STATE_FILE = TOOLS / "state.json"
LOG_FILE = TOOLS / "calls.jsonl"

JAVA = "http://127.0.0.1:8081"
PLAN_ID = "2124887242789089282"
CREDENTIAL = {"username": "wx95447477", "password": "weather12345"}

# TTL 压到 1 分钟后，等 70 秒一定过期；多给 10 秒是为了避开"刚过期又被判未过期"的边界
TTL_WAIT_SECONDS = 70

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


def call_count() -> int:
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


def weather(token: str):
    vo, status = http_json(f"{JAVA}/api/plans/{PLAN_ID}/weather", token)
    if isinstance(vo, dict) and "data" in vo:
        vo = vo["data"]
    return vo, status


def describe(vo: dict) -> str:
    return (f"HTTP 200 available={vo.get('available')} stale={vo.get('stale')} "
            f"reason={vo.get('reason')} days={len(vo.get('days') or [])}")


def main() -> int:
    if not LOG_FILE.exists():
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.touch()

    set_mode("ok")
    LOG_FILE.write_text("", encoding="utf-8")
    base = call_count()
    token = login()

    print("\n=== 阶段 0：先拿一份能缓存住的真数据 ===", flush=True)
    warm, status = weather(token)
    ok_warm = record("0.1 预热：available=true 且有逐日数据",
                     status == 200 and warm.get("available") is True
                     and len(warm.get("days") or []) > 0,
                     describe(warm))
    if not ok_warm:
        print("\n[中断] 预热没拿到数据，后面的 stale 场景无从谈起。", flush=True)
        return 1
    print(f"     expiresAt={warm.get('expiresAt')}（TTL 已压到 1 分钟）", flush=True)
    calls_after_warm = call_count() - base

    print(f"\n=== 阶段 1：等缓存过期（{TTL_WAIT_SECONDS}s），同时让上游开始失败 ===", flush=True)
    set_mode("http_500")
    time.sleep(TTL_WAIT_SECONDS)

    calls_before = call_count() - base
    vo, status = weather(token)
    calls_after = call_count() - base
    record("1.1 旧值仍在：available=true + stale=true",
           status == 200 and vo.get("available") is True and vo.get("stale") is True,
           describe(vo))
    record("1.2 旧数据没被清空（不是拿一句「暂不可用」换掉能看的内容）",
           len(vo.get("days") or []) > 0,
           f"逐日条数={len(vo.get('days') or [])}")
    record("1.3 本次刷新失败的原因进了结构化 reason（不是只有英文 note）",
           vo.get("reason") == "UPSTREAM_ERROR",
           f"reason={vo.get('reason')}（期望 UPSTREAM_ERROR）")
    record("1.4 这次刷新确实出网了", calls_after > calls_before,
           f"上游调用 {calls_before} → {calls_after}")

    print("\n=== 阶段 2：继续失败到熔断打开，旧值场景的原因要跟着变 ===", flush=True)
    for i in (2, 3):
        again, _ = weather(token)
        record(f"2.{i - 1} 第 {i} 次刷新仍归因于上游",
               again.get("reason") == "UPSTREAM_ERROR",
               f"reason={again.get('reason')}")

    calls_at_open = call_count() - base
    blocked, status = weather(token)
    calls_after_block = call_count() - base
    record("2.3 熔断后旧值场景的 reason 变成 CIRCUIT_OPEN",
           status == 200 and blocked.get("stale") is True
           and blocked.get("reason") == "CIRCUIT_OPEN",
           describe(blocked))
    record("2.4 熔断期间没有出网（上游调用冻结）",
           calls_after_block == calls_at_open,
           f"熔断前={calls_at_open} → 熔断后={calls_after_block}")
    record("2.5 旧值场景下两种失败的原因不同（界面才能说两句不同的话）",
           blocked.get("reason") != vo.get("reason"),
           f"真失败={vo.get('reason')} / 熔断={blocked.get('reason')}")
    record("2.6 旧数据在熔断期间仍然保留",
           len(blocked.get("days") or []) > 0,
           f"逐日条数={len(blocked.get('days') or [])}")

    print("\n=== 阶段 3：不因为反复刷新而打上游 ===", flush=True)
    # 口径要分开算：预热那 1 次是"正常取数"，不是"故障期的重试"。
    # 混在一起数过一次，把 1 次正常请求算成了风暴证据（断言假红）。
    warm_calls = calls_after_warm
    refresh_calls = call_count() - base - warm_calls
    refresh_requests = 4  # 1.1 一次 + 阶段 2 三次（含熔断拦截那次）
    record("3.1 故障期刷新只打到熔断阈值为止（无请求风暴）", refresh_calls <= 3,
           f"故障期 {refresh_requests} 次请求只产生 {refresh_calls} 次上游调用（阈值 3）；"
           f"另有预热 {warm_calls} 次正常取数，不计入")
    record("3.2 熔断拦截的那一次没有出网", calls_after_block == calls_at_open,
           f"上游调用停在 {calls_after_block}")

    print("\n=== 汇总 ===", flush=True)
    passed = sum(1 for r in RESULTS if r[1] == "PASS")
    failed = len(RESULTS) - passed
    print(f"共 {len(RESULTS)} 项：PASS {passed} / FAIL {failed}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
