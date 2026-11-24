"""TD-01：内部端点必须 fail-closed。

为什么单独开一个文件，而不是塞进 `test_inject_gate`：

TD-01 的失效模式不是"校验逻辑写错了"，而是**"没配置时整段校验被跳过"**。
后者用任何"正确令牌能进"的用例都测不出来——所以这里的重点全在三条负例上：
没密钥、没令牌、错令牌。正例只留一条，用来证明"修完还能用"。

关闭判据（与 `docs/ops/安全技术债登记.md` 一致）：
- 密钥为空时该端点返回 403；
- 配了密钥时不带令牌仍返回 403；
- 配了密钥带正确令牌返回 200。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.tools import internal_auth

SECRET = "td01-test-secret"
URL = "/v1/internal/metrics/weather-adjust"


# ---------------------------------------------------------------- 单元：check()

def test_empty_secret_is_denied_not_allowed():
    """核心用例：没配密钥 → 拒绝。

    这就是 TD-01 本身。改回 `if secret:` 的写法会让这条立刻变红。
    """
    denied = internal_auth.check({}, "")
    assert denied is not None, "密钥为空时不得放行"
    assert denied["code"] == internal_auth.CODE_NOT_CONFIGURED


def test_secret_present_but_no_token_is_denied():
    denied = internal_auth.check({}, SECRET)
    assert denied is not None
    assert denied["code"] == internal_auth.CODE_BAD_TOKEN


def test_secret_present_wrong_token_is_denied():
    denied = internal_auth.check({internal_auth.HEADER_TOKEN: "nope"}, SECRET)
    assert denied is not None
    assert denied["code"] == internal_auth.CODE_BAD_TOKEN


def test_correct_token_passes():
    assert internal_auth.check({internal_auth.HEADER_TOKEN: SECRET}, SECRET) is None


def test_token_header_name_is_case_insensitive_via_starlette():
    """Starlette 的 Headers 大小写不敏感；这里钉住我们取头时用的是小写常量。"""
    from starlette.datastructures import Headers

    headers = Headers({"X-Internal-Token": SECRET})
    assert internal_auth.check(headers, SECRET) is None


def test_empty_token_string_with_secret_is_denied():
    """显式空串不能被当成"没带令牌就跳过"。"""
    denied = internal_auth.check({internal_auth.HEADER_TOKEN: ""}, SECRET)
    assert denied is not None


# ---------------------------------------------------------------- 集成：真实路由

@pytest.fixture
def secret(monkeypatch):
    """临时给 settings 配一个密钥，用完自动恢复。"""
    monkeypatch.setattr(settings, "internal_secret", SECRET, raising=False)
    return SECRET


def test_endpoint_denies_when_secret_not_configured(monkeypatch):
    """未配密钥 → 403，且**不返回任何指标数据**。"""
    monkeypatch.setattr(settings, "internal_secret", "", raising=False)
    with TestClient(app) as client:
        resp = client.get(URL)
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == internal_auth.CODE_NOT_CONFIGURED
    assert "data" not in body, "被拒绝时不得夹带指标快照"


def test_endpoint_denies_without_token(secret):
    with TestClient(app) as client:
        resp = client.get(URL)
    assert resp.status_code == 403
    assert resp.json()["code"] == internal_auth.CODE_BAD_TOKEN


def test_endpoint_denies_with_wrong_token(secret):
    with TestClient(app) as client:
        resp = client.get(URL, headers={internal_auth.HEADER_TOKEN: "definitely-wrong"})
    assert resp.status_code == 403


def test_endpoint_works_with_correct_token(secret):
    with TestClient(app) as client:
        resp = client.get(URL, headers={internal_auth.HEADER_TOKEN: secret})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert {"gray", "outcome", "upstream", "latencyMs"} <= set(data.keys())
