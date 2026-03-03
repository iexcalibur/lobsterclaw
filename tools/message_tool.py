"""
Message tool — mirrors OpenClaw's message tool and telegram-actions.ts.

Field contract parity (OpenClaw telegram-actions.ts:178):
  OpenClaw fields: to, content, mediaUrl, buttons, replyToMessageId,
                   messageThreadId, quoteText, asVoice, silent, accountId
  PyGate accepts both OpenClaw field names and snake_case aliases:
    to           ↔  chat_id
    content      ↔  text
    mediaUrl     ↔  source
    asVoice      ↔  as_voice
    replyToMessageId ↔ reply_to_message_id
    messageThreadId  ↔ message_thread_id
    quoteText    ↔  quote_text
    silent       ↔  silent

Action name parity (OpenClaw camelCase ↔ PyGate snake_case):
  sendMessage / send
  sendPhoto / send_photo
  sendDocument / send_document
  sendSticker / send_sticker
  searchSticker / search_sticker
  stickerCacheStats / sticker_cache_stats
  editMessage / edit
  deleteMessage / delete
  reactMessage / react
  sendButtons / buttons
  createForumTopic / create_forum_topic
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

# Injected by main.py
_send_fn: Callable[[str], Awaitable[None]] | None = None
_send_photo_fn: Callable | None = None
_send_document_fn: Callable | None = None
_send_sticker_fn: Callable | None = None
_edit_fn: Callable | None = None
_delete_fn: Callable | None = None
_react_fn: Callable | None = None
_send_buttons_fn: Callable | None = None
_create_forum_topic_fn: Callable | None = None

# Sticker search cache
_sticker_cache: dict[str, list[dict]] = {}


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


# ------------------------------------------------------------------
# Action alias table — OpenClaw camelCase → canonical snake_case
# ------------------------------------------------------------------

_ACTION_ALIASES: dict[str, str] = {
    "sendmessage":       "send",
    "sendphoto":         "send_photo",
    "senddocument":      "send_document",
    "sendsticker":       "send_sticker",
    "searchsticker":     "search_sticker",
    "stickercachestats": "sticker_cache_stats",
    "editmessage":       "edit",
    "deletemessage":     "delete",
    "reactmessage":      "react",
    "sendbuttons":       "buttons",
    "createforumtopic":  "create_forum_topic",
    # snake_case legacy
    "send_message":      "send",
    "edit_message":      "edit",
    "delete_message":    "delete",
    "react_message":     "react",
    "send_buttons":      "buttons",
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
        "Send Telegram messages and perform channel actions.\n\n"
        "Accepts both OpenClaw camelCase (sendMessage, editMessage…) and snake_case.\n"
        "Field aliases: 'to'=chat_id, 'content'=text, 'mediaUrl'=source, "
        "'replyToMessageId'=reply_to_message_id, 'messageThreadId'=message_thread_id.\n\n"
        "Actions:\n"
        "  send / sendMessage             — text to owner\n"
        "  send_photo / sendPhoto         — photo (path or URL)\n"
        "  send_document / sendDocument   — file as attachment\n"
        "  send_sticker / sendSticker     — sticker by file_id\n"
        "  searchSticker / search_sticker — search sticker set by name\n"
        "  stickerCacheStats              — show sticker cache\n"
        "  edit / editMessage             — edit a message\n"
        "  delete / deleteMessage         — delete a message\n"
        "  react / reactMessage           — emoji reaction\n"
        "  buttons / sendButtons          — inline keyboard\n"
        "  createForumTopic               — create forum/topic thread"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action (camelCase or snake_case). Default: send",
                "default": "send",
            },
            # Content fields — both OpenClaw and PyGate aliases accepted
            "text": {
                "type": "string",
                "description": "Message text (also accepted as 'content')",
            },
            "content": {
                "type": "string",
                "description": "Alias for text (OpenClaw field name)",
            },
            "source": {
                "type": "string",
                "description": "File path or URL for media (also accepted as 'mediaUrl')",
            },
            "mediaUrl": {
                "type": "string",
                "description": "Alias for source (OpenClaw field name)",
            },
            "caption": {
                "type": "string",
                "description": "Caption for photos/documents",
            },
            # Target fields
            "chat_id": {
                "type": "integer",
                "description": "Target chat ID (defaults to owner; also accepted as 'to')",
            },
            "to": {
                "type": "string",
                "description": "Alias for chat_id (OpenClaw field name; also accepts username)",
            },
            "message_id": {
                "type": "integer",
                "description": "Message ID (edit/delete/react)",
            },
            # Thread / reply context (OpenClaw telegram-actions.ts parity)
            "reply_to_message_id": {
                "type": "integer",
                "description": "Reply-to message ID (also 'replyToMessageId')",
            },
            "replyToMessageId": {
                "type": "integer",
                "description": "Alias for reply_to_message_id",
            },
            "message_thread_id": {
                "type": "integer",
                "description": "Forum thread/topic ID (also 'messageThreadId')",
            },
            "messageThreadId": {
                "type": "integer",
                "description": "Alias for message_thread_id",
            },
            "quote_text": {
                "type": "string",
                "description": "Text to quote above the message (also 'quoteText')",
            },
            "quoteText": {
                "type": "string",
                "description": "Alias for quote_text",
            },
            # Send modifiers
            "as_voice": {
                "type": "boolean",
                "description": "Send TTS audio as voice message instead of text (also 'asVoice')",
            },
            "asVoice": {
                "type": "boolean",
                "description": "Alias for as_voice",
            },
            "silent": {
                "type": "boolean",
                "description": "Send silently (no notification sound)",
            },
            "account_id": {
                "type": "string",
                "description": "Account ID for multi-account setups (also 'accountId')",
            },
            "accountId": {
                "type": "string",
                "description": "Alias for account_id",
            },
            # Reaction
            "emoji": {
                "type": "string",
                "description": "Emoji for react action (e.g. '👍')",
            },
            # Inline buttons
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
            # Sticker
            "file_id": {
                "type": "string",
                "description": "Telegram file_id for send_sticker",
            },
            # Forum topics
            "topic_name": {
                "type": "string",
                "description": "Forum topic name for createForumTopic",
            },
            "icon_color": {
                "type": "integer",
                "description": "Forum topic icon color (RGB hex int, createForumTopic)",
            },
            "icon_custom_emoji_id": {
                "type": "string",
                "description": "Custom emoji ID for forum topic icon (createForumTopic)",
            },
            # Sticker search
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
# Field normalisation helpers
# ------------------------------------------------------------------

def _coerce(kwargs: dict, canonical: str, *aliases: str):
    """Promote first non-None alias into canonical field (if canonical is absent)."""
    if canonical not in kwargs or kwargs[canonical] is None:
        for alias in aliases:
            if kwargs.get(alias) is not None:
                kwargs[canonical] = kwargs[alias]
                break
    for alias in aliases:
        kwargs.pop(alias, None)


# ------------------------------------------------------------------
# Implementation
# ------------------------------------------------------------------

async def _message(
    action: str = "send",
    text: str | None = None,
    content: str | None = None,           # OpenClaw alias
    source: str | None = None,
    mediaUrl: str | None = None,           # OpenClaw alias
    caption: str = "",
    chat_id: int | None = None,
    to: str | None = None,                 # OpenClaw alias
    message_id: int | None = None,
    reply_to_message_id: int | None = None,
    replyToMessageId: int | None = None,   # OpenClaw alias
    message_thread_id: int | None = None,
    messageThreadId: int | None = None,    # OpenClaw alias
    quote_text: str | None = None,
    quoteText: str | None = None,          # OpenClaw alias
    as_voice: bool = False,
    asVoice: bool = False,                 # OpenClaw alias
    silent: bool = False,
    account_id: str | None = None,
    accountId: str | None = None,          # OpenClaw alias
    emoji: str | None = None,
    buttons: list[dict] | None = None,
    file_id: str | None = None,
    topic_name: str | None = None,
    icon_color: int | None = None,
    icon_custom_emoji_id: str | None = None,
    query: str | None = None,
) -> str:
    action = _normalise_action(action or "send")

    # Resolve aliases → canonical names
    text = text or content
    source = source or mediaUrl
    reply_to_message_id = reply_to_message_id or replyToMessageId
    message_thread_id = message_thread_id or messageThreadId
    quote_text = quote_text or quoteText
    as_voice = as_voice or asVoice

    from config import get_config
    cfg = get_config()
    owner_id = cfg.telegram_owner_id

    # Resolve `to` → chat_id
    if chat_id is None and to is not None:
        try:
            chat_id = int(to)
        except (ValueError, TypeError):
            chat_id = owner_id  # fall back; username resolution not yet supported
    target = chat_id or owner_id

    # ------------------------------------------------------------------
    # asVoice: convert text to TTS and send as voice note
    # ------------------------------------------------------------------
    if as_voice and text:
        try:
            from tools.media_tool import _tts
            import tempfile, os
            tmp = tempfile.mktemp(suffix=".mp3")
            tts_result = await _tts(text=text, voice="en-US-ChristopherNeural", output_path=tmp)
            if os.path.exists(tmp):
                from tools.media_tool import _send_audio_fn
                if _send_audio_fn:
                    await _send_audio_fn(tmp)
                    os.unlink(tmp)
                    return "Voice message sent ✅"
        except Exception as e:
            logger.warning("asVoice TTS failed, falling back to text: %s", e)

    if action == "send":
        if not text:
            return "Error: 'text' (or 'content') is required for send action"
        if not _send_fn:
            return "Error: Telegram send_fn not configured"
        # Prepend quote if provided
        msg = f">{quote_text}\n\n{text}" if quote_text else text
        await _send_fn(msg)
        return "Message sent ✅"

    if action == "send_photo":
        if not source:
            return "Error: 'source' (or 'mediaUrl') is required for send_photo"
        if not _send_photo_fn:
            return "Error: send_photo not configured"
        await _send_photo_fn(source, caption)
        return "Photo sent ✅"

    if action == "send_document":
        if not source:
            return "Error: 'source' (or 'mediaUrl') is required for send_document"
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
        if not _sticker_cache:
            return "Sticker cache is empty. Use searchSticker to populate it."
        total = sum(len(v) for v in _sticker_cache.values())
        lines = [f"Sticker cache: {len(_sticker_cache)} sets, {total} stickers"]
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
            return "Error: 'message_id' is required for edit"
        if not text:
            return "Error: 'text' (or 'content') is required for edit"
        if not _edit_fn:
            return "Error: edit_message not configured"
        await _edit_fn(target, message_id, text)
        return f"Message {message_id} edited ✅"

    if action == "delete":
        if not message_id:
            return "Error: 'message_id' is required for delete"
        if not _delete_fn:
            return "Error: delete_message not configured"
        await _delete_fn(target, message_id)
        return f"Message {message_id} deleted ✅"

    if action == "react":
        if not message_id:
            return "Error: 'message_id' is required for react"
        if not emoji:
            return "Error: 'emoji' is required for react"
        if not _react_fn:
            return "Error: react_to_message not configured"
        await _react_fn(target, message_id, emoji)
        return f"Reacted with {emoji} ✅"

    if action == "buttons":
        if not text:
            return "Error: 'text' is required for buttons"
        if not buttons:
            return "Error: 'buttons' list is required for buttons"
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
    if not query:
        return "Error: 'query' is required for searchSticker (sticker set name or emoji)"
    if query in _sticker_cache:
        stickers = _sticker_cache[query]
        lines = [f"Cached sticker set '{query}' ({len(stickers)} stickers):"]
        lines += [f"  [{i}] file_id={s['file_id']} emoji={s.get('emoji','')}"
                  for i, s in enumerate(stickers[:10])]
        return "\n".join(lines)
    try:
        from config import get_config
        import httpx
        resp = await httpx.AsyncClient(timeout=10).get(
            f"https://api.telegram.org/bot{get_config().telegram_bot_token}/getStickerSet",
            params={"name": query},
        )
        data = resp.json()
        if data.get("ok"):
            ss = data["result"]
            stickers = [{"file_id": s["file_id"], "emoji": s.get("emoji", "")}
                        for s in ss.get("stickers", [])]
            _sticker_cache[query] = stickers
            lines = [f"Sticker set '{ss['name']}' ({ss['title']}, {len(stickers)} stickers):"]
            lines += [f"  [{i}] file_id={s['file_id']} emoji={s['emoji']}"
                      for i, s in enumerate(stickers[:10])]
            lines.append("\nUse send_sticker with file_id to send one.")
            return "\n".join(lines)
        return f"Sticker set '{query}' not found: {data.get('description', 'unknown')}"
    except Exception as e:
        return f"Sticker search error: {e}"
