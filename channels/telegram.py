"""
Telegram channel — full mirror of OpenClaw's Telegram capabilities.

Receives: text, photos, documents, voice, audio, video, stickers, locations
Sends: text, photos, documents, voice/audio, stickers, inline buttons

Agent-callable actions via message_tool / telegram_actions_tool:
  send, edit, delete, react, send_photo, send_document,
  send_sticker, send_voice, create_forum_topic

Safety:
  - ParseMode: tries MARKDOWN first, falls back to plain text on parse errors
  - Message splitting respects word/line boundaries
  - Typing indicator refreshed every 4s during long agent runs
  - All temp files are cleaned up after use
"""

from __future__ import annotations

import asyncio
import logging
import os
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
from telegram.error import BadRequest
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

        # Wire approval gate
        approval.configure(
            send_fn=self._send_approval_request,
            timeout=self.cfg.exec_confirmation_timeout_seconds,
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

        # Approval inline buttons
        app.add_handler(CallbackQueryHandler(self._handle_callback))

        # Text messages
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
        return update.effective_user and update.effective_user.id == self.cfg.telegram_owner_id

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            return
        await update.message.reply_text("PyGate is running. Send me a message.")

    async def _cmd_reset(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            return
        self.history.clear(str(update.effective_user.id))
        await update.message.reply_text("Conversation reset.")

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            return
        help_text = (
            "*PyGate — Commands*\n\n"
            "/reset — Clear conversation history\n"
            "/status — Show bot and agent status\n"
            "/help — This message\n\n"
            "Send any text, photo, voice, video, or document to chat with the agent."
        )
        await self._safe_send(update.effective_chat.id, help_text)

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            return
        from config import get_config
        cfg = get_config()
        tools = self.agent.registry.get_names()
        status = (
            f"*PyGate Status*\n\n"
            f"Model: `{cfg.llm_model}` ({cfg.llm_provider})\n"
            f"Tools ({len(tools)}): {', '.join(tools)}\n"
            f"Cron: {'✅' if cfg.cron_enabled else '❌'}\n"
            f"Browser: {'✅' if cfg.browser_enabled else '❌'}\n"
            f"Exec: {'✅' if cfg.exec_enabled else '❌'}\n"
            f"Memory: {'✅' if cfg.memory_enabled else '❌'}\n"
        )
        await self._safe_send(update.effective_chat.id, status)

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    async def _handle_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            return
        text = update.message.text or ""
        await self._run_agent(update, text)

    async def _handle_photo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
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
        if not self._is_owner(update):
            return
        voice = update.message.voice
        tmp_path = None
        try:
            tmp_path = await self._download_file(voice.file_id, suffix=".ogg")
            text = f"[Voice message received: {tmp_path} ({voice.duration}s). Transcription not available — reply to acknowledge or ask me to process it.]"
            await self._run_agent(update, text)
        finally:
            _cleanup_file(tmp_path)

    async def _handle_audio(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
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
        if not self._is_owner(update):
            return
        video = update.message.video
        caption = update.message.caption or ""
        tmp_path = None
        try:
            suffix = Path(video.file_name or "video.mp4").suffix or ".mp4"
            tmp_path = await self._download_file(video.file_id, suffix=suffix)
            text = f"[Video received: {video.file_name or 'video'} ({video.duration}s) at {tmp_path}]{(' — ' + caption) if caption else ''}"
            await self._run_agent(update, text)
        finally:
            _cleanup_file(tmp_path)

    async def _handle_document(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            return
        doc = update.message.document
        caption = update.message.caption or ""
        tmp_path = None
        try:
            suffix = Path(doc.file_name or "file").suffix or ""
            tmp_path = await self._download_file(doc.file_id, suffix=suffix)
            # For images sent as documents
            if doc.mime_type and doc.mime_type.startswith("image/"):
                text = f"[Image document: {doc.file_name} at {tmp_path}]{(' — ' + caption) if caption else ''}"
            else:
                text = f"[Document received: {doc.file_name} ({doc.mime_type}) at {tmp_path}]{(' — ' + caption) if caption else ''}"
            await self._run_agent(update, text)
        finally:
            _cleanup_file(tmp_path)

    async def _handle_sticker(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            return
        sticker = update.message.sticker
        emoji = sticker.emoji or ""
        text = f"[Sticker received: {emoji} (set: {sticker.set_name or 'unknown'}, file_id: {sticker.file_id})]"
        await self._run_agent(update, text)

    async def _handle_location(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            return
        loc = update.message.location
        text = f"[Location shared: lat={loc.latitude}, lon={loc.longitude}]"
        await self._run_agent(update, text)

    # ------------------------------------------------------------------
    # Core agent run
    # ------------------------------------------------------------------

    async def _run_agent(self, update: Update, user_text: str) -> None:
        chat_id = update.effective_chat.id
        user_id = str(update.effective_user.id)

        # Expose inbound message_id as fallback for react tool
        if update.effective_message:
            from tools.message_tool import set_current_message_id
            set_current_message_id(update.effective_message.message_id)

        # Add to history
        self.history.add(user_id, "user", user_text)

        # Build prompt and messages
        tool_names = self.agent.registry.get_names()
        system = self.build_prompt(self.cfg, tool_names)
        messages = self.history.get_for_llm(user_id)

        # Start typing indicator, refresh it while agent runs
        typing_task = asyncio.create_task(
            self._typing_loop(chat_id)
        )

        try:
            reply = await self.agent.run(messages, system, session_id="main")
        except Exception as e:
            logger.exception("Agent run failed")
            reply = f"Sorry, something went wrong: {e}"
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

        if reply:
            self.history.add(user_id, "assistant", reply)
            await self._send_chunked(chat_id, reply)

    async def _typing_loop(self, chat_id: int) -> None:
        """Send typing action every TYPING_REFRESH_INTERVAL seconds until cancelled."""
        try:
            while True:
                await self._bot.send_chat_action(chat_id, ChatAction.TYPING)
                await asyncio.sleep(TYPING_REFRESH_INTERVAL)
        except asyncio.CancelledError:
            pass
        except Exception:
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
                await query.edit_message_text(
                    f"{query.message.text}\n\n— {action}"
                )
            else:
                await query.edit_message_text("This request has already been resolved.")
        elif data.startswith("btn:"):
            # Agent-defined inline button callback — pass back to history
            payload = data[4:]
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
        return last_msg.message_id if last_msg else None

    async def send_audio(self, audio_path: str) -> None:
        """Send an audio file as a voice message. Called by tts tool."""
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
        msg = await self._safe_send(target, text, **kwargs)
        return msg.message_id if msg else None

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

    # ------------------------------------------------------------------
    # Safe send with ParseMode fallback + chunking
    # ------------------------------------------------------------------

    async def _safe_send(
        self,
        chat_id: int,
        text: str,
        reply_markup=None,
    ) -> list[Message]:
        """Send text with markdown, falling back to plain text on parse errors."""
        chunks = _split_message(text)
        sent = []
        for i, chunk in enumerate(chunks):
            markup = reply_markup if i == 0 else None
            try:
                msg = await self._bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=markup,
                )
                sent.append(msg)
            except BadRequest as e:
                if "can't parse" in str(e).lower() or "parse" in str(e).lower():
                    # ParseMode failure — retry as plain text
                    try:
                        msg = await self._bot.send_message(
                            chat_id=chat_id,
                            text=chunk,
                            reply_markup=markup,
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
    Each button must have {text, callback_data}. Optional style is stored in data prefix
    only if the callback_data doesn't already contain it.
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


def _cleanup_file(path: str | None) -> None:
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass
