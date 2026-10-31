"""OpenRouter vs Anthropic routing for every agent LLM call."""

import pytest

import agencybill.config as cfg
import agencybill.database as database
from agencybill.agents.base import BaseAgent
from agencybill.config import openrouter_model_id, resolve_llm_settings


@pytest.fixture()
def llm_env(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(cfg, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(cfg, "OPENROUTER_BASE_URL", "https://openrouter.ai/api")
    monkeypatch.setattr(cfg, "MODEL", "claude-sonnet-4-6")
    # Ignore model and key overrides saved in the developer database.
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "absent.db")


def test_openrouter_slug_mapping():
    assert openrouter_model_id("claude-sonnet-4-6") == "anthropic/claude-sonnet-4.6"
    assert openrouter_model_id("claude-opus-4-7") == "anthropic/claude-opus-4.7"
    assert openrouter_model_id("claude-haiku-4-5") == "anthropic/claude-haiku-4.5"
    # Dated snapshots and already-qualified slugs are not rewritten.
    assert openrouter_model_id("claude-haiku-4-5-20251001") == "anthropic/claude-haiku-4-5-20251001"
    assert openrouter_model_id("anthropic/claude-sonnet-4.6") == "anthropic/claude-sonnet-4.6"
    assert openrouter_model_id("openai/gpt-4.1") == "openai/gpt-4.1"
    assert openrouter_model_id("claude-fable-5") == "anthropic/claude-fable-5"


def test_openrouter_key_routes_every_call(llm_env, monkeypatch):
    monkeypatch.setattr(cfg, "ANTHROPIC_API_KEY", "sk-ant-direct")
    monkeypatch.setattr(cfg, "OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(cfg, "MODEL", "claude-opus-4-7")

    settings = resolve_llm_settings()
    assert settings.provider == "openrouter"
    assert settings.api_key == "sk-or-test"
    assert settings.base_url == "https://openrouter.ai/api"
    assert settings.model == "anthropic/claude-opus-4.7"
    assert cfg.llm_footer_label() == "OpenRouter · anthropic/claude-opus-4.7"

    agent = BaseAgent("wf-test")
    assert agent.llm.provider == "openrouter"
    assert "openrouter.ai/api" in str(agent.client.base_url)
    assert "v1/v1" not in str(agent.client.base_url)

    captured = {}

    class _Messages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return object()

    agent.client.messages = _Messages()
    agent._create_message([{"role": "user", "content": "hello"}])
    assert captured["model"] == "anthropic/claude-opus-4.7"
    assert captured["system"] == agent.system_prompt


def test_full_openrouter_slug_is_not_remapped(llm_env, monkeypatch):
    monkeypatch.setattr(cfg, "OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(cfg, "MODEL", "openai/gpt-4.1")
    settings = resolve_llm_settings()
    assert settings.model == "openai/gpt-4.1"


def test_anthropic_key_keeps_direct_api(llm_env, monkeypatch):
    monkeypatch.setattr(cfg, "ANTHROPIC_API_KEY", "sk-ant-direct")
    settings = resolve_llm_settings()
    assert settings.provider == "anthropic"
    assert settings.api_key == "sk-ant-direct"
    assert settings.base_url is None
    assert settings.model == "claude-sonnet-4-6"
    assert cfg.llm_footer_label() == "Claude · claude-sonnet-4-6"

    agent = BaseAgent("wf-test")
    assert "api.anthropic.com" in str(agent.client.base_url)


def test_missing_key_raises(llm_env):
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY or ANTHROPIC_API_KEY"):
        BaseAgent("wf-test")
    assert cfg.llm_footer_label() == "LLM not configured"
