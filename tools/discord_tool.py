"""
tools/discord_tool.py — Live Discord tool (replaces channel_stubs.DISCORD_TOOL when enabled).

When DISCORD_ENABLED=true, main.py wires a DiscordChannel instance here so the
agent can send messages, create threads, react, etc. — just like the Telegram tool.

Actions: send | edit | delete | react | dm | thread | embed | pin | channels | status
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tools.registry import ToolDefinition

if TYPE_CHECKING:
    from channels.discord import DiscordChannel

logger = logging.getLogger(__name__)

_discord_channel: "DiscordChannel | None" = None


def set_discord_channel(channel: "DiscordChannel") -> None:
    """Called from main.py after DiscordChannel is created."""
    global _discord_channel
    _discord_channel = channel


def get_discord_channel() -> "DiscordChannel | None":
    return _discord_channel


async def _discord_action(action: str, **kwargs) -> str:
    ch = _discord_channel
    if ch is None:
        return "Discord channel is not wired. Check DISCORD_ENABLED=true in .env."

    import discord as _discord

    # ── send ──────────────────────────────────────────────────────────────────
    if action == "send":
        text = kwargs.get("text", "")
        channel_id = int(kwargs["channel_id"]) if kwargs.get("channel_id") else None
        await ch.send_message(text, channel_id=channel_id)
        return f"Message sent to {'channel ' + str(channel_id) if channel_id else 'default channel'}."

    # ── dm ────────────────────────────────────────────────────────────────────
    elif action == "dm":
        user_id = kwargs.get("user_id")
        text = kwargs.get("text", "")
        if not user_id:
            return "user_id is required for dm action."
        try:
            user = await ch._client.fetch_user(int(user_id))
            dm = await user.create_dm()
            for chunk in _split(text):
                await dm.send(chunk)
            return f"DM sent to user {user_id}."
        except Exception as exc:
            return f"DM failed: {exc}"

    # ── embed ─────────────────────────────────────────────────────────────────
    elif action == "embed":
        embed_data = kwargs.get("embed", {})
        channel_id = int(kwargs["channel_id"]) if kwargs.get("channel_id") else None
        title = embed_data.get("title", "")
        description = embed_data.get("description", "")
        color = int(embed_data.get("color", 0x1B4F8A))
        await ch.send_embed(title, description, color=color, channel_id=channel_id)
        return "Embed sent."

    # ── thread ────────────────────────────────────────────────────────────────
    elif action == "thread":
        channel_id = int(kwargs["channel_id"]) if kwargs.get("channel_id") else ch._default_channel_id
        text = kwargs.get("text", "")
        thread_id = kwargs.get("thread_id")
        if not channel_id:
            return "channel_id is required for thread action."
        try:
            if thread_id:
                thread = ch._client.get_channel(int(thread_id))
                if thread is None:
                    thread = await ch._client.fetch_channel(int(thread_id))
                await thread.send(text)
                return f"Reply sent to thread {thread_id}."
            else:
                channel = ch._client.get_channel(channel_id)
                msg = await channel.send(text)
                thread = await msg.create_thread(name=text[:100])
                return f"Thread created (id={thread.id})."
        except Exception as exc:
            return f"Thread action failed: {exc}"

    # ── react ─────────────────────────────────────────────────────────────────
    elif action == "react":
        channel_id = int(kwargs["channel_id"]) if kwargs.get("channel_id") else ch._default_channel_id
        message_id = kwargs.get("message_id")
        emoji = kwargs.get("emoji", "👍")
        if not (channel_id and message_id):
            return "channel_id and message_id are required for react."
        try:
            channel = ch._client.get_channel(channel_id)
            msg = await channel.fetch_message(int(message_id))
            await msg.add_reaction(emoji)
            return f"Reacted with {emoji}."
        except Exception as exc:
            return f"React failed: {exc}"

    # ── edit ──────────────────────────────────────────────────────────────────
    elif action == "edit":
        channel_id = int(kwargs["channel_id"]) if kwargs.get("channel_id") else ch._default_channel_id
        message_id = kwargs.get("message_id")
        text = kwargs.get("text", "")
        if not (channel_id and message_id):
            return "channel_id and message_id are required for edit."
        try:
            channel = ch._client.get_channel(channel_id)
            msg = await channel.fetch_message(int(message_id))
            await msg.edit(content=text[:2000])
            return f"Message {message_id} edited."
        except Exception as exc:
            return f"Edit failed: {exc}"

    # ── delete ────────────────────────────────────────────────────────────────
    elif action == "delete":
        channel_id = int(kwargs["channel_id"]) if kwargs.get("channel_id") else ch._default_channel_id
        message_id = kwargs.get("message_id")
        if not (channel_id and message_id):
            return "channel_id and message_id are required for delete."
        try:
            channel = ch._client.get_channel(channel_id)
            msg = await channel.fetch_message(int(message_id))
            await msg.delete()
            return f"Message {message_id} deleted."
        except Exception as exc:
            return f"Delete failed: {exc}"

    # ── pin ───────────────────────────────────────────────────────────────────
    elif action == "pin":
        channel_id = int(kwargs["channel_id"]) if kwargs.get("channel_id") else ch._default_channel_id
        message_id = kwargs.get("message_id")
        if not (channel_id and message_id):
            return "channel_id and message_id required for pin."
        try:
            channel = ch._client.get_channel(channel_id)
            msg = await channel.fetch_message(int(message_id))
            await msg.pin()
            return f"Message {message_id} pinned."
        except Exception as exc:
            return f"Pin failed: {exc}"

    # ── channels ──────────────────────────────────────────────────────────────
    elif action == "channels":
        try:
            guild_id = ch._guild_id
            if guild_id:
                guild = ch._client.get_guild(guild_id)
                if guild:
                    chans = [
                        f"#{c.name} (id={c.id})"
                        for c in guild.text_channels
                    ]
                    return "Text channels:\n" + "\n".join(chans)
            return "No guild configured. Set DISCORD_GUILD_ID in .env."
        except Exception as exc:
            return f"channels failed: {exc}"

    # ── status ────────────────────────────────────────────────────────────────
    elif action == "status":
        client = ch._client
        if client.is_ready():
            latency = round(client.latency * 1000)
            guilds = len(client.guilds)
            return (
                f"✅ Discord connected — {client.user} | "
                f"latency: {latency}ms | guilds: {guilds}"
            )
        return "❌ Discord client is not connected."

    else:
        return (
            f"Unknown action '{action}'. "
            "Valid: send | edit | delete | react | dm | thread | embed | pin | channels | status"
        )


def _split(text: str, n: int = 1900) -> list[str]:
    if len(text) <= n:
        return [text]
    chunks, while_text = [], text
    while while_text:
        chunks.append(while_text[:n])
        while_text = while_text[n:]
    return chunks


# ── Tool definition ───────────────────────────────────────────────────────────

DISCORD_TOOL = ToolDefinition(
    name="discord",
    description=(
        "Send messages and perform actions in Discord.\n\n"
        "Actions:\n"
        "  send     — send a text message to a channel\n"
        "  edit     — edit a message by ID\n"
        "  delete   — delete a message by ID\n"
        "  react    — add an emoji reaction to a message\n"
        "  dm       — send a direct message to a user\n"
        "  thread   — create a thread or reply in one\n"
        "  embed    — send a rich embed message\n"
        "  pin      — pin a message\n"
        "  channels — list text channels in the configured guild\n"
        "  status   — show Discord bot connection status"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action: send|edit|delete|react|dm|thread|embed|pin|channels|status",
            },
            "channel_id": {
                "type": "string",
                "description": "Discord channel ID (defaults to DISCORD_CHANNEL_ID from .env)",
            },
            "message_id": {
                "type": "string",
                "description": "Message ID (required for edit/delete/react/pin)",
            },
            "user_id": {
                "type": "string",
                "description": "Discord user ID (for dm action)",
            },
            "text": {
                "type": "string",
                "description": "Message content",
            },
            "emoji": {
                "type": "string",
                "description": "Emoji for react action (e.g. '👍' or 'thumbsup')",
            },
            "thread_id": {
                "type": "string",
                "description": "Thread channel ID (for replying in an existing thread)",
            },
            "embed": {
                "type": "object",
                "description": "Embed object: {title, description, color (int)}",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "color": {"type": "integer"},
                },
            },
        },
        "required": ["action"],
    },
    fn=lambda **kw: _discord_action(**kw),
)
