"""
Message tool — mirrors OpenClaw's telegram-actions.ts contract.

Action name parity (OpenClaw camelCase ↔ LobsterClaw snake_case):
  sendMessage / send           editMessage / edit
  deleteMessage / delete       reactMessage / react
  sendSticker / send_sticker   sendSticker / sticker (OpenClaw telegram.ts)
  searchSticker / search_sticker / sticker-search / sticker_search
  stickerCacheStats / sticker_cache_stats
  createForumTopic / create_forum_topic / topic-create / topic_create
  sendPhoto / send_photo       sendDocument / send_document
  sendButtons / buttons / sendButtons

Field aliases (OpenClaw camelCase → canonical):
  to          ↔ chat_id        content      ↔ text
  mediaUrl    ↔ source         fileId       ↔ file_id
  stickerId   ↔ file_id        name         ↔ topic_name
  iconColor   ↔ icon_color     iconCustomEmojiId ↔ icon_custom_emoji_id
  chatId      ↔ chat_id        messageId    ↔ message_id
  replyToMessageId ↔ reply_to_message_id
  messageThreadId  ↔ message_thread_id
  quoteText   ↔ quote_text     asVoice      ↔ as_voice
  accountId   ↔ account_id

Structured result contract (OpenClaw jsonResult parity):
  All actions return a JSON-serialisable dict (stringified by registry).
  send / sendMessage → {"ok": true, "messageId": ..., "chatId": ...}
  edit              → {"ok": true, "messageId": ..., "chatId": ...}
  delete            → {"ok": true, "deleted": true}
  react             → {"ok": true, "added": emoji} | {"ok": true, "removed": true}
  sendSticker       → {"ok": true, "messageId": ..., "chatId": ...}
  createForumTopic  → {"ok": true, "topicId": ..., "name": ..., "chatId": ...}
  searchSticker     → {"ok": true, "count": N, "stickers": [...]}
  stickerCacheStats → {"ok": true, "sets": N, "total": N}

Buttons 2D shape (OpenClaw telegram-actions.ts:30 parity):
  buttons = [[{text, callback_data, style?}], ...]   ← 2D rows preferred
  Flat list [{text, data/callback_data}] auto-promoted to [[...]] for compat.
  Max callback_data length: 64 chars. Prefix "btn:" added only if not present.

Reaction remove (OpenClaw parity):
  remove=true clears the reaction. emoji optional when remove=true.
  Fallback to inbound _current_message_id when message_id omitted.
"""

from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable

from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

# Injected by main.py — typed as Any for flexibility
_send_fn: Callable | None = None
_send_to_fn: Callable | None = None       # send_to(chat_id, text, **kwargs)
_send_photo_fn: Callable | None = None
_send_document_fn: Callable | None = None
_send_sticker_fn: Callable | None = None  # send_sticker(chat_id, file_id, **kwargs)
_edit_fn: Callable | None = None          # edit_message(chat_id, message_id, text, **kwargs)
_delete_fn: Callable | None = None        # delete_message(chat_id, message_id)
_react_fn: Callable | None = None         # react_to_message(chat_id, msg_id, emoji, remove)
_send_buttons_fn: Callable | None = None  # send_with_buttons(text, buttons_2d, chat_id)
_create_forum_topic_fn: Callable | None = None  # create_forum_topic(chat_id, name, **kwargs)
_pin_fn: Callable | None = None                 # pin_message(chat_id, message_id, **kwargs)
_unpin_fn: Callable | None = None               # unpin_message(chat_id, message_id)
_unpin_all_fn: Callable | None = None           # unpin_all_messages(chat_id)

# Most-recently-received inbound message_id — fallback for react without explicit id
_current_message_id: int | None = None

# Sticker search cache: set_name → [{fileId, emoji, description, setName}]
_sticker_cache: dict[str, list[dict]] = {}


def set_send_fn(fn: Callable[[str], Awaitable[None]]) -> None:
    global _send_fn
    _send_fn = fn


def set_send_to_fn(fn: Callable) -> None:
    global _send_to_fn
    _send_to_fn = fn


# Telegram channel instance for target resolution (@username / t.me / :topic:)
_channel_ref: object | None = None


def set_channel_ref(channel) -> None:
    """Set a reference to TelegramChannel so we can call resolve_chat_id."""
    global _channel_ref
    _channel_ref = channel


def set_telegram_fns(
    send_photo=None,
    send_document=None,
    send_sticker=None,
    edit=None,
    delete=None,
    react=None,
    send_buttons=None,
    create_forum_topic=None,
    send_to=None,
    pin=None,
    unpin=None,
    unpin_all=None,
) -> None:
    global _send_photo_fn, _send_document_fn, _send_sticker_fn
    global _edit_fn, _delete_fn, _react_fn, _send_buttons_fn, _create_forum_topic_fn
    global _send_to_fn, _pin_fn, _unpin_fn, _unpin_all_fn
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
    if send_to:
        _send_to_fn = send_to
    if pin:
        _pin_fn = pin
    if unpin:
        _unpin_fn = unpin
    if unpin_all:
        _unpin_all_fn = unpin_all


def set_current_message_id(msg_id: int | None) -> None:
    """Called by Telegram channel on inbound messages — enables react fallback."""
    global _current_message_id
    _current_message_id = msg_id


# ------------------------------------------------------------------
# Action alias table — OpenClaw camelCase / kebab-case → canonical
# ------------------------------------------------------------------

_ACTION_ALIASES: dict[str, str] = {
    # send / sendMessage
    "sendmessage":        "send",
    "send_message":       "send",
    # photo / document
    "sendphoto":          "send_photo",
    "senddocument":       "send_document",
    # sticker
    "sendsticker":        "send_sticker",
    "sticker":            "send_sticker",   # OpenClaw telegram.ts short form
    # sticker search
    "searchsticker":      "search_sticker",
    "sticker_search":     "search_sticker",
    "sticker-search":     "search_sticker",
    # sticker cache stats
    "stickercachestats":  "sticker_cache_stats",
    "sticker_cache_stats":"sticker_cache_stats",
    # edit
    "editmessage":        "edit",
    "edit_message":       "edit",
    # delete
    "deletemessage":      "delete",
    "delete_message":     "delete",
    # react
    "reactmessage":       "react",
    "react_message":      "react",
    # buttons
    "sendbuttons":        "buttons",
    "send_buttons":       "buttons",
    # forum topic
    "createforumtopic":   "create_forum_topic",
    "create_forum_topic": "create_forum_topic",
    "topic_create":       "create_forum_topic",
    "topic-create":       "create_forum_topic",
    # pin / unpin
    "pinmessage":         "pin",
    "pin_message":        "pin",
    "unpinmessage":       "unpin",
    "unpin_message":      "unpin",
    "unpinallmessages":   "unpin_all",
    "unpin_all_messages": "unpin_all",
}


def _normalise_action(action: str) -> str:
    # Normalise: lowercase, hyphens → underscores, strip
    key = action.lower().replace("-", "_").strip()
    return _ACTION_ALIASES.get(key, key)


# ------------------------------------------------------------------
# Button 2D normalisation
# ------------------------------------------------------------------

def _normalise_buttons(raw: list | None) -> list[list[dict]] | None:
    """
    Accept both:
      - 2D (OpenClaw): [[{text, callback_data}], ...]
      - Flat (LobsterClaw legacy): [{text, data/callback_data}]
    Returns 2D list of {text, callback_data, ?style}, or None.
    """
    if not raw:
        return None
    if not isinstance(raw, list) or len(raw) == 0:
        return None

    # Detect flat list: first element is a dict with "text" key (not a list)
    if isinstance(raw[0], dict):
        # Flat → wrap each button in its own row
        rows = [[btn] for btn in raw]
    elif isinstance(raw[0], list):
        rows = raw
    else:
        return None

    result = []
    for row in rows:
        norm_row = []
        for btn in row:
            if not isinstance(btn, dict):
                continue
            text = str(btn.get("text", "")).strip()
            # callback_data field: OpenClaw uses callback_data, LobsterClaw uses data
            cb = btn.get("callback_data") or btn.get("data") or text
            cb = str(cb).strip()
            if not text or not cb:
                continue
            # Truncate to Telegram's 64-char limit
            if len(cb) > 64:
                cb = cb[:64]
            style = btn.get("style")
            entry: dict = {"text": text, "callback_data": cb}
            if style:
                entry["style"] = str(style)
            norm_row.append(entry)
        if norm_row:
            result.append(norm_row)
    return result if result else None


# ------------------------------------------------------------------
# Structured JSON result helpers (mirrors OpenClaw jsonResult)
# ------------------------------------------------------------------

def _ok(**fields) -> str:
    return json.dumps({"ok": True, **fields})


def _err(reason: str, hint: str = "", **fields) -> str:
    return json.dumps({"ok": False, "reason": reason, "hint": hint, **fields})


# ------------------------------------------------------------------
# Tool definition
# ------------------------------------------------------------------

TOOL_DEFINITION = ToolDefinition(
    name="message",
    description=(
        "Send Telegram messages and perform channel actions.\n\n"
        "Full parity with OpenClaw telegram-actions.ts. Accepts both OpenClaw\n"
        "camelCase action names and snake_case.\n\n"
        "Actions:\n"
        "  send / sendMessage             — send text (or media) to a chat\n"
        "  send_photo / sendPhoto         — send photo (path or URL)\n"
        "  send_document / sendDocument   — send file as attachment\n"
        "  send_sticker / sendSticker / sticker — send sticker by fileId\n"
        "  search_sticker / searchSticker / sticker-search — search sticker set\n"
        "  sticker_cache_stats / stickerCacheStats — show sticker cache stats\n"
        "  edit / editMessage             — edit existing message\n"
        "  delete / deleteMessage         — delete message\n"
        "  react / reactMessage           — emoji reaction (remove=true to clear)\n"
        "  buttons / sendButtons          — send message with inline keyboard\n"
        "  create_forum_topic / createForumTopic / topic-create — create forum topic\n"
        "  pin / pinMessage       — pin a message in chat\n"
        "  unpin / unpinMessage   — unpin a specific message\n"
        "  unpin_all              — unpin all messages in chat\n\n"
        "Key fields:\n"
        "  to / chat_id     — target chat (defaults to owner if omitted)\n"
        "  content / text   — message text (omittable when mediaUrl set)\n"
        "  mediaUrl / source — media URL or path\n"
        "  fileId / stickerId / file_id — sticker file ID\n"
        "  name / topic_name — forum topic name\n"
        "  iconColor / icon_color — topic icon RGB int\n"
        "  iconCustomEmojiId — topic custom emoji icon ID\n"
        "  chatId / messageId — for edit/delete/react\n"
        "  replyToMessageId, messageThreadId, quoteText, asVoice, silent\n"
        "  remove=true — remove reaction (react action)\n"
        "  buttons — 2D rows [[{text, callback_data, style?}]] or flat [{text, data}]"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action name (camelCase or snake_case). Default: send",
                "default": "send",
            },
            # Text / content
            "text": {"type": "string", "description": "Message text (also 'content')"},
            "content": {"type": "string", "description": "Alias for text (OpenClaw)"},
            # Media
            "source": {"type": "string", "description": "Media path/URL (also 'mediaUrl')"},
            "mediaUrl": {"type": "string", "description": "Alias for source (OpenClaw)"},
            "caption": {"type": "string", "description": "Caption for photo/document"},
            # Target
            "chat_id": {"type": "integer", "description": "Target chat ID (also 'to' / 'chatId')"},
            "to": {"type": "string", "description": "Target chat id or username (OpenClaw field)"},
            "chatId": {"type": "string", "description": "Alias for to/chat_id (OpenClaw)"},
            # Message reference
            "message_id": {"type": "integer", "description": "Message ID (edit/delete/react; also 'messageId')"},
            "messageId": {"type": "integer", "description": "Alias for message_id (OpenClaw)"},
            # Thread context
            "reply_to_message_id": {"type": "integer", "description": "Reply-to ID (also 'replyToMessageId')"},
            "replyToMessageId": {"type": "integer", "description": "Alias for reply_to_message_id"},
            "message_thread_id": {"type": "integer", "description": "Forum thread ID (also 'messageThreadId')"},
            "messageThreadId": {"type": "integer", "description": "Alias for message_thread_id"},
            "quote_text": {"type": "string", "description": "Text to quote above message (also 'quoteText')"},
            "quoteText": {"type": "string", "description": "Alias for quote_text"},
            # Send modifiers
            "as_voice": {"type": "boolean", "description": "Send as TTS voice note (also 'asVoice')"},
            "asVoice": {"type": "boolean", "description": "Alias for as_voice"},
            "silent": {"type": "boolean", "description": "Send silently (no notification sound)"},
            # Account
            "account_id": {"type": "string", "description": "Account ID for multi-account (also 'accountId')"},
            "accountId": {"type": "string", "description": "Alias for account_id"},
            # Reaction
            "emoji": {"type": "string", "description": "Emoji for react (e.g. '👍')"},
            "remove": {"type": "boolean", "description": "Remove the reaction (react action)"},
            # Buttons — 2D rows (OpenClaw) or flat list (LobsterClaw compat)
            "buttons": {
                "type": "array",
                "description": (
                    "Inline keyboard. 2D rows (OpenClaw): [[{text, callback_data, style?}]].\n"
                    "Flat (LobsterClaw compat): [{text, data}] — auto-promoted to [[...]]."
                ),
                "items": {},
            },
            # Sticker
            "file_id": {"type": "string", "description": "Sticker file_id (also 'fileId' / 'stickerId')"},
            "fileId": {"type": "string", "description": "Alias for file_id (OpenClaw)"},
            "stickerId": {"type": "string", "description": "Alias for file_id (OpenClaw stickerId[])"},
            # Sticker search
            "query": {"type": "string", "description": "Sticker search query"},
            "limit": {"type": "integer", "description": "Max sticker results (searchSticker, default 5)"},
            # Forum topic
            "topic_name": {"type": "string", "description": "Topic name (also 'name')"},
            "name": {"type": "string", "description": "Alias for topic_name (OpenClaw field)"},
            "icon_color": {"type": "integer", "description": "Topic icon color RGB int (also 'iconColor')"},
            "iconColor": {"type": "integer", "description": "Alias for icon_color (OpenClaw)"},
            "icon_custom_emoji_id": {"type": "string", "description": "Topic custom emoji ID (also 'iconCustomEmojiId')"},
            "iconCustomEmojiId": {"type": "string", "description": "Alias for icon_custom_emoji_id (OpenClaw)"},
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
    # Text
    text: str | None = None,
    content: str | None = None,
    # Media
    source: str | None = None,
    mediaUrl: str | None = None,
    caption: str = "",
    # Target
    chat_id: int | None = None,
    to: str | None = None,
    chatId: str | None = None,              # OpenClaw alias
    # Message ref
    message_id: int | None = None,
    messageId: int | None = None,           # OpenClaw alias
    # Thread
    reply_to_message_id: int | None = None,
    replyToMessageId: int | None = None,
    message_thread_id: int | None = None,
    messageThreadId: int | None = None,
    quote_text: str | None = None,
    quoteText: str | None = None,
    # Modifiers
    as_voice: bool = False,
    asVoice: bool = False,
    silent: bool = False,
    # Account
    account_id: str | None = None,
    accountId: str | None = None,
    # Reaction
    emoji: str | None = None,
    remove: bool = False,
    # Buttons
    buttons: list | None = None,
    # Sticker
    file_id: str | None = None,
    fileId: str | None = None,              # OpenClaw alias
    stickerId: str | None = None,           # OpenClaw stickerId[] (first element)
    # Sticker search
    query: str | None = None,
    limit: int = 5,
    # Forum topic
    topic_name: str | None = None,
    name: str | None = None,               # OpenClaw field name
    icon_color: int | None = None,
    iconColor: int | None = None,          # OpenClaw alias
    icon_custom_emoji_id: str | None = None,
    iconCustomEmojiId: str | None = None,  # OpenClaw alias
) -> str:
    action = _normalise_action(action or "send")

    # ---- Resolve all field aliases → canonical ----
    text = text or content
    source = source or mediaUrl
    reply_to_message_id = reply_to_message_id or replyToMessageId
    message_thread_id = message_thread_id or messageThreadId
    quote_text = quote_text or quoteText
    as_voice = as_voice or asVoice
    message_id = message_id or messageId
    file_id = file_id or fileId or stickerId
    topic_name = topic_name or name
    icon_color = icon_color or iconColor
    icon_custom_emoji_id = icon_custom_emoji_id or iconCustomEmojiId

    # to / chat_id / chatId → effective_target
    from config import get_config
    cfg = get_config()
    owner_id = cfg.telegram_owner_id

    # Resolve 'to' or 'chatId' into a numeric chat_id where possible.
    # Also handles @username, https://t.me/slug, :topic:<chat_id>:<thread_id>
    raw_to = to or (str(chatId) if chatId else None)
    if chat_id is None and raw_to is not None:
        try:
            chat_id = int(raw_to)
        except (ValueError, TypeError):
            # Non-numeric: attempt async resolution via TelegramChannel
            if _channel_ref is not None:
                resolve_fn = getattr(_channel_ref, "resolve_chat_id", None)
                if resolve_fn is not None:
                    try:
                        resolved = await resolve_fn(raw_to)
                        try:
                            chat_id = int(resolved)
                        except (ValueError, TypeError):
                            pass  # unresolvable — will stay None → owner
                    except Exception as e:
                        logger.debug("resolve_chat_id failed for %r: %s", raw_to, e)
    target: int | str = chat_id or owner_id

    # ---- asVoice: synthesise TTS and send as voice note ----
    if as_voice and text:
        try:
            from tools.media_tool import _tts
            import tempfile, os
            tmp = tempfile.mktemp(suffix=".mp3")
            # Use configured TTS voice (not hardcoded)
            tts_voice = cfg.tts_voice if hasattr(cfg, "tts_voice") else "en-US-GuyNeural"
            await _tts(text=text, voice=tts_voice, output_path=tmp)
            if os.path.exists(tmp):
                from tools.media_tool import _send_audio_fn
                if _send_audio_fn:
                    await _send_audio_fn(tmp)
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    return _ok(messageId=None, chatId=target, asVoice=True)
        except Exception as e:
            logger.warning("asVoice TTS failed, falling back to text: %s", e)

    # ================================================================
    # P0: sendMessage — text or media-only, full target routing
    # ================================================================
    if action == "send":
        # media-only send is valid (no text required when source is present)
        if not text and not source:
            return _err("missing_content", "Provide 'text'/'content' or 'mediaUrl'/'source'.")

        # Normalise buttons to 2D
        btns_2d = _normalise_buttons(buttons)

        if source:
            # Media send (photo / document / voice)
            if _send_photo_fn and _looks_like_image(source):
                try:
                    result = await _send_photo_fn(
                        source,
                        caption or text or "",
                        chat_id=target,
                        reply_to_message_id=reply_to_message_id,
                        message_thread_id=message_thread_id,
                        silent=silent,
                    )
                    msg_id = result if isinstance(result, int) else None
                    return _ok(messageId=msg_id, chatId=target)
                except Exception as e:
                    return _err("send_photo_failed", str(e))
            elif _send_document_fn:
                try:
                    result = await _send_document_fn(
                        source,
                        caption or text or "",
                        chat_id=target,
                        reply_to_message_id=reply_to_message_id,
                        message_thread_id=message_thread_id,
                        silent=silent,
                    )
                    msg_id = result if isinstance(result, int) else None
                    return _ok(messageId=msg_id, chatId=target)
                except Exception as e:
                    return _err("send_document_failed", str(e))

        # Text send
        if not text:
            return _err("missing_content", "Provide 'text'/'content'.")
        msg = f">{quote_text}\n\n{text}" if quote_text else text

        if btns_2d and _send_buttons_fn:
            try:
                result = await _send_buttons_fn(
                    msg, btns_2d, target,
                    reply_to_message_id=reply_to_message_id,
                    message_thread_id=message_thread_id,
                    silent=silent,
                )
                msg_id = result if isinstance(result, int) else None
                return _ok(messageId=msg_id, chatId=target)
            except Exception as e:
                return _err("send_buttons_failed", str(e))

        # Plain text send — prefer send_to for routing; fallback to owner send_fn
        if _send_to_fn and (target != owner_id or raw_to):
            try:
                result = await _send_to_fn(
                    target, msg,
                    reply_to_message_id=reply_to_message_id,
                    message_thread_id=message_thread_id,
                    silent=silent,
                )
                msg_id = result if isinstance(result, int) else None
                return _ok(messageId=msg_id, chatId=target)
            except Exception as e:
                return _err("send_failed", str(e))
        elif _send_fn:
            try:
                await _send_fn(msg)
                return _ok(messageId=None, chatId=target)
            except Exception as e:
                return _err("send_failed", str(e))
        return _err("not_configured", "Telegram send_fn not configured.")

    # ================================================================
    # send_photo
    # ================================================================
    if action == "send_photo":
        if not source:
            return _err("missing_source", "Provide 'source' or 'mediaUrl'.")
        if not _send_photo_fn:
            return _err("not_configured", "send_photo not wired.")
        try:
            result = await _send_photo_fn(
                source, caption,
                chat_id=target,
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id,
                silent=silent,
            )
            msg_id = result if isinstance(result, int) else None
            return _ok(messageId=msg_id, chatId=target)
        except Exception as e:
            return _err("send_photo_failed", str(e))

    # ================================================================
    # send_document
    # ================================================================
    if action == "send_document":
        if not source:
            return _err("missing_source", "Provide 'source' or 'mediaUrl'.")
        if not _send_document_fn:
            return _err("not_configured", "send_document not wired.")
        try:
            result = await _send_document_fn(
                source, caption,
                chat_id=target,
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id,
                silent=silent,
            )
            msg_id = result if isinstance(result, int) else None
            return _ok(messageId=msg_id, chatId=target)
        except Exception as e:
            return _err("send_document_failed", str(e))

    # ================================================================
    # P0/P1: send_sticker — fileId / stickerId / file_id
    # ================================================================
    if action == "send_sticker":
        if not file_id:
            return _err("missing_file_id", "Provide 'fileId', 'stickerId', or 'file_id'.")
        if not _send_sticker_fn:
            return _err("not_configured", "send_sticker not wired.")
        try:
            result = await _send_sticker_fn(
                target, file_id,
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id,
            )
            msg_id = result if isinstance(result, int) else None
            return _ok(messageId=msg_id, chatId=target)
        except Exception as e:
            return _err("send_sticker_failed", str(e))

    # ================================================================
    # search_sticker
    # ================================================================
    if action == "search_sticker":
        if not query:
            return _err("missing_query", "Provide 'query' (sticker set name or emoji).")
        return await _search_sticker(query, int(limit or 5))

    # ================================================================
    # sticker_cache_stats
    # ================================================================
    if action == "sticker_cache_stats":
        total = sum(len(v) for v in _sticker_cache.values())
        return _ok(sets=len(_sticker_cache), total=total)

    # ================================================================
    # edit — structured result
    # ================================================================
    if action == "edit":
        effective_mid = message_id
        effective_cid = chat_id or owner_id
        if not effective_mid:
            return _err("missing_message_id", "Provide 'messageId' or 'message_id'.")
        if not text:
            return _err("missing_content", "Provide 'content'/'text'.")
        if not _edit_fn:
            return _err("not_configured", "edit_message not wired.")
        try:
            result = await _edit_fn(
                effective_cid, effective_mid, text,
                buttons=_normalise_buttons(buttons),
            )
            msg_id = result if isinstance(result, int) else effective_mid
            return _ok(messageId=msg_id, chatId=effective_cid)
        except Exception as e:
            return _err("edit_failed", str(e))

    # ================================================================
    # delete — structured result
    # ================================================================
    if action == "delete":
        effective_mid = message_id
        effective_cid = chat_id or owner_id
        if not effective_mid:
            return _err("missing_message_id", "Provide 'messageId' or 'message_id'.")
        if not _delete_fn:
            return _err("not_configured", "delete_message not wired.")
        try:
            await _delete_fn(effective_cid, effective_mid)
            return _ok(deleted=True)
        except Exception as e:
            return _err("delete_failed", str(e))

    # ================================================================
    # P1: react — remove + fallback to _current_message_id
    # ================================================================
    if action == "react":
        # Fallback to most-recently-received inbound message ID
        effective_mid = message_id or _current_message_id
        effective_cid = chat_id or owner_id

        if not effective_mid:
            return _err(
                "missing_message_id",
                "Telegram reaction requires a valid messageId. Do not retry.",
            )
        if not remove and not emoji:
            return _err("missing_emoji", "Provide 'emoji' or set remove=true.")
        if not _react_fn:
            return _err("not_configured", "react_to_message not wired.")
        try:
            await _react_fn(effective_cid, effective_mid, emoji, remove=remove)
            if remove or not emoji:
                return _ok(removed=True)
            return _ok(added=emoji)
        except Exception as e:
            is_invalid = "REACTION_INVALID" in str(e)
            return _err(
                "REACTION_INVALID" if is_invalid else "react_failed",
                (
                    "This emoji is not supported for Telegram reactions. "
                    "Add it to your reaction disallow list so you do not try it again."
                ) if is_invalid else "Reaction failed. Do not retry.",
                emoji=emoji,
            )

    # ================================================================
    # P1: buttons — 2D rows
    # ================================================================
    if action == "buttons":
        if not text:
            return _err("missing_content", "Provide 'text'.")
        btns_2d = _normalise_buttons(buttons)
        if not btns_2d:
            return _err("missing_buttons", "Provide non-empty 'buttons' array.")
        if not _send_buttons_fn:
            return _err("not_configured", "send_with_buttons not wired.")
        try:
            result = await _send_buttons_fn(
                text, btns_2d, target,
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id,
                silent=silent,
            )
            msg_id = result if isinstance(result, int) else None
            return _ok(messageId=msg_id, chatId=target)
        except Exception as e:
            return _err("send_buttons_failed", str(e))

    # ================================================================
    # P0: createForumTopic — iconColor / iconCustomEmojiId
    # ================================================================
    if action == "create_forum_topic":
        if not topic_name:
            return _err("missing_name", "Provide 'name' or 'topic_name'.")
        if not _create_forum_topic_fn:
            return _err("not_configured", "create_forum_topic not wired.")
        try:
            result = await _create_forum_topic_fn(
                target, topic_name,
                icon_color=icon_color,
                icon_custom_emoji_id=icon_custom_emoji_id,
            )
            if isinstance(result, dict):
                return _ok(**result)
            return _ok(topicId=result, name=topic_name, chatId=target)
        except Exception as e:
            return _err("create_topic_failed", str(e))

    # ================================================================
    # pin / unpin
    # ================================================================
    if action == "pin":
        effective_mid = message_id or messageId
        effective_cid = chat_id or owner_id
        if not effective_mid:
            return _err("missing_message_id", "Provide 'messageId' to pin.")
        if not _pin_fn:
            return _err("not_configured", "pin_message not wired.")
        try:
            await _pin_fn(effective_cid, effective_mid, disable_notification=silent)
            return _ok(pinned=True, messageId=effective_mid, chatId=effective_cid)
        except Exception as e:
            return _err("pin_failed", str(e))

    if action == "unpin":
        effective_mid = message_id or messageId
        effective_cid = chat_id or owner_id
        if not effective_mid:
            return _err("missing_message_id", "Provide 'messageId' to unpin.")
        if not _unpin_fn:
            return _err("not_configured", "unpin_message not wired.")
        try:
            await _unpin_fn(effective_cid, effective_mid)
            return _ok(unpinned=True, messageId=effective_mid, chatId=effective_cid)
        except Exception as e:
            return _err("unpin_failed", str(e))

    if action == "unpin_all":
        effective_cid = chat_id or owner_id
        if not _unpin_all_fn:
            return _err("not_configured", "unpin_all_messages not wired.")
        try:
            await _unpin_all_fn(effective_cid)
            return _ok(unpinnedAll=True, chatId=effective_cid)
        except Exception as e:
            return _err("unpin_all_failed", str(e))

    all_actions = (
        "send/sendMessage, send_photo/sendPhoto, send_document/sendDocument, "
        "send_sticker/sendSticker/sticker, search_sticker/searchSticker/sticker-search, "
        "sticker_cache_stats/stickerCacheStats, "
        "edit/editMessage, delete/deleteMessage, react/reactMessage, "
        "buttons/sendButtons, create_forum_topic/createForumTopic/topic-create, "
        "pin/pinMessage, unpin/unpinMessage, unpin_all"
    )
    return _err("unknown_action", f"Unknown action '{action}'. Use: {all_actions}")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _looks_like_image(path_or_url: str) -> bool:
    s = path_or_url.lower().split("?")[0]
    return s.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp"))


def _get_effective_sticker_cache() -> dict[str, list[dict]]:
    """Return the persistent sticker cache from the channel reference if available."""
    if _channel_ref is not None:
        get_fn = getattr(_channel_ref, "get_sticker_cache", None)
        if get_fn is not None:
            return get_fn()
    return _sticker_cache


def _update_sticker_cache(set_name: str, stickers: list[dict]) -> None:
    """Persist sticker cache entry via channel reference (SQLite-backed)."""
    if _channel_ref is not None:
        update_fn = getattr(_channel_ref, "update_sticker_cache", None)
        if update_fn is not None:
            update_fn(set_name, stickers)
            return
    _sticker_cache[set_name] = stickers


async def _search_sticker(query: str, limit: int = 5) -> str:
    cache = _get_effective_sticker_cache()
    if query in cache:
        stickers = cache[query][:limit]
        return _ok(
            count=len(stickers),
            stickers=[
                {"fileId": s.get("file_id") or s.get("fileId", ""),
                 "emoji": s.get("emoji", ""),
                 "description": s.get("description", ""),
                 "setName": s.get("setName") or query}
                for s in stickers
            ],
        )
    try:
        import httpx
        from config import get_config
        resp = await httpx.AsyncClient(timeout=10).get(
            f"https://api.telegram.org/bot{get_config().telegram_bot_token}/getStickerSet",
            params={"name": query},
        )
        data = resp.json()
        if data.get("ok"):
            ss = data["result"]
            raw_stickers = ss.get("stickers", [])
            stickers = [
                {"file_id": s["file_id"], "fileId": s["file_id"],
                 "emoji": s.get("emoji", ""), "setName": ss["name"],
                 "description": s.get("emoji", "")}
                for s in raw_stickers
            ]
            _update_sticker_cache(query, stickers)
            return _ok(
                count=len(stickers[:limit]),
                stickers=[
                    {"fileId": s["fileId"], "emoji": s["emoji"],
                     "description": s["description"], "setName": s["setName"]}
                    for s in stickers[:limit]
                ],
            )
        return _err("not_found", f"Sticker set '{query}' not found: {data.get('description', '')}")
    except Exception as e:
        return _err("search_failed", str(e))
