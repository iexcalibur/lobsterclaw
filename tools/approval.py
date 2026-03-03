from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

SendFn = Callable[[str, str], Awaitable[None]]  # (text, request_id) -> None

# Tool-specific pretty-print formatters (maps tool_name → formatter)
_TOOL_FORMATTERS: dict[str, Callable[[dict], str]] = {}


def register_tool_formatter(tool_name: str, formatter: Callable[[dict], str]) -> None:
    """Register a custom args-to-text formatter for a specific tool name."""
    _TOOL_FORMATTERS[tool_name] = formatter


def _default_format_args(tool_name: str, args: dict) -> str:
    """Format tool args for the approval message (tool-specific overrides supported)."""
    formatter = _TOOL_FORMATTERS.get(tool_name)
    if formatter:
        try:
            return formatter(args)
        except Exception:
            pass
    raw = json.dumps(args, indent=2, ensure_ascii=False)
    if len(raw) > 600:
        raw = raw[:600].rsplit("\n", 1)[0] + "\n…"
    return raw


class ApprovalGate:
    """Pauses tool execution and asks the user to Approve or Deny via Telegram."""

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future] = {}
        self._send_fn: SendFn | None = None
        self._notify_fn: Callable[[str], Awaitable[None]] | None = None  # for timeout notifications
        self._timeout: int = 120

    def configure(
        self,
        send_fn: SendFn,
        timeout: int = 120,
        notify_fn: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._send_fn = send_fn
        self._timeout = timeout
        self._notify_fn = notify_fn

    async def request(self, tool_name: str, args: dict) -> bool:
        if not self._send_fn:
            logger.warning("ApprovalGate has no send_fn — auto-denying '%s'", tool_name)
            return False

        request_id = uuid.uuid4().hex[:8]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._pending[request_id] = future

        args_preview = _default_format_args(tool_name, args)

        try:
            await self._send_fn(
                f"🔐 *Approval Required*\n\nTool: `{tool_name}`\n```\n{args_preview}\n```\n\n"
                f"Reply `✅ {request_id}` to approve or `❌ {request_id}` to deny "
                f"(timeout: {self._timeout}s)",
                request_id,
            )
        except Exception as e:
            logger.error("Failed to send approval request: %s", e)
            self._pending.pop(request_id, None)
            return False

        try:
            result = await asyncio.wait_for(asyncio.shield(future), timeout=self._timeout)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            logger.info("Approval timed out for '%s' (id=%s)", tool_name, request_id)
            # Notify user that the approval request timed out
            if self._notify_fn:
                try:
                    await self._notify_fn(
                        f"⏱ *Approval timed out* for `{tool_name}` (id=`{request_id}`) — action denied."
                    )
                except Exception:
                    pass
            return False

    def resolve(self, request_id: str, approved: bool) -> bool:
        future = self._pending.pop(request_id, None)
        if future and not future.done():
            future.set_result(approved)
            return True
        return False
