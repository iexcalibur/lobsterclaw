"""Tests for exec_tool.py — exec action, process tool schema."""
from __future__ import annotations

import os
import pytest

# Ensure exec is enabled before importing the tool (config may be cached)
os.environ["EXEC_ENABLED"] = "true"
try:
    import config as _cfg_mod
    _cfg_mod._config = None
except ImportError:
    pass

from tools.exec_tool import TOOL_DEFINITION, PROCESS_TOOL_DEFINITION, _exec


# ------------------------------------------------------------------
# Schema checks
# ------------------------------------------------------------------

def test_exec_schema_has_command():
    props = TOOL_DEFINITION.parameters["properties"]
    assert "command" in props


def test_exec_schema_has_timeout():
    props = TOOL_DEFINITION.parameters["properties"]
    assert "timeout" in props


def test_exec_schema_has_yield_ms():
    props = TOOL_DEFINITION.parameters["properties"]
    assert "yield_ms" in props


def test_exec_owner_only():
    assert TOOL_DEFINITION.owner_only is True


def test_process_owner_only():
    assert PROCESS_TOOL_DEFINITION.owner_only is True


def test_process_schema_has_action():
    props = PROCESS_TOOL_DEFINITION.parameters["properties"]
    assert "action" in props


def test_process_schema_has_pid():
    props = PROCESS_TOOL_DEFINITION.parameters["properties"]
    assert "pid" in props


# ------------------------------------------------------------------
# exec simple command
# ------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_config_cache():
    """Reset config cache so EXEC_ENABLED=true takes effect."""
    import config as cfg_mod
    orig = cfg_mod._config
    cfg_mod._config = None
    yield
    cfg_mod._config = orig


@pytest.mark.asyncio
async def test_exec_echo():
    result = await _exec(command="echo hello_pygate")
    assert "hello_pygate" in result


@pytest.mark.asyncio
async def test_exec_exit_code():
    result = await _exec(command="exit 1")
    # Non-zero exit should be reflected
    assert "1" in result or "exit" in result.lower() or "error" in result.lower()


@pytest.mark.asyncio
async def test_exec_timeout():
    result = await _exec(command="sleep 60", timeout=1)
    assert "timeout" in result.lower() or "timed out" in result.lower()


@pytest.mark.asyncio
async def test_exec_no_command():
    # Empty command may succeed (bash runs an empty string) or return an error — either is OK
    result = await _exec(command="")
    assert isinstance(result, str)
