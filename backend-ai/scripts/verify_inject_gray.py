"""M3-3 · S5 端到端验收：灰度白名单真的"只对名单里的人开"。

    APP_ENV=dev WEATHER_PROVIDER=open-meteo \
    .venv/Scripts/python.exe scripts/verify_inject_gray.py

## 这个脚本回答 PM 的六条验收标准

| # | 验收标准 | 对应断言 |
|---|---|---|
| 1 | 白名单用户确实开启，非白名单用户确实关闭 | 5.x / 6.x / 11.1 |
| 2 | 默认关闭状态没有发生变化 | 1.x（Prompt 逐字相同） |
| 3 | 灰度关闭后能够恢复旧行为 | 9.3（急停文件删除后重新注入） |
| 4 | 观测指标能区分成功、失败、熔断和拦截 | 10.x |
| 5 | 原有 Python / Java / 前端回归全部通过 | 脚本外的回归，见验收报告 |
| 6 | 提供完整测试证据与回滚步骤 | 本脚本 + `docs/ops/天气调整灰度与回滚.md` |

## 为什么全部用真 HTTP 头，而不是直接调 `decide()`

`decide()` 的单测在 `tests/test_inject_gate.py` 里（26 项）。这里要证的是**从请求头到
Prompt** 这一整条链：路由有没有解析头、身份有没有传进 `run_adjust`、拒绝了会不会仍然
返回合法建议。只测纯函数的话，这三件事一件都证明不了。

## 环境怎么造

`settings` 是进程级单例，脚本直接改再还原（`_env()` 上下文管理器）。
不这样做就得为每种组合起一个进程，跑一次几分钟，且无法断言"同一个进程里配置切换后行为立刻变"。
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ["APP_ENV"] = "dev"
os.environ["WEATHER_PROVIDER"] = "open-meteo"
os.environ["LLM_PROVIDER"] = "mock"
os.environ.pop("WEATHER_INJECT_ON_ADJUST", None)
os.environ.pop("WEATHER_INJECT_ADJUST_USERS", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.plan.adjust import validate_ops  # noqa: E402
from app.tools import adjust_metrics, inject_gate, weather_circuit, weather_inject  # noqa: E402
from app.tools.weather_providers import open_meteo  # noqa: E402
import wx_echo as w  # noqa: E402

TODAY = date.today()
SECRET = "gray-e2e-secret"
WHITELIST_USER = "1001"
OTHER_USER = "2002"

RESULTS: list[tuple[str, str, str]] = []


def check(item: str, ok: bool, detail: str) -> bool:
    status = "PASS" if ok else "FAIL"
    RESULTS.append((item, status, detail))
    print(f"[{status}] {item} —— {detail}", flush=True)
    return ok


@contextlib.contextmanager
def env(**overrides):
    """临时改配置，退出时还原。**包括 env 与 secret** —— 它们也是判定输入。"""
    snapshot = {
        "env": settings.env,
        "internal_secret": settings.internal_secret,
        "inject_on_adjust": settings.weather.inject_on_adjust,
        "inject_adjust_users": settings.weather.inject_adjust_users,
        "inject_adjust_kill_file": settings.weather.inject_adjust_kill_file,
        "provider": settings.weather.provider,
    }
    try:
        if "env" in overrides:
            settings.env = overrides["env"]
        if "secret" in overrides:
            settings.internal_secret = overrides["secret"]
        for key in ("inject_on_adjust", "inject_adjust_users", "inject_adjust_kill_file"):
            if key in overrides:
                setattr(settings.weather, key, overrides[key])
        yield
    finally:
        settings.env = snapshot["env"]
        settings.internal_secret = snapshot["internal_secret"]
        settings.weather.inject_on_adjust = snapshot["inject_on_adjust"]
        settings.weather.inject_adjust_users = snapshot["inject_adjust_users"]
        settings.weather.inject_adjust_kill_file = snapshot["inject_adjust_kill_file"]
        settings.weather.provider = snapshot["provider"]


def plan_of() -> dict:
    dates = [(TODAY - timedelta(days=1)).isoformat(), TODAY.isoformat()]
    return {
        "title": "成都两日",
        "totalDays": 2,
        "startDate": TODAY.isoformat(),
        "status": "IN_PROGRESS",
        "days": [
            {"dayIndex": index + 1, "date": day, "city": "成都", "theme": None,
             "items": [{"itemId": str(index + 1), "itemOrder": 1, "title": "宽窄巷子",
                        "startTime": "09:00", "endTime": "11:00", "note": None}]}
            for index, day in enumerate(dates)
        ],
    }


def suggest() -> dict:
    return {"summary": "把第一项挪到下午",
            "ops": [{"op": "UPDATE_ITEM", "itemId": "2",
                     "changes": [{"field": "startTime", "after": "15:00"}]}]}


def identity_headers(user_id: str, trace: str, secret: str = SECRET) -> dict:
    return {
        inject_gate.HEADER_USER: user_id,
        inject_gate.HEADER_SIG: inject_gate.sign(secret, user_id, trace),
    }


def main() -> int:
    print(f"今天 {TODAY}", flush=True)
    app.state.provider = w.EchoProvider(suggest())
    client = TestClient(app)
    stub = w.WeatherStub()
    open_meteo.fetch = stub
    plan = plan_of()

    def adjust(headers: dict | None = None, trace: str = "t", *,
               mode: str = w.MODE_OK) -> tuple[dict, w.EchoProvider]:
        weather_inject.reset_cache()
        weather_circuit.reset_all()
        stub.reset(mode=mode)
        stub.reset_calls()
        provider = w.EchoProvider(suggest())
        app.state.provider = provider
        headers = dict(headers or {})
        headers.setdefault("X-Trace-Id", trace)
        body = client.post("/v1/plan/adjust",
                           json={"instruction": "下午太累，改到三点", "plan": plan},
                           headers=headers).json()
        return body, provider

    def ok_body(body: dict) -> bool:
        data = body.get("data") or {}
        return body.get("code") == 0 and bool(data.get("ops")) and not validate_ops(data.get("ops"), plan)

    # ---------------------------------------------------------------- 1 默认状态
    print("\n=== 1 默认状态（什么都没配）===", flush=True)
    adjust_metrics.reset()
    body, provider = adjust()
    text = w.last_user(provider.sent)
    expected = ("【当前计划】\n" + json.dumps(plan, ensure_ascii=False, indent=2)
                + "\n\n【用户要求】\n下午太累，改到三点")
    check("1.1 默认 0 次上游调用", stub.calls == [], f"上游 {len(stub.calls)} 次")
    check("1.2 Prompt 与改造前逐字相同", text == expected, "拼接式期望值 == 全等")
    check("1.3 仍返回合法建议", ok_body(body), f"code={body.get('code')}")

    # ---------------------------------------------------------------- 2 生产 + 无白名单
    print("\n=== 2 prod + 开关开 + 白名单空 ===", flush=True)
    adjust_metrics.reset()
    with env(env="prod", secret=SECRET, inject_on_adjust=True, inject_adjust_users=""):
        body, provider = adjust(identity_headers(WHITELIST_USER, "p1"), trace="p1")
        snap = adjust_metrics.snapshot()
    check("2.1 生产没配白名单 → 0 次上游（fail-closed）", stub.calls == [], f"上游 {len(stub.calls)} 次")
    check("2.2 拒绝原因记为 GRAY_NO_WHITELIST",
          snap["gray"]["denied"].get(inject_gate.DENY_NO_WHITELIST) == 1, str(snap["gray"]))
    check("2.3 仍返回合法建议", ok_body(body), f"code={body.get('code')}")

    # ---------------------------------------------------------------- 3 白名单用户
    print("\n=== 3 prod + 白名单命中 ===", flush=True)
    adjust_metrics.reset()
    with env(env="prod", secret=SECRET, inject_on_adjust=True, inject_adjust_users=WHITELIST_USER):
        body, provider = adjust(identity_headers(WHITELIST_USER, "g1"), trace="g1")
        text = w.last_user(provider.sent)
        snap = adjust_metrics.snapshot()
    check("3.1 白名单用户被放行（注入块出现）", "【已核实的天气预报】" in text, "有注入块")
    check("3.2 白名单用户 1 次上游调用", len(stub.calls) == 1, f"上游 {len(stub.calls)} 次")
    check("3.3 灰度放行计数 +1", snap["gray"]["allowed"] == 1, str(snap["gray"]))
    check("3.4 结果记为 injected", snap["outcome"].get("injected") == 1, str(snap["outcome"]))
    check("3.5 仍返回合法建议", ok_body(body), f"code={body.get('code')}")

    # ---------------------------------------------------------------- 4 非白名单用户
    print("\n=== 4 prod + 身份可信但不在名单里 ===", flush=True)
    adjust_metrics.reset()
    with env(env="prod", secret=SECRET, inject_on_adjust=True, inject_adjust_users=WHITELIST_USER):
        body, provider = adjust(identity_headers(OTHER_USER, "n1"), trace="n1")
        text = w.last_user(provider.sent)
        snap = adjust_metrics.snapshot()
    check("4.1 非白名单用户 0 次上游", stub.calls == [], f"上游 {len(stub.calls)} 次")
    check("4.2 非白名单用户无注入块", "【已核实的天气预报】" not in text, "无注入块")
    check("4.3 拒绝原因记为 NOT_IN_WHITELIST",
          snap["gray"]["denied"].get(inject_gate.DENY_NOT_WHITELISTED) == 1, str(snap["gray"]))
    check("4.4 仍返回合法建议（灰度拒绝不影响调整本身）", ok_body(body), f"code={body.get('code')}")

    # ---------------------------------------------------------------- 5 身份缺失/伪造
    print("\n=== 5 prod + 身份不可信（三种伪造）===", flush=True)
    cases = [
        ("5.1 完全不带头", {}, "无身份头"),
        ("5.2 只有 X-User-Id，没有签名", {inject_gate.HEADER_USER: WHITELIST_USER}, "缺签名"),
        ("5.3 签名用别的密钥算", identity_headers(WHITELIST_USER, "f3", secret="wrong"), "签名不符"),
        ("5.4 用别的 trace 的签名（重放）",
         {inject_gate.HEADER_USER: WHITELIST_USER,
          inject_gate.HEADER_SIG: inject_gate.sign(SECRET, WHITELIST_USER, "other-trace")}, "跨请求重放"),
        ("5.5 身份写成非数字", {inject_gate.HEADER_USER: "admin",
                                inject_gate.HEADER_SIG: inject_gate.sign(SECRET, "admin", "f5")}, "非数字"),
    ]
    for label, headers, note in cases:
        adjust_metrics.reset()
        with env(env="prod", secret=SECRET, inject_on_adjust=True,
                 inject_adjust_users=WHITELIST_USER):
            trace = label.split(" ")[0]
            body, provider = adjust(headers, trace=trace)
            snap = adjust_metrics.snapshot()
        check(f"{label} → 0 次上游", stub.calls == [], f"{note}；上游 {len(stub.calls)} 次")
        check(f"{label} → 记 IDENTITY_UNTRUSTED",
              snap["gray"]["denied"].get(inject_gate.DENY_UNTRUSTED) == 1, str(snap["gray"]))
        check(f"{label} → 仍返回合法建议", ok_body(body), f"code={body.get('code')}")

    # ---------------------------------------------------------------- 6 服务端没配密钥
    print("\n=== 6 服务端未配置共享密钥 ===", flush=True)
    adjust_metrics.reset()
    with env(env="prod", secret="", inject_on_adjust=True, inject_adjust_users=WHITELIST_USER):
        body, _ = adjust(identity_headers(WHITELIST_USER, "s1"), trace="s1")
        snap = adjust_metrics.snapshot()
    check("6.1 没密钥时即使头齐全也不放行", stub.calls == [], f"上游 {len(stub.calls)} 次")
    check("6.2 原因仍是 IDENTITY_UNTRUSTED",
          snap["gray"]["denied"].get(inject_gate.DENY_UNTRUSTED) == 1, str(snap["gray"]))

    # ---------------------------------------------------------------- 7 大小写/空格容错
    print("\n=== 7 白名单书写容错 ===", flush=True)
    adjust_metrics.reset()
    with env(env="prod", secret=SECRET, inject_on_adjust=True,
             inject_adjust_users=f"  {WHITELIST_USER} , , 9999 "):
        body, provider = adjust(identity_headers(WHITELIST_USER, "w1"), trace="w1")
        text = w.last_user(provider.sent)
    check("7.1 名单里的空格与空项不影响命中", "【已核实的天气预报】" in text, "注入块出现")

    # ---------------------------------------------------------------- 8 dev 实例级
    print("\n=== 8 dev + 无白名单（实例级，S4 兼容）===", flush=True)
    adjust_metrics.reset()
    with env(env="dev", secret="", inject_on_adjust=True, inject_adjust_users=""):
        body, provider = adjust()
        text = w.last_user(provider.sent)
    check("8.1 dev 无白名单仍然注入（S4 的 63 项矩阵靠这条）",
          "【已核实的天气预报】" in text, "有注入块")

    # ---------------------------------------------------------------- 9 急停
    print("\n=== 9 运行时急停与恢复 ===", flush=True)
    kill_file = ROOT / ".tools" / "gray-kill-test.flag"
    kill_file.parent.mkdir(parents=True, exist_ok=True)
    if kill_file.exists():
        kill_file.unlink()
    adjust_metrics.reset()
    with env(env="prod", secret=SECRET, inject_on_adjust=True,
             inject_adjust_users=WHITELIST_USER, inject_adjust_kill_file=str(kill_file)):
        body, provider = adjust(identity_headers(WHITELIST_USER, "k1"), trace="k1")
        snap = adjust_metrics.snapshot()
        check("9.1 急停文件不存在时正常注入",
              "【已核实的天气预报】" in w.last_user(provider.sent), "有注入块")

        kill_file.write_text("stop", encoding="utf-8")
        body, provider = adjust(identity_headers(WHITELIST_USER, "k2"), trace="k2")
        snap_killed = adjust_metrics.snapshot()
        check("9.2 急停文件出现后立即停止（不重启）", stub.calls == [], f"上游 {len(stub.calls)} 次")
        check("9.3 拒绝原因记为 GRAY_KILLED",
              snap_killed["gray"]["denied"].get(inject_gate.DENY_KILLED) == 1, str(snap_killed["gray"]))

        kill_file.unlink()
        body, provider = adjust(identity_headers(WHITELIST_USER, "k3"), trace="k3")
        text = w.last_user(provider.sent)
        check("9.4 删除急停文件后恢复注入（旧行为回来了）",
              "【已核实的天气预报】" in text and len(stub.calls) == 1,
              f"上游 {len(stub.calls)} 次")
        check("9.5 恢复后仍返回合法建议", ok_body(body), f"code={body.get('code')}")

    # ---------------------------------------------------------------- 10 观测口径
    print("\n=== 10 观测指标四类可分 ===", flush=True)
    adjust_metrics.reset()
    with env(env="prod", secret=SECRET, inject_on_adjust=True, inject_adjust_users=WHITELIST_USER):
        adjust(identity_headers(WHITELIST_USER, "m1"), trace="m1")       # injected
        adjust(identity_headers(OTHER_USER, "m2"), trace="m2")           # gray_denied
        adjust(identity_headers(WHITELIST_USER, "m3"), trace="m3",
               mode=w.MODE_500)                                          # no_weather（上游 500）
        snap = adjust_metrics.snapshot()
    outcome = snap["outcome"]
    check("10.1 成功与拒绝分开计数",
          outcome.get("injected") == 1 and outcome.get("gray_denied") == 1, str(outcome))
    check("10.2 上游失败不被算成熔断", outcome.get("circuit_open") is None, str(outcome))
    check("10.3 灰度放行/拒绝分开", snap["gray"]["allowed"] == 2, str(snap["gray"]))
    check("10.4 延迟样本只来自放行的请求",
          snap["latencyMs"]["weather"]["samples"] == 2, str(snap["latencyMs"]))
    check("10.5 上游调用数与城市数可算缓存节省率",
          snap["upstream"]["cities"] >= snap["upstream"]["calls"] >= 0
          and 0.0 <= snap["upstream"]["cacheSavedRate"] <= 1.0, str(snap["upstream"]))

    # ---------------------------------------------------------------- 11 响应体不泄漏身份
    print("\n=== 11 身份不出后端 ===", flush=True)
    with env(env="prod", secret=SECRET, inject_on_adjust=True, inject_adjust_users=WHITELIST_USER):
        body, _ = adjust(identity_headers(WHITELIST_USER, "x1"), trace="x1")
    raw = json.dumps(body, ensure_ascii=False)
    check("11.1 响应里没有 userId / 签名 / 白名单",
          WHITELIST_USER not in raw and "Identity-Sig" not in raw and SECRET not in raw,
          f"响应键 {sorted(body.keys())}")
    check("11.2 响应结构与改造前一致",
          sorted((body.get("data") or {}).keys()) == ["mock", "ops", "summary"],
          str(sorted((body.get("data") or {}).keys())))

    # ---------------------------------------------------------------- 12 指标端点鉴权
    print("\n=== 12 观测端点不裸奔 ===", flush=True)
    with env(env="prod", secret=SECRET, inject_on_adjust=True, inject_adjust_users=WHITELIST_USER):
        no_token = client.get("/v1/internal/metrics/weather-adjust")
        with_token = client.get("/v1/internal/metrics/weather-adjust",
                                headers={"X-Internal-Token": SECRET})
        wrong_token = client.get("/v1/internal/metrics/weather-adjust",
                                 headers={"X-Internal-Token": "nope"})
    check("12.1 配了密钥时无令牌 → 403", no_token.status_code == 403, f"{no_token.status_code}")
    check("12.2 令牌不对 → 403", wrong_token.status_code == 403, f"{wrong_token.status_code}")
    check("12.3 令牌正确 → 200 且含四类计数",
          with_token.status_code == 200
          and {"gray", "outcome", "upstream", "latencyMs"} <= set(with_token.json()["data"].keys()),
          f"{with_token.status_code}")

    # ---------------------------------------------------------------- 汇总
    failed = [item for item, status, _ in RESULTS if status == "FAIL"]
    print("\n" + "=" * 78, flush=True)
    print(f"共 {len(RESULTS)} 项断言：PASS {len(RESULTS) - len(failed)} / FAIL {len(failed)}", flush=True)
    if failed:
        print("失败项：" + "、".join(failed), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
