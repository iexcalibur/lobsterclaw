"""Tests for channel_stubs.py — schema parity and disabled-channel guard."""
from __future__ import annotations

import os
import pytest

# Ensure all channels are disabled during tests
os.environ.setdefault("DISCORD_ENABLED", "false")
os.environ.setdefault("SLACK_ENABLED", "false")
os.environ.setdefault("WHATSAPP_ENABLED", "false")

from tools.channel_stubs import (
    DISCORD_TOOL,
    SLACK_TOOL,
    WHATSAPP_TOOL,
    _discord,
    _slack,
    _whatsapp,
)


# ------------------------------------------------------------------
# Schema presence
# ------------------------------------------------------------------

def test_discord_schema():
    assert DISCORD_TOOL.name == "discord"
    props = DISCORD_TOOL.parameters["properties"]
    assert "action" in props
    assert "text" in props
    assert "channel_id" in props
    assert "embed" in props


def test_slack_schema():
    assert SLACK_TOOL.name == "slack"
    props = SLACK_TOOL.parameters["properties"]
    assert "action" in props
    assert "channel" in props
    assert "blocks" in props


def test_whatsapp_schema():
    assert WHATSAPP_TOOL.name == "whatsapp"
    props = WHATSAPP_TOOL.parameters["properties"]
    assert "action" in props
    assert "to" in props
    assert "media_type" in props


# ------------------------------------------------------------------
# Disabled channel guard — returns friendly message, never raises
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discord_disabled():
    result = await _discord(action="send", text="hello")
    assert "not enabled" in result.lower() or "discord_enabled" in result.lower()


@pytest.mark.asyncio
async def test_slack_disabled():
    result = await _slack(action="send", text="hello")
    assert "not enabled" in result.lower() or "slack_enabled" in result.lower()


@pytest.mark.asyncio
async def test_whatsapp_disabled():
    result = await _whatsapp(action="send", text="hello", to="123@s.whatsapp.net")
    assert "not enabled" in result.lower() or "whatsapp_enabled" in result.lower()


# ------------------------------------------------------------------
# Schema actions list is comprehensive (not empty)
# ------------------------------------------------------------------

def test_discord_actions_described():
    desc = DISCORD_TOOL.description
    for action in ("send", "edit", "delete", "react", "dm", "thread", "embed"):
        assert action in desc


def test_slack_actions_described():
    desc = SLACK_TOOL.description
    for action in ("send", "edit", "delete", "react", "dm", "thread"):
        assert action in desc


def test_whatsapp_actions_described():
    desc = WHATSAPP_TOOL.description
    for action in ("send", "react", "delete"):
        assert action in desc
