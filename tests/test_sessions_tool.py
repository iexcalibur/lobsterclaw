"""Tests for sessions_tool.py — contract shape, parameter parity."""
from __future__ import annotations

import pytest

from tools.sessions_tool import (
    SESSIONS_SPAWN_TOOL,
    SESSIONS_LIST_TOOL,
    SESSIONS_HISTORY_TOOL,
    SESSIONS_SEND_TOOL,
    SESSION_STATUS_TOOL,
    SUBAGENTS_TOOL,
    AGENTS_LIST_TOOL,
)


# ------------------------------------------------------------------
# Schema parity checks (OpenClaw sessions-spawn-tool.ts params)
# ------------------------------------------------------------------

def test_spawn_has_runtime_param():
    props = SESSIONS_SPAWN_TOOL.parameters["properties"]
    assert "runtime" in props


def test_spawn_has_agent_id_param():
    props = SESSIONS_SPAWN_TOOL.parameters["properties"]
    assert "agent_id" in props


def test_spawn_has_session_key_param():
    props = SESSIONS_SPAWN_TOOL.parameters["properties"]
    assert "session_key" in props


def test_spawn_has_visibility_param():
    props = SESSIONS_SPAWN_TOOL.parameters["properties"]
    assert "visibility" in props


def test_spawn_has_a2a_param():
    props = SESSIONS_SPAWN_TOOL.parameters["properties"]
    assert "a2a" in props


def test_spawn_has_thinking_param():
    props = SESSIONS_SPAWN_TOOL.parameters["properties"]
    assert "thinking" in props


def test_spawn_has_sandbox_param():
    props = SESSIONS_SPAWN_TOOL.parameters["properties"]
    assert "sandbox" in props


def test_spawn_has_attachments_param():
    props = SESSIONS_SPAWN_TOOL.parameters["properties"]
    assert "attachments" in props


# ------------------------------------------------------------------
# sessions_send parity (OpenClaw sessions-send-tool.ts)
# ------------------------------------------------------------------

def test_send_has_visibility_param():
    props = SESSIONS_SEND_TOOL.parameters["properties"]
    assert "visibility" in props


def test_send_has_role_param():
    props = SESSIONS_SEND_TOOL.parameters["properties"]
    assert "role" in props


# ------------------------------------------------------------------
# subagents has steer action
# ------------------------------------------------------------------

def test_subagents_steer_in_schema():
    desc = SUBAGENTS_TOOL.description
    assert "steer" in desc


def test_subagents_message_param():
    props = SUBAGENTS_TOOL.parameters["properties"]
    assert "message" in props
    assert "run_id" in props


# ------------------------------------------------------------------
# All tools have names set
# ------------------------------------------------------------------

@pytest.mark.parametrize("tool", [
    SESSIONS_SPAWN_TOOL,
    SESSIONS_LIST_TOOL,
    SESSIONS_HISTORY_TOOL,
    SESSIONS_SEND_TOOL,
    SESSION_STATUS_TOOL,
    SUBAGENTS_TOOL,
    AGENTS_LIST_TOOL,
])
def test_tool_name_set(tool):
    assert tool.name
    assert tool.fn is not None


# ------------------------------------------------------------------
# sessions_spawn without manager raises or returns error (no crash)
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spawn_no_manager():
    """sessions_spawn with no manager set returns an error string."""
    from agent.subagent import set_subagent_manager
    set_subagent_manager(None)
    try:
        from tools.sessions_tool import _sessions_spawn
        result = await _sessions_spawn(task="test")
        assert isinstance(result, str)
        assert len(result) > 0
    finally:
        from agent.subagent import SubagentManager
        set_subagent_manager(SubagentManager())
