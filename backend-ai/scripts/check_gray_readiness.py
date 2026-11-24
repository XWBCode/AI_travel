"""灰度开启前的环境自检（M3-3 · S5 · G1 门槛）。

    # 只看当前 shell 的配置（部署前在服务器上跑）
    ./.venv/Scripts/python.exe scripts/check_gray_readiness.py

    # 同时核对"跑着的服务"与这份配置是否一致（推荐）
    ./.venv/Scripts/python.exe scripts/check_gray_readiness.py --url http://127.0.0.1:8000

## 为什么要有这个脚本

PM 的 G1 前置条件是"生产环境核验完成 + 首批账号确认"。而这件事**不能靠人肉看配置文件**：
- `APP_ENV` 没设，白名单闸门就不生效，但服务照样能起、日志照样正常，肉眼看不出来；
- 两端 `AI_INTERNAL_SECRET` 不一致时，灰度只是"永远不生效"，没有任何错误提示；
- 白名单里写错一个 id，表现和"这人没被加进去"完全一样。

所以把这三件事变成**能返回非 0 退出码的检查**。脚本只读、不打印密钥原文，
每一项都给一句"如果不通过该怎么办"。

## 判据分三档

- `FAIL`：不修就不能开 G1（退出码 1）
- `WARN`：能开，但要明确知道代价（例如本地跑时 env 未声明）
- `OK`：通过

## 刻意不检查的东西

- 和风天气凭据是否有效（那是 D-03 的联调脚本的事，`verify_weather_qweather.py`）
- 上游额度与账单（属运营，不属配置）
- 白名单里的人是否"真的该在里面"（这是 PM 的判断，不是脚本的）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.tools import inject_gate  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []


def report(level: str, item: str, detail: str) -> None:
    RESULTS.append((level, item, detail))
    print(f"[{level:4}] {item} —— {detail}", flush=True)


def mask(secret: str) -> str:
    """只报长度与前 2 位：足够判断"配了没、是不是同一个"，又不足以还原。"""
    if not secret:
        return "（空）"
    return f"{secret[:2]}…（{len(secret)} 位）"


def main() -> int:
    parser = argparse.ArgumentParser(description="灰度开启前的环境自检")
    parser.add_argument("--url", default="", help="可选：AI 服务地址，用来核对运行中的服务")
    args = parser.parse_args()

    settings = Settings.load()
    cfg = settings.weather
    env = (settings.env or "").strip().lower()

    print("=" * 78)
    print("灰度前置自检（G1）")
    print("=" * 78)

    # ---------------------------------------------------------------- 1 环境声明
    if env in ("prod", "production"):
        report("OK", "1. APP_ENV 已声明为生产", f"env={settings.env}")
    elif env in ("dev", "test"):
        report("WARN", "1. APP_ENV 是开发/测试环境",
               f"env={settings.env}：白名单为空时会对整个实例放行，只适合本地")
    else:
        report("FAIL", "1. APP_ENV 未声明",
               "生产忘设时白名单闸门不生效（功能会对所有人开）。设 APP_ENV=prod 或 SPRING_PROFILES_ACTIVE=prod")

    # ---------------------------------------------------------------- 2 数据源
    if cfg.enabled:
        report("OK", "2. 天气数据源已启用", f"provider={cfg.provider}")
    else:
        report("FAIL", "2. 天气数据源未启用", "WEATHER_PROVIDER=none：灰度打开了也不会查天气")

    # ---------------------------------------------------------------- 3 总开关
    if cfg.inject_on_adjust:
        report("WARN", "3. 总开关已是开启状态",
               "G1 未批准前应为 false。若这是演练环境可忽略，生产上请先确认")
    else:
        report("OK", "3. 总开关仍是关闭（PM 要求的默认值）", "WEATHER_INJECT_ON_ADJUST=false")

    # ---------------------------------------------------------------- 4 密钥
    if settings.internal_secret:
        report("OK", "4. 内部共享密钥已配置", mask(settings.internal_secret))
    else:
        report("FAIL", "4. 内部共享密钥为空",
               "AI_INTERNAL_SECRET 空 = 身份一律不可信 = 灰度永远不生效")

    # ---------------------------------------------------------------- 5 白名单
    users = inject_gate.parse_whitelist(cfg.inject_adjust_users)
    if users:
        bad = [item for item in users if not item.isdigit()]
        detail = f"{len(users)} 个：{', '.join(sorted(users))}"
        if bad:
            report("FAIL", "5. 白名单含非法 id", f"{detail}；非数字项 {bad} 永远不会命中")
        else:
            report("OK", "5. 白名单已配置", detail)
    elif env in ("prod", "production"):
        report("FAIL", "5. 生产环境白名单为空", "GRAY_NO_WHITELIST：所有请求都会被拒，功能等于没开")
    else:
        report("WARN", "5. 白名单为空（非生产）", "dev/test 下表示整个实例放行；生产必须配")

    # ---------------------------------------------------------------- 6 急停路径
    kill_path = cfg.inject_adjust_kill_file
    if not kill_path:
        report("WARN", "6. 未配置急停文件路径",
               "出问题只能靠改环境变量 + 重启（L2）。建议配一个运维有写权限的路径")
    else:
        target = Path(kill_path)
        parent = target.parent if target.parent != Path("") else Path(".")
        if target.exists():
            report("FAIL", "6. 急停文件当前存在", f"{kill_path}：服务现在处于急停状态，开启前必须删掉")
        elif parent.exists() and os.access(parent, os.W_OK):
            report("OK", "6. 急停路径可写", kill_path)
        else:
            report("FAIL", "6. 急停路径不可写", f"{kill_path}：出问题时 touch 不出来，等于没有 L1")

    # ---------------------------------------------------------------- 7 运行中的服务
    if args.url:
        check_service(args.url.rstrip("/"), settings.internal_secret)
    else:
        report("WARN", "7. 未核对运行中的服务", "加 --url http://host:port 可以确认服务侧配置与这份一致")

    # ---------------------------------------------------------------- 8 fail-open
    # 第 8 项不是"灰度能不能跑"，而是"这个服务会不会被人白拿"。
    # 指标端点在密钥为空时**不做任何校验**（TD-01），灰度关着的时候它照样能读。
    # 所以它必须和 G1 一样有门槛：生产部署前必须 fail-closed。
    if not settings.internal_secret:
        report("FAIL", "8. 指标端点会 fail-open（TD-01）",
               "AI_INTERNAL_SECRET 为空时 /v1/internal/* 不校验令牌，任何人都能读走观测数据")
    elif args.url:
        check_fail_closed(args.url.rstrip("/"), env)
    else:
        report("WARN", "8. 未核验指标端点是否 fail-closed（TD-01）",
               "加 --url 可实测；TD-01 未修前不得用于生产")

    # ---------------------------------------------------------------- 汇总
    failed = [item for level, item, _ in RESULTS if level == "FAIL"]
    warned = [item for level, item, _ in RESULTS if level == "WARN"]
    print("\n" + "=" * 78)
    print(f"共 {len(RESULTS)} 项：FAIL {len(failed)} / WARN {len(warned)} / "
          f"OK {len(RESULTS) - len(failed) - len(warned)}")
    if failed:
        print("不能开启 G1，先修：" + "、".join(failed))
    elif warned:
        print("可以开启，但已知代价见上面的 WARN：" + "、".join(warned))
    else:
        print("全部通过，可按 ops 文档 §4 开启 G1")
    return 1 if failed else 0


def check_service(base_url: str, secret: str) -> None:
    """核对运行中的服务：密钥是否同值、指标端点是否可达、四类计数是否在。"""
    url = f"{base_url}/v1/internal/metrics/weather-adjust"
    request = urllib.request.Request(url, headers={"X-Internal-Token": secret})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 403:
            report("FAIL", "7. 服务侧密钥与此处不一致", "403：两端 AI_INTERNAL_SECRET 不同值，灰度不会生效")
        else:
            report("FAIL", "7. 指标端点异常", f"HTTP {e.code}")
        return
    except Exception as e:                              # noqa: BLE001 —— 连不上就是连不上
        report("FAIL", "7. 连不上 AI 服务", f"{base_url}：{type(e).__name__}")
        return

    data = payload.get("data") or {}
    missing = {"gray", "outcome", "upstream", "latencyMs"} - set(data.keys())
    if missing:
        report("FAIL", "7. 指标结构不符", f"缺 {sorted(missing)}：服务可能还是旧版本")
        return
    # 这里只说明"带令牌能读到"，是否"真的校验了令牌"由 7.2 用错令牌去证。
    report("OK", "7. 指标端点可读（带令牌）",
           f"gray.allowed={data['gray']['allowed']}，outcome={data['outcome'] or '{}'}")

    # 只验证"对的令牌能进"是不够的：服务端要是压根不校验，任何令牌都能进，
    # 上面这行就会把"两端都没配密钥"误报成"密钥一致"。所以再拿一个错令牌试一次。
    if secret:
        bogus = secret + "-definitely-wrong"
        wrong = urllib.request.Request(url, headers={"X-Internal-Token": bogus})
        try:
            with urllib.request.urlopen(wrong, timeout=5) as response:
                wrong_status = response.status
        except urllib.error.HTTPError as e:
            wrong_status = e.code
        except Exception as e:                          # noqa: BLE001
            report("WARN", "7.2 错令牌探测失败", f"{type(e).__name__}")
            wrong_status = None
        if wrong_status in (401, 403):
            report("OK", "7.2 错令牌被拒绝", f"HTTP {wrong_status}：服务端确实在校验令牌")
        elif wrong_status == 200:
            report("FAIL", "7.2 错令牌也被放行",
                   "服务端根本没校验令牌（多半是 AI_INTERNAL_SECRET 没配到服务端）："
                   "第 7 项的\"可读\"不能当作密钥一致，灰度身份也不可信")
    if data["gray"]["allowed"] and not data["outcome"]:
        report("WARN", "7.1 有放行但无结果计数", "说明计数写入点没生效，检查是否旧版本")


def check_fail_closed(base_url: str, env: str) -> None:
    """不带令牌去读指标端点，看服务是拒绝还是放行。

    这是 TD-01 的现场判据：
    - 403 = 已经 fail-closed（修好了）；
    - 200 = 仍然 fail-open，任何人都能读走观测数据。
    """
    url = f"{base_url}/v1/internal/metrics/weather-adjust"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            status = response.status
    except urllib.error.HTTPError as e:
        status = e.code
    except Exception as e:                              # noqa: BLE001
        report("WARN", "8. 无法核验指标端点（TD-01）", f"{base_url}：{type(e).__name__}")
        return

    if status in (401, 403):
        report("OK", "8. 指标端点拒绝无令牌请求",
               f"无令牌 → HTTP {status}。注意：这只证明“配了密钥时会拒绝”，"
               f"密钥为空时仍会放行（TD-01 未修，修法见技术债登记）")
    elif status == 200:
        level = "FAIL" if env in ("prod", "production") else "WARN"
        report(level, "8. 指标端点仍是 fail-open（TD-01）",
               "无令牌 → HTTP 200：任何人都能读走观测数据；生产部署前必须改成 403")
    else:
        report("WARN", "8. 指标端点返回异常（TD-01）", f"无令牌 → HTTP {status}，需人工确认")


if __name__ == "__main__":
    raise SystemExit(main())
