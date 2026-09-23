"""Provider 运行时配置（后台填 Key 即生效）测试。"""
from __future__ import annotations

import pytest

from app.core.auth import hash_password
from app.services.provider_config import (
    MASK_PREFIX,
    get_effective,
    llm_configs,
    masked_view,
    oauth_enabled,
    save_fields,
    sync_llm_gateway,
    zhihu_real,
)


def _login_admin(client, db_session):
    from app.models import User as U

    if db_session.query(U).filter(U.username == "cfgadmin").first() is None:
        db_session.add(U(id="usr_cfgadmin", username="cfgadmin", password_hash=hash_password("adminpass123"), role="admin"))
        db_session.commit()
    return client.post("/api/auth/login", json={"username": "cfgadmin", "password": "adminpass123"})


def test_config_defaults_empty_then_real(client, db_session):
    _login_admin(client, db_session)
    r = client.get("/api/admin/providers/config").json()
    assert r["mode"]["zhihu"] == "MOCK"
    assert r["mode"]["web"] == "MOCK"


def test_save_and_mask_flow(client, db_session):
    _login_admin(client, db_session)
    # 保存配置
    r = client.post(
        "/api/admin/providers/config",
        json={"fields": {"zhihu_base_url": "https://api.example-zhihu.com", "zhihu_api_key": "sk-secret-abcd1234"}},
    )
    assert r.status_code == 200
    assert r.json()["mode"]["zhihu"] == "REAL"

    # 读取：key 脱敏（只保留末 4 位），绝不返回完整 Key
    view = client.get("/api/admin/providers/config").json()
    key_item = next(i for i in view["items"] if i["key"] == "zhihu_api_key")
    assert key_item["value"] == MASK_PREFIX + "1234"
    assert "sk-secret" not in key_item["value"]
    assert zhihu_real(db_session) is True

    # 工厂切到 Real Provider
    from app.services.zhihu.search import get_zhihu_provider

    p = get_zhihu_provider(db_session)
    assert p.is_demo is False
    assert p.base_url == "https://api.example-zhihu.com"

    # 掩码原样提交 = 未修改（不会破坏已存 Key）
    r2 = client.post(
        "/api/admin/providers/config",
        json={"fields": {"zhihu_api_key": MASK_PREFIX + "1234"}},
    )
    assert r2.status_code == 200
    assert zhihu_real(db_session) is True

    # 清空 Key → 回退 MOCK
    client.post("/api/admin/providers/config", json={"fields": {"zhihu_api_key": ""}})
    assert zhihu_real(db_session) is False

    # 审计有记录
    logs = client.get("/api/admin/audit").json()
    assert any(i["action"] == "SETTING_CHANGED" and i["target"] == "provider_config" for i in logs["items"][:5])


def test_env_fallback_priority(client, db_session):
    # 无 DB 配置时用 env（测试环境为空 → 空）
    eff = get_effective(db_session)
    assert eff["zhihu_api_key"] == ""
    # DB 优先于 env
    save_fields(db_session, {"zhihu_search_path": "/official/search"})
    eff2 = get_effective(db_session)
    assert eff2["zhihu_search_path"] == "/official/search"


def test_cross_origin_base_url_requires_fresh_or_cleared_key(client, db_session):
    _login_admin(client, db_session)
    first = client.post(
        "/api/admin/providers/config",
        json={"fields": {
            "llm_base_url": "https://api.example.com:443/v1/",
            "llm_api_key": "sk-origin-a",
            "llm_model": "m",
        }},
    )
    assert first.status_code == 200
    assert get_effective(db_session)["llm_base_url"] == "https://api.example.com/v1"

    rejected = client.post(
        "/api/admin/providers/config",
        json={"fields": {"llm_base_url": "https://other.example.com/v1"}},
    )
    assert rejected.status_code == 422
    assert "sk-origin-a" not in rejected.text
    assert get_effective(db_session)["llm_base_url"] == "https://api.example.com/v1"

    # 同 origin 只改路径可以沿用；跨 origin 则可明确清空，且不会重新继承旧值。
    same_origin = client.post(
        "/api/admin/providers/config",
        json={"fields": {"llm_base_url": "https://API.EXAMPLE.COM/v2"}},
    )
    assert same_origin.status_code == 200
    cleared = client.post(
        "/api/admin/providers/config",
        json={"fields": {"llm_base_url": "https://other.example.com/v1", "llm_api_key": ""}},
    )
    assert cleared.status_code == 200
    effective = get_effective(db_session)
    assert effective["llm_base_url"] == "https://other.example.com/v1"
    assert effective["llm_api_key"] == ""


def test_web_key_never_inherits_zhihu_key_cross_origin(client, db_session):
    _login_admin(client, db_session)
    same_origin = client.post(
        "/api/admin/providers/config",
        json={"fields": {"zhihu_api_key": "zhihu-only-secret"}},
    )
    assert same_origin.status_code == 200
    assert get_effective(db_session)["web_api_key"] == "zhihu-only-secret"

    changed = client.post(
        "/api/admin/providers/config",
        json={"fields": {"web_base_url": "https://search.example.net/v1", "web_api_key": ""}},
    )
    assert changed.status_code == 200
    effective = get_effective(db_session)
    assert effective["web_base_url"] == "https://search.example.net/v1"
    assert effective["web_api_key"] == ""
    assert effective["zhihu_api_key"] == "zhihu-only-secret"


def test_custom_paths_flow_into_live_provider_and_reject_external_urls(db_session):
    from app.services.zhihu.search import get_zhihu_provider

    save_fields(db_session, {"zhihu_api_key": "real-secret", "zhihu_search_path": "/custom/search"})
    provider = get_zhihu_provider(db_session)
    assert provider.paths["zhihu_search_path"] == "/custom/search"
    with pytest.raises(ValueError):
        save_fields(db_session, {"web_search_path": "//other.test/path"})
    with pytest.raises(ValueError):
        save_fields(db_session, {"zhihu_search_path": "/x?redirect=//other.test"})


def test_cleared_env_secret_does_not_fall_back_to_env(db_session, monkeypatch):
    from app.services import provider_config

    monkeypatch.setattr(provider_config.settings, "zhihu_api_key", "env-secret")
    assert get_effective(db_session)["zhihu_api_key"] == "env-secret"
    save_fields(db_session, {"zhihu_api_key": ""})
    assert get_effective(db_session)["zhihu_api_key"] == ""
    assert get_zhihu_is_mock(db_session)


def get_zhihu_is_mock(db_session):
    from app.services.zhihu.search import get_zhihu_provider

    return get_zhihu_provider(db_session).is_demo


def test_mock_flag_disables_unconfigured_providers_and_hot(client, db_session):
    from app.services.system_settings import set_setting
    from app.services.zhihu.client import ZhihuProviderError
    from app.services.zhihu.search import get_web_search_provider, get_zhihu_provider

    set_setting(db_session, "enable_mock_provider", False)
    with pytest.raises(ZhihuProviderError):
        get_zhihu_provider(db_session)
    with pytest.raises(ZhihuProviderError):
        get_web_search_provider(db_session)
    assert client.get("/api/hot").status_code == 200
    assert client.get("/api/hot").json()["items"] == []
    _login_admin(client, db_session)
    assert masked_view(db_session)["mode"]["zhihu"] == "DISABLED"
    modes = {item["key"]: item["mode"] for item in client.get("/api/admin/providers").json()["items"]}
    assert modes["zhihu_search"] == modes["web_search"] == modes["zhihu_knowledge"] == "DISABLED"


def test_config_requires_admin(client):
    r = client.get("/api/admin/providers/config")
    assert r.status_code in (401, 403)


def test_llm_probe_is_isolated_and_requires_new_key_for_new_origin(client, db_session, monkeypatch):
    from app.api import routes_admin
    from app.services.llm.gateway import LLMError, gateway

    _login_admin(client, db_session)
    save_fields(db_session, {
        "llm_base_url": "https://93.184.216.34/v1", "llm_api_key": "live-secret", "llm_model": "live-model",
    })
    before = gateway.status_snapshot()
    seen = []

    async def probe_chat(self, *args, **kwargs):
        seen.append((self is gateway, self.api_key, self.base_url, self.record_usage))
        raise LLMError("authentication")

    monkeypatch.setattr(routes_admin.LLMGateway, "chat", probe_chat)
    rejected = client.post("/api/admin/providers/config/test-llm", json={"fields": {
        "llm_base_url": "https://93.184.216.35/v1", "llm_api_key": "••••cret",
    }})
    assert rejected.json()["ok"] is False and not seen
    response = client.post("/api/admin/providers/config/test-llm", json={"fields": {
        "llm_base_url": "https://93.184.216.35/v1", "llm_api_key": "new-secret", "llm_model": "probe-model",
    }})
    assert response.json()["error"] == "authentication"
    assert seen == [(False, "new-secret", "https://93.184.216.35/v1", False)]
    assert gateway.status_snapshot() == before
    assert "new-secret" not in response.text


def test_config_change_increments_generation_and_clears_capabilities(db_session):
    from app.services.llm.gateway import gateway

    save_fields(db_session, {
        "llm_base_url": "https://93.184.216.34/v1", "llm_api_key": "secret", "llm_model": "m",
    })
    initial = gateway.generation
    key = gateway._capability_key(gateway.base_url, gateway.model, gateway.api_key, initial)
    gateway._capabilities[key] = (float("inf"), False, False)
    save_fields(db_session, {"llm_model": "m2"})
    assert gateway.generation == initial + 1
    assert not gateway._capabilities


def test_backup_llm_configs_are_encrypted_ordered_and_hot_loaded(db_session):
    from app.models import SystemSetting
    from app.services.llm.gateway import gateway

    save_fields(db_session, {
        "llm_base_url": "https://93.184.216.34/v1",
        "llm_api_key": "primary-secret",
        "llm_model": "primary-model",
        "llm_fallback_1_base_url": "https://93.184.216.35/v1",
        "llm_fallback_1_api_key": "backup-secret",
        "llm_fallback_1_model": "backup-model",
    })

    configs = llm_configs(db_session)
    assert [item["model"] for item in configs] == ["primary-model", "backup-model"]
    assert gateway.endpoint_count == 2
    raw = db_session.get(SystemSetting, "provider_config").value
    assert str(raw["llm_fallback_1_api_key"]).startswith("enc:v1:")
    assert "backup-secret" not in str(raw)

    # A different Gunicorn worker can begin stale; every status/case boundary
    # resynchronizes it from the encrypted DB configuration.
    gateway.configure_endpoints([], fallback_to_env=False)
    assert gateway.endpoint_count == 0
    sync_llm_gateway(db_session)
    assert gateway.endpoint_count == 2


def test_oauth_config_is_encrypted_masked_and_hot_enabled(client, db_session):
    from app.models import SystemSetting

    _login_admin(client, db_session)
    response = client.post("/api/admin/providers/config", json={"fields": {
        "zhihu_oauth_app_id": "hackathon-app-id",
        "zhihu_oauth_app_key": "oauth-app-secret-9876",
        "zhihu_oauth_redirect_uri": "http://localhost:5173/login/zhihu",
    }})
    assert response.status_code == 200
    assert response.json()["mode"]["oauth"] == "REAL"
    assert oauth_enabled(db_session) is True

    view = client.get("/api/admin/providers/config").json()
    oauth_items = {item["key"]: item for item in view["items"] if item["group"] == "oauth"}
    assert set(oauth_items) == {
        "zhihu_oauth_app_id", "zhihu_oauth_app_key", "zhihu_oauth_redirect_uri",
    }
    assert oauth_items["zhihu_oauth_app_key"]["value"] == MASK_PREFIX + "9876"
    assert "oauth-app-secret" not in str(view)

    raw = db_session.get(SystemSetting, "provider_config").value
    assert str(raw["zhihu_oauth_app_key"]).startswith("enc:v1:")
    assert "oauth-app-secret" not in str(raw)
    providers = client.get("/api/auth/providers").json()
    assert providers == {"zhihu": {"enabled": True, "callback_path": "/login/zhihu"}}


@pytest.mark.parametrize("redirect_uri", [
    "https://evil.example/login/zhihu",
    "http://localhost:5173/login/zhihu?next=/admin",
    "http://localhost:5173/not-the-callback",
])
def test_oauth_redirect_uri_rejects_unregistered_or_ambiguous_targets(
    client, db_session, redirect_uri,
):
    _login_admin(client, db_session)
    response = client.post("/api/admin/providers/config", json={"fields": {
        "zhihu_oauth_redirect_uri": redirect_uri,
    }})
    assert response.status_code == 422
    assert get_effective(db_session)["zhihu_oauth_redirect_uri"] == ""
