"""
Discord channel — full LobsterClaw channel implementation using discord.py.

Mirrors TelegramChannel's interface:
  - run_async() / stop_async()  — lifecycle (called from main.py async runner)
  - send_message(text)          — primary outbound

Features:
  - DM and guild (server) message handling
  - Mention gating in guilds (only responds when mentioned or in DM)
  - Per-channel conversation history via HistoryManager
  - Typing indicator while agent runs
  - Long message splitting (Discord 2000-char limit)
  - Streaming preview — edits a "thinking..." message with live output
  - Reaction lifecycle: ⏳ thinking → ⚙️ working → ✅ done / ❌ error
  - Slash commands: /new (clear history), /help
  - File attachment reading (text files forwarded to agent)
  - Embeds for structured responses
  - Owner-only DM policy option

Config (.env):
  DISCORD_ENABLED=true
  DISCORD_BOT_TOKEN=your-bot-token
  DISCORD_GUILD_ID=123456789          # optional: restrict to one server
  DISCORD_CHANNEL_ID=123456789        # optional: default channel for send_message()
  DISCORD_OWNER_ID=123456789          # your Discord user ID
  DISCORD_DM_POLICY=owner             # owner | open
  DISCORD_MENTION_REQUIRED=true       # only respond when @mentioned in guilds
  DISCORD_STREAMING=true              # live preview while agent thinks

Requires: discord.py>=2.3.0  (pip install "discord.py>=2.3.0")
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Awaitable, Callable

try:
    import discord
    from discord.ext import commands
    from discord import app_commands
    _DISCORD_AVAILABLE = True
except ImportError:
    _DISCORD_AVAILABLE = False

from config import get_config

if TYPE_CHECKING:
    from agent.history import HistoryManager
    from agent.loop import AgentLoop
    from tools.approval import ApprovalGate

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 1900   # Discord hard limit is 2000 — keep buffer
TYPING_REFRESH = 8          # seconds between typing indicator refreshes


# ── Helpers ───────────────────────────────────────────────────────────────────

def _split_message(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split long text at paragraph / word boundaries."""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        cut = text.rfind("\n\n", 0, max_len)
        if cut == -1:
            cut = text.rfind("\n", 0, max_len)
        if cut == -1:
            cut = text.rfind(" ", 0, max_len)
        if cut == -1:
            cut = max_len
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return chunks


def _strip_mentions(text: str, bot_id: int) -> str:
    """Remove the bot's own mention from the start of a message."""
    import re
    return re.sub(rf"<@!?{bot_id}>\s*", "", text).strip()


# ── Channel class ─────────────────────────────────────────────────────────────

class DiscordChannel:
    """
    LobsterClaw Discord channel.

    Lifecycle (called from main.py):
        await channel.run_async()   — blocks until stopped
        await channel.stop_async()  — graceful shutdown
    """

    def __init__(
        self,
        agent: "AgentLoop",
        history: "HistoryManager",
        approval: "ApprovalGate",
        build_prompt: Callable,
    ) -> None:
        if not _DISCORD_AVAILABLE:
            raise RuntimeError(
                "discord.py is not installed. Run: pip install 'discord.py>=2.3.0'"
            )

        self.cfg = get_config()
        self.agent = agent
        self.history = history
        self.approval = approval
        self.build_prompt = build_prompt

        self._token: str = getattr(self.cfg, "discord_bot_token", "")
        self._guild_id: int | None = getattr(self.cfg, "discord_guild_id", 0) or None
        self._default_channel_id: int | None = getattr(self.cfg, "discord_channel_id", 0) or None
        self._owner_id: int | None = getattr(self.cfg, "discord_owner_id", 0) or None
        self._dm_policy: str = getattr(self.cfg, "discord_dm_policy", "owner")
        self._mention_required: bool = getattr(self.cfg, "discord_mention_required", True)
        self._streaming: bool = getattr(self.cfg, "discord_streaming", True)

        # Build the bot client
        intents = discord.Intents.default()
        intents.message_content = True   # required for reading message text
        intents.members = False

        self._client = commands.Bot(
            command_prefix="!lc_",   # unused — we use slash commands
            intents=intents,
            help_command=None,
        )
        self._tree = self._client.tree

        # Active typing tasks: channel_id → asyncio.Task
        self._typing_tasks: dict[int, asyncio.Task] = {}

        self._setup_events()
        self._setup_slash_commands()

    # ── Event wiring ──────────────────────────────────────────────────────────

    def _setup_events(self) -> None:
        client = self._client

        @client.event
        async def on_ready() -> None:
            logger.info(
                "Discord bot ready — logged in as %s (id=%s)",
                client.user, client.user.id,
            )
            # Sync slash commands to guild or globally
            try:
                if self._guild_id:
                    guild = discord.Object(id=self._guild_id)
                    self._tree.copy_global_to(guild=guild)
                    await self._tree.sync(guild=guild)
                else:
                    await self._tree.sync()
                logger.info("Discord slash commands synced")
            except Exception as exc:
                logger.warning("Discord slash command sync failed: %s", exc)

        @client.event
        async def on_message(message: discord.Message) -> None:
            # Ignore our own messages
            if message.author == client.user:
                return
            # Ignore bots
            if message.author.bot:
                return

            is_dm = isinstance(message.channel, discord.DMChannel)

            # DM policy check
            if is_dm:
                if self._dm_policy == "owner" and self._owner_id:
                    if message.author.id != self._owner_id:
                        await message.channel.send(
                            "Sorry, I only respond to my owner in DMs."
                        )
                        return
            else:
                # Guild message — mention gating
                if self._guild_id and message.guild and message.guild.id != self._guild_id:
                    return   # wrong guild
                if self._mention_required and client.user not in message.mentions:
                    return   # not mentioned — stay silent

            # Extract text (strip bot mention)
            text = message.content or ""
            if client.user:
                text = _strip_mentions(text, client.user.id)

            # Append text attachments
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith("text"):
                    try:
                        file_bytes = await attachment.read()
                        file_text = file_bytes.decode("utf-8", errors="replace")
                        text += f"\n\n[Attachment: {attachment.filename}]\n{file_text[:4000]}"
                    except Exception as exc:
                        logger.warning("Failed to read attachment %s: %s", attachment.filename, exc)

            if not text.strip():
                return

            await self._handle_message(message, text)

    def _setup_slash_commands(self) -> None:
        tree = self._tree

        @tree.command(name="new", description="Start a fresh conversation (clears history)")
        async def slash_new(interaction: discord.Interaction) -> None:
            context_key = self._context_key_from_interaction(interaction)
            self.history.clear(context_key)
            await interaction.response.send_message(
                "✅ Conversation cleared. Starting fresh.", ephemeral=True
            )

        @tree.command(name="help", description="Show LobsterClaw help")
        async def slash_help(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(
                "**LobsterClaw Discord Bot**\n"
                "- Mention me or DM me to chat\n"
                "- `/new` — clear conversation history\n"
                "- `/help` — this message\n"
                "- Attach text files to share them with me",
                ephemeral=True,
            )

    # ── Core message handler ──────────────────────────────────────────────────

    async def _handle_message(self, message: discord.Message, text: str) -> None:
        """Run the agent and send the reply."""
        context_key = self._context_key(message)

        # Add to history
        self.history.add(context_key, "user", text)

        # Build prompt
        tool_names = self.agent.registry.get_names()
        _runtime_info = {
            "channel": "discord",
            "capabilities": ["embeds", "slash_commands", "file_attachments"],
            "agent_id": getattr(self.cfg, "agent_id", ""),
        }
        system = self.build_prompt(
            self.cfg,
            tool_names,
            runtime_info=_runtime_info,
        )
        messages = self.history.get_for_llm(context_key)

        # Reaction: thinking
        await self._react(message, "⏳")

        # Typing indicator task
        typing_task = asyncio.create_task(self._typing_loop(message.channel))

        # Streaming state
        preview_msg: discord.Message | None = None
        last_preview: str = ""

        async def _stream_callback(accumulated: str) -> None:
            nonlocal preview_msg, last_preview
            if not self._streaming:
                return
            if len(accumulated) - len(last_preview) < 80:
                return  # throttle — only update every ~80 chars
            last_preview = accumulated
            try:
                preview = accumulated[:MAX_MESSAGE_LENGTH]
                if preview_msg is None:
                    preview_msg = await message.channel.send(f"_{preview}_")
                else:
                    await preview_msg.edit(content=f"_{preview}_")
            except Exception:
                pass

        async def _on_tool_start() -> None:
            await self._react(message, "⚙️")

        use_streaming = getattr(self.cfg, "llm_streaming", True) and self._streaming

        try:
            reply = await self.agent.run(
                messages,
                system,
                session_id=context_key,
                stream_callback=_stream_callback if use_streaming else None,
                on_tool_start=_on_tool_start if use_streaming else None,
            )
            await self._react(message, "✅")
        except Exception as exc:
            logger.exception("Discord agent run failed")
            reply = f"Sorry, something went wrong: {type(exc).__name__}"
            await self._react(message, "❌")
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

        # Add reply to history
        self.history.add(context_key, "assistant", reply)

        # Check for NO_REPLY token
        silent_token = getattr(self.cfg, "silent_reply_token", "NO_REPLY")
        if reply.strip() == silent_token:
            if preview_msg:
                try:
                    await preview_msg.delete()
                except Exception:
                    pass
            return

        # Send reply — delete streaming preview first, then send final chunks
        if preview_msg:
            try:
                await preview_msg.delete()
            except Exception:
                pass

        chunks = _split_message(reply)
        for chunk in chunks:
            await message.channel.send(chunk)

    # ── Typing loop ───────────────────────────────────────────────────────────

    async def _typing_loop(self, channel) -> None:
        """Keep Discord typing indicator alive."""
        try:
            while True:
                async with channel.typing():
                    await asyncio.sleep(TYPING_REFRESH)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    # ── Reactions ─────────────────────────────────────────────────────────────

    async def _react(self, message: discord.Message, emoji: str) -> None:
        """Add a reaction, silently ignoring failures."""
        try:
            # Clear previous reactions from bot first
            await message.clear_reactions()
            await message.add_reaction(emoji)
        except Exception:
            pass

    # ── Context key ───────────────────────────────────────────────────────────

    def _context_key(self, message: discord.Message) -> str:
        """Unique conversation context per channel (or DM per user)."""
        if isinstance(message.channel, discord.DMChannel):
            return f"discord:dm:{message.author.id}"
        return f"discord:ch:{message.channel.id}"

    def _context_key_from_interaction(self, interaction: discord.Interaction) -> str:
        if isinstance(interaction.channel, discord.DMChannel):
            return f"discord:dm:{interaction.user.id}"
        return f"discord:ch:{interaction.channel_id}"

    # ── Outbound helpers (used by tools via discord_tool.py) ─────────────────

    async def send_message(self, text: str, channel_id: int | None = None) -> None:
        """Send a message to a channel. Used by background tasks / cron."""
        cid = channel_id or self._default_channel_id
        if not cid:
            logger.warning("discord.send_message: no channel_id configured")
            return
        channel = self._client.get_channel(cid)
        if channel is None:
            try:
                channel = await self._client.fetch_channel(cid)
            except Exception as exc:
                logger.error("discord.send_message: channel fetch failed: %s", exc)
                return
        for chunk in _split_message(text):
            await channel.send(chunk)

    async def send_embed(
        self,
        title: str,
        description: str,
        color: int = 0x1B4F8A,
        channel_id: int | None = None,
    ) -> None:
        """Send a Discord embed."""
        cid = channel_id or self._default_channel_id
        if not cid:
            return
        channel = self._client.get_channel(cid)
        if channel is None:
            return
        embed = discord.Embed(title=title, description=description, color=color)
        await channel.send(embed=embed)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def run_async(self) -> None:
        """Start the Discord bot. Called from main.py async runner."""
        if not self._token:
            raise ValueError(
                "DISCORD_BOT_TOKEN is not set. Add it to .env and restart."
            )
        logger.info("Starting Discord channel...")
        await self._client.start(self._token)

    async def stop_async(self) -> None:
        """Graceful shutdown."""
        logger.info("Stopping Discord channel...")
        try:
            await self._client.close()
        except Exception as exc:
            logger.debug("Discord stop_async: %s", exc)
