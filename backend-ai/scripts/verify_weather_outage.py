"""A · 连续上游故障 10 分钟场景验证（真实进程 + 可控上游）。

场景设计见 `docs/acceptance/A-连续故障10分钟-场景设计.md`，本脚本是它的执行部分。

    # 主场景（10 分钟，生产参数）
    python scripts/verify_weather_outage.py outage
    # 恢复场景（压缩冷却到 20s，只为证明"能自己回来"）
    python scripts/verify_weather_outage.py recovery

前提：
- 故障 Python 实例 :8100 已由 run_fault_server.py 启动（状态/日志文件路径见 .tools/fault/）
- Java :8080 已启动且 AI_SERVICE_URL 指向 :8100、WEATHER_PROVIDER=qweather

## 为什么这个脚本这么啰嗦

因为这场的结论是"没出事"。而"没出事"最容易被写成一堆看起来专业的废话，
真正的证据只有两个数字：**上游被调用了多少次**、**每个阶段用户拿到了什么**。
所以每条断言都要打印实测值，且"应该没有请求"的断言必须靠计数证明，
不能靠"耗时很短"——一个安静的无限重试循环同样很快。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT.parent / ".tools" / "fault" / "state.json"
LOG_FILE = ROOT.parent / ".tools" / "fault" / "calls.jsonl"
# 原始采样落盘：断言口径改了以后可以重算，不必再等 10 分钟重跑一遍。
# （本次就吃过亏：第一版断言用耗时判别熔断，写错了，只能靠时间线人工复核。）
SERIES_FILE = ROOT.parent / ".tools" / "fault" / "series.json"

JAVA = "http://127.0.0.1:8080"
PY = "http://127.0.0.1:8100"
PLAN_ID = "2124887242789089282"
CREDENTIAL = {"username": "wx95447477", "password": "weather12345"}

RESULTS: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str) -> bool:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} —— {detail}", flush=True)
    return ok


def skip(name: str, why: str) -> None:
    RESULTS.append((name, "SKIP", why))
    print(f"[SKIP] {name} —— {why}", flush=True)


def http_json(url: str, token: str | None = None, timeout: float = 30.0, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def timed(url: str, token: str | None = None, timeout: float = 30.0):
    start = time.monotonic()
    try:
        payload = http_json(url, token, timeout=timeout)
        return payload, (time.monotonic() - start) * 1000.0, None
    except urllib.error.HTTPError as e:
        return None, (time.monotonic() - start) * 1000.0, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return None, (time.monotonic() - start) * 1000.0, type(e).__name__


def set_mode(mode: str) -> None:
    STATE_FILE.write_text(json.dumps({"mode": mode}), encoding="utf-8")


def call_count() -> int:
    """上游被调用次数。这是"没有请求风暴"的唯一硬证据。"""
    try:
        return sum(1 for _ in LOG_FILE.open(encoding="utf-8"))
    except OSError:
        return 0


def weather_java(token: str, timeout: float = 12.0):
    """Java 统一响应信封是 {code,message,data,traceId}，断言必须落在 data 上。

    第一版忘了这层，导致 P0 直接读到 available=None 并假红一次——
    这正是"断言写在错误的层"的典型：它不会报错，只会告诉你"字段不存在"。
    """
    vo, ms, err = timed(f"{JAVA}/api/plans/{PLAN_ID}/weather", token, timeout=timeout)
    if isinstance(vo, dict) and "data" in vo:
        vo = vo["data"]
    return vo, ms, err


def weather_py(days: int = 3, timeout: float = 12.0):
    start = (date.today() + timedelta(days=1)).isoformat()
    return timed(f"{PY}/v1/weather?city=%E6%88%90%E9%83%BD&start_date={start}&days={days}", None, timeout=timeout)


def date_list(vo: dict | None) -> list[str]:
    return [d.get("date") for d in (vo or {}).get("days") or []]


def login() -> str:
    payload = http_json(f"{JAVA}/api/auth/login", None, method="POST", body=CREDENTIAL)
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
    return payload["accessToken"]


def health_ok() -> tuple[bool, bool]:
    j, _, _ = timed(f"{JAVA}/api/health", timeout=5)
    p, _, _ = timed(f"{PY}/v1/health", timeout=5)
    return j is not None, p is not None


def assert_circuit(py_series: list[dict], calls_at_start: int) -> None:
    """熔断三段式断言：打开 → 冻结 → 半开探测一次后继续冻结。

    这里踩过一个坑，值得写下来：第一版把「耗时 < 500ms」当作"熔断返回"的判据，
    结果假红了两次。原因是注入的 500 在内存里直接抛，一样是毫秒级——
    **真失败和熔断返回从外部完全同形**（reason 同为 UPSTREAM_ERROR）。
    唯一的区别是"这次请求有没有真的打到上游"，所以判据只能是调用计数。

    第二个坑：探测次数最初写成"统计 calls > 熔断时基线的样本数"，
    那会把探测之后的每一次请求都算成一次探测（13 次）。
    探测是**增量事件**，只能数 calls 的**跳变次数**。
    """
    if len(py_series) < 3:
        skip("P3 熔断冻结", "采样太少，无法判定")
        return

    def no_upstream_growth(idx: int) -> bool:
        """这一笔请求没有打到上游（调用数与前一笔相同）。"""
        return py_series[idx]["calls"] == py_series[idx - 1]["calls"]

    open_idx = next(
        (i for i in range(1, len(py_series)) if py_series[i]["ok"] is False and no_upstream_growth(i)),
        None,
    )
    if not record("P3 出现熔断快速失败样本", open_idx is not None,
                  f"{len(py_series)} 次直连请求中，第 {open_idx + 1 if open_idx is not None else '-'} 次起不再打上游"):
        return

    calls_at_open = py_series[open_idx]["calls"]
    attempts = py_series[-1]["calls"] - calls_at_start
    attempts_after_open = py_series[-1]["calls"] - calls_at_open

    # 探测 = 熔断之后 call 计数发生增长的次数（跳变），不是"样本中 calls 大于基线"的个数
    probes = [i for i in range(open_idx + 1, len(py_series))
              if py_series[i]["calls"] > py_series[i - 1]["calls"]]

    record("P3 熔断后上游调用冻结（只允许 1 次半开探测）", attempts_after_open <= 1,
           f"熔断时累计={calls_at_open}，结束时={py_series[-1]['calls']}，新增={attempts_after_open}")
    record("P3 故障期上游调用次数远少于请求次数（无请求风暴）", attempts <= 5,
           f"{len(py_series)} 次直连请求只产生 {attempts} 次上游调用（阈值 3 + 至多 1 次探测）")
    record("P4 冷却到点后只发生 1 次半开探测", len(probes) == 1,
           f"探测次数={len(probes)}" + (f"，发生在 T+{py_series[probes[0]]['t']:.0f}s" if probes else ""))

    if probes:
        gap = py_series[probes[0]]["t"] - py_series[open_idx]["t"]
        record("P4 探测间隔接近配置冷却 300s（±60s）", 240 <= gap <= 360, f"熔断→探测间隔 {gap:.0f}s")
        frozen_after = len(py_series) - 1 - probes[0]
        record("P4 探测失败后冷却延长，窗口内不再探测", len(probes) == 1,
               f"探测后仍有 {frozen_after} 次请求，上游调用 0 次新增")
    else:
        skip("P4 半开探测", "窗口内未到冷却时点（冷却 300s，需 >5 分钟故障期）")


# ============================================================
# 主场景：10 分钟持续 500
# ============================================================

def scenario_outage(total_seconds: int = 600) -> int:
    print("=== 主场景：上游连续失败 10 分钟（生产参数：threshold=3, cooldown=300s）===\n", flush=True)
    token = login()
    set_mode("ok")

    # --- P0 基线 ---
    base, base_ms, err = weather_java(token)
    if not record("P0 基线可用", base is not None and base.get("available") is True,
                  f"available={(base or {}).get('available')} err={err} 耗时={base_ms:.0f}ms"):
        return 1
    base_dates = date_list(base)
    record("P0 source 正确", base.get("source") == "QWEATHER", str(base.get("source")))
    record("P0 非 stale", base.get("stale") in (False, None), str(base.get("stale")))
    calls_after_first = call_count()
    print(f"     基线日期={base_dates} 已记录上游调用={calls_after_first}\n", flush=True)

    # --- P0 预热：缓存命中 ---
    time.sleep(1.0)
    warm, warm_ms, _ = weather_java(token)
    calls_after_warm = call_count()
    record("P0 二次请求命中缓存（上游调用不增）", calls_after_warm == calls_after_first,
           f"调用数 {calls_after_first}→{calls_after_warm}")
    record("P0 缓存命中更快", warm_ms < base_ms, f"{warm_ms:.0f}ms < {base_ms:.0f}ms")
    record("P0 缓存响应日期与基线一致", date_list(warm) == base_dates, f"{date_list(warm)}")

    # --- P1/P2 故障开始 ---
    print("\n--- 故障注入：http_500 持续 10 分钟 ---\n", flush=True)
    set_mode("http_500")
    outage_start = time.monotonic()
    calls_at_outage_start = call_count()

    java_series: list[dict] = []
    py_series: list[dict] = []
    health_checks: list[tuple[bool, bool]] = []
    next_java = 0.0
    next_py = 0.0
    next_health = 0.0

    while True:
        elapsed = time.monotonic() - outage_start
        if elapsed >= total_seconds:
            break
        now = time.monotonic()
        if now >= next_health:
            health_checks.append(health_ok())
            next_health = now + 60
        if now >= next_java:
            vo, ms, e = weather_java(token)
            java_series.append(
                {
                    "t": elapsed,
                    "ok": vo is not None,
                    "available": (vo or {}).get("available"),
                    "stale": (vo or {}).get("stale"),
                    "reason": (vo or {}).get("reason"),
                    "dates": date_list(vo),
                    "ms": ms,
                    "err": e,
                }
            )
            next_java = now + 30
        if now >= next_py:
            vo, ms, e = weather_py()
            py_series.append(
                {
                    "t": elapsed,
                    "ok": vo is not None and vo.get("ok") is True,
                    "reason": (vo or {}).get("reason"),
                    "ms": ms,
                    "err": e,
                    "calls": call_count(),
                }
            )
            next_py = now + 20
        time.sleep(0.5)

    outage_end_calls = call_count()
    set_mode("ok")
    print(f"\n--- 故障结束（上游调用累计 {outage_end_calls}）---\n", flush=True)

    SERIES_FILE.write_text(
        json.dumps(
            {
                "fault": "http_500",
                "duration_seconds": total_seconds,
                "base_dates": base_dates,
                "calls_at_outage_start": calls_at_outage_start,
                "calls_at_outage_end": outage_end_calls,
                "java": java_series,
                "py": py_series,
                "health": health_checks,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )

    # --- 断言：J 轨 ---
    usable = [s for s in java_series if s["ok"]]
    record("P1/P2 Java 侧全程没有 5xx（都拿到结构化响应）", len(usable) == len(java_series),
           f"{len(usable)}/{len(java_series)} 次成功")

    fresh_served = [s for s in java_series if s["available"] is True and s["stale"] in (False, None)]
    stale_served = [s for s in java_series if s["available"] is True and s["stale"] is True]
    record("P1 有效缓存期内照常返回新鲜数据", len(fresh_served) > 0,
           f"{len(fresh_served)} 次 available=true/stale=false")
    record("P2 缓存过期后降级为 stale（旧值仍在，不换成空）", len(stale_served) > 0,
           f"{len(stale_served)} 次 available=true/stale=true")
    record("P2 没有任何一次变成 available=false（有旧值就不该说'暂不可用'）",
           all(s["available"] is True for s in usable),
           f"available 分布={sorted({str(s['available']) for s in usable})}")

    bad_dates = [s for s in usable if s["dates"] != base_dates]
    record("P1/P2 日期始终等于基线（不得前移/后移一天）", not bad_dates,
           f"偏差次数={len(bad_dates)}" + (f" 例如 {bad_dates[0]['dates']}" if bad_dates else ""))

    max_ms = max((s["ms"] for s in usable), default=0)
    record("P1/P2 天气接口最坏耗时 < 6s（Java 超时 5s + 余量）", max_ms < 6000, f"最坏 {max_ms:.0f}ms")

    # --- 断言：P 轨（熔断机制） ---
    #
    # 判据必须用「上游调用计数是否增长」，不能用「耗时短」。
    # 第一版就是用耗时 < 500ms 当"熔断返回"的判据，结果两条断言假红：
    # 注入的 500 是内存里直接抛的，同样 < 500ms —— 真失败与熔断返回从外部**完全同形**
    # （这正是设计 §6.1 预告的问题：两者 reason 一样、耗时相近，运维分不出是"上游挂了"
    #   还是"我们在保护自己"）。只有"这次请求有没有真的打到上游"能区分。
    assert_circuit(py_series, calls_at_outage_start)

    record("P5 全程 Java 健康检查均 200", all(j for j, _ in health_checks), f"{sum(1 for j, _ in health_checks if j)}/{len(health_checks)}")
    record("P5 全程 Python 健康检查均 200", all(p for _, p in health_checks), f"{sum(1 for _, p in health_checks if p)}/{len(health_checks)}")

    # --- 恢复的初步观察（生产冷却 600s，本场大概率等不到） ---
    print("\n--- 故障已结束，观察恢复（生产冷却下可能要等 10 分钟以上）---\n", flush=True)
    time.sleep(5)
    after_vo, after_ms, _ = weather_java(token)
    recovered_now = after_vo is not None and after_vo.get("stale") is not True
    if recovered_now:
        record("P6 故障结束后立即恢复（缓存仍有效，未触碰熔断）", True, f"stale={after_vo.get('stale')}")
    else:
        skip("P6 故障结束后立即恢复", "当前仍在熔断冷却期或缓存 stale，属预期行为（见设计 §4.1 P6）")

    return summarize()


# ============================================================
# 恢复场景：压缩冷却到 20s
# ============================================================

def scenario_recovery() -> int:
    print("=== 恢复场景：压缩冷却 20s，验证熔断能自行解除 ===\n", flush=True)
    token = login()
    set_mode("ok")
    base, _, err = weather_java(token)
    if not record("R0 基线可用", base is not None and base.get("available") is True,
                  f"err={err} 日期={date_list(base)}"):
        return 1

    set_mode("http_500")
    calls0 = call_count()
    # 连续打到熔断（直连 Python，避开 Java 缓存）
    for i in range(6):
        vo, ms, _ = weather_py()
        print(f"     第 {i + 1} 次 直连: ok={(vo or {}).get('ok')} 耗时={ms:.0f}ms 调用累计={call_count()}", flush=True)
        time.sleep(1)
    calls_open = call_count()
    record("R1 连续失败后熔断，上游调用停止增长", calls_open - calls0 <= 3,
           f"6 次请求只产生 {calls_open - calls0} 次上游调用（阈值 3）")

    set_mode("ok")
    print(f"     已切回 ok，等待冷却 20s …", flush=True)
    time.sleep(22)

    probe, ms, _ = weather_py()
    calls_probe = call_count()
    record("R2 冷却到点后半开探测成功并恢复", probe is not None and probe.get("ok") is True,
           f"ok={None if probe is None else probe.get('ok')} 耗时={ms:.0f}ms")
    record("R3 恢复时确实重新请求了上游", calls_probe > calls_open, f"{calls_open}→{calls_probe}")

    # 日期口径：这里的请求是「Python 直连 + 明天起 3 天」，与 Java 用计划日期取的那次
    # 不是同一个窗口。第一版拿 base_dates 去比，必然对不上——那是我在比两样不同的东西。
    expected_py = [(date.today() + timedelta(days=i + 1)).isoformat() for i in range(3)]
    record("R3 恢复后日期正确", date_list(probe) == expected_py, f"{date_list(probe)} 期望 {expected_py}")

    # 缓存断言必须落在 Java 层：Python 侧没有缓存（缓存只在 Java 进程内），
    # 拿直连请求断言"走缓存"是个假命题。
    weather_java(token)  # 预热：让 Java 写入一条新缓存
    c1 = call_count()
    jv2, jms2, _ = weather_java(token)
    c2 = call_count()
    record("R4 Java 恢复后写入缓存，二次请求不再打上游", c2 == c1, f"{c1}→{c2}，耗时 {jms2:.0f}ms")

    jv, jms, _ = weather_java(token)
    record("R5 Java 侧已恢复为新鲜数据", jv is not None and jv.get("available") is True,
           f"available={(jv or {}).get('available')} stale={(jv or {}).get('stale')} 耗时={jms:.0f}ms")

    return summarize()


def summarize() -> int:
    fails = [r for r in RESULTS if r[1] == "FAIL"]
    skips = [r for r in RESULTS if r[1] == "SKIP"]
    passes = [r for r in RESULTS if r[1] == "PASS"]
    print("\n==== 汇总 ====")
    print(f"PASS {len(passes)} / FAIL {len(fails)} / SKIP {len(skips)}")
    if skips:
        print("SKIP 不算通过——没有断言就没有证明力。")
    for name, _, detail in fails:
        print(f"  FAIL: {name} —— {detail}")
    return 1 if fails else 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "outage"
    if mode == "outage":
        seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 600
        return scenario_outage(seconds)
    if mode == "recovery":
        return scenario_recovery()
    print(f"未知模式 {mode}，可选 outage | recovery")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
