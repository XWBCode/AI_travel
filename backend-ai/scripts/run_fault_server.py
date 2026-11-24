"""启动一个「上游可被人为搞坏」的天气服务实例。

用法（由 verify_weather_outage.py 调用，一般不用手动跑）：

    FAULT_STATE_FILE=/tmp/w.json FAULT_LOG_FILE=/tmp/w.jsonl \
    WEATHER_PROVIDER=qweather WEATHER_CACHE_TTL_MINUTES=1 \
    python scripts/run_fault_server.py --port 8100

要点：
- 凭据用**临时生成**的 Ed25519 密钥：签名要真算（401 重试路径不能被绕过），
  但请求根本不会发出去，所以不需要真账号，也不可能骚扰到和风天气。
- 产品代码零改动：只在 uvicorn 起来之前替换 provider 的出网点。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    # 生成临时密钥：JWT 必须真的签（否则测不到签名/401 分支），但不会发出去
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    import os

    os.environ.setdefault("QWEATHER_API_HOST", "fault.local.invalid")
    os.environ.setdefault("QWEATHER_DEVELOPER_ID", "QFAULT00001")
    os.environ.setdefault("QWEATHER_PROJECT_ID", "FAULTPROJ01")
    os.environ.setdefault("QWEATHER_KEY_ID", "FAULTKEY001")
    os.environ["QWEATHER_PRIVATE_KEY_PEM"] = pem

    import fault_upstream

    fault_upstream.install()

    from app.main import create_app

    print(
        f"[fault-server] 已启动 :{args.port} "
        f"state={fault_upstream.STATE_FILE} log={fault_upstream.LOG_FILE}",
        flush=True,
    )
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
