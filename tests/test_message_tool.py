"""
Tests for message_tool.py — OpenClaw parity:
  - Action name normalisation (camelCase / kebab-case / snake_case)
  - Structured JSON results (ok/reason fields)
  - Buttons 2D normalisation
  - Reaction remove + fallback message_id
  - sticker aliases (fileId / stickerId)
  - forum topic (name alias + iconColor / iconCustomEmojiId)
  - P0: sticker / sticker-search / topic-create action aliases
"""
from __future__ import annotations

import json
import pytest

from tools.message_tool import (
    _normalise_action,
    _normalise_buttons,
    _message,
    _sticker_cache,
    TOOL_DEFINITION,
    set_send_fn,
    set_current_message_id,
    set_telegram_fns,
)


# ------------------------------------------------------------------
# Action normalisation — full alias table (P0)
# ------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("send", "send"),
    ("sendMessage", "send"),
    ("sendmessage", "send"),
    ("editMessage", "edit"),
    ("edit_message", "edit"),
    ("deleteMessage", "delete"),
    ("delete_message", "delete"),
    ("reactMessage", "react"),
    ("react_message", "react"),
    ("sendSticker", "send_sticker"),
    # P0: OpenClaw short form "sticker"
    ("sticker", "send_sticker"),
    ("sendPhoto", "send_photo"),
    ("sendDocument", "send_document"),
    ("searchSticker", "search_sticker"),
    # P0: kebab-case sticker-search
    ("sticker-search", "search_sticker"),
    ("sticker_search", "search_sticker"),
    ("stickerCacheStats", "sticker_cache_stats"),
    ("createForumTopic", "create_forum_topic"),
    # P0: kebab-case topic-create
    ("topic-create", "create_forum_topic"),
    ("topic_create", "create_forum_topic"),
    ("sendButtons", "buttons"),
    ("send_buttons", "buttons"),
    # snake_case passthrough
    ("send_photo", "send_photo"),
    ("delete", "delete"),
])
def test_normalise_action(raw, expected):
    assert _normalise_action(raw) == expected


# ------------------------------------------------------------------
# Button 2D normalisation (P1)
# ------------------------------------------------------------------

def test_buttons_2d_passthrough():
    raw = [[{"text": "Yes", "callback_data": "yes"}, {"text": "No", "callback_data": "no"}]]
    out = _normalise_buttons(raw)
    assert out == [[{"text": "Yes", "callback_data": "yes"}, {"text": "No", "callback_data": "no"}]]


def test_buttons_flat_promoted():
    # Flat list (LobsterClaw legacy) → 2D rows
    raw = [{"text": "A", "data": "a"}, {"text": "B", "callback_data": "b"}]
    out = _normalise_buttons(raw)
    assert out is not None
    assert len(out) == 2
    assert out[0][0]["callback_data"] == "a"
    assert out[1][0]["callback_data"] == "b"


def test_buttons_cb_truncated_to_64():
    long_cb = "x" * 100
    raw = [[{"text": "click", "callback_data": long_cb}]]
    out = _normalise_buttons(raw)
    assert out and len(out[0][0]["callback_data"]) == 64


def test_buttons_none_returns_none():
    assert _normalise_buttons(None) is None
    assert _normalise_buttons([]) is None


# ------------------------------------------------------------------
# Structured JSON result on send with no fn (P1)
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_without_fn_returns_json_error():
    set_send_fn(None)
    result = await _message(action="send", text="hello")
    data = json.loads(result)
    assert data["ok"] is False
    assert "reason" in data


@pytest.mark.asyncio
async def test_send_without_text_returns_json_error():
    set_send_fn(None)
    result = await _message(action="send")
    data = json.loads(result)
    assert data["ok"] is False


# ------------------------------------------------------------------
# send with mock fn → structured ok result
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_with_mock_fn():
    sent = []
    async def mock_send(text):
        sent.append(text)

    set_send_fn(mock_send)
    result = await _message(action="send", text="hi there")
    data = json.loads(result)
    assert data["ok"] is True
    assert sent == ["hi there"]
    set_send_fn(None)


# ------------------------------------------------------------------
# camelCase sendMessage works end-to-end
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_camel_case_send_message():
    sent = []
    async def mock_send(text):
        sent.append(text)
    set_send_fn(mock_send)
    result = await _message(action="sendMessage", text="camel test")
    data = json.loads(result)
    assert data["ok"] is True
    assert sent == ["camel test"]
    set_send_fn(None)


# ------------------------------------------------------------------
# content alias works (P0)
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_content_alias():
    sent = []
    async def mock_send(text):
        sent.append(text)
    set_send_fn(mock_send)
    result = await _message(action="send", content="content alias test")
    data = json.loads(result)
    assert data["ok"] is True
    assert sent == ["content alias test"]
    set_send_fn(None)


# ------------------------------------------------------------------
# sticker cache stats — structured result (P1)
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sticker_cache_stats_empty():
    _sticker_cache.clear()
    result = await _message(action="stickerCacheStats")
    data = json.loads(result)
    assert data["ok"] is True
    assert data["sets"] == 0
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_sticker_cache_stats_with_data():
    _sticker_cache["Animals"] = [{"file_id": "abc", "emoji": "🐱"}]
    result = await _message(action="stickerCacheStats")
    data = json.loads(result)
    assert data["ok"] is True
    assert data["sets"] == 1
    assert data["total"] == 1
    _sticker_cache.clear()


# ------------------------------------------------------------------
# P1: sticker fileId / stickerId alias
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_sticker_file_id_alias():
    called_with = {}
    async def mock_sticker(chat_id, file_id, **kwargs):
        called_with["file_id"] = file_id
        return None
    set_telegram_fns(send_sticker=mock_sticker)
    result = await _message(action="sendSticker", fileId="FILE_ABC")
    data = json.loads(result)
    assert data["ok"] is True
    assert called_with["file_id"] == "FILE_ABC"


@pytest.mark.asyncio
async def test_send_sticker_sticker_id_alias():
    called_with = {}
    async def mock_sticker(chat_id, file_id, **kwargs):
        called_with["file_id"] = file_id
    set_telegram_fns(send_sticker=mock_sticker)
    result = await _message(action="sticker", stickerId="STICKER_ID")
    data = json.loads(result)
    assert data["ok"] is True
    assert called_with["file_id"] == "STICKER_ID"


# ------------------------------------------------------------------
# P1: react remove=True + fallback to _current_message_id
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_react_remove():
    called = {}
    async def mock_react(chat_id, msg_id, emoji, remove=False):
        called["remove"] = remove
        called["emoji"] = emoji
    set_telegram_fns(react=mock_react)
    set_current_message_id(42)
    result = await _message(action="react", remove=True, emoji="👍")
    data = json.loads(result)
    assert data["ok"] is True
    assert data.get("removed") is True
    assert called["remove"] is True


@pytest.mark.asyncio
async def test_react_fallback_message_id():
    """When message_id is omitted, use _current_message_id."""
    called = {}
    async def mock_react(chat_id, msg_id, emoji, remove=False):
        called["msg_id"] = msg_id
    set_telegram_fns(react=mock_react)
    set_current_message_id(99)
    result = await _message(action="reactMessage", emoji="❤️")
    data = json.loads(result)
    assert data["ok"] is True
    assert called["msg_id"] == 99
    set_current_message_id(None)


@pytest.mark.asyncio
async def test_react_no_message_id_or_fallback():
    """Without message_id and no fallback, returns soft JSON error."""
    set_telegram_fns(react=None)
    set_current_message_id(None)
    result = await _message(action="react", emoji="👍")
    data = json.loads(result)
    assert data["ok"] is False
    assert "message_id" in data.get("reason", "").lower() or "message" in data.get("hint", "").lower()


# ------------------------------------------------------------------
# P0: topic-create / createForumTopic with iconColor
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_forum_topic_with_icon_color():
    called = {}
    async def mock_create(chat_id, name, icon_color=None, icon_custom_emoji_id=None):
        called["name"] = name
        called["icon_color"] = icon_color
        return {"topicId": 5, "name": name, "chatId": chat_id}
    set_telegram_fns(create_forum_topic=mock_create)
    result = await _message(action="topic-create", name="My Topic", iconColor=7322096)
    data = json.loads(result)
    assert data["ok"] is True
    assert called["name"] == "My Topic"
    assert called["icon_color"] == 7322096


@pytest.mark.asyncio
async def test_create_forum_topic_missing_name():
    result = await _message(action="createForumTopic")
    data = json.loads(result)
    assert data["ok"] is False


# ------------------------------------------------------------------
# Unknown action returns JSON error
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_action():
    result = await _message(action="teleport")
    data = json.loads(result)
    assert data["ok"] is False
    assert "unknown_action" in data.get("reason", "")


# ------------------------------------------------------------------
# Schema field presence
# ------------------------------------------------------------------

def test_tool_schema_has_required_fields():
    props = TOOL_DEFINITION.parameters["properties"]
    for field in ("action", "text", "content", "to", "chatId", "fileId", "stickerId",
                  "name", "iconColor", "iconCustomEmojiId", "remove", "buttons",
                  "query", "limit", "messageId"):
        assert field in props, f"Missing schema field: {field}"
