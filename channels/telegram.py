from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
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
    from agent.prompt import build_system_prompt
    from tools.approval import ApprovalGate

logger = logging.getLogger(__name__)


class TelegramChannel:
    def __init__(
        self,
        agent: "AgentLoop",
        history: "HistoryManager",
        approval: "ApprovalGate",
        build_prompt,
    ) -> None:
        self.cfg = get_config()
        self.agent = agent
        self.history = history
        self.approval = approval
        self._build_prompt = build_prompt

        # One lock per user — prevents overlapping agent runs
        self._locks: dict[int, asyncio.Lock] = {}

        self.app = Application.builder().token(self.cfg.telegram_bot_token).build()

        # Wire approval gate to this channel's send function
        self.approval.configure(
            send_fn=self._send_approval_request,
            timeout=self.cfg.exec_confirmation_timeout_seconds,
        )

        # Register handlers
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("reset", self._cmd_reset))
        self.app.add_handler(CommandHandler("help", self._cmd_help))
        self.app.add_handler(CommandHandler("status", self._cmd_status))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))
        self.app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, self._handle_image))
        self.app.add_handler(CallbackQueryHandler(self._handle_callback))

    # ------------------------------------------------------------------
    # Auth guard
    # ------------------------------------------------------------------

    def _is_owner(self, user_id: int) -> bool:
        return user_id == self.cfg.telegram_owner_id

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update.effective_user.id):
            return
        await update.message.reply_text(
            "👋 Personal AI assistant ready.\n\n"
            "Commands:\n"
            "/reset — Clear conversation history\n"
            "/status — Show active cron jobs\n"
            "/help — Show this message"
        )

    async def _cmd_reset(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update.effective_user.id):
            return
        self.history.clear(update.effective_user.id)
        await update.message.reply_text("Conversation cleared ✅")

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update.effective_user.id):
            return
        await update.message.reply_text(
            "*Commands*\n"
            "/reset — Clear conversation history\n"
            "/status — Show active cron jobs and system status\n"
            "/help — Show this message\n\n"
            "Just send a message to chat with the AI assistant.",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update.effective_user.id):
            return
        # Let agent respond with status info
        await self._run_agent(update, "Show me the status of all scheduled cron jobs")

    # ------------------------------------------------------------------
    # Message handler
    # ------------------------------------------------------------------

    async def _handle_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update.effective_user.id):
            return
        await self._run_agent(update, update.message.text)

    async def _handle_image(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update.effective_user.id):
            return

        # Download and pass to agent as image analysis request
        if update.message.photo:
            photo = update.message.photo[-1]  # largest size
            file = await photo.get_file()
        elif update.message.document:
            file = await update.message.document.get_file()
        else:
            return

        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            await file.download_to_drive(f.name)
            tmp_path = f.name

        caption = update.message.caption or "Describe this image."
        prompt = f"[Image attached at {tmp_path}] {caption}"
        await self._run_agent(update, prompt)

    # ------------------------------------------------------------------
    # Core: run agent loop
    # ------------------------------------------------------------------

    async def _run_agent(self, update: Update, text: str) -> None:
        user_id = update.effective_user.id

        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()

        async with self._locks[user_id]:
            # Add user message to history
            self.history.add(user_id, "user", text)

            # Show typing indicator
            await update.effective_chat.send_action(ChatAction.TYPING)

            try:
                messages = self.history.get_for_llm(user_id)
                system = self._build_prompt(self.cfg, self.agent.registry.get_names())
                reply = await self.agent.run(messages, system)

                # Store assistant reply
                self.history.add(user_id, "assistant", reply)

                # Send reply (split if over Telegram's 4096 char limit)
                for chunk in _split_message(reply):
                    await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)

            except Exception as e:
                logger.exception("Agent error")
                await update.message.reply_text(f"⚠️ Error: {e}")

    # ------------------------------------------------------------------
    # Approval callbacks
    # ------------------------------------------------------------------

    async def _send_approval_request(self, text: str, request_id: str) -> None:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{request_id}"),
            InlineKeyboardButton("❌ Deny", callback_data=f"deny:{request_id}"),
        ]])
        await self.app.bot.send_message(
            chat_id=self.cfg.telegram_owner_id,
            text=text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()

        data = query.data or ""
        if not (data.startswith("approve:") or data.startswith("deny:")):
            return

        action, request_id = data.split(":", 1)
        approved = action == "approve"
        resolved = self.approval.resolve(request_id, approved)

        label = "✅ Approved" if approved else "❌ Denied"
        suffix = label if resolved else "⏰ Expired"
        try:
            await query.edit_message_text(
                f"{query.message.text}\n\n{suffix}",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Send helpers (used by tools and cron jobs)
    # ------------------------------------------------------------------

    async def send_message(self, text: str) -> None:
        for chunk in _split_message(text):
            await self.app.bot.send_message(
                chat_id=self.cfg.telegram_owner_id,
                text=chunk,
                parse_mode=ParseMode.MARKDOWN,
            )

    async def send_audio(self, file_path: str) -> None:
        import os
        with open(file_path, "rb") as f:
            await self.app.bot.send_voice(
                chat_id=self.cfg.telegram_owner_id,
                voice=f,
            )
        os.unlink(file_path)  # clean up temp file

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> None:
        logger.info("Starting Telegram bot (owner_id=%s)", self.cfg.telegram_owner_id)
        self.app.run_polling(drop_pending_updates=True)


def _split_message(text: str, limit: int = 4000) -> list[str]:
    """Split a long message into Telegram-safe chunks."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks
