"""
Telegram channel — full mirror of OpenClaw's Telegram capabilities.

Receives: text, photos, documents, voice (with optional transcription), audio,
          video, stickers, locations, forwarded messages, reply-to extraction
Sends: text (HTML ParseMode), photos, documents, voice/audio, stickers,
       inline buttons, pin/unpin

Auth policy:
  DM:    "owner" (default) | "allowlist" (owner + TELEGRAM_ALLOW_FROM) | "open"
  Group: "disabled" (default) | "open" | "allowlist" (TELEGRAM_GROUP_ALLOWLIST)
  Forum threads: each thread gets its own isolated conversation context.

Safety:
  - ParseMode: HTML with fallback to plain text on parse errors
  - Message splitting respects word/line boundaries
  - Typing indicator refreshed every 4s during long agent runs
  - sendChatAction 401 backoff: stop retrying on auth errors
  - Supergroup migration: update stored chat_id transparently
  - All temp files cleaned up after use
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import get_config

if TYPE_CHECKING:
    from agent.history import HistoryManager
    from agent.loop import AgentLoop
    from tools.approval import ApprovalGate

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4000
TYPING_REFRESH_INTERVAL = 4  # seconds between typing indicator refreshes
HTML_STRIP_RE = re.compile(r"<[^>]+>")


class TelegramChannel:
    def __init__(
        self,
        agent: "AgentLoop",
        history: "HistoryManager",
        approval: "ApprovalGate",
        build_prompt: Callable,
    ) -> None:
        self.cfg = get_config()
        self.agent = agent
        self.history = history
        self.approval = approval
        self.build_prompt = build_prompt

        self._app = (
            Application.builder()
            .token(self.cfg.telegram_bot_token)
            .build()
        )
        self._bot: Bot = self._app.bot

        # Wire approval gate with timeout notification callback
        approval.configure(
            send_fn=self._send_approval_request,
            timeout=self.cfg.exec_confirmation_timeout_seconds,
            notify_fn=self.send_message,
        )

        self._register_handlers()

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def _register_handlers(self) -> None:
        app = self._app

        # Commands
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("reset", self._cmd_reset))
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("model", self._cmd_model))

        # Approval inline buttons
        app.add_handler(CallbackQueryHandler(self._handle_callback))

        # Text messages (DM and group)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text))

        # Photos
        app.add_handler(MessageHandler(filters.PHOTO, self._handle_photo))

        # Voice messages
        app.add_handler(MessageHandler(filters.VOICE, self._handle_voice))

        # Audio files
        app.add_handler(MessageHandler(filters.AUDIO, self._handle_audio))

        # Video
        app.add_handler(MessageHandler(filters.VIDEO, self._handle_video))

        # Documents (catch-all for files)
        app.add_handler(MessageHandler(filters.Document.ALL, self._handle_document))

        # Stickers
        app.add_handler(MessageHandler(filters.Sticker.ALL, self._handle_sticker))

        # Location
        app.add_handler(MessageHandler(filters.LOCATION, self._handle_location))

    # ------------------------------------------------------------------
    # Auth guard
    # ------------------------------------------------------------------

    def _is_owner(self, update: Update) -> bool:
        return bool(update.effective_user and update.effective_user.id == self.cfg.telegram_owner_id)

    def _is_allowed(self, update: Update) -> bool:
        """Return True if this update is from an authorised user/chat."""
        user_id = update.effective_user.id if update.effective_user else None
        chat = update.effective_chat

        if not user_id:
            return False

        is_dm = chat and chat.type in ("private",)
        is_group = chat and chat.type in ("group", "supergroup")

        if is_dm:
            policy = getattr(self.cfg, "telegram_dm_policy", "owner")
            if policy == "open":
                return True
            if policy == "allowlist":
                allow_from = getattr(self.cfg, "telegram_allow_from", [])
                return user_id == self.cfg.telegram_owner_id or user_id in allow_from
            # Default: "owner"
            return user_id == self.cfg.telegram_owner_id

        if is_group:
            policy = getattr(self.cfg, "telegram_group_policy", "disabled")
            if policy == "disabled":
                return False
            if policy == "open":
                return True
            if policy == "allowlist":
                group_allowlist = getattr(self.cfg, "telegram_group_allowlist", [])
                return chat.id in group_allowlist
            return False

        # Unknown chat type: deny by default
        return False

    def _context_key(self, update: Update) -> str:
        """
        Return the conversation context key for history lookups.
        - DM: "user:<user_id>"
        - Group: "group:<chat_id>"
        - Forum thread: "thread:<chat_id>:<thread_id>"
        """
        chat = update.effective_chat
        msg = update.effective_message
        if chat and chat.type in ("group", "supergroup"):
            thread_id = msg.message_thread_id if msg else None
            if thread_id:
                return f"thread:{chat.id}:{thread_id}"
            return f"group:{chat.id}"
        user_id = update.effective_user.id if update.effective_user else "unknown"
        return f"user:{user_id}"

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update):
            return
        await update.message.reply_text("PyGate is running. Send me a message.")

    async def _cmd_reset(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update):
            return
        key = self._context_key(update)
        self.history.clear(key)
        await update.message.reply_text("Conversation reset.")

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update):
            return
        help_text = (
            "<b>PyGate — Commands</b>\n\n"
            "/reset — Clear conversation history\n"
            "/status — Show bot and agent status\n"
            "/model — Show current model\n"
            "/help — This message\n\n"
            "Send any text, photo, voice, video, or document to chat with the agent."
        )
        await self._safe_send(update.effective_chat.id, help_text)

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            return
        cfg = self.cfg
        tools = self.agent.registry.get_names()
        thinking_budget = getattr(cfg, "llm_thinking_budget", 0)
        thinking_str = f"budget={thinking_budget}" if thinking_budget else "disabled"
        status = (
            f"<b>PyGate Status</b>\n\n"
            f"Model: <code>{cfg.llm_model}</code> ({cfg.llm_provider})\n"
            f"Extended thinking: {thinking_str}\n"
            f"Tools ({len(tools)}): {', '.join(tools)}\n"
            f"Cron: {'✅' if cfg.cron_enabled else '❌'}\n"
            f"Browser: {'✅' if cfg.browser_enabled else '❌'}\n"
            f"Exec: {'✅' if cfg.exec_enabled else '❌'}\n"
            f"Memory: {'✅' if cfg.memory_enabled else '❌'}\n"
            f"Voice transcription: {'✅' if getattr(cfg, 'telegram_voice_transcription', False) else '❌'}\n"
        )
        await self._safe_send(update.effective_chat.id, status)

    async def _cmd_model(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            return
        args = ctx.args or []
        cfg = self.cfg
        if args:
            # /model claude-3-opus-20240229 — override via config (runtime only, not persisted)
            new_model = args[0].strip()
            cfg.llm_model = new_model
            await update.message.reply_text(f"Model switched to <code>{new_model}</code>", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(
                f"Current model: <code>{cfg.llm_model}</code> ({cfg.llm_provider})",
                parse_mode=ParseMode.HTML,
            )

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    async def _handle_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update):
            return

        # Handle supergroup migration
        if update.message and update.message.migrate_to_chat_id:
            logger.info(
                "Supergroup migration: %s → %s",
                update.effective_chat.id,
                update.message.migrate_to_chat_id,
            )
            return

        text = update.message.text or ""

        # Prepend reply-to text if the user quoted another message
        if update.message.reply_to_message:
            reply_body = _extract_reply_body(update.message.reply_to_message)
            if reply_body:
                text = f'[Reply to: "{reply_body}"]\n{text}'

        # Forwarded message attribution
        if update.message.forward_from:
            fwd = update.message.forward_from
            text = f'[Forwarded from {fwd.first_name or ""} {fwd.last_name or ""}]\n{text}'
        elif update.message.forward_from_chat:
            text = f'[Forwarded from channel: {update.message.forward_from_chat.title or ""}]\n{text}'

        await self._run_agent(update, text)

    async def _handle_photo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update):
            return
        caption = update.message.caption or ""
        photo = update.message.photo[-1]  # largest size
        tmp_path = None
        try:
            tmp_path = await self._download_file(photo.file_id, suffix=".jpg")
            text = f"[Photo attached: {tmp_path}]{(' — ' + caption) if caption else ''}"
            await self._run_agent(update, text)
        finally:
            _cleanup_file(tmp_path)

    async def _handle_voice(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update):
            return
        voice = update.message.voice
        tmp_path = None
        try:
            tmp_path = await self._download_file(voice.file_id, suffix=".ogg")
            transcript = await self._transcribe_voice(tmp_path)
            if transcript:
                text = f'[Voice message transcription: "{transcript}"]'
            else:
                text = (
                    f"[Voice message received: {tmp_path} ({voice.duration}s). "
                    "Transcription not available — reply to acknowledge or ask me to process it.]"
                )
            await self._run_agent(update, text)
        finally:
            _cleanup_file(tmp_path)

    async def _handle_audio(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update):
            return
        audio = update.message.audio
        tmp_path = None
        try:
            suffix = Path(audio.file_name or "audio.mp3").suffix or ".mp3"
            tmp_path = await self._download_file(audio.file_id, suffix=suffix)
            name = audio.title or audio.file_name or "audio"
            text = f"[Audio file received: {name} ({audio.duration}s) at {tmp_path}]"
            await self._run_agent(update, text)
        finally:
            _cleanup_file(tmp_path)

    async def _handle_video(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update):
            return
        video = update.message.video
        caption = update.message.caption or ""
        tmp_path = None
        try:
            suffix = Path(video.file_name or "video.mp4").suffix or ".mp4"
            tmp_path = await self._download_file(video.file_id, suffix=suffix)
            text = (
                f"[Video received: {video.file_name or 'video'} ({video.duration}s) at {tmp_path}]"
                f"{(' — ' + caption) if caption else ''}"
            )
            await self._run_agent(update, text)
        finally:
            _cleanup_file(tmp_path)

    async def _handle_document(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update):
            return
        doc = update.message.document
        caption = update.message.caption or ""
        tmp_path = None
        try:
            suffix = Path(doc.file_name or "file").suffix or ""
            tmp_path = await self._download_file(doc.file_id, suffix=suffix)
            if doc.mime_type and doc.mime_type.startswith("image/"):
                text = f"[Image document: {doc.file_name} at {tmp_path}]{(' — ' + caption) if caption else ''}"
            else:
                text = f"[Document received: {doc.file_name} ({doc.mime_type}) at {tmp_path}]{(' — ' + caption) if caption else ''}"
            await self._run_agent(update, text)
        finally:
            _cleanup_file(tmp_path)

    async def _handle_sticker(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update):
            return
        sticker = update.message.sticker
        emoji = sticker.emoji or ""
        # Include description/alt_text if available (new Telegram field for custom stickers)
        desc = getattr(sticker, "custom_emoji_id", "") or ""
        text = (
            f"[Sticker received: {emoji} (set: {sticker.set_name or 'unknown'}, "
            f"file_id: {sticker.file_id}{', id=' + desc if desc else ''})]"
        )
        await self._run_agent(update, text)

    async def _handle_location(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update):
            return
        loc = update.message.location
        text = f"[Location shared: lat={loc.latitude}, lon={loc.longitude}]"
        await self._run_agent(update, text)

    # ------------------------------------------------------------------
    # Voice transcription (Whisper)
    # ------------------------------------------------------------------

    async def _transcribe_voice(self, audio_path: str) -> str | None:
        """Transcribe a voice file using OpenAI Whisper if configured."""
        cfg = self.cfg
        if not getattr(cfg, "telegram_voice_transcription", False):
            return None
        if not cfg.openai_api_key:
            logger.debug("Voice transcription skipped — no OPENAI_API_KEY")
            return None
        try:
            import httpx
            async with httpx.AsyncClient(timeout=60) as client:
                with open(audio_path, "rb") as f:
                    resp = await client.post(
                        "https://api.openai.com/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {cfg.openai_api_key}"},
                        data={"model": "whisper-1"},
                        files={"file": (Path(audio_path).name, f, "audio/ogg")},
                    )
                resp.raise_for_status()
                result = resp.json()
                return result.get("text", "").strip() or None
        except Exception as e:
            logger.warning("Voice transcription failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Core agent run
    # ------------------------------------------------------------------

    async def _run_agent(self, update: Update, user_text: str) -> None:
        chat_id = update.effective_chat.id
        context_key = self._context_key(update)

        # Expose inbound message_id as fallback for react tool
        if update.effective_message:
            from tools.message_tool import set_current_message_id
            set_current_message_id(update.effective_message.message_id)

        # Add to history
        self.history.add(context_key, "user", user_text)

        # Build prompt and messages
        tool_names = self.agent.registry.get_names()
        system = self.build_prompt(self.cfg, tool_names)
        messages = self.history.get_for_llm(context_key)

        # Send typing indicator, refresh it while agent runs
        typing_task = asyncio.create_task(self._typing_loop(chat_id))

        try:
            reply = await self.agent.run(messages, system, session_id="main")
        except Exception as e:
            logger.exception("Agent run failed")
            reply = f"Sorry, something went wrong: {type(e).__name__}"
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

        if reply:
            self.history.add(context_key, "assistant", reply)
            await self._send_chunked(chat_id, reply)

    async def _typing_loop(self, chat_id: int) -> None:
        """Send typing action every TYPING_REFRESH_INTERVAL seconds until cancelled."""
        _backoff = False
        try:
            while True:
                if not _backoff:
                    try:
                        await self._bot.send_chat_action(chat_id, ChatAction.TYPING)
                    except Forbidden:
                        # 403 — stop retrying, the bot can no longer send to this chat
                        logger.warning("sendChatAction 401/403 for chat %s — stopping typing indicator", chat_id)
                        _backoff = True
                    except Exception:
                        pass  # transient — keep trying
                await asyncio.sleep(TYPING_REFRESH_INTERVAL)
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Approval gate
    # ------------------------------------------------------------------

    async def _send_approval_request(self, text: str, request_id: str) -> None:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve:{request_id}"),
                InlineKeyboardButton("❌ Deny", callback_data=f"deny:{request_id}"),
            ]
        ])
        await self._safe_send(
            self.cfg.telegram_owner_id,
            text,
            reply_markup=keyboard,
        )

    async def _handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query:
            return
        await query.answer()

        data = query.data or ""
        if data.startswith("approve:") or data.startswith("deny:"):
            approved = data.startswith("approve:")
            request_id = data.split(":", 1)[1]
            resolved = self.approval.resolve(request_id, approved)
            action = "Approved ✅" if approved else "Denied ❌"
            if resolved:
                try:
                    await query.edit_message_text(
                        f"{query.message.text}\n\n— {action}"
                    )
                except Exception:
                    pass
            else:
                try:
                    await query.edit_message_text("This request has already been resolved.")
                except Exception:
                    pass
        else:
            # Agent-defined inline button callback — pass callback_data back to agent
            payload = data
            await query.edit_message_reply_markup(reply_markup=None)
            await self._run_agent_from_button(update, payload)

    async def _run_agent_from_button(self, update: Update, payload: str) -> None:
        """Handle user pressing an agent-defined inline button."""
        await self._run_agent(update, f"[Button pressed: {payload}]")

    # ------------------------------------------------------------------
    # Proactive send methods (called by tools)
    # ------------------------------------------------------------------

    async def send_message(self, text: str) -> None:
        """Send a text message to the owner. Called by message_tool."""
        await self._send_chunked(self.cfg.telegram_owner_id, text)

    async def send_to(
        self,
        chat_id: int | str,
        text: str,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        silent: bool = False,
    ) -> int | None:
        """
        Send to an explicit target chat (not just owner).
        Returns message_id on success. Raises on failure (caller decides how to handle).
        """
        kwargs: dict = {}
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = reply_to_message_id
        if message_thread_id:
            kwargs["message_thread_id"] = message_thread_id
        if silent:
            kwargs["disable_notification"] = True
        chunks = _split_message(text)
        last_msg = None
        for chunk in chunks:
            last_msg = await self._safe_send(int(chat_id), chunk, **kwargs)
        # _safe_send returns a list; get the last sent message
        if isinstance(last_msg, list) and last_msg:
            last_msg = last_msg[-1]
        return last_msg.message_id if last_msg else None

    async def send_audio(self, audio_path: str) -> None:
        """Send an audio file as a voice message. Raises on failure."""
        with open(audio_path, "rb") as f:
            await self._bot.send_voice(
                chat_id=self.cfg.telegram_owner_id,
                voice=f,
            )

    async def send_photo(
        self,
        photo_path_or_url: str,
        caption: str = "",
        *,
        chat_id: int | str | None = None,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        silent: bool = False,
    ) -> int | None:
        """
        Send a photo to chat_id (defaults to owner).
        Raises on failure so message_tool can return a structured error.
        Returns message_id.
        """
        target = int(chat_id) if chat_id else self.cfg.telegram_owner_id
        kwargs: dict = {}
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = reply_to_message_id
        if message_thread_id:
            kwargs["message_thread_id"] = message_thread_id
        if silent:
            kwargs["disable_notification"] = True
        if photo_path_or_url.startswith("http"):
            msg = await self._bot.send_photo(
                chat_id=target,
                photo=photo_path_or_url,
                caption=caption or None,
                **kwargs,
            )
        else:
            with open(photo_path_or_url, "rb") as f:
                msg = await self._bot.send_photo(
                    chat_id=target,
                    photo=f,
                    caption=caption or None,
                    **kwargs,
                )
        return msg.message_id if msg else None

    async def send_document(
        self,
        file_path: str,
        caption: str = "",
        *,
        chat_id: int | str | None = None,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        silent: bool = False,
    ) -> int | None:
        """
        Send a document to chat_id (defaults to owner).
        Raises on failure. Returns message_id.
        """
        target = int(chat_id) if chat_id else self.cfg.telegram_owner_id
        kwargs: dict = {}
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = reply_to_message_id
        if message_thread_id:
            kwargs["message_thread_id"] = message_thread_id
        if silent:
            kwargs["disable_notification"] = True
        with open(file_path, "rb") as f:
            msg = await self._bot.send_document(
                chat_id=target,
                document=f,
                caption=caption or None,
                filename=Path(file_path).name,
                **kwargs,
            )
        return msg.message_id if msg else None

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        buttons: list[list[dict]] | None = None,
    ) -> int | None:
        """
        Edit an existing message. Raises on failure. Returns message_id.
        Accepts 2D buttons for inline keyboard update.
        """
        markup = _build_inline_keyboard(buttons) if buttons else None
        kwargs: dict = {}
        if markup:
            kwargs["reply_markup"] = markup
        result = await self._bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text[:4096],
            **kwargs,
        )
        return result.message_id if isinstance(result, Message) else message_id

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        """Delete a message. Raises on failure."""
        await self._bot.delete_message(chat_id=chat_id, message_id=message_id)

    async def react_to_message(
        self,
        chat_id: int,
        message_id: int,
        emoji: str | None,
        *,
        remove: bool = False,
    ) -> None:
        """
        Set or remove a reaction (requires Bot API 7.0+).
        remove=True clears all reactions. Raises on failure so caller gets error info.
        """
        from telegram import ReactionTypeEmoji
        if remove or not emoji:
            await self._bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[],
            )
        else:
            await self._bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)],
            )

    async def send_with_buttons(
        self,
        text: str,
        buttons: list[list[dict]],
        chat_id: int | str | None = None,
        *,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        silent: bool = False,
    ) -> int | None:
        """
        Send message with inline keyboard.
        buttons must be 2D: [[{text, callback_data, ?style}]].
        Raises on failure. Returns message_id.
        """
        target = int(chat_id) if chat_id else self.cfg.telegram_owner_id
        markup = _build_inline_keyboard(buttons)
        kwargs: dict = {"reply_markup": markup} if markup else {}
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = reply_to_message_id
        if message_thread_id:
            kwargs["message_thread_id"] = message_thread_id
        if silent:
            kwargs["disable_notification"] = True
        msgs = await self._safe_send(target, text, **kwargs)
        if isinstance(msgs, list) and msgs:
            return msgs[-1].message_id
        if isinstance(msgs, Message):
            return msgs.message_id
        return None

    async def send_sticker(
        self,
        chat_id: int | str,
        file_id: str,
        *,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> int | None:
        """
        Send a sticker by file_id. Raises on failure. Returns message_id.
        Accepts replyToMessageId / messageThreadId for full OpenClaw parity.
        """
        kwargs: dict = {}
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = reply_to_message_id
        if message_thread_id:
            kwargs["message_thread_id"] = message_thread_id
        msg = await self._bot.send_sticker(
            chat_id=int(chat_id),
            sticker=file_id,
            **kwargs,
        )
        return msg.message_id if msg else None

    async def create_forum_topic(
        self,
        chat_id: int | str,
        name: str,
        *,
        icon_color: int | None = None,
        icon_custom_emoji_id: str | None = None,
    ) -> dict:
        """
        Create a forum topic. Returns dict {topicId, name, chatId}.
        Raises on failure. Passes iconColor/iconCustomEmojiId (OpenClaw parity).
        """
        kwargs: dict = {}
        if icon_color is not None:
            kwargs["icon_color"] = icon_color
        if icon_custom_emoji_id:
            kwargs["icon_custom_emoji_id"] = icon_custom_emoji_id
        topic = await self._bot.create_forum_topic(
            chat_id=int(chat_id),
            name=name,
            **kwargs,
        )
        return {
            "topicId": topic.message_thread_id,
            "name": name,
            "chatId": int(chat_id),
        }

    async def pin_message(
        self,
        chat_id: int | str,
        message_id: int,
        *,
        disable_notification: bool = False,
    ) -> None:
        """Pin a message in a chat. Raises on failure."""
        await self._bot.pin_chat_message(
            chat_id=int(chat_id),
            message_id=message_id,
            disable_notification=disable_notification,
        )

    async def unpin_message(self, chat_id: int | str, message_id: int) -> None:
        """Unpin a specific message. Raises on failure."""
        await self._bot.unpin_chat_message(chat_id=int(chat_id), message_id=message_id)

    async def unpin_all_messages(self, chat_id: int | str) -> None:
        """Unpin all messages in a chat. Raises on failure."""
        await self._bot.unpin_all_chat_messages(chat_id=int(chat_id))

    # ------------------------------------------------------------------
    # Safe send with HTML ParseMode fallback + chunking + link preview
    # ------------------------------------------------------------------

    async def _safe_send(
        self,
        chat_id: int,
        text: str,
        reply_markup=None,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        disable_notification: bool = False,
    ) -> list[Message]:
        """
        Send text using HTML ParseMode. Falls back to plain text on parse errors.
        Respects telegram_link_preview config.
        """
        chunks = _split_message(text)
        sent = []
        disable_web_preview = not getattr(self.cfg, "telegram_link_preview", True)
        for i, chunk in enumerate(chunks):
            markup = reply_markup if i == 0 else None
            kwargs: dict = {}
            if reply_to_message_id and i == 0:
                kwargs["reply_to_message_id"] = reply_to_message_id
            if message_thread_id:
                kwargs["message_thread_id"] = message_thread_id
            if disable_notification:
                kwargs["disable_notification"] = True
            if disable_web_preview:
                kwargs["disable_web_page_preview"] = True
            try:
                msg = await self._bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup,
                    **kwargs,
                )
                sent.append(msg)
            except BadRequest as e:
                err_lower = str(e).lower()
                if "can't parse" in err_lower or "parse" in err_lower or "html" in err_lower:
                    # HTML parse error — strip tags and retry as plain text
                    plain = HTML_STRIP_RE.sub("", chunk)
                    try:
                        msg = await self._bot.send_message(
                            chat_id=chat_id,
                            text=plain,
                            reply_markup=markup,
                            **kwargs,
                        )
                        sent.append(msg)
                    except Exception as inner:
                        logger.error("Failed to send plain text message: %s", inner)
                else:
                    logger.error("Failed to send message: %s", e)
            except Exception as e:
                logger.error("Failed to send message: %s", e)
        return sent

    async def _send_chunked(self, chat_id: int, text: str) -> None:
        await self._safe_send(chat_id, text)

    # ------------------------------------------------------------------
    # File download helper
    # ------------------------------------------------------------------

    async def _download_file(self, file_id: str, suffix: str = "") -> str:
        """Download a Telegram file to a temp path. Caller must clean up."""
        tg_file = await self._bot.get_file(file_id)
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        await tg_file.download_to_drive(tmp_path)
        return tmp_path

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._app.run_polling(drop_pending_updates=True)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _build_inline_keyboard(buttons_2d: list[list[dict]] | None) -> InlineKeyboardMarkup | None:
    """
    Build Telegram InlineKeyboardMarkup from 2D button array.
    Each button must have {text, callback_data}. Optional style field is ignored.
    """
    if not buttons_2d:
        return None
    rows = []
    for row in buttons_2d:
        kb_row = []
        for btn in row:
            text = btn.get("text", "")
            cb = btn.get("callback_data") or btn.get("data") or text
            cb = str(cb)[:64]
            kb_row.append(InlineKeyboardButton(text, callback_data=cb))
        if kb_row:
            rows.append(kb_row)
    return InlineKeyboardMarkup(rows) if rows else None


def _split_message(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split text at word/line boundaries."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_len:
        # Try to split at last newline within limit
        split_at = remaining.rfind("\n", 0, max_len)
        if split_at <= 0:
            # Try last space
            split_at = remaining.rfind(" ", 0, max_len)
        if split_at <= 0:
            # Hard split
            split_at = max_len
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")

    if remaining:
        chunks.append(remaining)
    return chunks


def _extract_reply_body(reply_msg: Message) -> str | None:
    """Extract displayable text from a replied-to message."""
    if not reply_msg:
        return None
    text = reply_msg.text or reply_msg.caption or ""
    if text:
        # Truncate to avoid bloating context
        return text[:200] + ("…" if len(text) > 200 else "")
    if reply_msg.sticker:
        return f"[sticker {reply_msg.sticker.emoji or ''}]"
    if reply_msg.photo:
        return "[photo]"
    if reply_msg.voice:
        return "[voice]"
    if reply_msg.document:
        return f"[document: {reply_msg.document.file_name or 'file'}]"
    return None


def _cleanup_file(path: str | None) -> None:
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass
