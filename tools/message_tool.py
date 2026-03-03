"""
Message tool — mirrors OpenClaw's message tool and telegram-actions.ts.

Action name parity:
  OpenClaw uses camelCase:  sendMessage, editMessage, deleteMessage, sendSticker,
                             searchSticker, stickerCacheStats, createForumTopic
  PyGate accepts both camelCase (OpenClaw-style) and snake_case aliases.

Telegram actions (1:1 with telegram-actions.ts):
  sendMessage / send            — send text
  sendPhoto / send_photo        — send photo
  sendDocument / send_document  — send document
  sendSticker / send_sticker    — send sticker by file_id
  searchSticker                 — search sticker set by name
  stickerCacheStats             — show sticker cache statistics
  editMessage / edit            — edit a message
  deleteMessage / delete        — delete a message
  reactMessage / react          — add emoji reaction
  sendButtons / buttons         — inline keyboard
  createForumTopic              — create a forum topic
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

# Injected by main.py after Telegram is ready
_send_fn: Callable[[str], Awaitable[None]] | None = None
_send_photo_fn: Callable | None = None
_send_document_fn: Callable | None = None
_send_sticker_fn: Callable | None = None
_search_sticker_fn: Callable | None = None
_edit_fn: Callable | None = None
_delete_fn: Callable | None = None
_react_fn: Callable | None = None
_send_buttons_fn: Callable | None = None
_create_forum_topic_fn: Callable | None = None

# Sticker search cache (in-memory)
_sticker_cache: dict[str, list[dict]] = {}


def set_send_fn(fn: Callable[[str], Awaitable[None]]) -> None:
    global _send_fn
    _send_fn = fn


def set_telegram_fns(
    send_photo=None,
    send_document=None,
    send_sticker=None,
    search_sticker=None,
    edit=None,
    delete=None,
    react=None,
    send_buttons=None,
    create_forum_topic=None,
) -> None:
    global _send_photo_fn, _send_document_fn, _send_sticker_fn, _search_sticker_fn
    global _edit_fn, _delete_fn, _react_fn, _send_buttons_fn, _create_forum_topic_fn
    if send_photo:
        _send_photo_fn = send_photo
    if send_document:
        _send_document_fn = send_document
    if send_sticker:
        _send_sticker_fn = send_sticker
    if search_sticker:
        _search_sticker_fn = search_sticker
    if edit:
        _edit_fn = edit
    if delete:
        _delete_fn = delete
    if react:
        _react_fn = react
    if send_buttons:
        _send_buttons_fn = send_buttons
    if create_forum_topic:
        _create_forum_topic_fn = create_forum_topic


# ------------------------------------------------------------------
# Action name normalisation: camelCase → snake_case
# ------------------------------------------------------------------

_ACTION_ALIASES: dict[str, str] = {
    # OpenClaw camelCase → canonical snake_case
    "sendmessage":          "send",
    "sendphoto":            "send_photo",
    "senddocument":         "send_document",
    "sendsticker":          "send_sticker",
    "searchsticker":        "search_sticker",
    "stickercachestats":    "sticker_cache_stats",
    "editmessage":          "edit",
    "deletemessage":        "delete",
    "reactmessage":         "react",
    "sendbuttons":          "buttons",
    "createforumtopic":     "create_forum_topic",
    # Legacy aliases
    "send_message":         "send",
    "edit_message":         "edit",
    "delete_message":       "delete",
    "react_message":        "react",
    "send_buttons":         "buttons",
}


def _normalise_action(action: str) -> str:
    lowered = action.lower().replace("-", "_")
    return _ACTION_ALIASES.get(lowered, lowered)


# ------------------------------------------------------------------
# Tool definition
# ------------------------------------------------------------------

TOOL_DEFINITION = ToolDefinition(
    name="message",
    description=(
        "Send Telegram messages and perform Telegram actions.\n\n"
        "Accepts both OpenClaw camelCase (sendMessage, editMessage, etc.)\n"
        "and snake_case aliases (send, edit, etc.).\n\n"
        "Actions:\n"
        "  send / sendMessage                — send text to owner\n"
        "  send_photo / sendPhoto            — send a photo (path or URL)\n"
        "  send_document / sendDocument      — send a file as attachment\n"
        "  send_sticker / sendSticker        — send sticker by file_id\n"
        "  searchSticker / search_sticker    — search for stickers in a sticker set\n"
        "  stickerCacheStats                 — show sticker cache stats\n"
        "  edit / editMessage                — edit a previously sent message\n"
        "  delete / deleteMessage            — delete a message\n"
        "  react / reactMessage              — add emoji reaction\n"
        "  buttons / sendButtons             — send message with inline keyboard\n"
        "  createForumTopic / create_forum_topic — create a forum/topic thread"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": (
                    "Action (camelCase or snake_case):\n"
                    "send|sendMessage | send_photo|sendPhoto | send_document|sendDocument |\n"
                    "send_sticker|sendSticker | searchSticker | stickerCacheStats |\n"
                    "edit|editMessage | delete|deleteMessage | react|reactMessage |\n"
                    "buttons|sendButtons | createForumTopic"
                ),
                "default": "send",
            },
            "text": {
                "type": "string",
                "description": "Message text (send, edit, buttons)",
            },
            "source": {
                "type": "string",
                "description": "File path or URL (send_photo, send_document)",
            },
            "caption": {
                "type": "string",
                "description": "Caption for photos/documents",
            },
            "chat_id": {
                "type": "integer",
                "description": "Target chat ID (defaults to owner)",
            },
            "message_id": {
                "type": "integer",
                "description": "Message ID (required for edit/delete/react)",
            },
            "emoji": {
                "type": "string",
                "description": "Emoji for react action (e.g. '👍')",
            },
            "buttons": {
                "type": "array",
                "description": "Inline keyboard buttons [{text, data}]",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "data": {"type": "string"},
                    },
                },
            },
            "file_id": {
                "type": "string",
                "description": "Telegram file_id (send_sticker)",
            },
            "topic_name": {
                "type": "string",
                "description": "Forum topic name (createForumTopic)",
            },
            "query": {
                "type": "string",
                "description": "Sticker set name or emoji query (searchSticker)",
            },
        },
        "required": [],
    },
    fn=lambda **kw: _message(**kw),
)


# ------------------------------------------------------------------
# Implementation
# ------------------------------------------------------------------

async def _message(
    action: str = "send",
    text: str | None = None,
    source: str | None = None,
    caption: str = "",
    chat_id: int | None = None,
    message_id: int | None = None,
    emoji: str | None = None,
    buttons: list[dict] | None = None,
    file_id: str | None = None,
    topic_name: str | None = None,
    query: str | None = None,
) -> str:
    action = _normalise_action(action or "send")

    from config import get_config
    owner_id = get_config().telegram_owner_id
    target = chat_id or owner_id

    if action == "send":
        if not text:
            return "Error: 'text' is required for send action"
        if not _send_fn:
            return "Error: Telegram send_fn not configured"
        await _send_fn(text)
        return "Message sent ✅"

    if action == "send_photo":
        if not source:
            return "Error: 'source' (path or URL) is required for send_photo"
        if not _send_photo_fn:
            return "Error: send_photo not configured"
        await _send_photo_fn(source, caption)
        return "Photo sent ✅"

    if action == "send_document":
        if not source:
            return "Error: 'source' (file path) is required for send_document"
        if not _send_document_fn:
            return "Error: send_document not configured"
        await _send_document_fn(source, caption)
        return "Document sent ✅"

    if action == "send_sticker":
        if not file_id:
            return "Error: 'file_id' is required for send_sticker"
        if not _send_sticker_fn:
            return "Error: send_sticker not configured"
        await _send_sticker_fn(target, file_id)
        return "Sticker sent ✅"

    if action == "search_sticker":
        return await _search_sticker(query or "")

    if action == "sticker_cache_stats":
        total_sticker_sets = len(_sticker_cache)
        total_stickers = sum(len(v) for v in _sticker_cache.values())
        if not _sticker_cache:
            return "Sticker cache is empty. Use searchSticker to populate it."
        lines = [f"Sticker cache: {total_sticker_sets} sets, {total_stickers} stickers cached"]
        for set_name, stickers in list(_sticker_cache.items())[:10]:
            lines.append(f"  {set_name}: {len(stickers)} stickers")
        return "\n".join(lines)

    if action == "create_forum_topic":
        if not topic_name:
            return "Error: 'topic_name' is required for createForumTopic"
        if not _create_forum_topic_fn:
            return "Error: create_forum_topic not configured"
        return await _create_forum_topic_fn(target, topic_name)

    if action == "edit":
        if not message_id:
            return "Error: 'message_id' is required for edit action"
        if not text:
            return "Error: 'text' is required for edit action"
        if not _edit_fn:
            return "Error: edit_message not configured"
        await _edit_fn(target, message_id, text)
        return f"Message {message_id} edited ✅"

    if action == "delete":
        if not message_id:
            return "Error: 'message_id' is required for delete action"
        if not _delete_fn:
            return "Error: delete_message not configured"
        await _delete_fn(target, message_id)
        return f"Message {message_id} deleted ✅"

    if action == "react":
        if not message_id:
            return "Error: 'message_id' is required for react action"
        if not emoji:
            return "Error: 'emoji' is required for react action"
        if not _react_fn:
            return "Error: react_to_message not configured"
        await _react_fn(target, message_id, emoji)
        return f"Reacted with {emoji} ✅"

    if action == "buttons":
        if not text:
            return "Error: 'text' is required for buttons action"
        if not buttons:
            return "Error: 'buttons' list is required for buttons action"
        if not _send_buttons_fn:
            return "Error: send_with_buttons not configured"
        await _send_buttons_fn(text, buttons, target)
        return "Message with buttons sent ✅"

    all_actions = (
        "send/sendMessage, send_photo/sendPhoto, send_document/sendDocument, "
        "send_sticker/sendSticker, searchSticker, stickerCacheStats, "
        "edit/editMessage, delete/deleteMessage, react/reactMessage, "
        "buttons/sendButtons, createForumTopic"
    )
    return f"Unknown action '{action}'. Use: {all_actions}"


async def _search_sticker(query: str) -> str:
    """Search stickers in a named set or by emoji. Caches results."""
    if not query:
        return "Error: 'query' is required for searchSticker (set name or emoji)"

    # Check cache first
    if query in _sticker_cache:
        stickers = _sticker_cache[query]
        results = "\n".join(
            f"  [{i}] file_id={s.get('file_id', '?')} emoji={s.get('emoji', '')}"
            for i, s in enumerate(stickers[:10])
        )
        return f"Cached stickers for '{query}':\n{results}"

    # Try to search via Telegram bot API if configured
    try:
        from config import get_config
        cfg = get_config()
        import httpx
        resp = await httpx.AsyncClient(timeout=10).get(
            f"https://api.telegram.org/bot{cfg.telegram_bot_token}/getStickerSet",
            params={"name": query},
        )
        data = resp.json()
        if data.get("ok"):
            sticker_set = data["result"]
            stickers = [
                {"file_id": s["file_id"], "emoji": s.get("emoji", "")}
                for s in sticker_set.get("stickers", [])
            ]
            _sticker_cache[query] = stickers
            results = "\n".join(
                f"  [{i}] file_id={s['file_id']} emoji={s['emoji']}"
                for i, s in enumerate(stickers[:10])
            )
            return (
                f"Sticker set '{sticker_set['name']}' ({sticker_set['title']}):\n"
                f"{results}\n\n"
                f"Use send_sticker with file_id to send one."
            )
        return f"Sticker set '{query}' not found: {data.get('description', 'unknown error')}"
    except Exception as e:
        return f"Sticker search error: {e}"
