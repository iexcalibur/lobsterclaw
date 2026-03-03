"""
Channel action stubs — schema-compatible skeletons for Discord, Slack, and WhatsApp.

These mirror OpenClaw's multi-channel message action surfaces in terms of schema.
They return "channel not connected" at runtime unless the channel is enabled via .env.

Enable a channel by setting:
  DISCORD_ENABLED=true   DISCORD_BOT_TOKEN=...   DISCORD_GUILD_ID=...
  SLACK_ENABLED=true     SLACK_BOT_TOKEN=...      SLACK_CHANNEL_ID=...
  WHATSAPP_ENABLED=true  (uses WhatsApp Web session from openclaw/web channel)

This gives strict 1:1 schema parity for tool contract migrations, even if
the underlying channel is not yet wired up.
"""

from __future__ import annotations

import logging

from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)


# ==================================================================
# Discord
# ==================================================================

DISCORD_TOOL = ToolDefinition(
    name="discord",
    description=(
        "Send messages and perform actions in Discord. Requires DISCORD_ENABLED=true.\n\n"
        "Actions:\n"
        "  send         — send a message to a channel\n"
        "  edit         — edit a sent message\n"
        "  delete       — delete a message\n"
        "  react        — add an emoji reaction\n"
        "  dm           — send a direct message to a user\n"
        "  thread       — create or reply in a thread\n"
        "  embed        — send a rich embed message\n"
        "  pin          — pin/unpin a message\n"
        "  channels     — list available channels\n"
        "  status       — show Discord bot connection status"
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
                "description": "Emoji for react action (name or Unicode)",
            },
            "thread_id": {
                "type": "string",
                "description": "Thread ID (for thread replies)",
            },
            "embed": {
                "type": "object",
                "description": "Embed object {title, description, color, fields, url, thumbnail}",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "color": {"type": "integer"},
                    "url": {"type": "string"},
                },
            },
        },
        "required": ["action"],
    },
    fn=lambda **kw: _discord(**kw),
)


async def _discord(action: str, **kwargs) -> str:
    from config import get_config
    cfg = get_config()
    if not getattr(cfg, "discord_enabled", False):
        return (
            "Discord channel is not enabled. "
            "Set DISCORD_ENABLED=true and DISCORD_BOT_TOKEN=... in .env to activate."
        )

    # Future: wire up discord.py bot when DISCORD_ENABLED=true
    return f"Discord action '{action}' is schema-compatible but not yet wired. Set DISCORD_ENABLED=true and restart."


# ==================================================================
# Slack
# ==================================================================

SLACK_TOOL = ToolDefinition(
    name="slack",
    description=(
        "Send messages and perform actions in Slack. Requires SLACK_ENABLED=true.\n\n"
        "Actions:\n"
        "  send         — post a message to a channel\n"
        "  edit         — update a message\n"
        "  delete       — delete a message\n"
        "  react        — add an emoji reaction\n"
        "  dm           — send a direct message to a user\n"
        "  thread       — reply in a message thread\n"
        "  upload       — upload a file to a channel\n"
        "  channels     — list channels the bot is in\n"
        "  users        — list workspace users\n"
        "  status       — show Slack bot connection status"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action: send|edit|delete|react|dm|thread|upload|channels|users|status",
            },
            "channel": {
                "type": "string",
                "description": "Slack channel name or ID (defaults to SLACK_CHANNEL_ID from .env)",
            },
            "timestamp": {
                "type": "string",
                "description": "Message timestamp / ts (required for edit/delete/react/thread)",
            },
            "user_id": {
                "type": "string",
                "description": "Slack user ID (for dm action)",
            },
            "text": {
                "type": "string",
                "description": "Message text (supports Slack mrkdwn formatting)",
            },
            "emoji": {
                "type": "string",
                "description": "Emoji name for react (without colons, e.g. 'thumbsup')",
            },
            "blocks": {
                "type": "array",
                "description": "Slack Block Kit blocks array for rich messages",
                "items": {"type": "object"},
            },
            "file_path": {
                "type": "string",
                "description": "Local file path to upload (upload action)",
            },
        },
        "required": ["action"],
    },
    fn=lambda **kw: _slack(**kw),
)


async def _slack(action: str, **kwargs) -> str:
    from config import get_config
    cfg = get_config()
    if not getattr(cfg, "slack_enabled", False):
        return (
            "Slack channel is not enabled. "
            "Set SLACK_ENABLED=true and SLACK_BOT_TOKEN=... in .env to activate."
        )
    return f"Slack action '{action}' is schema-compatible but not yet wired. Set SLACK_ENABLED=true and restart."


# ==================================================================
# WhatsApp
# ==================================================================

WHATSAPP_TOOL = ToolDefinition(
    name="whatsapp",
    description=(
        "Send messages and perform actions via WhatsApp. Requires WHATSAPP_ENABLED=true.\n\n"
        "Actions:\n"
        "  send         — send a text message to a contact or group\n"
        "  sendMedia    — send an image, video, or document\n"
        "  react        — react to a message with an emoji\n"
        "  delete       — delete a sent message\n"
        "  groups       — list WhatsApp groups\n"
        "  contacts     — list recent contacts\n"
        "  status       — show WhatsApp connection status"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action: send|sendMedia|react|delete|groups|contacts|status",
            },
            "to": {
                "type": "string",
                "description": "Recipient JID (phone@s.whatsapp.net or group@g.us)",
            },
            "message_id": {
                "type": "string",
                "description": "Message ID (for react/delete)",
            },
            "text": {
                "type": "string",
                "description": "Message text",
            },
            "media_path": {
                "type": "string",
                "description": "Local path or URL for media (sendMedia action)",
            },
            "media_type": {
                "type": "string",
                "description": "Media type: image | video | document | audio (sendMedia action)",
            },
            "caption": {
                "type": "string",
                "description": "Caption for media messages",
            },
            "emoji": {
                "type": "string",
                "description": "Emoji for react action",
            },
        },
        "required": ["action"],
    },
    fn=lambda **kw: _whatsapp(**kw),
)


async def _whatsapp(action: str, **kwargs) -> str:
    from config import get_config
    cfg = get_config()
    if not getattr(cfg, "whatsapp_enabled", False):
        return (
            "WhatsApp channel is not enabled. "
            "Set WHATSAPP_ENABLED=true in .env to activate. "
            "Requires a WhatsApp Web session (see workspace/TOOLS.md)."
        )
    return f"WhatsApp action '{action}' is schema-compatible but not yet wired. Set WHATSAPP_ENABLED=true and restart."
