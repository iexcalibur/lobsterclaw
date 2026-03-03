"""
Message tool — mirrors OpenClaw's message tool and telegram-actions.ts.

Provides agent-callable Telegram actions:
  send          — send a text message
  send_photo    — send a photo (path or URL)
  send_document — send a file/document
  send_sticker  — send a sticker by file_id
  edit          — edit an existing message
  delete        — delete a message
  react         — add a reaction to a message
  buttons       — send a message with inline buttons
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
_edit_fn: Callable | None = None
_delete_fn: Callable | None = None
_react_fn: Callable | None = None
_send_buttons_fn: Callable | None = None
_create_forum_topic_fn: Callable | None = None


def set_send_fn(fn: Callable[[str], Awaitable[None]]) -> None:
    global _send_fn
    _send_fn = fn


def set_telegram_fns(
    send_photo=None,
    send_document=None,
    send_sticker=None,
    edit=None,
    delete=None,
    react=None,
    send_buttons=None,
    create_forum_topic=None,
) -> None:
    global _send_photo_fn, _send_document_fn, _send_sticker_fn
    global _edit_fn, _delete_fn, _react_fn, _send_buttons_fn, _create_forum_topic_fn
    if send_photo:
        _send_photo_fn = send_photo
    if send_document:
        _send_document_fn = send_document
    if send_sticker:
        _send_sticker_fn = send_sticker
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


TOOL_DEFINITION = ToolDefinition(
    name="message",
    description=(
        "Send Telegram messages and perform Telegram actions.\n"
        "Actions:\n"
        "  send          — send a text message to the owner\n"
        "  send_photo    — send a photo (local path or URL)\n"
        "  send_document — send a file as a document attachment\n"
        "  send_sticker  — (not supported yet)\n"
        "  edit          — edit a previously sent message\n"
        "  delete        — delete a message\n"
        "  react         — add emoji reaction to a message\n"
        "  buttons       — send a message with inline choice buttons"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action: send | send_photo | send_document | send_sticker | edit | delete | react | buttons | create_forum_topic",
                "default": "send",
            },
            "text": {
                "type": "string",
                "description": "Message text (for send, edit, buttons actions)",
            },
            "source": {
                "type": "string",
                "description": "File path or URL (for send_photo, send_document)",
            },
            "caption": {
                "type": "string",
                "description": "Caption for photos/documents",
            },
            "chat_id": {
                "type": "integer",
                "description": "Target chat ID (defaults to owner; required for edit/delete/react)",
            },
            "message_id": {
                "type": "integer",
                "description": "Message ID (required for edit/delete/react)",
            },
            "emoji": {
                "type": "string",
                "description": "Emoji character for react action (e.g. '👍')",
            },
            "buttons": {
                "type": "array",
                "description": "List of button objects [{text, data}] for buttons action",
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
                "description": "Telegram file_id for send_sticker action",
            },
            "topic_name": {
                "type": "string",
                "description": "Forum topic name for create_forum_topic action",
            },
        },
        "required": [],
    },
    fn=lambda **kw: _message(**kw),
)


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
) -> str:
    action = (action or "send").lower().strip()

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
            return "Error: 'file_id' is required for send_sticker action"
        if not _send_sticker_fn:
            return "Error: send_sticker not configured"
        from config import get_config
        target = chat_id or get_config().telegram_owner_id
        await _send_sticker_fn(target, file_id)
        return "Sticker sent ✅"

    if action == "create_forum_topic":
        if not topic_name:
            return "Error: 'topic_name' is required for create_forum_topic action"
        if not _create_forum_topic_fn:
            return "Error: create_forum_topic not configured"
        from config import get_config
        target = chat_id or get_config().telegram_owner_id
        return await _create_forum_topic_fn(target, topic_name)

    if action == "edit":
        if not message_id:
            return "Error: 'message_id' is required for edit action"
        if not text:
            return "Error: 'text' is required for edit action"
        if not _edit_fn:
            return "Error: edit_message not configured"
        from config import get_config
        target = chat_id or get_config().telegram_owner_id
        await _edit_fn(target, message_id, text)
        return f"Message {message_id} edited ✅"

    if action == "delete":
        if not message_id:
            return "Error: 'message_id' is required for delete action"
        if not _delete_fn:
            return "Error: delete_message not configured"
        from config import get_config
        target = chat_id or get_config().telegram_owner_id
        await _delete_fn(target, message_id)
        return f"Message {message_id} deleted ✅"

    if action == "react":
        if not message_id:
            return "Error: 'message_id' is required for react action"
        if not emoji:
            return "Error: 'emoji' is required for react action"
        if not _react_fn:
            return "Error: react_to_message not configured"
        from config import get_config
        target = chat_id or get_config().telegram_owner_id
        await _react_fn(target, message_id, emoji)
        return f"Reacted with {emoji} ✅"

    if action == "buttons":
        if not text:
            return "Error: 'text' is required for buttons action"
        if not buttons:
            return "Error: 'buttons' list is required for buttons action"
        if not _send_buttons_fn:
            return "Error: send_with_buttons not configured"
        from config import get_config
        target = chat_id or get_config().telegram_owner_id
        await _send_buttons_fn(text, buttons, target)
        return "Message with buttons sent ✅"

    return f"Unknown action '{action}'. Use: send, send_photo, send_document, send_sticker, edit, delete, react, buttons, create_forum_topic"
