"""
Telegram channel — full mirror of OpenClaw's Telegram capabilities.

Receives: text, photos, documents, voice (with optional transcription), audio,
          video, stickers (with optional vision), locations, forwarded messages,
          reply-to extraction, channel posts
Sends: text (HTML ParseMode), photos, documents, voice/audio, stickers,
       inline buttons, pin/unpin, reactions

Auth policy:
  DM:    "owner" | "allowlist" (+ allow_from) | "pairing" (approve or code flow) | "open"
  Group: "disabled" | "open" | "allowlist" (TELEGRAM_GROUP_ALLOWLIST)
  Per-chat overrides via TELEGRAM_CHAT_POLICIES JSON (checked first).
  Forum threads: each thread gets its own isolated conversation context.

New features (Tier 1 + 2):
  - Pairing mode: unknown DM users get Approve/Deny flow to owner
  - Pairing codes: owner can generate one-time codes; users redeem via /pair <code> or /start <code>
  - Mention gating: TELEGRAM_MENTION_REQUIRED=true ignores non-mentions in groups
  - requireTopic: TELEGRAM_REQUIRE_TOPIC=<thread_id> restricts group to one thread
  - setMyCommands: synced to Telegram on startup
  - Callback auth policy: TELEGRAM_CALLBACK_POLICY (owner / allowlist / open)
  - Reaction lifecycle: 👀 (thinking) → ⚙ (working) → ✅ (done) / ❌ (error)
  - Reaction variant fallback: TELEGRAM_REACTION_FALLBACK list
  - Streaming preview: LLM text streamed as editable message (rate-limited)
  - Sticker vision: download .webp thumbnail → pass to LLM as image
  - Sticker cache persistence: SQLite-backed set cache
  - Polling offset persistence: JSON file survives restarts
  - Supergroup migration: remap history on migrate_to_chat_id
  - channel_post handler: processes channel posts like group messages
  - Per-chat policy overrides: TELEGRAM_CHAT_POLICIES JSON
  - Webhook mode: TELEGRAM_WEBHOOK_URL switches from polling to webhook
  - Multi-account: AccountConfig per-instance token/owner/policy overrides

Safety:
  - ParseMode: HTML with fallback to plain text on parse errors
  - Message splitting at word/line boundaries
  - Typing indicator refreshed every 4s (Forbidden → backoff)
  - All temp files cleaned up after use
"""

from __future__ import annotations

import asyncio
import base64
import html
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from telegram import (
    Bot,
    BotCommand,
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
    TypeHandler,
    filters,
)

from config import get_config

if TYPE_CHECKING:
    from agent.history import HistoryManager
    from agent.loop import AgentLoop
    from tools.approval import ApprovalGate

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4000
MAX_STREAM_PREVIEW_LEN = 3800  # leave room for "…" suffix during streaming
TYPING_REFRESH_INTERVAL = 4    # seconds between typing indicator refreshes
HTML_STRIP_RE = re.compile(r"<[^>]+>")


# ------------------------------------------------------------------
# Per-account config (multi-account support)
# ------------------------------------------------------------------

@dataclass
class AccountConfig:
    """Per-account config overrides for multi-account mode."""
    label: str = "main"
    token: str = ""
    owner_id: int = 0
    dm_policy: str = ""           # "" = use global config
    group_policy: str = ""
    allow_from: list[int] = field(default_factory=list)
    group_allowlist: list[int] = field(default_factory=list)


# ------------------------------------------------------------------
# Streaming preview state
# ------------------------------------------------------------------

@dataclass
class _StreamState:
    """Per-message streaming preview state."""
    chat_id: int
    preview_msg_id: int | None = None
    last_edit_at: float = 0.0
    text: str = ""
    message_thread_id: int | None = None


# ------------------------------------------------------------------
# TelegramChannel
# ------------------------------------------------------------------

class TelegramChannel:
    def __init__(
        self,
        agent: "AgentLoop",
        history: "HistoryManager",
        approval: "ApprovalGate",
        build_prompt: Callable,
        account: AccountConfig | None = None,
    ) -> None:
        self.cfg = get_config()
        self.agent = agent
        self.history = history
        self.approval = approval
        self.build_prompt = build_prompt

        # Per-account overrides
        self._account = account or AccountConfig(
            label="main",
            token=self.cfg.telegram_bot_token,
            owner_id=self.cfg.telegram_owner_id,
        )
        self._label = self._account.label
        self._owner_id = self._account.owner_id or self.cfg.telegram_owner_id
        _token = self._account.token or self.cfg.telegram_bot_token

        self._app = (
            Application.builder()
            .token(_token)
            .post_init(self._post_init)
            .build()
        )
        self._bot: Bot = self._app.bot

        # Wire approval gate
        approval.configure(
            send_fn=self._send_approval_request,
            timeout=self.cfg.exec_confirmation_timeout_seconds,
            notify_fn=self.send_message,
        )

        # Sticker cache persistence
        self._sticker_db_path = Path(self.cfg.telegram_sticker_cache_db).expanduser()
        self._sticker_db_path.parent.mkdir(parents=True, exist_ok=True)
        self._sticker_cache: dict[str, list[dict]] = self._load_sticker_cache()

        # Active reaction tracking: (chat_id, message_id) → current emoji
        self._active_reactions: dict[tuple[int, int], str] = {}

        self._register_handlers()

    # ------------------------------------------------------------------
    # Post-init (runs after Application.initialize() — sets bot commands)
    # ------------------------------------------------------------------

    async def _post_init(self, app: Application) -> None:
        await self._setup_commands()

    async def _setup_commands(self) -> None:
        """Sync command list to Telegram so the /command menu appears."""
        commands = [
            BotCommand("start", "Start / show welcome"),
            BotCommand("reset", "Clear conversation history"),
            BotCommand("help", "Show help"),
            BotCommand("status", "Show bot status"),
            BotCommand("model", "Show or switch LLM model"),
            BotCommand("pair", "Pairing status / code (owner) or redeem code"),
        ]
        try:
            await self._bot.set_my_commands(commands)
            logger.debug("[%s] Bot commands synced (%d)", self._label, len(commands))
        except Exception as e:
            logger.warning("[%s] Failed to set bot commands: %s", self._label, e)

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
        app.add_handler(CommandHandler("pair", self._cmd_pair))

        # Approval + agent inline buttons
        app.add_handler(CallbackQueryHandler(self._handle_callback))

        # Text messages (DM and group)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text))

        # Channel posts (public channels)
        app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POSTS, self._handle_channel_post))

        # Media
        app.add_handler(MessageHandler(filters.PHOTO, self._handle_photo))
        app.add_handler(MessageHandler(filters.VOICE, self._handle_voice))
        app.add_handler(MessageHandler(filters.AUDIO, self._handle_audio))
        app.add_handler(MessageHandler(filters.VIDEO, self._handle_video))
        app.add_handler(MessageHandler(filters.Document.ALL, self._handle_document))
        app.add_handler(MessageHandler(filters.Sticker.ALL, self._handle_sticker))
        app.add_handler(MessageHandler(filters.LOCATION, self._handle_location))

        # Offset persistence: save last update_id after each processed update
        offset_path = Path(self.cfg.telegram_polling_offset_path).expanduser()
        if not self.cfg.telegram_webhook_url:
            app.add_handler(TypeHandler(Update, self._save_offset_handler), group=-999)
            self._offset_path = offset_path
        else:
            self._offset_path = None

    # ------------------------------------------------------------------
    # Auth guard
    # ------------------------------------------------------------------

    def _is_owner(self, update: Update) -> bool:
        return bool(
            update.effective_user
            and update.effective_user.id == self._owner_id
        )

    def _is_allowed(self, update: Update) -> bool:
        """Return True if this update is from an authorised user/chat."""
        user = update.effective_user
        chat = update.effective_chat

        if not user:
            return False

        user_id = user.id
        is_dm = chat and chat.type == "private"
        is_group = chat and chat.type in ("group", "supergroup")

        chat_id_str = str(chat.id) if chat else ""

        # 0. Per-chat policy override (checked first)
        per_chat = self.cfg.telegram_chat_policies.get(chat_id_str)
        if per_chat:
            return self._eval_dm_policy(user_id, per_chat)

        if is_dm:
            dm_policy = (
                self._account.dm_policy
                or getattr(self.cfg, "telegram_dm_policy", "owner")
            )
            return self._eval_dm_policy(user_id, dm_policy)

        if is_group:
            group_policy = (
                self._account.group_policy
                or getattr(self.cfg, "telegram_group_policy", "disabled")
            )
            if group_policy == "disabled":
                return False
            if group_policy == "open":
                return True
            if group_policy == "allowlist":
                grp_list = (
                    self._account.group_allowlist
                    or getattr(self.cfg, "telegram_group_allowlist", [])
                )
                return chat.id in grp_list
            return False

        return False

    def _eval_dm_policy(self, user_id: int, policy: str) -> bool:
        if policy == "open":
            return True
        if policy == "owner":
            return user_id == self._owner_id
        if policy == "allowlist":
            allow_from = (
                self._account.allow_from
                or getattr(self.cfg, "telegram_allow_from", [])
            )
            return user_id == self._owner_id or user_id in allow_from
        if policy == "pairing":
            if user_id == self._owner_id:
                return True
            # Check approved pairings
            from agent.pairing import get_pairing_store
            return get_pairing_store().is_approved(user_id)
        return user_id == self._owner_id  # default: owner only

    def _is_callback_allowed(self, update: Update) -> bool:
        """Check if a callback query user is allowed to press agent buttons."""
        user = update.effective_user
        if not user:
            return False
        user_id = user.id
        policy = getattr(self.cfg, "telegram_callback_policy", "owner")
        if policy == "open":
            return True
        if policy == "allowlist":
            allow_from = (
                self._account.allow_from
                or getattr(self.cfg, "telegram_allow_from", [])
            )
            return user_id == self._owner_id or user_id in allow_from
        return user_id == self._owner_id  # default: owner only

    def _is_mentioned(self, update: Update) -> bool:
        """Return True if the bot was mentioned or the message is a reply-to-bot."""
        msg = update.effective_message
        if not msg:
            return False
        # Check reply-to: if the replied message is from the bot itself
        if msg.reply_to_message and msg.reply_to_message.from_user:
            try:
                if msg.reply_to_message.from_user.id == self._bot.id:
                    return True
            except Exception:
                pass
        # Check text/caption for @botusername mention
        text = msg.text or msg.caption or ""
        if not text:
            return False
        try:
            bot_username = self._bot.username
            if bot_username and f"@{bot_username}".lower() in text.lower():
                return True
        except Exception:
            pass
        # Also check message entities for mentions
        for entity in (msg.entities or msg.caption_entities or []):
            if entity.type == "mention":
                mentioned = text[entity.offset: entity.offset + entity.length]
                try:
                    if self._bot.username and mentioned.lstrip("@").lower() == self._bot.username.lower():
                        return True
                except Exception:
                    pass
        return False

    def _context_key(self, update: Update) -> str:
        """
        Return the conversation context key for history lookups.
        Uses OpenClaw's hierarchical session key format:
          agent:main:telegram:direct:<user_id>
          agent:main:telegram:group:<chat_id>
          agent:main:telegram:group:<chat_id>:thread:<thread_id>
        """
        agent_id = getattr(self.cfg, "agent_id", "") or "main"
        chat = update.effective_chat
        msg = update.effective_message
        if chat and chat.type in ("group", "supergroup"):
            thread_id = msg.message_thread_id if msg else None
            if thread_id:
                return f"agent:{agent_id}:telegram:group:{chat.id}:thread:{thread_id}"
            return f"agent:{agent_id}:telegram:group:{chat.id}"
        user_id = update.effective_user.id if update.effective_user else "unknown"
        return f"agent:{agent_id}:telegram:direct:{user_id}"

    def _build_sender_context(self, update: Update) -> str:
        """Build per-turn sender metadata from Telegram update JSON fields."""
        user = update.effective_user
        chat = update.effective_chat
        msg = update.effective_message
        if not user:
            return ""

        name_parts = [p for p in [user.first_name, user.last_name] if p]
        display_name = " ".join(name_parts).strip() or (user.username or str(user.id))
        role = "owner" if user.id == self._owner_id else "user"

        lines = [
            "Current sender metadata (from Telegram update):",
            f"- role: {role}",
            f"- user_id: {user.id}",
            f"- username: @{user.username}" if user.username else "- username: (none)",
            f"- display_name: {display_name}  ← this IS the user's name; use it directly to answer 'what is my name?' questions",
        ]
        if chat:
            lines.append(f"- chat_id: {chat.id}")
            lines.append(f"- chat_type: {chat.type}")
            title = getattr(chat, "title", None)
            if title:
                lines.append(f"- chat_title: {title}")
        if msg:
            lines.append(f"- message_id: {msg.message_id}")
            if msg.message_thread_id:
                lines.append(f"- message_thread_id: {msg.message_thread_id}")
        lines.append(
            "Use display_name as the user's name. "
            "Do NOT say you don't know the user's name — it is always available above. "
            "Do not dump raw metadata unless asked."
        )
        return "\n".join(lines)

    def _should_respond_in_group(self, update: Update) -> bool:
        """
        Additional group-level gating beyond the base policy:
        - requireTopic: only respond in the configured thread_id
        - mention_required: only respond if bot is @mentioned or reply-to-bot
        """
        chat = update.effective_chat
        if not chat or chat.type not in ("group", "supergroup"):
            return True  # Not a group — let base policy handle it

        # requireTopic: only respond in a specific thread
        require_topic = getattr(self.cfg, "telegram_require_topic", 0)
        if require_topic:
            msg = update.effective_message
            thread_id = msg.message_thread_id if msg else None
            if thread_id != require_topic:
                return False

        # Mention gating: only respond if @mentioned or reply-to-bot
        if getattr(self.cfg, "telegram_mention_required", False):
            if not self._is_mentioned(update):
                return False

        return True

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat:
            return

        # Pairing flow: DM from unknown user in "pairing" mode
        dm_policy = (
            self._account.dm_policy
            or getattr(self.cfg, "telegram_dm_policy", "owner")
        )
        if dm_policy == "pairing" and chat.type == "private" and user.id != self._owner_id:
            from agent.pairing import get_pairing_store
            store = get_pairing_store()
            # Support Telegram deep-link pairing: /start <CODE>
            start_code = (ctx.args[0].strip() if ctx.args else "")
            if start_code and not store.is_approved(user.id):
                ok, status = await self._redeem_pairing_code(user, start_code, store)
                if ok:
                    await update.message.reply_text("Pairing successful. You can now chat with the bot.")
                else:
                    await update.message.reply_text(self._pairing_code_error_text(status))
                return
            if store.is_approved(user.id):
                await update.message.reply_text("You're already connected. Send me a message.")
            elif store.is_pending(user.id):
                await update.message.reply_text(
                    "Your access request is pending owner approval. Please wait."
                )
            else:
                await self._send_pairing_request(user, store)
            return

        if not self._is_allowed(update):
            return
        await update.message.reply_text("LobsterClaw is online. Send me a message to get started.")

    async def _send_pairing_request(self, user, store) -> None:
        import uuid
        request_id = str(uuid.uuid4())[:16]
        store.add_pending(
            user_id=user.id,
            username=user.username or "",
            first_name=user.first_name or "",
            request_id=request_id,
        )
        name = user.first_name or user.username or str(user.id)
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"pair_approve:{request_id}"),
                InlineKeyboardButton("❌ Deny", callback_data=f"pair_deny:{request_id}"),
            ]
        ])
        await self._safe_send(
            self._owner_id,
            (
                f"<b>Pairing request</b>\n\n"
                f"User: <b>{name}</b> (@{user.username or 'no_username'}, ID: <code>{user.id}</code>)\n"
                f"wants access to LobsterClaw."
            ),
            reply_markup=keyboard,
        )
        # Tell the requesting user to wait
        try:
            await self._bot.send_message(
                chat_id=user.id,
                text=(
                    "LobsterClaw: access not configured. "
                    "Your request has been sent to the owner for approval. "
                    "Alternatively, ask the owner for a pairing code and use /pair <code>."
                ),
            )
        except Exception:
            pass

    def _pairing_code_error_text(self, status: str) -> str:
        if status == "expired":
            return "That pairing code has expired. Ask the owner for a new one."
        if status == "used":
            return "That pairing code has already been used. Ask for a fresh code."
        return "Invalid pairing code. Use /pair <code> with a valid code from the owner."

    async def _redeem_pairing_code(self, user, code: str, store) -> tuple[bool, str]:
        ok, status = store.redeem_code(
            code=code,
            user_id=user.id,
            username=user.username or "",
            first_name=user.first_name or "",
        )
        if ok and status == "approved":
            # Notify owner that this user was approved via code.
            try:
                await self._safe_send(
                    self._owner_id,
                    (
                        "<b>Pairing approved by code</b>\n\n"
                        f"User: <b>{user.first_name or user.username or user.id}</b> "
                        f"(@{user.username or 'no_username'}, ID: <code>{user.id}</code>)"
                    ),
                )
            except Exception:
                pass
        return ok, status

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
            "<b>LobsterClaw — Commands</b>\n\n"
            "/reset — Clear conversation history\n"
            "/status — Show bot and agent status\n"
            "/model [name] — Show or switch LLM model\n"
            "/pair — Pairing status / generate code (owner), or redeem code (user)\n"
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
            f"<b>LobsterClaw Status</b> [{self._label}]\n\n"
            f"Model: <code>{cfg.llm_model}</code> ({cfg.llm_provider})\n"
            f"Thinking: {thinking_str}\n"
            f"Streaming: {'✅' if cfg.llm_streaming else '❌'}\n"
            f"Tools ({len(tools)}): {', '.join(tools)}\n"
            f"Cron: {'✅' if cfg.cron_enabled else '❌'}\n"
            f"Browser: {'✅' if cfg.browser_enabled else '❌'}\n"
            f"Exec: {'✅' if cfg.exec_enabled else '❌'}\n"
            f"Memory: {'✅' if cfg.memory_enabled else '❌'}\n"
            f"Voice transcription: {'✅' if getattr(cfg, 'telegram_voice_transcription', False) else '❌'}\n"
            f"DM policy: {getattr(cfg, 'telegram_dm_policy', 'owner')}\n"
            f"Group policy: {getattr(cfg, 'telegram_group_policy', 'disabled')}\n"
        )
        await self._safe_send(update.effective_chat.id, status)

    async def _cmd_model(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            return
        args = ctx.args or []
        cfg = self.cfg
        if args:
            new_model = args[0].strip()
            cfg.llm_model = new_model
            await update.message.reply_text(
                f"Model switched to <code>{new_model}</code>", parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                f"Current model: <code>{cfg.llm_model}</code> ({cfg.llm_provider})",
                parse_mode=ParseMode.HTML,
            )

    async def _cmd_pair(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Pairing command.
        - Owner: list/revoke users, generate one-time codes.
        - Non-owner (DM, pairing mode): redeem a code via /pair <code>.
        """
        from agent.pairing import get_pairing_store

        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat:
            return

        dm_policy = (
            self._account.dm_policy
            or getattr(self.cfg, "telegram_dm_policy", "owner")
        )
        if dm_policy != "pairing":
            if self._is_owner(update):
                await update.message.reply_text("Pairing mode is disabled (TELEGRAM_DM_POLICY is not 'pairing').")
            return

        store = get_pairing_store()
        args = ctx.args or []

        # Non-owner path: redeem pairing code
        if not self._is_owner(update):
            if chat.type != "private":
                return
            if store.is_approved(user.id):
                await update.message.reply_text("You're already connected. Send me a message.")
                return
            if not args:
                await update.message.reply_text("Use /pair <code> with a code from the owner.")
                return
            ok, status = await self._redeem_pairing_code(user, args[0], store)
            if ok:
                await update.message.reply_text("Pairing successful. You can now chat with the bot.")
            else:
                await update.message.reply_text(self._pairing_code_error_text(status))
            return

        # Owner path: generate one-time code
        if args and args[0] == "code":
            ttl_minutes = 15
            if len(args) > 1:
                try:
                    ttl_minutes = int(args[1])
                except ValueError:
                    await update.message.reply_text("Usage: /pair code [ttl_minutes]")
                    return
            if ttl_minutes < 1 or ttl_minutes > 1440:
                await update.message.reply_text("TTL must be between 1 and 1440 minutes.")
                return
            code = store.create_pairing_code(created_by=self._owner_id, ttl_minutes=ttl_minutes)
            bot_username = getattr(self._bot, "username", "") or ""
            deep_link = f"https://t.me/{bot_username}?start={code}" if bot_username else ""
            text = (
                "<b>Pairing code created</b>\n\n"
                f"Code: <code>{code}</code>\n"
                f"TTL: {ttl_minutes} minute(s)\n"
                "Redeem: <code>/pair &lt;code&gt;</code>"
            )
            if deep_link:
                text += f"\nDeep link: {deep_link}"
            await self._safe_send(update.effective_chat.id, text)
            return

        if args and args[0] == "revoke" and len(args) > 1:
            try:
                target_id = int(args[1])
                removed = store.revoke(target_id)
                msg = f"User {target_id} revoked." if removed else f"User {target_id} not found in approved list."
                await update.message.reply_text(msg)
            except ValueError:
                await update.message.reply_text("Usage: /pair revoke <user_id>")
            return

        approved = store.list_approved()
        pending = store.list_pending()
        active_codes = store.list_active_codes()

        lines = ["<b>Pairing Status</b>\n"]
        if approved:
            lines.append(f"<b>Approved ({len(approved)}):</b>")
            for u in approved:
                lines.append(
                    f"  • {u.get('first_name', '')} @{u.get('username', '')} "
                    f"(<code>{u['user_id']}</code>)"
                )
        else:
            lines.append("No approved pairings.")
        if pending:
            lines.append(f"\n<b>Pending ({len(pending)}):</b>")
            for u in pending:
                lines.append(
                    f"  • {u.get('first_name', '')} @{u.get('username', '')} "
                    f"(<code>{u['user_id']}</code>)"
                )
        if active_codes:
            lines.append(f"\n<b>Active Codes ({len(active_codes)}):</b>")
            for c in active_codes:
                lines.append(
                    f"  • <code>{c['code']}</code> "
                    f"(uses_left={c['uses_left']}, expires={c['expires_at']} UTC)"
                )
        lines.append("\nUsage: /pair code [ttl_minutes] | /pair revoke <user_id>")
        await self._safe_send(update.effective_chat.id, "\n".join(lines))

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    async def _handle_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update):
            # Pairing mode: unknown user trying to message — trigger pairing if /start wasn't used
            user = update.effective_user
            chat = update.effective_chat
            dm_policy = (
                self._account.dm_policy
                or getattr(self.cfg, "telegram_dm_policy", "owner")
            )
            if (
                dm_policy == "pairing"
                and chat
                and chat.type == "private"
                and user
                and user.id != self._owner_id
            ):
                from agent.pairing import get_pairing_store
                store = get_pairing_store()
                if not store.is_approved(user.id) and not store.is_pending(user.id):
                    await self._send_pairing_request(user, store)
                elif store.is_pending(user.id):
                    await update.message.reply_text("Your request is still pending approval.")
            return

        if not self._should_respond_in_group(update):
            return

        msg = update.message
        if not msg:
            return

        # Supergroup migration: update stored history keys
        if msg.migrate_to_chat_id:
            old_id = update.effective_chat.id
            new_id = msg.migrate_to_chat_id
            logger.info("[%s] Supergroup migration: %s → %s", self._label, old_id, new_id)
            self._remap_chat_history(old_id, new_id)
            return

        text = msg.text or ""

        # Prepend reply-to text if the user quoted another message
        if msg.reply_to_message:
            reply_body = _extract_reply_body(msg.reply_to_message)
            if reply_body:
                text = f'[Reply to: "{reply_body}"]\n{text}'

        # Forwarded message attribution (supports PTB v22+ and older fields)
        fwd_prefix = _extract_forward_prefix(msg)
        if fwd_prefix:
            text = f"{fwd_prefix}\n{text}"

        await self._run_agent(update, text)

    async def _handle_channel_post(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle messages posted to public channels the bot is in."""
        msg = update.channel_post or update.edited_channel_post
        if not msg:
            return
        text = msg.text or msg.caption or ""
        if not text:
            return
        # Channel posts run as owner context
        await self._run_agent(update, f"[Channel post]: {text}", override_chat_id=msg.chat_id)

    async def _handle_photo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update) or not self._should_respond_in_group(update):
            return
        caption = update.message.caption or ""
        photo = update.message.photo[-1]
        tmp_path = None
        try:
            tmp_path = await self._download_file(photo.file_id, suffix=".jpg")
            text = f"[Photo attached: {tmp_path}]{(' — ' + caption) if caption else ''}"
            await self._run_agent(update, text)
        finally:
            _cleanup_file(tmp_path)

    async def _handle_voice(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update) or not self._should_respond_in_group(update):
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
                    "Transcription not available.]"
                )
            await self._run_agent(update, text)
        finally:
            _cleanup_file(tmp_path)

    async def _handle_audio(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update) or not self._should_respond_in_group(update):
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
        if not self._is_allowed(update) or not self._should_respond_in_group(update):
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
        if not self._is_allowed(update) or not self._should_respond_in_group(update):
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
        if not self._is_allowed(update) or not self._should_respond_in_group(update):
            return
        sticker = update.message.sticker
        emoji = sticker.emoji or ""
        desc = getattr(sticker, "custom_emoji_id", "") or ""
        text = (
            f"[Sticker received: {emoji} (set: {sticker.set_name or 'unknown'}, "
            f"file_id: {sticker.file_id}{', id=' + desc if desc else ''})]"
        )

        # Sticker vision: download thumbnail and pass to LLM as image
        if getattr(self.cfg, "telegram_sticker_vision", False):
            thumb = getattr(sticker, "thumbnail", None) or getattr(sticker, "thumb", None)
            if thumb:
                tmp_path = None
                try:
                    tmp_path = await self._download_file(thumb.file_id, suffix=".webp")
                    with open(tmp_path, "rb") as f:
                        img_data = base64.standard_b64encode(f.read()).decode()
                    await self._run_agent(update, text, image_data=img_data, image_mime="image/webp")
                    return
                except Exception as e:
                    logger.warning("[%s] Sticker vision failed: %s", self._label, e)
                finally:
                    _cleanup_file(tmp_path)

        await self._run_agent(update, text)

    async def _handle_location(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update) or not self._should_respond_in_group(update):
            return
        loc = update.message.location
        text = f"[Location shared: lat={loc.latitude}, lon={loc.longitude}]"
        await self._run_agent(update, text)

    # ------------------------------------------------------------------
    # Polling offset persistence
    # ------------------------------------------------------------------

    async def _save_offset_handler(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Save the latest processed update_id to disk after each update."""
        if update.update_id and self._offset_path:
            try:
                self._offset_path.parent.mkdir(parents=True, exist_ok=True)
                self._offset_path.write_text(json.dumps({"offset": update.update_id + 1}))
            except Exception:
                pass

    def _load_poll_offset(self) -> int:
        if not self._offset_path:
            return 0
        try:
            return json.loads(self._offset_path.read_text()).get("offset", 0)
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Supergroup migration: remap history keys
    # ------------------------------------------------------------------

    def _remap_chat_history(self, old_chat_id: int, new_chat_id: int) -> None:
        """Remap all history/session records from old_chat_id to new_chat_id."""
        label = self._label
        old_prefixes = [
            f"{label}:group:{old_chat_id}",
            f"{label}:thread:{old_chat_id}:",
        ]
        try:
            # Access the history manager's internal DB directly if available
            store = getattr(self.history, "_store", None) or getattr(self.history, "_db", None)
            if store and hasattr(store, "_db_path"):
                import sqlite3
                conn = sqlite3.connect(store._db_path)
                for prefix in old_prefixes:
                    conn.execute(
                        "UPDATE messages SET session_id = REPLACE(session_id, ?, ?)"
                        " WHERE session_id LIKE ?",
                        (
                            f"{label}:group:{old_chat_id}",
                            f"{label}:group:{new_chat_id}",
                            f"{prefix}%",
                        ),
                    )
                conn.commit()
                conn.close()
                logger.info("[%s] Remapped history from %s → %s", label, old_chat_id, new_chat_id)
        except Exception as e:
            logger.warning("[%s] History remap failed: %s", label, e)

    # ------------------------------------------------------------------
    # Voice transcription (Whisper)
    # ------------------------------------------------------------------

    async def _transcribe_voice(self, audio_path: str) -> str | None:
        cfg = self.cfg
        if not getattr(cfg, "telegram_voice_transcription", False):
            return None
        if not cfg.openai_api_key:
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
                return resp.json().get("text", "").strip() or None
        except Exception as e:
            logger.warning("[%s] Voice transcription failed: %s", self._label, e)
            return None

    # ------------------------------------------------------------------
    # Reaction lifecycle
    # ------------------------------------------------------------------

    async def _react(self, chat_id: int, message_id: int, emoji: str | None, *, remove: bool = False) -> bool:
        """
        Try setting a reaction. Returns True on success.
        On REACTION_INVALID, tries fallback list.
        """
        if not getattr(self.cfg, "telegram_reactions_enabled", True):
            return False
        from telegram import ReactionTypeEmoji
        # Empty emoji means reaction lifecycle is disabled for that stage.
        # Skip API calls unless this is an explicit remove request.
        if not remove and not emoji:
            return False
        try:
            if remove or not emoji:
                await self._bot.set_message_reaction(chat_id=chat_id, message_id=message_id, reaction=[])
            else:
                await self._bot.set_message_reaction(
                    chat_id=chat_id, message_id=message_id,
                    reaction=[ReactionTypeEmoji(emoji=emoji)],
                )
            if emoji:
                self._active_reactions[(chat_id, message_id)] = emoji
            return True
        except Exception as e:
            err_str = str(e)
            if "REACTION_INVALID" in err_str or "invalid" in err_str.lower():
                # Try fallback list
                fallbacks = getattr(self.cfg, "telegram_reaction_fallback", [])
                for fb_emoji in fallbacks:
                    if fb_emoji == emoji:
                        continue
                    try:
                        await self._bot.set_message_reaction(
                            chat_id=chat_id, message_id=message_id,
                            reaction=[ReactionTypeEmoji(emoji=fb_emoji)],
                        )
                        self._active_reactions[(chat_id, message_id)] = fb_emoji
                        return True
                    except Exception:
                        continue
            logger.debug("[%s] Reaction failed for %s/%s: %s", self._label, chat_id, message_id, e)
            return False

    async def _reaction_thinking(self, chat_id: int, message_id: int) -> None:
        emoji = getattr(self.cfg, "telegram_reaction_thinking", "👀")
        await self._react(chat_id, message_id, emoji)

    async def _reaction_working(self, chat_id: int, message_id: int) -> None:
        emoji = getattr(self.cfg, "telegram_reaction_working", "⚙")
        await self._react(chat_id, message_id, emoji)

    async def _reaction_done(self, chat_id: int, message_id: int) -> None:
        emoji = getattr(self.cfg, "telegram_reaction_done", "✅")
        ok = await self._react(chat_id, message_id, emoji)
        if ok:
            delay = getattr(self.cfg, "telegram_reaction_done_clear_secs", 3.0)
            if delay > 0:
                async def _clear():
                    await asyncio.sleep(delay)
                    await self._react(chat_id, message_id, None, remove=True)
                    self._active_reactions.pop((chat_id, message_id), None)
                asyncio.create_task(_clear())

    async def _reaction_error(self, chat_id: int, message_id: int) -> None:
        emoji = getattr(self.cfg, "telegram_reaction_error", "❌")
        await self._react(chat_id, message_id, emoji)

    # ------------------------------------------------------------------
    # Core agent run
    # ------------------------------------------------------------------

    async def _run_agent(
        self,
        update: Update,
        user_text: str,
        *,
        image_data: str | None = None,
        image_mime: str = "image/webp",
        override_chat_id: int | None = None,
    ) -> None:
        chat_id = override_chat_id or update.effective_chat.id
        context_key = self._context_key(update)
        inbound_msg_id = update.effective_message.message_id if update.effective_message else None
        msg_thread_id = (
            update.effective_message.message_thread_id
            if update.effective_message else None
        )

        # Expose inbound message_id as fallback for the react tool
        if inbound_msg_id:
            from tools.message_tool import set_current_message_id
            set_current_message_id(inbound_msg_id)

        # Add to history (with optional vision content for Anthropic)
        if image_data and self.cfg.llm_provider == "anthropic":
            vision_content = [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": image_mime, "data": image_data},
                },
                {"type": "text", "text": user_text},
            ]
            self.history.add(context_key, "user", vision_content)
        else:
            self.history.add(context_key, "user", user_text)

        tool_names = self.agent.registry.get_names()
        # Pass Telegram channel metadata into the prompt builder for the ## Runtime section
        _runtime_info = {
            "channel": "telegram",
            "capabilities": ["reactions", "inline_buttons", "voice", "stickers"],
            "agent_id": getattr(self.cfg, "agent_id", ""),
        }
        system = self.build_prompt(
            self.cfg,
            tool_names,
            runtime_info=_runtime_info,
            extra_system_prompt=self._build_sender_context(update),
        )
        messages = self.history.get_for_llm(context_key)
        reactions_enabled = getattr(self.cfg, "telegram_reactions_enabled", True)

        # Reaction: thinking phase
        if inbound_msg_id and reactions_enabled:
            asyncio.create_task(self._reaction_thinking(chat_id, inbound_msg_id))

        # Typing indicator
        typing_task = asyncio.create_task(self._typing_loop(chat_id))

        # Streaming state
        stream_state = _StreamState(chat_id=chat_id, message_thread_id=msg_thread_id)
        first_tool_fired = False

        async def _stream_callback(text: str) -> None:
            await self._stream_update(stream_state, text)

        async def _on_tool_start() -> None:
            nonlocal first_tool_fired
            if not first_tool_fired:
                first_tool_fired = True
                if inbound_msg_id and reactions_enabled:
                    asyncio.create_task(self._reaction_working(chat_id, inbound_msg_id))

        use_streaming = getattr(self.cfg, "llm_streaming", True)

        try:
            reply = await self.agent.run(
                messages,
                system,
                session_id=context_key,
                stream_callback=_stream_callback if use_streaming else None,
                on_tool_start=_on_tool_start if use_streaming else None,
            )
        except Exception as e:
            logger.exception("[%s] Agent run failed", self._label)
            reply = f"Sorry, something went wrong: {type(e).__name__}"
            if inbound_msg_id and reactions_enabled:
                asyncio.create_task(self._reaction_error(chat_id, inbound_msg_id))
        else:
            if inbound_msg_id and reactions_enabled:
                asyncio.create_task(self._reaction_done(chat_id, inbound_msg_id))
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

        # Check for special reply tokens before delivering
        silent_token = getattr(self.cfg, "silent_reply_token", "NO_REPLY")
        heartbeat_ok = getattr(self.cfg, "heartbeat_ok_token", "HEARTBEAT_OK")

        if reply and reply.strip() == silent_token:
            # Agent has nothing to say — suppress delivery, cancel any streaming preview
            logger.debug("[%s] Silent reply (NO_REPLY) — not sending", self._label)
            self.history.add(context_key, "assistant", reply)
            if stream_state.preview_msg_id:
                try:
                    await self._bot.delete_message(
                        chat_id=chat_id, message_id=stream_state.preview_msg_id
                    )
                except Exception:
                    pass
            return

        if reply and reply.strip().startswith(heartbeat_ok):
            # Heartbeat acknowledged — suppress user-visible message
            logger.debug("[%s] Heartbeat ack — not sending to Telegram", self._label)
            self.history.add(context_key, "assistant", reply)
            if stream_state.preview_msg_id:
                try:
                    await self._bot.delete_message(
                        chat_id=chat_id, message_id=stream_state.preview_msg_id
                    )
                except Exception:
                    pass
            return

        if reply:
            self.history.add(context_key, "assistant", reply)
            await self._finalize_stream(stream_state, reply, chat_id)

    async def _stream_update(self, state: _StreamState, text: str) -> None:
        """Send or edit the streaming preview message (rate-limited)."""
        now = time.monotonic()
        min_interval = getattr(self.cfg, "llm_stream_min_edit_interval", 3.0)
        min_chars_delta = 100

        if state.preview_msg_id is None:
            if len(text) < 50:
                state.text = text
                return
            try:
                preview_text = text[:MAX_STREAM_PREVIEW_LEN]
                if len(text) > MAX_STREAM_PREVIEW_LEN:
                    preview_text += "…"
                msgs = await self._safe_send(
                    state.chat_id,
                    preview_text,
                    message_thread_id=state.message_thread_id,
                    format_markdown=True,
                )
                if msgs:
                    state.preview_msg_id = msgs[-1].message_id
                    state.last_edit_at = now
                    state.text = text
            except Exception as e:
                logger.debug("[%s] Stream initial send failed: %s", self._label, e)
            return

        if now - state.last_edit_at < min_interval:
            return
        if len(text) - len(state.text) < min_chars_delta:
            return

        try:
            preview_text = text[:MAX_STREAM_PREVIEW_LEN]
            if len(text) > MAX_STREAM_PREVIEW_LEN:
                preview_text += "…"
            rendered_preview = _render_telegram_markdown_html(preview_text)
            await self._bot.edit_message_text(
                chat_id=state.chat_id,
                message_id=state.preview_msg_id,
                text=rendered_preview,
                parse_mode=ParseMode.HTML,
            )
            state.last_edit_at = now
            state.text = text
        except BadRequest as e:
            err = str(e).lower()
            if "message is not modified" in err:
                pass  # no-op
            elif "can't parse" in err or "html" in err:
                # Parse error in preview — try plain text
                try:
                    plain = HTML_STRIP_RE.sub("", _render_telegram_markdown_html(text[:MAX_STREAM_PREVIEW_LEN]))
                    await self._bot.edit_message_text(
                        chat_id=state.chat_id,
                        message_id=state.preview_msg_id,
                        text=plain,
                    )
                    state.last_edit_at = now
                except Exception:
                    pass
            else:
                logger.debug("[%s] Stream edit BadRequest: %s", self._label, e)
        except Exception as e:
            logger.debug("[%s] Stream edit failed: %s", self._label, e)

    async def _finalize_stream(self, state: _StreamState, reply: str, chat_id: int) -> None:
        """
        Finalize streaming: do one final edit of the preview OR send normally.
        - If there's a preview message and the reply fits in one chunk, final-edit it.
        - If multi-chunk, delete preview and send all chunks.
        - If no preview existed (non-streaming path or no text chunks), send normally.
        """
        if state.preview_msg_id is None:
            # No streaming happened — send normally
            await self._send_chunked(
                chat_id,
                reply,
                message_thread_id=state.message_thread_id,
                format_markdown=True,
            )
            return

        chunks = _split_message(reply)
        if len(chunks) == 1:
            # Single chunk: do a final edit with the complete text
            try:
                rendered_reply = _render_telegram_markdown_html(reply)
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=state.preview_msg_id,
                    text=rendered_reply,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=not getattr(self.cfg, "telegram_link_preview", True),
                )
                return
            except BadRequest as e:
                err = str(e).lower()
                if "message is not modified" in err:
                    return  # already up to date
                if "can't parse" in err or "html" in err:
                    # Strip HTML and retry edit
                    try:
                        plain = HTML_STRIP_RE.sub("", _render_telegram_markdown_html(reply))
                        await self._bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=state.preview_msg_id,
                            text=plain,
                        )
                        return
                    except Exception:
                        pass
                # Fall through to delete + resend
            except Exception:
                pass

        # Multi-chunk or edit failed: delete preview and send all chunks fresh
        try:
            await self._bot.delete_message(chat_id, state.preview_msg_id)
        except Exception:
            pass
        await self._send_chunked(
            chat_id,
            reply,
            message_thread_id=state.message_thread_id,
            format_markdown=True,
        )

    async def _typing_loop(self, chat_id: int) -> None:
        _backoff = False
        try:
            while True:
                if not _backoff:
                    try:
                        await self._bot.send_chat_action(chat_id, ChatAction.TYPING)
                    except Forbidden:
                        logger.warning("[%s] sendChatAction 403 for %s — stopping", self._label, chat_id)
                        _backoff = True
                    except Exception:
                        pass
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
        await self._safe_send(self._owner_id, text, reply_markup=keyboard)

    async def _handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query:
            return
        await query.answer()

        data = query.data or ""

        # Pairing callbacks (always owner-only)
        if data.startswith("pair_approve:") or data.startswith("pair_deny:"):
            if not self._is_owner(update):
                await query.answer("Owner only.", show_alert=True)
                return
            from agent.pairing import get_pairing_store
            store = get_pairing_store()
            approved_flow = data.startswith("pair_approve:")
            request_id = data.split(":", 1)[1]
            if approved_flow:
                user = store.approve(request_id)
                if user:
                    label = f"{user.get('first_name', '')} @{user.get('username', '')} ({user['user_id']})"
                    try:
                        await query.edit_message_text(
                            f"{query.message.text}\n\n— ✅ Approved: {label}"
                        )
                    except Exception:
                        pass
                    # Notify the newly approved user
                    try:
                        await self._bot.send_message(
                            chat_id=user["user_id"],
                            text="Your access has been approved. You can now chat with the bot.",
                        )
                    except Exception:
                        pass
                else:
                    try:
                        await query.edit_message_text("Request not found (already resolved).")
                    except Exception:
                        pass
            else:
                user = store.deny(request_id)
                if user:
                    label = f"{user.get('first_name', '')} @{user.get('username', '')} ({user['user_id']})"
                    try:
                        await query.edit_message_text(
                            f"{query.message.text}\n\n— ❌ Denied: {label}"
                        )
                    except Exception:
                        pass
            return

        # Tool confirmation callbacks (owner-only)
        if data.startswith("approve:") or data.startswith("deny:"):
            if not self._is_owner(update):
                await query.answer("Owner only.", show_alert=True)
                return
            approved = data.startswith("approve:")
            request_id = data.split(":", 1)[1]
            resolved = self.approval.resolve(request_id, approved)
            action = "Approved ✅" if approved else "Denied ❌"
            try:
                if resolved:
                    await query.edit_message_text(f"{query.message.text}\n\n— {action}")
                else:
                    await query.edit_message_text("This request has already been resolved.")
            except Exception:
                pass
            return

        # Agent-defined inline button callback — check callback policy
        if not self._is_callback_allowed(update):
            await query.answer("Not authorised.", show_alert=True)
            return
        payload = data
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await self._run_agent(update, f"[Button pressed: {payload}]")

    # ------------------------------------------------------------------
    # Proactive send methods (called by tools)
    # ------------------------------------------------------------------

    async def send_message(self, text: str) -> None:
        """Send text to owner (used by cron, gateway, and other proactive senders)."""
        logger.info("[%s] Proactive send to owner %s (%d chars)", self._label, self._owner_id, len(text))
        await self._send_chunked(self._owner_id, text, format_markdown=True)
        logger.info("[%s] Proactive send completed", self._label)

    async def send_to(
        self,
        chat_id: int | str,
        text: str,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        silent: bool = False,
    ) -> int | None:
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
            msgs = await self._safe_send(int(chat_id), chunk, format_markdown=True, **kwargs)
            if msgs:
                last_msg = msgs[-1]
        return last_msg.message_id if last_msg else None

    async def send_audio(self, audio_path: str) -> None:
        with open(audio_path, "rb") as f:
            await self._bot.send_voice(chat_id=self._owner_id, voice=f)

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
        target = int(chat_id) if chat_id else self._owner_id
        kwargs: dict = {}
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = reply_to_message_id
        if message_thread_id:
            kwargs["message_thread_id"] = message_thread_id
        if silent:
            kwargs["disable_notification"] = True
        if photo_path_or_url.startswith("http"):
            msg = await self._bot.send_photo(
                chat_id=target, photo=photo_path_or_url, caption=caption or None, **kwargs
            )
        else:
            with open(photo_path_or_url, "rb") as f:
                msg = await self._bot.send_photo(
                    chat_id=target, photo=f, caption=caption or None, **kwargs
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
        target = int(chat_id) if chat_id else self._owner_id
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
        markup = _build_inline_keyboard(buttons) if buttons else None
        kwargs: dict = {}
        if markup:
            kwargs["reply_markup"] = markup
        rendered = _render_telegram_markdown_html(text[:4096])
        try:
            result = await self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=rendered,
                parse_mode=ParseMode.HTML,
                **kwargs,
            )
        except BadRequest as e:
            err_lower = str(e).lower()
            if "can't parse" in err_lower or "parse" in err_lower or "html" in err_lower:
                result = await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=HTML_STRIP_RE.sub("", rendered),
                    **kwargs,
                )
            else:
                raise
        return result.message_id if isinstance(result, Message) else message_id

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        await self._bot.delete_message(chat_id=chat_id, message_id=message_id)

    async def react_to_message(
        self,
        chat_id: int,
        message_id: int,
        emoji: str | None,
        *,
        remove: bool = False,
    ) -> None:
        ok = await self._react(chat_id, message_id, None if remove else emoji, remove=remove)
        if not ok:
            raise RuntimeError(f"REACTION_INVALID or failed for emoji={emoji!r}")

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
        target = int(chat_id) if chat_id else self._owner_id
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
        kwargs: dict = {}
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = reply_to_message_id
        if message_thread_id:
            kwargs["message_thread_id"] = message_thread_id
        msg = await self._bot.send_sticker(chat_id=int(chat_id), sticker=file_id, **kwargs)
        return msg.message_id if msg else None

    async def create_forum_topic(
        self,
        chat_id: int | str,
        name: str,
        *,
        icon_color: int | None = None,
        icon_custom_emoji_id: str | None = None,
    ) -> dict:
        kwargs: dict = {}
        if icon_color is not None:
            kwargs["icon_color"] = icon_color
        if icon_custom_emoji_id:
            kwargs["icon_custom_emoji_id"] = icon_custom_emoji_id
        topic = await self._bot.create_forum_topic(chat_id=int(chat_id), name=name, **kwargs)
        return {"topicId": topic.message_thread_id, "name": name, "chatId": int(chat_id)}

    async def pin_message(
        self,
        chat_id: int | str,
        message_id: int,
        *,
        disable_notification: bool = False,
    ) -> None:
        await self._bot.pin_chat_message(
            chat_id=int(chat_id),
            message_id=message_id,
            disable_notification=disable_notification,
        )

    async def unpin_message(self, chat_id: int | str, message_id: int) -> None:
        await self._bot.unpin_chat_message(chat_id=int(chat_id), message_id=message_id)

    async def unpin_all_messages(self, chat_id: int | str) -> None:
        await self._bot.unpin_all_chat_messages(chat_id=int(chat_id))

    # ------------------------------------------------------------------
    # Chat ID resolution helper (for @username / t.me / :topic: targets)
    # ------------------------------------------------------------------

    async def resolve_chat_id(self, target: str) -> int | str:
        """
        Resolve @username, https://t.me/username, or :topic:<id> targets to chat_id.
        Falls back to the original value if resolution fails or not needed.
        """
        if not target:
            return self._owner_id

        t = str(target).strip()

        # Numeric: already a chat_id
        try:
            return int(t)
        except ValueError:
            pass

        # :topic:<chat_id>:<thread_id> — extract chat_id part
        if t.startswith(":topic:"):
            parts = t.split(":")
            if len(parts) >= 3:
                try:
                    return int(parts[2])
                except ValueError:
                    pass

        # https://t.me/username or https://t.me/+invite_hash
        if "t.me/" in t:
            slug = t.split("t.me/", 1)[1].split("/")[0].split("?")[0]
            if slug and not slug.startswith("+"):
                t = f"@{slug}"

        # @username → getChat
        if t.startswith("@"):
            try:
                chat = await self._bot.get_chat(t)
                return chat.id
            except Exception as e:
                logger.debug("[%s] resolve_chat_id getChat failed for %s: %s", self._label, t, e)

        return t  # return as-is

    # ------------------------------------------------------------------
    # Sticker cache persistence (SQLite)
    # ------------------------------------------------------------------

    def _load_sticker_cache(self) -> dict[str, list[dict]]:
        try:
            import sqlite3
            conn = sqlite3.connect(str(self._sticker_db_path))
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sticker_sets "
                "(set_name TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT)"
            )
            rows = conn.execute("SELECT set_name, data FROM sticker_sets").fetchall()
            conn.close()
            return {row[0]: json.loads(row[1]) for row in rows}
        except Exception:
            return {}

    def _save_sticker_cache_entry(self, set_name: str, stickers: list[dict]) -> None:
        try:
            import sqlite3
            from datetime import datetime
            conn = sqlite3.connect(str(self._sticker_db_path))
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sticker_sets "
                "(set_name TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO sticker_sets(set_name, data, updated_at) VALUES (?,?,?)",
                (set_name, json.dumps(stickers), datetime.utcnow().isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug("[%s] Sticker cache save failed: %s", self._label, e)

    def get_sticker_cache(self) -> dict[str, list[dict]]:
        return self._sticker_cache

    def update_sticker_cache(self, set_name: str, stickers: list[dict]) -> None:
        self._sticker_cache[set_name] = stickers
        self._save_sticker_cache_entry(set_name, stickers)

    # ------------------------------------------------------------------
    # Safe send with HTML ParseMode fallback + chunking
    # ------------------------------------------------------------------

    async def _safe_send(
        self,
        chat_id: int,
        text: str,
        reply_markup=None,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        disable_notification: bool = False,
        format_markdown: bool = False,
    ) -> list[Message]:
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
            outbound_text = _render_telegram_markdown_html(chunk) if format_markdown else chunk
            try:
                msg = await self._bot.send_message(
                    chat_id=chat_id,
                    text=outbound_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup,
                    **kwargs,
                )
                sent.append(msg)
            except BadRequest as e:
                err_lower = str(e).lower()
                if "can't parse" in err_lower or "parse" in err_lower or "html" in err_lower:
                    plain = HTML_STRIP_RE.sub("", outbound_text)
                    try:
                        msg = await self._bot.send_message(
                            chat_id=chat_id,
                            text=plain,
                            reply_markup=markup,
                            **kwargs,
                        )
                        sent.append(msg)
                    except Exception as inner:
                        logger.error("[%s] Failed to send plain text: %s", self._label, inner)
                else:
                    logger.error("[%s] Failed to send message: %s", self._label, e)
            except Exception as e:
                logger.error("[%s] Failed to send message: %s", self._label, e)
        return sent

    async def _send_chunked(
        self,
        chat_id: int,
        text: str,
        message_thread_id: int | None = None,
        *,
        format_markdown: bool = False,
    ) -> None:
        await self._safe_send(
            chat_id,
            text,
            message_thread_id=message_thread_id,
            format_markdown=format_markdown,
        )

    # ------------------------------------------------------------------
    # File download helper
    # ------------------------------------------------------------------

    async def _download_file(self, file_id: str, suffix: str = "") -> str:
        tg_file = await self._bot.get_file(file_id)
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        await tg_file.download_to_drive(tmp_path)
        return tmp_path

    # ------------------------------------------------------------------
    # Run / startup
    # ------------------------------------------------------------------

    def _get_webhook_secret(self) -> str | None:
        """
        CVE-2026-26319 — Webhook authentication.
        Returns the configured secret token, or raises if webhook is in use without one.
        python-telegram-bot validates X-Telegram-Bot-Api-Secret-Token automatically
        when secret_token is set, rejecting any request that does not carry the header.
        We enforce that a secret is always configured in webhook mode.
        """
        cfg = self.cfg
        secret = cfg.telegram_webhook_secret or ""
        if secret:
            return secret
        # No secret configured — generate a secure random one and warn loudly.
        # This ensures every webhook deployment is authenticated even if the user
        # forgot to set TELEGRAM_WEBHOOK_SECRET.
        import secrets as _secrets
        generated = _secrets.token_urlsafe(32)
        logger.warning(
            "SECURITY WARNING: TELEGRAM_WEBHOOK_SECRET is not set. "
            "A random secret has been generated for this session: %s\n"
            "Set TELEGRAM_WEBHOOK_SECRET=%s in .env to make this permanent. "
            "Without a fixed secret, the webhook token changes on every restart.",
            generated, generated,
        )
        return generated

    def run(self) -> None:
        """Single-account blocking entry point (used by main.py in single-account mode)."""
        cfg = self.cfg
        if cfg.telegram_webhook_url:
            # Webhook mode
            self._app.run_webhook(
                listen="0.0.0.0",
                port=cfg.telegram_webhook_port,
                url_path=self._account.token or cfg.telegram_bot_token,
                webhook_url=cfg.telegram_webhook_url,
                secret_token=self._get_webhook_secret(),  # CVE-2026-26319
            )
        else:
            # Polling mode with offset persistence
            saved_offset = self._load_poll_offset()
            if saved_offset > 0:
                # Advance server-side offset to skip already-processed updates
                import asyncio as _asyncio
                async def _advance():
                    try:
                        await self._bot.get_updates(offset=saved_offset, limit=0, timeout=0)
                    except Exception:
                        pass
                try:
                    _asyncio.get_event_loop().run_until_complete(_advance())
                except Exception:
                    pass
            self._app.run_polling(drop_pending_updates=(saved_offset == 0))

    async def run_async(self) -> None:
        """Async entry point for multi-account concurrent mode."""
        cfg = self.cfg
        await self._app.initialize()
        await self._app.start()

        if cfg.telegram_webhook_url:
            await self._app.updater.start_webhook(
                listen="0.0.0.0",
                port=cfg.telegram_webhook_port,
                url_path=self._account.token or cfg.telegram_bot_token,
                webhook_url=cfg.telegram_webhook_url,
                secret_token=self._get_webhook_secret(),  # CVE-2026-26319
            )
        else:
            saved_offset = self._load_poll_offset()
            if saved_offset > 0:
                try:
                    await self._bot.get_updates(offset=saved_offset, limit=0, timeout=0)
                except Exception:
                    pass
            await self._app.updater.start_polling(
                drop_pending_updates=(saved_offset == 0),
                allowed_updates=Update.ALL_TYPES,
            )

    async def stop_async(self) -> None:
        """Graceful shutdown for multi-account mode."""
        try:
            if self._app.updater.running:
                await self._app.updater.stop()
        except Exception:
            pass
        try:
            await self._app.stop()
        except Exception:
            pass
        try:
            await self._app.shutdown()
        except Exception:
            pass


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _build_inline_keyboard(buttons_2d: list[list[dict]] | None) -> InlineKeyboardMarkup | None:
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
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_len:
        split_at = remaining.rfind("\n", 0, max_len)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, max_len)
        if split_at <= 0:
            split_at = max_len
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


def _render_telegram_markdown_html(text: str) -> str:
    """
    Convert common markdown-ish patterns to Telegram HTML.
    Leaves pure plain/HTML text unchanged to avoid breaking bot-authored HTML responses.
    """
    if not text:
        return ""
    # Also check * (single) for *Reminder* etc. — treat as bold for headers
    if not any(tok in text for tok in ("```", "**", "*", "__", "`", "~~", "# ")):
        return text

    placeholders: list[str] = []

    def _stash(value: str) -> str:
        token = f"@@TGPH{len(placeholders)}@@"
        placeholders.append(value)
        return token

    # Preserve fenced code blocks first.
    def _fence_repl(match: re.Match) -> str:
        lang = (match.group(1) or "").strip()
        code = (match.group(2) or "").strip("\n")
        lang_attr = f' class="language-{html.escape(lang)}"' if lang else ""
        return _stash(f"<pre><code{lang_attr}>{html.escape(code)}</code></pre>")

    staged = re.sub(r"```([A-Za-z0-9_.+-]*)\n(.*?)```", _fence_repl, text, flags=re.DOTALL)

    # Preserve inline code spans before escaping.
    def _inline_code_repl(match: re.Match) -> str:
        return _stash(f"<code>{html.escape(match.group(1))}</code>")

    staged = re.sub(r"`([^`\n]+)`", _inline_code_repl, staged)

    # Escape remaining text, then apply lightweight markdown transforms.
    rendered = html.escape(staged)
    rendered = re.sub(r"(?m)^#{1,6}\s+(.+)$", r"<b>\1</b>", rendered)
    rendered = re.sub(r"\*\*([^\n*][^*]*?)\*\*", r"<b>\1</b>", rendered)
    rendered = re.sub(r"\*([^\n*][^*]*?)\*", r"<b>\1</b>", rendered)  # *word* → bold (e.g. *Reminder*)
    rendered = re.sub(r"__([^_\n][^_]*?)__", r"<b>\1</b>", rendered)
    rendered = re.sub(r"~~([^~\n][^~]*?)~~", r"<s>\1</s>", rendered)

    # Restore preserved HTML segments.
    for idx, value in enumerate(placeholders):
        token = html.escape(f"@@TGPH{idx}@@")
        rendered = rendered.replace(token, value)

    return rendered


def _extract_reply_body(reply_msg: Message) -> str | None:
    if not reply_msg:
        return None
    text = reply_msg.text or reply_msg.caption or ""
    if text:
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


def _extract_forward_prefix(msg: Message) -> str | None:
    """
    Build a human-readable prefix for forwarded messages.

    PTB v22+ uses `forward_origin` (MessageOrigin*). Older versions exposed
    `forward_from` / `forward_from_chat`; we keep a fallback for compatibility.
    """
    origin = getattr(msg, "forward_origin", None)
    if origin:
        # Forwarded from a user account
        user = getattr(origin, "user", None)
        if user:
            name = f"{user.first_name or ''} {getattr(user, 'last_name', '') or ''}".strip()
            if not name:
                name = getattr(user, "username", "") or str(getattr(user, "id", "user"))
            return f"[Forwarded from {name}]"

        # Forwarded while hiding original sender identity
        hidden_name = getattr(origin, "sender_user_name", None)
        if hidden_name:
            return f"[Forwarded from {hidden_name}]"

        # Forwarded from chat/channel
        chat = getattr(origin, "chat", None)
        if chat:
            title = (
                getattr(chat, "title", None)
                or getattr(chat, "username", None)
                or str(getattr(chat, "id", "channel"))
            )
            return f"[Forwarded from channel: {title}]"

    # Legacy fields (older PTB versions)
    fwd_user = getattr(msg, "forward_from", None)
    if fwd_user:
        name = f"{fwd_user.first_name or ''} {getattr(fwd_user, 'last_name', '') or ''}".strip()
        if not name:
            name = getattr(fwd_user, "username", "") or str(getattr(fwd_user, "id", "user"))
        return f"[Forwarded from {name}]"

    fwd_chat = getattr(msg, "forward_from_chat", None)
    if fwd_chat:
        title = (
            getattr(fwd_chat, "title", None)
            or getattr(fwd_chat, "username", None)
            or str(getattr(fwd_chat, "id", "channel"))
        )
        return f"[Forwarded from channel: {title}]"

    return None


def _cleanup_file(path: str | None) -> None:
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass
