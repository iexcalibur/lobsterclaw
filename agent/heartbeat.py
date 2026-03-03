"""
Heartbeat runner — mirrors OpenClaw's periodic agent wake system.

When HEARTBEAT_ENABLED=true, the agent is woken on a schedule and runs with
HEARTBEAT.md loaded into its context. Use HEARTBEAT.md to define periodic tasks
(e.g. check reminders, summarize the day, monitor something).

If HEARTBEAT.md is empty or contains only comments, the heartbeat is a no-op.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from agent.workspace import DEFAULT_WORKSPACE_DIR, _is_effectively_empty
from config import get_config

logger = logging.getLogger(__name__)

AgentFn = Callable[[str, str], Awaitable[str]]   # (message, system_prompt) -> reply
SendFn = Callable[[str], Awaitable[None]]


class HeartbeatRunner:
    def __init__(self) -> None:
        self.cfg = get_config()
        self._scheduler: AsyncIOScheduler | None = None
        self._agent_fn: AgentFn | None = None
        self._send_fn: SendFn | None = None

    def configure(self, agent_fn: AgentFn, send_fn: SendFn) -> None:
        self._agent_fn = agent_fn
        self._send_fn = send_fn

    def start(self, scheduler: AsyncIOScheduler) -> None:
        """Attach heartbeat job to an existing scheduler."""
        self._scheduler = scheduler
        schedule = getattr(self.cfg, "heartbeat_schedule", "0 * * * *")  # default: every hour

        try:
            trigger = CronTrigger.from_crontab(schedule)
            scheduler.add_job(
                self._fire,
                trigger=trigger,
                id="__heartbeat__",
                replace_existing=True,
            )
            logger.info("Heartbeat scheduled: %s", schedule)
        except Exception as e:
            logger.error("Failed to schedule heartbeat: %s", e)

    async def _fire(self) -> None:
        logger.info("Heartbeat firing")

        # Active-hours guard: skip if current local hour is outside configured window
        import datetime as _dt
        _now_hour = _dt.datetime.now().hour
        _start = getattr(self.cfg, "heartbeat_active_hours_start", 0)
        _end = getattr(self.cfg, "heartbeat_active_hours_end", 23)
        if _start <= _end:
            # Normal range e.g. 8..22
            if not (_start <= _now_hour <= _end):
                logger.debug("Heartbeat suppressed outside active hours %d-%d (now=%d)", _start, _end, _now_hour)
                return
        else:
            # Wrapping range e.g. 22..6 (overnight)
            if not (_now_hour >= _start or _now_hour <= _end):
                logger.debug("Heartbeat suppressed outside active hours %d-%d (now=%d)", _start, _end, _now_hour)
                return

        # Check if HEARTBEAT.md has any actual tasks
        heartbeat_path = DEFAULT_WORKSPACE_DIR / "HEARTBEAT.md"
        if not heartbeat_path.exists():
            logger.debug("HEARTBEAT.md not found — skipping")
            return

        content = heartbeat_path.read_text(encoding="utf-8").strip()
        if _is_effectively_empty(content):
            logger.debug("HEARTBEAT.md is empty — skipping")
            return

        if not self._agent_fn or not self._send_fn:
            logger.warning("Heartbeat: agent_fn or send_fn not configured")
            return

        try:
            from agent.prompt import build_system_prompt
            system = build_system_prompt(get_config(), include_heartbeat=True)
            reply = await self._agent_fn(
                "[Heartbeat] Please check your HEARTBEAT.md tasks and act on any that are due.",
                system,
            )
            if reply and reply.strip():
                await self._send_fn(f"🔄 *Heartbeat*\n\n{reply}")
        except Exception as e:
            logger.error("Heartbeat agent run failed: %s", e)
