"""Tests for message_tool.py — action name aliases, camelCase, sticker cache."""
from __future__ import annotations

import pytest

from tools.message_tool import (
    _normalise_action,
    _message,
    _sticker_cache,
    TOOL_DEFINITION,
    set_send_fn,
    set_telegram_fns,
)


# ------------------------------------------------------------------
# Action name normalisation (OpenClaw camelCase ↔ snake_case)
# ------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("send", "send"),
    ("sendMessage", "send"),
    ("sendmessage", "send"),
    ("editMessage", "edit"),
    ("deleteMessage", "delete"),
    ("reactMessage", "react"),
    ("sendSticker", "send_sticker"),
    ("sendPhoto", "send_photo"),
    ("sendDocument", "send_document"),
    ("searchSticker", "search_sticker"),
    ("stickerCacheStats", "sticker_cache_stats"),
    ("createForumTopic", "create_forum_topic"),
    ("sendButtons", "buttons"),
    # snake_case passes through
    ("send_photo", "send_photo"),
    ("delete", "delete"),
])
def test_normalise_action(raw, expected):
    assert _normalise_action(raw) == expected


# ------------------------------------------------------------------
# send with no fn configured
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_without_fn():
    set_send_fn(None)
    result = await _message(action="send", text="hello")
    assert "not configured" in result.lower() or "error" in result.lower()


@pytest.mark.asyncio
async def test_send_without_text():
    set_send_fn(None)
    result = await _message(action="send")
    assert "required" in result.lower() or "error" in result.lower()


# ------------------------------------------------------------------
# send with mock fn
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_with_mock_fn():
    sent = []
    async def mock_send(text):
        sent.append(text)

    set_send_fn(mock_send)
    result = await _message(action="send", text="hi there")
    assert "sent" in result.lower()
    assert sent == ["hi there"]
    set_send_fn(None)


# ------------------------------------------------------------------
# camelCase alias works end-to-end
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_camel_case_send_message():
    sent = []
    async def mock_send(text):
        sent.append(text)
    set_send_fn(mock_send)
    result = await _message(action="sendMessage", text="camel test")
    assert "sent" in result.lower()
    assert sent == ["camel test"]
    set_send_fn(None)


# ------------------------------------------------------------------
# sticker_cache_stats on empty cache
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sticker_cache_stats_empty():
    _sticker_cache.clear()
    result = await _message(action="stickerCacheStats")
    assert "empty" in result.lower()


# ------------------------------------------------------------------
# sticker_cache_stats with data
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sticker_cache_stats_with_data():
    _sticker_cache["Animals"] = [{"file_id": "abc", "emoji": "🐱"}]
    result = await _message(action="stickerCacheStats")
    assert "Animals" in result
    assert "1 sticker" in result or "1" in result
    _sticker_cache.clear()


# ------------------------------------------------------------------
# Unknown action
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_action():
    result = await _message(action="teleport")
    assert "unknown action" in result.lower()


# ------------------------------------------------------------------
# Schema
# ------------------------------------------------------------------

def test_tool_schema_has_required_actions():
    props = TOOL_DEFINITION.parameters["properties"]
    assert "action" in props
    assert "text" in props
    assert "file_id" in props
    assert "query" in props  # for searchSticker
    assert "topic_name" in props
