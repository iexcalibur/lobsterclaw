"""Tests for the ToolRegistry."""
import os
import pytest
from unittest.mock import patch


def _env_patch():
    return patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "tok",
        "TELEGRAM_OWNER_ID": "1",
        "ANTHROPIC_API_KEY": "sk",
        "LLM_PROVIDER": "anthropic",
        "TOOLS_ALLOW": "web_fetch,exec",
        "TOOLS_DENY": "browser",
    }, clear=True)


def _make_registry():
    import config as cfg_mod
    cfg_mod._config = None
    from tools.registry import ToolRegistry, ToolDefinition

    async def _noop(**kw):
        return "ok"

    registry = ToolRegistry()
    for name in ("web_fetch", "exec", "browser", "write"):
        registry.register(ToolDefinition(
            name=name,
            description=f"Tool {name}",
            parameters={"type": "object", "properties": {}, "required": []},
            fn=_noop,
        ))
    return registry


@pytest.mark.asyncio
async def test_allowed_tool_runs():
    with _env_patch():
        registry = _make_registry()
        result = await registry.execute("web_fetch", {"url": "http://example.com"})
        assert result == "ok"


@pytest.mark.asyncio
async def test_denied_tool_blocked():
    with _env_patch():
        registry = _make_registry()
        result = await registry.execute("browser", {"action": "navigate"})
        assert "denied" in result.lower() or "blocked" in result.lower() or "not allowed" in result.lower()


@pytest.mark.asyncio
async def test_tool_not_in_allow_list_blocked():
    with _env_patch():
        registry = _make_registry()
        result = await registry.execute("write", {"path": "/tmp/x", "content": "x"})
        assert "not allowed" in result.lower() or "denied" in result.lower() or "blocked" in result.lower()


def test_get_names_returns_all_registered():
    with _env_patch():
        registry = _make_registry()
        names = registry.get_names()
        assert isinstance(names, list)


def test_get_anthropic_tools():
    with _env_patch():
        registry = _make_registry()
        tools = registry.get_anthropic_tools()
        assert isinstance(tools, list)
        for t in tools:
            assert "name" in t
            assert "description" in t
            assert "input_schema" in t


def test_get_openai_tools():
    with _env_patch():
        registry = _make_registry()
        tools = registry.get_openai_tools()
        assert isinstance(tools, list)
        for t in tools:
            assert t["type"] == "function"
            assert "function" in t
