"""TD-02：`/v1/*` 接口级鉴权。

这个文件盯三件事：

1. **哪些路径受保护**：`/v1/plan|chat|weather` 要，`/v1/health` 与 `/v1/internal/*` 不要。
   health 必须无条件可读，否则监控和 readiness 都得先造一个密钥。
2. **四档判定的边界**：配了密钥只认密钥；没配密钥时靠 `APP_ENV` 与来源地址兜底。
3. **来源判定只认字面地址**：不解析主机名，不信任 `X-Forwarded-For`（这条链路上没有代理，
   任何客户端都能伪造那个头）。

注意：本文件测的是 `_loopback` 而不是 `is_loopback`——`conftest.py` 的 autouse fixture
会把 `is_loopback` 短路为恒真（为了让 `TestClient` 的存量用例保持原语义）。
需要真实来源判定的用例会把 `is_loopback` 换回 `_loopback`。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.tools import service_guard

SECRET = "td02-test-secret"
HEADER = service_guard.HEADER_TOKEN


# ---------------------------------------------------------------- 路径白/黑名单

@pytest.mark.parametrize("path", [
    "/v1/plan/adjust",
    "/v1/plan/generate",
    "/v1/plan/intent",
    "/v1/plan/stream",
    "/v1/chat/stream",
    "/v1/weather",
])
def test_business_paths_are_protected(path):
    assert service_guard.is_protected(path) is True


@pytest.mark.parametrize("path", [
    "/v1/health",
    "/v1/internal/metrics/weather-adjust",
    "/api/plans",
    "/",
    "/docs",
])
def test_exempt_paths_are_not_protected(path):
    assert service_guard.is_protected(path) is False


# ---------------------------------------------------------------- 来源判定

@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True),
    ("127.0.0.53", True),
    ("::1", True),
    ("172.18.176.1", False),      # Hyper-V 虚拟网卡，不是回环
    ("192.168.1.4", False),       # 真实局域网
    ("10.0.0.8", False),
    ("localhost", False),         # 不解析主机名
    ("", False),
    (None, False),
])
def test_loopback_detection(host, expected):
    assert service_guard._loopback(host) is expected


# ---------------------------------------------------------------- decide：四档

def test_with_secret_correct_token_passes():
    assert service_guard.decide(
        {HEADER: SECRET}, secret=SECRET, env="prod", client_host="8.8.8.8",
    ) is None, "有密钥且令牌正确时，来源不应成为拒绝理由"


def test_with_secret_missing_token_denied():
    denied = service_guard.decide({}, secret=SECRET, env="dev", client_host="127.0.0.1")
    assert denied is not None and denied["code"] == service_guard.CODE_BAD_TOKEN


def test_with_secret_wrong_token_denied():
    denied = service_guard.decide(
        {HEADER: "nope"}, secret=SECRET, env="dev", client_host="127.0.0.1",
    )
    assert denied is not None and denied["code"] == service_guard.CODE_BAD_TOKEN


@pytest.mark.parametrize("env", ["prod", "production", "PROD", " Production "])
def test_no_secret_in_prod_is_denied(env):
    denied = service_guard.decide({}, secret="", env=env, client_host="127.0.0.1")
    assert denied is not None
    assert denied["code"] == service_guard.CODE_NOT_CONFIGURED


def test_no_secret_dev_loopback_passes(monkeypatch):
    """dev + 回环 = 放行。这是保留给本地调试的口子，不是安全漏洞。"""
    monkeypatch.setattr(service_guard, "is_loopback", service_guard._loopback)
    assert service_guard.decide({}, secret="", env="dev", client_host="127.0.0.1") is None
    assert service_guard.decide({}, secret="", env="", client_host="::1") is None


def test_no_secret_dev_non_loopback_is_denied(monkeypatch):
    """关键负例：没配密钥时，外部来源进不来。

    这条是"即使有人把 host 误设成 0.0.0.0，也还有一层"的证据。
    """
    monkeypatch.setattr(service_guard, "is_loopback", service_guard._loopback)
    denied = service_guard.decide({}, secret="", env="dev", client_host="192.168.1.4")
    assert denied is not None
    assert denied["code"] == service_guard.CODE_NOT_LOOPBACK


# ---------------------------------------------------------------- 中间件接线

@pytest.fixture
def with_secret(monkeypatch):
    monkeypatch.setattr(settings, "internal_secret", SECRET, raising=False)
    return SECRET


def test_endpoint_denied_without_token(with_secret):
    with TestClient(app) as client:
        resp = client.get("/v1/weather?city=成都&days=1")
    assert resp.status_code == 403
    assert resp.json()["code"] == service_guard.CODE_BAD_TOKEN


def test_endpoint_passes_with_token(with_secret):
    with TestClient(app) as client:
        resp = client.get("/v1/weather?city=成都&days=1", headers={HEADER: with_secret})
    assert resp.status_code != 403, "带正确令牌不得被闸门拦截"


def test_health_is_always_reachable(with_secret):
    """health 不受闸门影响——否则监控先得配密钥。"""
    with TestClient(app) as client:
        resp = client.get("/v1/health")
    assert resp.status_code == 200


def test_health_is_reachable_even_in_prod_without_secret(monkeypatch):
    monkeypatch.setattr(settings, "internal_secret", "", raising=False)
    monkeypatch.setattr(settings, "env", "prod", raising=False)
    with TestClient(app) as client:
        resp = client.get("/v1/health")
    assert resp.status_code == 200
