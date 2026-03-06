"""
Nightly memory summarization — mirrors OpenClaw's memory metabolism pattern.

Runs on a cron schedule (default: 23:30 every night).

What it does:
  1. Reads today's daily log from the configured memory directory: YYYY-MM-DD.md
  2. Reads current MEMORY.md (for dedup context)
  3. Asks the agent to distil new durable facts into MEMORY.md
  4. Suppresses user-visible output (NO_REPLY)

This is the "memory metabolism" layer OpenClaw uses to prevent MEMORY.md
from going stale and ensure every day's conversations contribute to long-term
memory even if the context window never hit the compaction threshold.

Config:
  NIGHTLY_MEMORY_ENABLED=true         (default: true)
  NIGHTLY_MEMORY_SCHEDULE=30 23 * * * (default: 23:30 every night)
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from agent.workspace import _is_effectively_empty
from config import get_config
from tools.memory_tool import _get_memory_dir, _get_memory_md_path

logger = logging.getLogger(__name__)

AgentFn = Callable[[str, str], Awaitable[str]]

# ── Prompts ────────────────────────────────────────────────────────────────────

NIGHTLY_SYSTEM_PROMPT = """\
Nightly memory consolidation turn.
Your job: read today's daily log and update MEMORY.md with any new durable facts.
Rules:
- APPEND to MEMORY.md — never overwrite or delete existing content.
- Only add facts that are genuinely durable: preferences, decisions, important context,
  recurring patterns, things the user explicitly said to remember.
- Skip: routine chats, one-off tasks, anything that won't matter next week.
- Keep entries concise — one short sentence per fact.
- If today's log has nothing worth keeping, reply with NO_REPLY.
- Do not send any user-visible message — this is a background maintenance turn.
"""

NIGHTLY_USER_PROMPT_TEMPLATE = """\
Nightly memory consolidation.

Today's daily log ({date}):
---
{daily_log}
---

Current MEMORY.md (for reference — do not duplicate existing entries):
---
{memory_md}
---

Review the daily log. Append any new durable facts to MEMORY.md using the write tool.
Use the path: memory/{date}.md for reference only; write updates to MEMORY.md.
If there is nothing new worth keeping, reply with NO_REPLY.
"""


class NightlyMemoryRunner:
    """Runs nightly memory consolidation on a cron schedule."""

    def __init__(self) -> None:
        self.cfg = get_config()
        self._agent_fn: AgentFn | None = None
        self._scheduler: AsyncIOScheduler | None = None

    def configure(self, agent_fn: AgentFn) -> None:
        self._agent_fn = agent_fn

    def start(self, scheduler: AsyncIOScheduler) -> None:
        """Attach nightly job to an existing APScheduler instance."""
        if not getattr(self.cfg, "nightly_memory_enabled", True):
            logger.info("Nightly memory consolidation disabled (NIGHTLY_MEMORY_ENABLED=false)")
            return

        schedule = getattr(self.cfg, "nightly_memory_schedule", "30 23 * * *")
        if not schedule:
            logger.info("Nightly memory consolidation disabled (empty schedule)")
            return

        try:
            trigger = CronTrigger.from_crontab(schedule)
            scheduler.add_job(
                self._fire,
                trigger=trigger,
                id="__nightly_memory__",
                replace_existing=True,
            )
            logger.info("Nightly memory consolidation scheduled: %s", schedule)
        except Exception as e:
            logger.error("Failed to schedule nightly memory job: %s", e)

    async def _fire(self) -> None:
        """Run the nightly consolidation turn."""
        logger.info("Nightly memory consolidation firing")

        today_str = date.today().isoformat()
        daily_path = _get_memory_dir() / f"{today_str}.md"
        memory_path = _get_memory_md_path()

        # ── 1. Read today's daily log ──────────────────────────────────────────
        daily_log = _safe_read(daily_path)
        if not daily_log or _is_effectively_empty(daily_log):
            logger.info("Nightly memory: no daily log for %s — skipping", today_str)
            return

        # ── 2. Read current MEMORY.md ──────────────────────────────────────────
        memory_md = _safe_read(memory_path) or "(empty — no MEMORY.md yet)"

        # ── 3. Build prompt ────────────────────────────────────────────────────
        prompt = NIGHTLY_USER_PROMPT_TEMPLATE.format(
            date=today_str,
            daily_log=_truncate(daily_log, 12_000),
            memory_md=_truncate(memory_md, 6_000),
        )

        # ── 4. Run agent (isolated, no history, no user-visible reply) ─────────
        if not self._agent_fn:
            logger.warning("Nightly memory: no agent_fn configured — skipping")
            return

        try:
            reply = await self._agent_fn(prompt, NIGHTLY_SYSTEM_PROMPT)
            no_reply_token = getattr(self.cfg, "silent_reply_token", "NO_REPLY")
            heartbeat_ok = getattr(self.cfg, "heartbeat_ok_token", "HEARTBEAT_OK")
            if reply and reply.strip() not in (no_reply_token, heartbeat_ok):
                # Agent sent a message instead of writing via tool — log it but don't forward
                logger.info(
                    "Nightly memory: agent replied with text (%d chars) — suppressed",
                    len(reply),
                )
            logger.info("Nightly memory consolidation complete for %s", today_str)
        except Exception as e:
            logger.error("Nightly memory consolidation failed: %s", e, exc_info=True)


# ── Module-level singleton ─────────────────────────────────────────────────────

_runner: NightlyMemoryRunner | None = None


def get_nightly_memory_runner() -> NightlyMemoryRunner:
    global _runner
    if _runner is None:
        _runner = NightlyMemoryRunner()
    return _runner


# ── Helpers ────────────────────────────────────────────────────────────────────

def _safe_read(path: Path) -> str | None:
    try:
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.warning("Nightly memory: could not read %s: %s", path, e)
        return None


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + f"\n\n[... {len(text) - max_chars} chars truncated ...]\n\n" + text[-half:]
