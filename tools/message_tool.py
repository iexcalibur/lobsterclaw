from __future__ import annotations

import logging
from typing import Awaitable, Callable

from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

# Injected by main.py after Telegram channel is ready
_send_fn: Callable[[str], Awaitable[None]] | None = None


def set_send_fn(fn: Callable[[str], Awaitable[None]]) -> None:
    global _send_fn
    _send_fn = fn


TOOL_DEFINITION = ToolDefinition(
    name="message",
    description="Send a Telegram message to the owner. Use this to send proactive updates, reminders, or follow-ups.",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The message text to send (Markdown supported)"},
        },
        "required": ["text"],
    },
    fn=lambda **kw: _message(**kw),
)


async def _message(text: str) -> str:
    if not _send_fn:
        return "Error: Telegram send function not configured"
    try:
        await _send_fn(text)
        return "Message sent"
    except Exception as e:
        logger.exception("Failed to send message")
        return f"Error sending message: {e}"
