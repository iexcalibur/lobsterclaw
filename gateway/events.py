"""Gateway event bus — broadcasts real-time events to connected WebSocket clients."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class GatewayEventBus:
    """Pub/sub event bus backed by asyncio Queues. The gateway WebSocket
    subscribes; other components (agent loop, tool registry, cron) publish."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        event = {
            "type": event_type,
            "data": data or {},
            "ts": time.time(),
        }
        dead: list[asyncio.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


event_bus = GatewayEventBus()
