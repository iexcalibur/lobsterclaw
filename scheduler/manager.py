from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from config import get_config

logger = logging.getLogger(__name__)

# Injected after Telegram is ready
SendFn = Callable[[str], Awaitable[None]]
AgentFn = Callable[[str], Awaitable[str]]


class CronManager:
    """Persists cron jobs in SQLite and runs them via APScheduler."""

    def __init__(self) -> None:
        self.cfg = get_config()
        self.scheduler = AsyncIOScheduler()
        self._send_fn: SendFn | None = None
        self._agent_fn: AgentFn | None = None
        self._init_db()

    def configure(self, send_fn: SendFn, agent_fn: AgentFn | None = None) -> None:
        self._send_fn = send_fn
        self._agent_fn = agent_fn

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._restore_jobs()
        self.scheduler.start()
        logger.info("CronManager started")

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        self.cfg.cron_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.cfg.cron_db))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id          TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                schedule    TEXT NOT NULL,
                message     TEXT NOT NULL,
                enabled     INTEGER DEFAULT 1,
                created_at  TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def _restore_jobs(self) -> None:
        conn = sqlite3.connect(str(self.cfg.cron_db))
        rows = conn.execute("SELECT id, description, schedule, message FROM jobs WHERE enabled=1").fetchall()
        conn.close()
        for job_id, description, schedule, message in rows:
            self._schedule(job_id, schedule, message)
        logger.info("Restored %d cron jobs", len(rows))

    # ------------------------------------------------------------------
    # Internal scheduling
    # ------------------------------------------------------------------

    def _schedule(self, job_id: str, schedule: str, message: str) -> None:
        try:
            if "T" in schedule and not any(c in schedule for c in "*/"):
                # ISO datetime — one-time
                run_date = datetime.fromisoformat(schedule)
                trigger = DateTrigger(run_date=run_date)
            else:
                # Cron expression
                trigger = CronTrigger.from_crontab(schedule)

            self.scheduler.add_job(
                self._fire,
                trigger=trigger,
                id=job_id,
                replace_existing=True,
                args=[job_id, message],
            )
            logger.debug("Scheduled job %s: %s", job_id, schedule)
        except Exception as e:
            logger.error("Failed to schedule job %s: %s", job_id, e)

    async def _fire(self, job_id: str, message: str) -> None:
        logger.info("Firing cron job: %s", job_id)
        if not self._send_fn:
            logger.warning("No send_fn configured — cannot deliver cron job %s", job_id)
            return

        try:
            if self._agent_fn:
                # Let the agent handle the reminder (adds context, can use tools)
                reply = await self._agent_fn(f"[Scheduled reminder] {message}")
                await self._send_fn(reply)
            else:
                await self._send_fn(f"⏰ *Reminder*\n\n{message}")
        except Exception as e:
            logger.error("Cron job %s failed to deliver: %s", job_id, e)

    # ------------------------------------------------------------------
    # Public API (used by cron_tool.py)
    # ------------------------------------------------------------------

    async def add_job(self, description: str, schedule: str, message: str) -> str:
        job_id = uuid.uuid4().hex[:8]
        now = datetime.now().isoformat(timespec="seconds")

        conn = sqlite3.connect(str(self.cfg.cron_db))
        conn.execute(
            "INSERT INTO jobs(id, description, schedule, message, created_at) VALUES (?,?,?,?,?)",
            (job_id, description, schedule, message, now),
        )
        conn.commit()
        conn.close()

        self._schedule(job_id, schedule, message)
        return f"Job scheduled ✅\nID: `{job_id}`\nSchedule: `{schedule}`\nMessage: {message}"

    async def list_jobs(self) -> str:
        conn = sqlite3.connect(str(self.cfg.cron_db))
        rows = conn.execute(
            "SELECT id, description, schedule, message, enabled FROM jobs ORDER BY created_at"
        ).fetchall()
        conn.close()

        if not rows:
            return "No scheduled jobs."

        lines = ["**Scheduled Jobs:**\n"]
        for job_id, description, schedule, message, enabled in rows:
            status = "✅" if enabled else "⏸"
            lines.append(f"{status} `{job_id}` — {description}\n   Schedule: `{schedule}`\n   Message: {message}")
        return "\n\n".join(lines)

    async def remove_job(self, job_id: str) -> str:
        conn = sqlite3.connect(str(self.cfg.cron_db))
        affected = conn.execute("DELETE FROM jobs WHERE id=?", (job_id,)).rowcount
        conn.commit()
        conn.close()

        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            pass

        if affected:
            return f"Job `{job_id}` removed ✅"
        return f"No job found with ID `{job_id}`"

    async def status(self) -> str:
        running = self.scheduler.running
        conn = sqlite3.connect(str(self.cfg.cron_db))
        count = conn.execute("SELECT COUNT(*) FROM jobs WHERE enabled=1").fetchone()[0]
        conn.close()
        jobs = self.scheduler.get_jobs()
        next_runs = []
        for j in jobs[:5]:
            next_run = str(j.next_run_time)[:19] if j.next_run_time else "N/A"
            next_runs.append(f"  `{j.id}`: next run at {next_run}")
        next_section = "\n".join(next_runs) if next_runs else "  (none)"
        return (
            f"Scheduler running: {'✅' if running else '❌'}\n"
            f"Active jobs: {count}\n"
            f"Upcoming:\n{next_section}"
        )
