"""
Canvas event bus — asyncio Queues for bidirectional agent ↔ frontend communication.

The agent calls wait_event(session_id, timeout) to block until the frontend
sends a user interaction (click, input, submit, eval_result).

The WebSocket handler calls put_event(session_id, event) when a client message arrives.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Per-session event queues: session_id → Queue of event dicts
_queues: dict[str, asyncio.Queue] = {}

# Pending eval results: eval_id → Future
_eval_futures: dict[str, asyncio.Future] = {}


def get_queue(session_id: str) -> asyncio.Queue:
    if session_id not in _queues:
        _queues[session_id] = asyncio.Queue(maxsize=100)
    return _queues[session_id]


async def put_event(session_id: str, event: dict) -> None:
    """Called by WebSocket handler when a client message arrives."""
    # Handle eval results separately (they go to a specific future)
    if event.get("type") == "eval_result":
        eval_id = event.get("id", "")
        future = _eval_futures.get(eval_id)
        if future and not future.done():
            future.set_result(event.get("result"))
        return

    q = get_queue(session_id)
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:
        logger.warning("canvas event queue full for session %s — dropping event", session_id)


async def wait_event(session_id: str, timeout: float = 60.0) -> dict | None:
    """
    Wait for the next user interaction event from session_id.
    Returns the event dict, or None on timeout.
    Events: click, input, submit, event (custom).
    """
    q = get_queue(session_id)
    try:
        return await asyncio.wait_for(q.get(), timeout=timeout)
    except asyncio.TimeoutError:
        return None


def register_eval_future(eval_id: str) -> asyncio.Future:
    """Register a future for an eval result. Call this before pushing eval to frontend."""
    loop = asyncio.get_event_loop()
    future: asyncio.Future = loop.create_future()
    _eval_futures[eval_id] = future
    return future


async def wait_eval_result(eval_id: str, timeout: float = 15.0) -> Any:
    """Wait for the result of a JS eval pushed to the frontend."""
    future = _eval_futures.get(eval_id)
    if not future:
        return None
    try:
        return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        _eval_futures.pop(eval_id, None)


def clear_session(session_id: str) -> None:
    """Remove event queue for a session (called on session close)."""
    _queues.pop(session_id, None)
