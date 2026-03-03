"""Tests for config loading and validation."""
import os
import pytest
from unittest.mock import patch


def _make_config(**overrides):
    """Create a Config with minimal required env vars + overrides."""
    env = {
        "TELEGRAM_BOT_TOKEN": "test:token",
        "TELEGRAM_OWNER_ID": "12345",
        "ANTHROPIC_API_KEY": "sk-test",
        "LLM_PROVIDER": "anthropic",
        **{k: str(v) for k, v in overrides.items()},
    }
    with patch.dict(os.environ, env, clear=True):
        import importlib
        import config as config_mod
        config_mod._config = None  # reset singleton
        importlib.reload(config_mod)
        return config_mod.Config().validate()


def test_default_provider():
    cfg = _make_config()
    assert cfg.llm_provider == "anthropic"


def test_custom_model():
    cfg = _make_config(LLM_MODEL="gpt-4o")
    assert cfg.llm_model == "gpt-4o"


def test_tools_allow_list():
    cfg = _make_config(TOOLS_ALLOW="web_fetch,web_search,read")
    assert "web_fetch" in cfg.tools_allow
    assert "web_search" in cfg.tools_allow
    assert "exec" not in cfg.tools_allow


def test_tools_deny_overrides_allow():
    cfg = _make_config(TOOLS_ALLOW="web_fetch,exec", TOOLS_DENY="exec")
    assert "exec" in cfg.tools_deny


def test_cron_disabled():
    cfg = _make_config(CRON_ENABLED="false")
    assert cfg.cron_enabled is False


def test_exec_disabled_default():
    cfg = _make_config()
    assert cfg.exec_enabled is False


def test_memory_enabled_default():
    cfg = _make_config()
    assert cfg.memory_enabled is True


def test_validation_missing_token():
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        env = {"TELEGRAM_OWNER_ID": "12345", "ANTHROPIC_API_KEY": "sk-x", "LLM_PROVIDER": "anthropic"}
        with patch.dict(os.environ, env, clear=True):
            import config as config_mod
            config_mod._config = None
            config_mod.Config().validate()


def test_validation_missing_api_key():
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        env = {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_OWNER_ID": "12345", "LLM_PROVIDER": "anthropic"}
        with patch.dict(os.environ, env, clear=True):
            import config as config_mod
            config_mod._config = None
            config_mod.Config().validate()


def test_data_path_property():
    cfg = _make_config(DATA_DIR="/tmp/pygate_test")
    from pathlib import Path
    assert cfg.data_path == Path("/tmp/pygate_test")


def test_web_search_providers():
    cfg = _make_config(WEB_SEARCH_PROVIDER="grok")
    assert cfg.web_search_provider == "grok"


def test_memory_semantic_flag():
    cfg = _make_config(MEMORY_SEMANTIC="true")
    assert cfg.memory_semantic is True


def test_subagents_config():
    cfg = _make_config(SUBAGENTS_MAX_DEPTH="5", SUBAGENTS_MAX_CHILDREN="10")
    assert cfg.subagents_max_depth == 5
    assert cfg.subagents_max_children == 10
