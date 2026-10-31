"""Admin page persistence for the OpenRouter API key."""

from fastapi.testclient import TestClient

import agencybill.config as cfg
from agencybill.agents.base import BaseAgent
from agencybill.config import resolve_llm_settings
from agencybill.database import db
from agencybill.web.app import app
from agencybill.settings_store import (
    clear_model,
    clear_openrouter_api_key,
    mask_api_key,
    set_model,
    set_openrouter_api_key,
)

_DB_KEY = "sk-or-v1-db-override-9k3m"
_ENV_KEY = "sk-or-v1-env-fallback-77aa"
_ADMIN_KEY = "sk-or-v1-admin-panel-demo-7f3a"


def test_db_key_overrides_env(tmp_db, monkeypatch):
    monkeypatch.setattr(cfg, "OPENROUTER_API_KEY", _ENV_KEY)
    monkeypatch.setattr(cfg, "ANTHROPIC_API_KEY", "sk-ant-direct")
    monkeypatch.setattr(cfg, "MODEL", "claude-sonnet-4-6")
    set_openrouter_api_key(_DB_KEY)

    settings = resolve_llm_settings()
    assert settings.provider == "openrouter"
    assert settings.api_key == _DB_KEY
    assert settings.api_key != _ENV_KEY
    assert settings.model == "anthropic/claude-sonnet-4.6"
    assert settings.base_url == "https://openrouter.ai/api"

    agent = BaseAgent("wf-admin")
    assert agent.llm.api_key == _DB_KEY
    assert agent.llm.provider == "openrouter"
    assert "openrouter.ai/api" in str(agent.client.base_url)


def test_clear_falls_back_to_env(tmp_db, monkeypatch):
    monkeypatch.setattr(cfg, "OPENROUTER_API_KEY", _ENV_KEY)
    monkeypatch.setattr(cfg, "ANTHROPIC_API_KEY", "sk-ant-direct")
    monkeypatch.setattr(cfg, "MODEL", "claude-opus-4-7")
    set_openrouter_api_key(_DB_KEY)
    assert resolve_llm_settings().api_key == _DB_KEY

    clear_openrouter_api_key()
    settings = resolve_llm_settings()
    assert settings.provider == "openrouter"
    assert settings.api_key == _ENV_KEY
    assert settings.model == "anthropic/claude-opus-4.7"

    with db() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'openrouter_api_key'"
        ).fetchone()
    assert row is None


def test_masked_display_does_not_contain_full_key(tmp_db, monkeypatch):
    monkeypatch.setattr(cfg, "OPENROUTER_API_KEY", _ENV_KEY)
    monkeypatch.setattr(cfg, "MODEL", "claude-sonnet-4-6")
    set_openrouter_api_key(_DB_KEY)

    hint = mask_api_key(_DB_KEY)
    assert hint == "••••9k3m"
    assert _DB_KEY not in hint
    assert _ENV_KEY not in hint

    client = TestClient(app)
    response = client.get("/admin")
    assert response.status_code == 200
    body = response.text
    assert _DB_KEY not in body
    assert _ENV_KEY not in body
    assert "••••9k3m" in body
    assert "••••77aa" in body
    assert 'type="password"' in body
    assert 'data-provider="openrouter"' in body


def test_admin_route_save_and_clear_updates_llm_settings(tmp_db, monkeypatch):
    monkeypatch.setattr(cfg, "OPENROUTER_API_KEY", _ENV_KEY)
    monkeypatch.setattr(cfg, "ANTHROPIC_API_KEY", "sk-ant-direct")
    monkeypatch.setattr(cfg, "MODEL", "claude-sonnet-4-6")

    client = TestClient(app)
    page = client.get("/admin")
    assert page.status_code == 200
    assert "Admin" in page.text
    assert "not set" in page.text
    assert _ENV_KEY not in page.text
    assert 'data-provider="openrouter"' in page.text
    assert "anthropic/claude-sonnet-4.6" in page.text

    blank = client.post("/admin/openrouter-key", data={"openrouter_api_key": "   "})
    assert blank.status_code == 400
    assert "Enter an OpenRouter API key" in blank.text
    assert resolve_llm_settings().api_key == _ENV_KEY
    assert _ENV_KEY not in blank.text

    saved = client.post(
        "/admin/openrouter-key",
        data={"openrouter_api_key": _ADMIN_KEY},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    location = saved.headers["location"]
    assert location == "/admin?notice=saved"
    assert _ADMIN_KEY not in location

    settings = resolve_llm_settings()
    assert settings.provider == "openrouter"
    assert settings.api_key == _ADMIN_KEY
    assert settings.model == "anthropic/claude-sonnet-4.6"

    shown = client.get(location)
    assert shown.status_code == 200
    assert _ADMIN_KEY not in shown.text
    assert _ENV_KEY not in shown.text
    assert "••••7f3a" in shown.text
    assert "OpenRouter key saved" in shown.text
    assert "OpenRouter · anthropic/claude-sonnet-4.6" in shown.text
    assert 'data-provider="openrouter"' in shown.text

    with db() as conn:
        audit = conn.execute("SELECT details_json FROM audit_events").fetchall()
    assert all(_ADMIN_KEY not in (row["details_json"] or "") for row in audit)

    cleared = client.post("/admin/openrouter-key/clear", follow_redirects=False)
    assert cleared.status_code == 303
    assert _ADMIN_KEY not in cleared.headers["location"]

    settings = resolve_llm_settings()
    assert settings.api_key == _ENV_KEY
    assert settings.provider == "openrouter"

    after = client.get(cleared.headers["location"])
    assert "Saved OpenRouter key cleared" in after.text
    assert "not set" in after.text
    assert _ADMIN_KEY not in after.text
    assert _ENV_KEY not in after.text
    assert "••••77aa" in after.text


def test_admin_route_requires_existing_basic_auth(tmp_db, monkeypatch):
    import agencybill.web.app as webapp

    monkeypatch.setattr(webapp, "WEB_PASSWORD", "secret")
    monkeypatch.setattr(webapp, "WEB_USER", "agencybill")
    monkeypatch.setattr(cfg, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(cfg, "ANTHROPIC_API_KEY", "sk-ant-direct")
    monkeypatch.setattr(cfg, "MODEL", "claude-sonnet-4-6")

    client = TestClient(webapp.app)
    denied = client.get("/admin")
    assert denied.status_code == 401
    denied_post = client.post(
        "/admin/openrouter-key",
        data={"openrouter_api_key": _ADMIN_KEY},
    )
    assert denied_post.status_code == 401
    assert resolve_llm_settings().api_key == "sk-ant-direct"
    assert resolve_llm_settings().provider == "anthropic"

    allowed = client.get("/admin", auth=("agencybill", "secret"))
    assert allowed.status_code == 200
    assert "Admin" in allowed.text
    denied_model = client.post("/admin/model", data={"model": "openai/gpt-4.1"})
    assert denied_model.status_code == 401
    assert resolve_llm_settings().model == "claude-sonnet-4-6"


def test_saved_model_overrides_env_and_admin_field_updates_it(tmp_db, monkeypatch):
    monkeypatch.setattr(cfg, "OPENROUTER_API_KEY", _ENV_KEY)
    monkeypatch.setattr(cfg, "ANTHROPIC_API_KEY", "sk-ant-direct")
    monkeypatch.setattr(cfg, "MODEL", "claude-sonnet-4-6")

    set_model("claude-opus-4-7")
    settings = resolve_llm_settings()
    assert settings.model == "anthropic/claude-opus-4.7"
    assert settings.provider == "openrouter"

    agent = BaseAgent("wf-model")
    assert agent.llm.model == "anthropic/claude-opus-4.7"

    clear_model()
    assert resolve_llm_settings().model == "anthropic/claude-sonnet-4.6"

    client = TestClient(app)
    page = client.get("/admin")
    assert page.status_code == 200
    assert 'id="llm-model"' in page.text
    assert 'name="model"' in page.text
    assert 'value="claude-sonnet-4-6"' in page.text
    assert "Environment variable AGENCYBILL_MODEL" in page.text

    blank = client.post("/admin/model", data={"model": "  "})
    assert blank.status_code == 400
    assert "Enter a model id" in blank.text
    assert resolve_llm_settings().model == "anthropic/claude-sonnet-4.6"

    spaced = client.post("/admin/model", data={"model": "claude sonnet"})
    assert spaced.status_code == 400
    assert "cannot contain spaces" in spaced.text

    saved = client.post(
        "/admin/model",
        data={"model": "openai/gpt-4.1"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == "/admin?notice=model_saved"
    assert resolve_llm_settings().model == "openai/gpt-4.1"

    shown = client.get("/admin?notice=model_saved")
    assert "Model saved" in shown.text
    assert 'value="openai/gpt-4.1"' in shown.text
    assert "Saved model (overrides AGENCYBILL_MODEL)" in shown.text
    assert "OpenRouter · openai/gpt-4.1" in shown.text

    cleared = client.post("/admin/model/clear", follow_redirects=False)
    assert cleared.status_code == 303
    assert resolve_llm_settings().model == "anthropic/claude-sonnet-4.6"
    after = client.get(cleared.headers["location"])
    assert "Saved model cleared" in after.text
    assert 'value="claude-sonnet-4-6"' in after.text
