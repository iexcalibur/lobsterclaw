"""Tests for gateway_tool.py — schema, policy action, config.schema."""
from __future__ import annotations

import pytest

from tools.gateway_tool import TOOL_DEFINITION, _gateway


# ------------------------------------------------------------------
# Schema parity
# ------------------------------------------------------------------

def test_schema_has_action():
    props = TOOL_DEFINITION.parameters["properties"]
    assert "action" in props


def test_schema_has_key_value():
    props = TOOL_DEFINITION.parameters["properties"]
    assert "key" in props
    assert "value" in props


def test_schema_has_raw():
    props = TOOL_DEFINITION.parameters["properties"]
    assert "raw" in props


def test_owner_only_flag():
    assert TOOL_DEFINITION.owner_only is True


# ------------------------------------------------------------------
# Status action (doesn't require external services)
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_action_status():
    result = await _gateway(action="status")
    assert isinstance(result, str)
    assert len(result) > 0


# ------------------------------------------------------------------
# config.schema action returns field names
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_config_schema():
    result = await _gateway(action="config.schema")
    assert "llm_provider" in result or "telegram" in result.lower()


# ------------------------------------------------------------------
# config.get for a known key
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_config_get_existing_key():
    result = await _gateway(action="config.get", key="llm_provider")
    assert isinstance(result, str)
    assert "=" in result or "llm_provider" in result


@pytest.mark.asyncio
async def test_config_get_missing_key():
    # config.get with unknown key returns full config (or a subset); just verify no crash
    result = await _gateway(action="config.get", key="does_not_exist_xyz")
    assert isinstance(result, str)
    assert len(result) > 0


# ------------------------------------------------------------------
# policy action returns summary
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_policy_action():
    result = await _gateway(action="policy")
    assert isinstance(result, str)
    assert len(result) > 0


# ------------------------------------------------------------------
# Unknown action
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_action():
    result = await _gateway(action="explode")
    assert "unknown" in result.lower() or "error" in result.lower()
