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
                id              TEXT PRIMARY KEY,
                description     TEXT NOT NULL,
                schedule        TEXT NOT NULL,
                message         TEXT NOT NULL,
                enabled         INTEGER DEFAULT 1,
                created_at      TEXT NOT NULL,
                run_count       INTEGER DEFAULT 0,
                last_run        TEXT,
                session_target  TEXT DEFAULT 'main',
                delivery        TEXT DEFAULT 'agent',
                delete_after_run INTEGER DEFAULT 0
            )
        """)
        # Migrate: add delete_after_run column if it doesn't exist (for existing DBs)
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN delete_after_run INTEGER DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_runs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id      TEXT NOT NULL,
                fired_at    TEXT NOT NULL,
                status      TEXT NOT NULL,
                error       TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_job ON job_runs(job_id, fired_at DESC)")
        conn.commit()
        conn.close()

    def _restore_jobs(self) -> None:
        conn = sqlite3.connect(str(self.cfg.cron_db))
        rows = conn.execute(
            "SELECT id, schedule, message FROM jobs WHERE enabled=1"
        ).fetchall()
        now_dt = datetime.now()
        conn.close()
        restored = 0
        stale_ids: list[str] = []
        for job_id, schedule, message in rows:
            # Skip (and clean up) ISO datetime one-shots whose fire time has passed.
            # These are stale rows from a crash between fire and remove_job().
            if re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", schedule.strip()):
                try:
                    fire_dt = datetime.fromisoformat(schedule.strip())
                    if fire_dt <= now_dt:
                        logger.info(
                            "Skipping stale one-shot job %s (was due %s, now %s) — disabling",
                            job_id, schedule, now_dt.isoformat(timespec="seconds"),
                        )
                        stale_ids.append(job_id)
                        continue
                except ValueError:
                    pass  # unparseable — let _schedule() handle/reject it
            try:
                self._schedule(job_id, schedule, message)
                restored += 1
            except Exception as e:
                logger.warning("Could not restore job %s: %s", job_id, e)
        # Disable stale one-shot rows so they don't re-fire on next restart
        if stale_ids:
            conn2 = sqlite3.connect(str(self.cfg.cron_db))
            for sid in stale_ids:
                conn2.execute("UPDATE jobs SET enabled=0 WHERE id=?", (sid,))
            conn2.commit()
            conn2.close()
            logger.info("Disabled %d stale one-shot job(s) on restore", len(stale_ids))
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

    async def _get_recent_context(self, n: int = 5) -> str:
        """Fetch N most recent messages from the main session history for context."""
        try:
            from agent.sessions import get_session_store
            store = get_session_store()
            messages = await store.get_messages("main", limit=n)
            if not messages:
                return ""
            lines = []
            for msg in messages[-n:]:
                role = msg["role"].upper()
                content = msg["content"]
                if len(content) > 200:
                    content = content[:200] + "..."
                lines.append(f"[{role}]: {content}")
            return "Recent conversation context:\n" + "\n".join(lines)
        except Exception:
            return ""

    async def _fire(self, job_id: str, message: str) -> None:
        logger.info("Firing cron job: %s", job_id)
        conn = sqlite3.connect(str(self.cfg.cron_db))
        fired_at = datetime.now().isoformat(timespec="seconds")
        # Fetch session_target, delivery, delete_after_run alongside update
        conn.execute(
            "UPDATE jobs SET run_count=run_count+1, last_run=? WHERE id=?",
            (fired_at, job_id),
        )
        row = conn.execute(
            "SELECT session_target, delivery, delete_after_run FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        conn.commit()
        conn.close()

        session_target = (row[0] if row else None) or "main"
        delivery = (row[1] if row else None) or "agent"
        delete_after_run = bool(row[2]) if row and len(row) > 2 else False

        if not self._send_fn:
            logger.warning("No send_fn — cannot deliver cron job %s", job_id)
            self._record_run(job_id, fired_at, "skipped", "No send_fn")
            await self._publish_cron_event(job_id, message, "skipped", "No send function configured")
            return

        _ran_ok = False  # tracks whether this run completed successfully

        try:
            outbound: str | None = None

            if delivery == "direct":
                outbound = f"⏰ **Reminder**\n\n{message}"
            elif session_target == "isolated":
                if self._agent_fn:
                    import asyncio
                    asyncio.create_task(
                        self._fire_isolated(job_id, fired_at, message)
                    )
                    return
                else:
                    outbound = f"⏰ **Reminder** (isolated mode unavailable)\n\n{message}"
            else:
                if self._agent_fn:
                    context = await self._get_recent_context(5)
                    prompt = f"[Scheduled reminder — deliver this to the user] {message}"
                    if context:
                        prompt = f"{context}\n\n{prompt}"

                    logger.info("Cron %s: running agent for delivery...", job_id)
                    reply = await self._agent_fn(prompt)
                    logger.info("Cron %s: agent replied (%d chars)", job_id, len(reply) if reply else 0)

                    silent_token = "NO_REPLY"
                    heartbeat_ok = "HEARTBEAT_OK"
                    if reply and reply.strip() in (silent_token, heartbeat_ok):
                        reply = f"⏰ Reminder: {message}"

                    outbound = reply if reply and reply.strip() else f"⏰ Reminder: {message}"
                else:
                    outbound = f"⏰ **Reminder**\n\n{message}"

            if outbound:
                logger.info("Cron %s: sending message to Telegram (%d chars)...", job_id, len(outbound))
                await self._send_fn(outbound)
                logger.info("Cron %s: Telegram send completed", job_id)
                _ran_ok = True
                self._record_run(job_id, fired_at, "ok")
                await self._publish_cron_event(job_id, message, "ok", outbound)

        except Exception as e:
            logger.error("Cron job %s fire failed: %s", job_id, e, exc_info=True)
            self._record_run(job_id, fired_at, "error", str(e))
            await self._publish_cron_event(job_id, message, "error", str(e))
            try:
                if self._send_fn:
                    await self._send_fn(f"⏰ Reminder (agent unavailable): {message}")
            except Exception:
                pass
        finally:
            # deleteAfterRun: remove ONLY on successful execution.
            # If the job errored, keep it in DB so it can be inspected/retried.
            if delete_after_run and _ran_ok:
                try:
                    self.remove_job(job_id)
                    logger.info("deleteAfterRun: removed job %s after successful run", job_id)
                except Exception as e:
                    logger.warning("deleteAfterRun cleanup failed for %s: %s", job_id, e)

    async def _fire_isolated(self, job_id: str, fired_at: str, message: str) -> None:
        """Spawn an isolated sub-agent for this cron job run."""
        # Fetch delete_after_run for this job
        delete_after = False
        try:
            conn = sqlite3.connect(str(self.cfg.cron_db))
            row = conn.execute("SELECT delete_after_run FROM jobs WHERE id=?", (job_id,)).fetchone()
            conn.close()
            delete_after = bool(row[0]) if row else False
        except Exception:
            pass

        _isolated_ok = False
        try:
            from agent.subagent import get_subagent_manager
            mgr = get_subagent_manager()
            await mgr.spawn(
                task=f"[Isolated cron job] {message}",
                label=f"cron-{job_id}",
                parent_session_id="main",
            )
            _isolated_ok = True
            self._record_run(job_id, fired_at, "ok")
        except Exception as e:
            logger.error("Isolated cron job %s failed: %s", job_id, e)
            self._record_run(job_id, fired_at, "error", str(e))
        finally:
            # Only remove on success
            if delete_after and _isolated_ok:
                try:
                    self.remove_job(job_id)
                    logger.info("deleteAfterRun: removed isolated job %s after successful run", job_id)
                except Exception as e:
                    logger.warning("deleteAfterRun cleanup failed for %s: %s", job_id, e)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def add_job(
        self,
        schedule: str,
        message: str,
        description: str = "",
        session_target: str = "main",
        delivery: str = "agent",
        enabled: bool = True,
        delete_after_run: bool = False,
    ) -> str:
        # Validate schedule before writing to DB
        try:
            self._parse_trigger(schedule)
        except Exception as e:
            return f"Invalid schedule '{schedule}': {e}"

        # Validate session_target
        if session_target not in ("main", "isolated"):
            return "Error: session_target must be 'main' or 'isolated'"
        # delivery: agent | direct | webhook (webhook stored but not yet wired)
        if delivery not in ("agent", "direct", "webhook"):
            delivery = "agent"  # graceful fallback

        # One-shot jobs (ISO datetime → DateTrigger) must always be deleted after
        # their single run. APScheduler removes the DateTrigger from memory after
        # firing, but the DB row would stay with enabled=1 and re-fire on restart
        # unless we force delete_after_run=True here.
        if re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", schedule.strip()):
            delete_after_run = True

        job_id = uuid.uuid4().hex[:8]
        now = datetime.now().isoformat(timespec="seconds")
        desc = description or message[:60]

        conn = sqlite3.connect(str(self.cfg.cron_db))
        conn.execute(
            "INSERT INTO jobs(id, description, schedule, message, created_at, session_target, delivery, enabled, delete_after_run) VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, desc, schedule, message, now, session_target, delivery, 1 if enabled else 0, 1 if delete_after_run else 0),
        )
        conn.commit()
        conn.close()

        if enabled:
            self._schedule(job_id, schedule, message)
        target_note = f"\nSession: {session_target} / delivery: {delivery}" if session_target != "main" or delivery != "agent" else ""
        enabled_note = " (disabled)" if not enabled else ""
        delete_note = "\nOne-shot: deletes after first run" if delete_after_run else ""
        return (
            f"Job scheduled ✅{enabled_note}\n"
            f"ID: `{job_id}`\n"
            f"Schedule: `{schedule}`\n"
            f"Message: {message}{target_note}{delete_note}"
        )

    def list_jobs(self, include_disabled: bool = False) -> str:
        conn = sqlite3.connect(str(self.cfg.cron_db))
        query = "SELECT id, description, schedule, message, enabled, run_count, last_run, session_target, delivery FROM jobs"
        if not include_disabled:
            query += " WHERE enabled=1"
        query += " ORDER BY created_at"
        rows = conn.execute(query).fetchall()
        conn.close()

        if not rows:
            return "No scheduled jobs."

        lines = ["**Scheduled Jobs:**\n"]
        for row in rows:
            job_id, description, schedule, message, enabled, run_count, last_run = row[:7]
            session_target = row[7] if len(row) > 7 else "main"
            delivery = row[8] if len(row) > 8 else "agent"
            _ = session_target  # used below
            __ = delivery
            status_icon = "✅" if enabled else "⏸️"
            sched_job = None
            try:
                sched_job = self.scheduler.get_job(job_id)
            except Exception:
                pass
            next_run = ""
            if sched_job and getattr(sched_job, "next_run_time", None):
                next_run = f"\n   Next run: {str(sched_job.next_run_time)[:19]}"
            target_str = ""
            if session_target != "main" or delivery != "agent":
                target_str = f"\n   Target: {session_target} / {delivery}"
            lines.append(
                f"{status_icon} `{job_id}` — {description}\n"
                f"   Schedule: `{schedule}`\n"
                f"   Message: {message}\n"
                f"   Runs: {run_count}{next_run}{target_str}"
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

    async def _publish_cron_event(self, job_id: str, message: str, status: str, detail: str | None = None) -> None:
        """Publish a cron fire event to the Gateway event bus (if available)."""
        try:
            from gateway.events import event_bus
            await event_bus.publish("cron.fired", {
                "job_id": job_id,
                "message": message,
                "status": status,
                "detail": detail,
            })
        except Exception:
            pass

    def _record_run(self, job_id: str, fired_at: str, status: str, error: str | None = None) -> None:
        try:
            conn = sqlite3.connect(str(self.cfg.cron_db))
            conn.execute(
                "INSERT INTO job_runs(job_id, fired_at, status, error) VALUES (?,?,?,?)",
                (job_id, fired_at, status, error),
            )
            # Keep only last 100 runs per job
            conn.execute(
                """DELETE FROM job_runs WHERE id IN (
                    SELECT id FROM job_runs WHERE job_id=?
                    ORDER BY fired_at DESC LIMIT -1 OFFSET 100
                )""",
                (job_id,),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug("Failed to record run: %s", e)

    def get_run_history(self, job_id: str, limit: int = 20) -> str:
        conn = sqlite3.connect(str(self.cfg.cron_db))
        # Verify job exists
        row = conn.execute("SELECT description FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            conn.close()
            return f"No job found with ID `{job_id}`"
        runs = conn.execute(
            "SELECT fired_at, status, error FROM job_runs WHERE job_id=? ORDER BY fired_at DESC LIMIT ?",
            (job_id, limit),
        ).fetchall()
        conn.close()

        if not runs:
            return f"No run history for job `{job_id}` yet."

        lines = [f"Run history for `{job_id}` ({row[0]}):"]
        for fired_at, status, error in runs:
            icon = "✅" if status == "ok" else ("⚠️" if status == "skipped" else "❌")
            err_str = f" — {error}" if error else ""
            lines.append(f"  {icon} {fired_at}{err_str}")
        return "\n".join(lines)

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
