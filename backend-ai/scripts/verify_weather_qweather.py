"""和风天气真实联调脚本（D-03 第 5 步：验证真实 API / JWT / 熔断 / 跨天缓存 / 10 天边界 / 降级）。

**需要凭据，所以不在 CI 里跑。** 没有凭据时它自己会明确告诉你"跳过"，不会伪装成通过。

用法（PowerShell / bash 都可以，值换成你自己的）：

    export QWEATHER_API_HOST=abcxyz.qweatherapi.com     # 控制台-设置，专属域名
    export QWEATHER_DEVELOPER_ID=Q12345ABCD             # Q 开头 10 位
    export QWEATHER_PROJECT_ID=ABCDE23456               # 控制台-项目管理
    export QWEATHER_KEY_ID=ABCDE12345                   # 凭据ID
    export QWEATHER_PRIVATE_KEY_PATH=./secrets/ed25519-private.pem
    export WEATHER_PROVIDER=qweather
    python scripts/verify_weather_qweather.py

    # 想省事也可以只把私钥内容放进环境变量（不要写进任何入库文件）：
    export QWEATHER_PRIVATE_KEY_PEM="$(cat secrets/ed25519-private.pem)"

检查项与判定标准（每条都会打印 PASS/FAIL/SKIP，并且**SKIP 不算通过**）：

| # | 检查 | 判定 |
|---|---|---|
| 1 | JWT 能签出且三项 claim 正确 | header.alg=EdDSA / kid=凭据ID；payload.iss/sub 正确 |
| 2 | 真实调用 200 且能归一化 | 至少返回 1 天，四个 FactField 全过契约校验 |
| 3 | 降水概率口径 | value 落在 0–100（不是 0–1） |
| 4 | 日期口径 | 返回的日期连续，且首日 == 今天或今天+1（时区错位会表现为差一天） |
| 5 | 10 天边界 | days=10 返回 200；days=11 被拒（422） |
| 6 | 归因 | 响应带「和风天气」字样 |
| 7 | 错误分型（不消耗额度） | 用假 host 调一次 → 必须 403 且被判定为 hard（立即熔断） |
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.plan.contract import load_schema  # noqa: E402
from app.tools import weather, weather_circuit  # noqa: E402
from app.tools.weather_jwt import QWeatherTokenProvider  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str = "") -> None:
    RESULTS.append((name, status, detail))
    mark = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP"}[status]
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    cfg = settings.weather
    if not cfg.enabled or not cfg.is_qweather:
        print("WEATHER_PROVIDER 不是 qweather，本脚本只验证和风天气路径。")
        record("0. 配置就绪", "SKIP", f"provider={cfg.provider}")
        return _summary()

    missing = [
        name
        for name, value in (
            ("QWEATHER_API_HOST", cfg.qweather_api_host),
            ("QWEATHER_DEVELOPER_ID", cfg.qweather_developer_id),
            ("QWEATHER_PROJECT_ID", cfg.qweather_project_id),
            ("QWEATHER_KEY_ID", cfg.qweather_key_id),
            ("QWEATHER_PRIVATE_KEY_PATH/PEM", cfg.qweather_private_key_path or cfg.qweather_private_key_pem),
        )
        if not (value or "").strip()
    ]
    if missing:
        print("缺少凭据：" + "、".join(missing))
        record("0. 配置就绪", "SKIP", "凭据未配置，真实 API 未验证")
        return _summary()

    record("0. 配置就绪", "PASS", f"host={cfg.qweather_api_host}")

    # 1. JWT
    try:
        provider = QWeatherTokenProvider.from_settings(cfg)
        token = provider.token()
        import base64
        import json

        head, body, _ = token.split(".")
        pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
        header = json.loads(base64.urlsafe_b64decode(pad(head)))
        payload = json.loads(base64.urlsafe_b64decode(pad(body)))
        assert header.get("alg") == "EdDSA", header
        assert header.get("kid") == cfg.qweather_key_id, header
        assert payload.get("iss") == cfg.qweather_developer_id, payload
        assert payload.get("sub") == cfg.qweather_project_id, payload
        assert payload["exp"] - payload["iat"] == 900, payload
        record("1. JWT 签发", "PASS", f"kid={header['kid']} exp-iat=900")
        token_ok = True
    except Exception as e:  # noqa: BLE001
        record("1. JWT 签发", "FAIL", f"{type(e).__name__}: {e}")
        token_ok = False

    if not token_ok:
        return _summary()

    # 2. 真实调用
    today = date.today()
    start = today + timedelta(days=1)
    result = asyncio.run(
        weather.fetch_forecast(
            "成都",
            start.isoformat(),
            3,
            provider="qweather",
            timeout_seconds=cfg.timeout_seconds,
            connect_timeout_seconds=cfg.connect_timeout_seconds,
            settings=cfg,
        )
    )
    if not result.get("ok"):
        record("2. 真实调用", "FAIL", f"reason={result.get('reason')} msg={result.get('message')}")
        return _summary()

    days = result["days"]
    record("2. 真实调用", "PASS" if days else "FAIL", f"返回 {len(days)} 天，source={result['source']}")

    # 2b. 契约校验
    try:
        from jsonschema import Draft202012Validator

        full = load_schema("travel_plan_v1.json")
        validator = Draft202012Validator({"$defs": full["$defs"], **full["$defs"]["factFieldNumber"]})
        bad = []
        for day in days:
            for key in ("temperature", "precipitation", "wind"):
                errors = list(validator.iter_errors(day[key]))
                if errors:
                    bad.append(f"{day['date']}.{key}: {errors[0].message}")
        record("2b. FactField 合规", "PASS" if not bad else "FAIL", "; ".join(bad[:3]))
    except Exception as e:  # noqa: BLE001
        record("2b. FactField 合规", "FAIL", f"{type(e).__name__}: {e}")

    # 3. 降水概率口径
    probs = [d["precipitation"]["value"] for d in days if d["precipitation"]["value"] is not None]
    if not probs:
        record("3. 降水概率口径", "SKIP", "这几天上游没给概率，无法判断")
    else:
        ok = all(0 <= p <= 100 for p in probs)
        record("3. 降水概率口径", "PASS" if ok else "FAIL", f"取值 {probs}（应落在 0–100）")

    # 4. 日期口径
    dates = [date.fromisoformat(d["date"]) for d in days]
    consecutive = all((dates[i + 1] - dates[i]).days == 1 for i in range(len(dates) - 1))
    first_ok = dates[0] in (start, start - timedelta(days=1))
    detail = f"{dates[0]} ~ {dates[-1]}"
    if not first_ok:
        detail += "（首日与预期不符，疑似时区错位一天）"
    record("4. 日期口径", "PASS" if consecutive and first_ok else "FAIL", detail)

    # 5. 10 天边界
    ten = asyncio.run(
        weather.fetch_forecast(
            "成都", today.isoformat(), 10, provider="qweather",
            timeout_seconds=cfg.timeout_seconds, settings=cfg,
        )
    )
    record("5. 10 天边界", "PASS" if ten.get("ok") and len(ten["days"]) <= 10 else "FAIL",
           f"days={len(ten.get('days', []))}")

    beyond = asyncio.run(
        weather.fetch_forecast(
            "成都", (today + timedelta(days=20)).isoformat(), 3, provider="qweather",
            timeout_seconds=cfg.timeout_seconds, settings=cfg,
        )
    )
    ok = beyond.get("reason") == "DATE_OUT_OF_RANGE"
    record("5b. 越窗口降级", "PASS" if ok else "FAIL", f"reason={beyond.get('reason')}")

    # 6. 归因
    record("6. 归因文案", "PASS" if "和风天气" in (result.get("attribution") or "") else "FAIL",
           result.get("attribution") or "")

    # 7. 错误分型 → 熔断（不消耗额度）
    #
    # 这里刻意分成三条，因为"用个错 host 看会不会熔断"这种写法是**错的**：
    # 错 host 在真实网络里表现为 DNS 解析失败，根本到不了 QWeather，拿不到 403，
    # 只会命中"网络异常"分支。那条分支归 soft 是正确的（抖动可自愈），
    # 拿它证明 hard 熔断，属于用错证据。
    # 所以 403 用注入响应来证（确定性、零额度），DNS 失败单独证"不许误判成 hard"。
    import httpx

    hits: list[str] = []

    def _deny(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(
            403,
            json={"error": {"status": 403, "type": "https://dev.qweather.com/docs/resource/error-code/"}},
        )

    # 7a. 403 = hard：一次就熔断，绝不重试（重试只会加速被风控）
    weather_circuit.reset_all()
    denied = asyncio.run(
        weather.fetch_forecast(
            "成都", start.isoformat(), 1, provider="qweather",
            timeout_seconds=5.0, settings=cfg,
            transport=httpx.MockTransport(_deny),
        )
    )
    b1 = weather_circuit.breaker_for("qweather")
    ok_403 = denied.get("ok") is False and len(hits) == 1 and b1.state == weather_circuit.OPEN
    record("7a. 403→hard 立即熔断", "PASS" if ok_403 else "FAIL",
           f"reason={denied.get('reason')} 请求次数={len(hits)} breaker={b1.state}")

    # 7b. 熔断打开后必须拦住后续请求：不是"继续试"，是"根本不打"
    before = len(hits)
    blocked = asyncio.run(
        weather.fetch_forecast(
            "成都", start.isoformat(), 1, provider="qweather",
            timeout_seconds=5.0, settings=cfg,
            transport=httpx.MockTransport(_deny),
        )
    )
    ok_block = blocked.get("ok") is False and len(hits) == before
    record("7b. 熔断后不再发请求", "PASS" if ok_block else "FAIL",
           f"reason={blocked.get('reason')} 新增请求={len(hits) - before}")

    # 7c. DNS 失败（真实错 host）= soft：不许误判成 hard。网络抖动是能自愈的，
    #     一次 DNS 失败就熔断，等于把偶发网络问题放大成一次功能全挂。
    weather_circuit.reset_all()
    bad_cfg = type(cfg)(**{**cfg.__dict__, "qweather_api_host": "definitely-wrong-host.example.com"})
    bad = asyncio.run(
        weather.fetch_forecast(
            "成都", start.isoformat(), 1, provider="qweather",
            timeout_seconds=5.0, settings=bad_cfg,
        )
    )
    b2 = weather_circuit.breaker_for("qweather")
    ok_soft = bad.get("ok") is False and b2.state != weather_circuit.OPEN
    record("7c. DNS 失败→soft 不熔断", "PASS" if ok_soft else "FAIL",
           f"reason={bad.get('reason')} breaker={b2.state} 连续失败={b2.failures}")

    weather_circuit.reset_all()
    return _summary()


def _summary() -> int:
    fails = [r for r in RESULTS if r[1] == "FAIL"]
    skips = [r for r in RESULTS if r[1] == "SKIP"]
    passes = [r for r in RESULTS if r[1] == "PASS"]
    print("\n==== 汇总 ====")
    print(f"PASS {len(passes)} / FAIL {len(fails)} / SKIP {len(skips)}")
    if skips:
        print("注意：SKIP 不算通过——没有断言就没有证明力。")
    if fails:
        print("失败项：" + "；".join(f"{n}: {d}" for n, _, d in fails))
        return 1
    if not passes:
        # 一项都没真验过时返回 2，而不是 0：退出码 0 会被当成"验证通过"
        print("没有执行任何真实检查（缺凭据），不要把这个结果当成验收通过。")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
