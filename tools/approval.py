from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

SendFn = Callable[[str, str], Awaitable[None]]  # (text, request_id) -> None


class ApprovalGate:
    """Pauses tool execution and asks the user to Approve or Deny via Telegram."""

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future] = {}
        self._send_fn: SendFn | None = None
        self._timeout: int = 120

    def configure(self, send_fn: SendFn, timeout: int = 120) -> None:
        self._send_fn = send_fn
        self._timeout = timeout

    async def request(self, tool_name: str, args: dict) -> bool:
        if not self._send_fn:
            logger.warning("ApprovalGate has no send_fn — auto-denying '%s'", tool_name)
            return False

        request_id = uuid.uuid4().hex[:8]
        loop = asyncio.get_event_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._pending[request_id] = future

        args_preview = json.dumps(args, indent=2)[:600]

        try:
            await self._send_fn(
                f"🔐 *Approval Required*\n\nTool: `{tool_name}`\n```\n{args_preview}\n```",
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
            return False

    def resolve(self, request_id: str, approved: bool) -> bool:
        future = self._pending.pop(request_id, None)
        if future and not future.done():
            future.set_result(approved)
            return True
        return False
