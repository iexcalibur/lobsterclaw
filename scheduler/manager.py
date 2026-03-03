"""
Cron manager — APScheduler + SQLite persistence.

Mirrors OpenClaw's cron scheduler: supports one-time (ISO datetime),
interval shorthand (30m/2h/1d), and cron expressions.

Actions: add, list, remove, update, run_now, enable/disable, wake, status
"""

from __future__ import annotations

import logging
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import get_config

logger = logging.getLogger(__name__)

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
    # DB
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
                created_at  TEXT NOT NULL,
                run_count   INTEGER DEFAULT 0,
                last_run    TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _restore_jobs(self) -> None:
        conn = sqlite3.connect(str(self.cfg.cron_db))
        rows = conn.execute(
            "SELECT id, schedule, message FROM jobs WHERE enabled=1"
        ).fetchall()
        conn.close()
        restored = 0
        for job_id, schedule, message in rows:
            try:
                self._schedule(job_id, schedule, message)
                restored += 1
            except Exception as e:
                logger.warning("Could not restore job %s: %s", job_id, e)
        logger.info("Restored %d cron jobs", restored)

    # ------------------------------------------------------------------
    # Scheduling helpers
    # ------------------------------------------------------------------

    def _parse_trigger(self, schedule: str):
        """Parse schedule string into an APScheduler trigger."""
        schedule = schedule.strip()

        # ISO datetime → one-time
        if re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", schedule):
            run_date = datetime.fromisoformat(schedule)
            return DateTrigger(run_date=run_date)

        # Interval shorthand: 30m, 2h, 1d, 7d
        m = re.fullmatch(r"(\d+)(m|h|d|s)", schedule)
        if m:
            qty, unit = int(m.group(1)), m.group(2)
            delta = {"s": timedelta(seconds=qty), "m": timedelta(minutes=qty),
                     "h": timedelta(hours=qty), "d": timedelta(days=qty)}[unit]
            return IntervalTrigger(seconds=int(delta.total_seconds()))

        # Cron expression (5 or 6 fields)
        return CronTrigger.from_crontab(schedule)

    def _schedule(self, job_id: str, schedule: str, message: str) -> None:
        try:
            trigger = self._parse_trigger(schedule)
            self.scheduler.add_job(
                self._fire,
                trigger=trigger,
                id=job_id,
                replace_existing=True,
                args=[job_id, message],
            )
            logger.debug("Scheduled job %s: %s", job_id, schedule)
        except Exception as e:
            logger.error("Failed to schedule job %s (%s): %s", job_id, schedule, e)
            raise

    async def _fire(self, job_id: str, message: str) -> None:
        logger.info("Firing cron job: %s", job_id)
        conn = sqlite3.connect(str(self.cfg.cron_db))
        conn.execute(
            "UPDATE jobs SET run_count=run_count+1, last_run=? WHERE id=?",
            (datetime.now().isoformat(timespec="seconds"), job_id),
        )
        conn.commit()
        conn.close()

        if not self._send_fn:
            logger.warning("No send_fn — cannot deliver cron job %s", job_id)
            return

        try:
            if self._agent_fn:
                reply = await self._agent_fn(f"[Scheduled reminder] {message}")
                await self._send_fn(reply)
            else:
                await self._send_fn(f"⏰ *Reminder*\n\n{message}")
        except Exception as e:
            logger.error("Cron job %s fire failed: %s", job_id, e)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def add_job(self, schedule: str, message: str, description: str = "") -> str:
        # Validate schedule before writing to DB
        try:
            self._parse_trigger(schedule)
        except Exception as e:
            return f"Invalid schedule '{schedule}': {e}"

        job_id = uuid.uuid4().hex[:8]
        now = datetime.now().isoformat(timespec="seconds")
        desc = description or message[:60]

        conn = sqlite3.connect(str(self.cfg.cron_db))
        conn.execute(
            "INSERT INTO jobs(id, description, schedule, message, created_at) VALUES (?,?,?,?,?)",
            (job_id, desc, schedule, message, now),
        )
        conn.commit()
        conn.close()

        self._schedule(job_id, schedule, message)
        return (
            f"Job scheduled ✅\n"
            f"ID: `{job_id}`\n"
            f"Schedule: `{schedule}`\n"
            f"Message: {message}"
        )

    def list_jobs(self) -> str:
        conn = sqlite3.connect(str(self.cfg.cron_db))
        rows = conn.execute(
            "SELECT id, description, schedule, message, enabled, run_count, last_run FROM jobs ORDER BY created_at"
        ).fetchall()
        conn.close()

        if not rows:
            return "No scheduled jobs."

        lines = ["**Scheduled Jobs:**\n"]
        for job_id, description, schedule, message, enabled, run_count, last_run in rows:
            status_icon = "✅" if enabled else "⏸️"
            sched_job = None
            try:
                sched_job = self.scheduler.get_job(job_id)
            except Exception:
                pass
            next_run = ""
            if sched_job and sched_job.next_run_time:
                next_run = f"\n   Next run: {str(sched_job.next_run_time)[:19]}"
            lines.append(
                f"{status_icon} `{job_id}` — {description}\n"
                f"   Schedule: `{schedule}`\n"
                f"   Message: {message}\n"
                f"   Runs: {run_count}{next_run}"
            )
        return "\n\n".join(lines)

    def remove_job(self, job_id: str) -> str:
        conn = sqlite3.connect(str(self.cfg.cron_db))
        affected = conn.execute("DELETE FROM jobs WHERE id=?", (job_id,)).rowcount
        conn.commit()
        conn.close()
        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            pass
        return f"Job `{job_id}` removed ✅" if affected else f"No job found with ID `{job_id}`"

    async def update_job(self, job_id: str, patch: dict) -> str:
        conn = sqlite3.connect(str(self.cfg.cron_db))
        row = conn.execute(
            "SELECT schedule, message, description FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not row:
            conn.close()
            return f"No job found with ID `{job_id}`"

        schedule, message, description = row
        new_schedule = patch.get("schedule", schedule)
        new_message = patch.get("message", message)
        new_description = patch.get("description", description)

        # Validate new schedule if changed
        if "schedule" in patch:
            try:
                self._parse_trigger(new_schedule)
            except Exception as e:
                conn.close()
                return f"Invalid schedule '{new_schedule}': {e}"

        conn.execute(
            "UPDATE jobs SET schedule=?, message=?, description=? WHERE id=?",
            (new_schedule, new_message, new_description, job_id),
        )
        conn.commit()
        conn.close()

        # Reschedule if scheduler is running
        if self.scheduler.running:
            try:
                self.scheduler.remove_job(job_id)
            except Exception:
                pass
            self._schedule(job_id, new_schedule, new_message)

        return f"Job `{job_id}` updated ✅"

    async def run_job_now(self, job_id: str) -> str:
        conn = sqlite3.connect(str(self.cfg.cron_db))
        row = conn.execute("SELECT message FROM jobs WHERE id=?", (job_id,)).fetchone()
        conn.close()
        if not row:
            return f"No job found with ID `{job_id}`"
        message = row[0]
        await self._fire(job_id, message)
        return f"Job `{job_id}` triggered manually ✅"

    async def set_job_enabled(self, job_id: str, enabled: bool) -> str:
        conn = sqlite3.connect(str(self.cfg.cron_db))
        row = conn.execute("SELECT schedule, message FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            conn.close()
            return f"No job found with ID `{job_id}`"
        conn.execute("UPDATE jobs SET enabled=? WHERE id=?", (1 if enabled else 0, job_id))
        conn.commit()
        conn.close()

        if enabled:
            schedule, message = row
            self._schedule(job_id, schedule, message)
            return f"Job `{job_id}` enabled ✅"
        else:
            try:
                self.scheduler.remove_job(job_id)
            except Exception:
                pass
            return f"Job `{job_id}` disabled ⏸️"

    async def wake(self, text: str = "") -> str:
        """Trigger an immediate heartbeat-style agent run."""
        if not self._agent_fn or not self._send_fn:
            return "Agent/send not configured for wake"
        wake_message = text or "Wake event triggered. Check HEARTBEAT.md tasks."
        try:
            reply = await self._agent_fn(f"[Wake event] {wake_message}")
            await self._send_fn(reply)
            return "Wake triggered ✅"
        except Exception as e:
            return f"Wake failed: {e}"

    def status(self) -> str:
        running = self.scheduler.running
        conn = sqlite3.connect(str(self.cfg.cron_db))
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM jobs WHERE enabled=1").fetchone()[0]
        conn.close()
        sched_jobs = self.scheduler.get_jobs()
        next_runs = []
        for j in sorted(sched_jobs, key=lambda x: x.next_run_time or datetime.max)[:5]:
            nrt = str(j.next_run_time)[:19] if j.next_run_time else "N/A"
            next_runs.append(f"  `{j.id}`: {nrt}")
        next_section = "\n".join(next_runs) if next_runs else "  (none scheduled)"
        return (
            f"Scheduler: {'✅ running' if running else '❌ stopped'}\n"
            f"Jobs: {active} active / {total} total\n"
            f"Upcoming:\n{next_section}"
        )
