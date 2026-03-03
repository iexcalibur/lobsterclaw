"""
Sub-agent system — mirrors OpenClaw's subagent-spawn.ts + subagent-registry.ts

Architecture:
  - The main agent calls sessions_spawn(task=..., label=...) as a tool
  - SubagentManager spawns an isolated AgentLoop in a background asyncio task
  - The sub-agent runs with its own session history (depth tracking)
  - On completion, the result is auto-announced back to the parent via Telegram
  - Depth and concurrency limits are enforced before spawning

Limits (matching OpenClaw defaults):
  MAX_SPAWN_DEPTH    = 3   (main → child → grandchild max)
  MAX_CHILDREN       = 5   (per parent session)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from agent.sessions import (
    MAX_CHILDREN_PER_SESSION,
    MAX_SESSION_DEPTH,
    SessionRecord,
    get_session_store,
)
from config import get_config

logger = logging.getLogger(__name__)

SendFn = Callable[[str], Awaitable[None]]


@dataclass
class SubagentRun:
    run_id: str
    session_id: str
    label: str
    task: str
    parent_session_id: str
    depth: int
    status: str = "running"   # running | completed | error | cancelled
    result: str | None = None
    task_handle: asyncio.Task | None = field(default=None, repr=False)


class SubagentManager:
    """
    Manages sub-agent lifecycle: spawn, track, announce completion.
    A single shared instance is wired in main.py.
    """

    def __init__(self) -> None:
        self._registry: dict[str, SubagentRun] = {}   # run_id → SubagentRun
        self._send_fn: SendFn | None = None
        self._agent_loop_factory: Callable | None = None

    def configure(
        self,
        send_fn: SendFn,
        agent_loop_factory: Callable,
    ) -> None:
        """
        agent_loop_factory(registry) → AgentLoop  (called to create fresh loops)
        """
        self._send_fn = send_fn
        self._agent_loop_factory = agent_loop_factory

    # ------------------------------------------------------------------
    # Spawn
    # ------------------------------------------------------------------

    async def spawn(
        self,
        task: str,
        label: str = "",
        parent_session_id: str = "main",
        model: str | None = None,
    ) -> dict:
        """
        Spawn a sub-agent for the given task. Returns immediately with run info.
        The sub-agent runs in the background and auto-announces when done.
        """
        store = get_session_store()
        cfg = get_config()

        # Depth check
        parent = await store.get_session(parent_session_id)
        parent_depth = parent.depth if parent else 0
        child_depth = parent_depth + 1
        if child_depth > MAX_SESSION_DEPTH:
            return {
                "status": "forbidden",
                "error": f"sessions_spawn not allowed at depth {child_depth} (max: {MAX_SESSION_DEPTH})",
            }

        # Concurrency check
        active_children = await store.count_active_children(parent_session_id)
        if active_children >= MAX_CHILDREN_PER_SESSION:
            return {
                "status": "forbidden",
                "error": (
                    f"Max active sub-agents reached ({active_children}/{MAX_CHILDREN_PER_SESSION}). "
                    f"Wait for existing sub-agents to complete before spawning more."
                ),
            }

        # Create session record
        child_label = label or f"subagent-{child_depth}"
        child_model = model or cfg.llm_model
        session = await store.create_session(
            label=child_label,
            parent_id=parent_session_id,
            model=child_model,
            depth=child_depth,
        )

        run_id = str(uuid.uuid4())[:8]
        run = SubagentRun(
            run_id=run_id,
            session_id=session.id,
            label=child_label,
            task=task,
            parent_session_id=parent_session_id,
            depth=child_depth,
        )
        self._registry[run_id] = run

        # Launch in background
        task_coro = self._run_subagent(run, session, child_model)
        run.task_handle = asyncio.create_task(task_coro, name=f"subagent-{run_id}")

        logger.info(
            "Spawned sub-agent run_id=%s session=%s label=%s depth=%d",
            run_id, session.id, child_label, child_depth,
        )
        return {
            "status": "accepted",
            "run_id": run_id,
            "session_id": session.id,
            "label": child_label,
            "depth": child_depth,
            "note": (
                "Sub-agent is running in background. "
                "Results will be auto-announced when complete. "
                "Do NOT poll or sleep — continue your response."
            ),
        }

    async def _run_subagent(
        self,
        run: SubagentRun,
        session: SessionRecord,
        model: str,
    ) -> None:
        """Background task: run the sub-agent, update registry, announce result."""
        store = get_session_store()

        if not self._agent_loop_factory:
            await store.update_session_status(session.id, "error", error="SubagentManager not configured")
            return

        try:
            from agent.prompt import build_system_prompt
            from tools.registry import ToolRegistry

            cfg = get_config()
            registry: ToolRegistry = self._agent_loop_factory()

            # Subagent system prompt includes context about its role
            system = build_system_prompt(cfg, registry.get_names()) + (
                f"\n\n[Subagent Context] You are a sub-agent (depth {run.depth}/{MAX_SESSION_DEPTH}) "
                f"spawned by the main session to complete a specific task. "
                f"Complete the task and return a clear, concise result. "
                f"Do not ask follow-up questions — complete the task autonomously."
            )

            messages = [{"role": "user", "content": f"[Subagent Task]: {run.task}"}]

            from agent.loop import AgentLoop
            agent = AgentLoop(registry)
            result = await agent.run(messages, system, session_id=session.id)

            run.status = "completed"
            run.result = result
            await store.update_session_status(session.id, "completed")
            await store.append_message(session.id, "user", run.task)
            await store.append_message(session.id, "assistant", result)

            logger.info("Sub-agent run_id=%s completed", run.run_id)

            # Auto-announce result to main Telegram channel
            if self._send_fn:
                label_str = f" *{run.label}*" if run.label else ""
                announce = (
                    f"✅ Sub-agent{label_str} completed:\n\n{result}"
                )
                await self._send_fn(announce)

        except Exception as e:
            logger.exception("Sub-agent run_id=%s failed", run.run_id)
            run.status = "error"
            run.result = str(e)
            await store.update_session_status(session.id, "error", error=str(e))
            if self._send_fn:
                label_str = f" *{run.label}*" if run.label else ""
                await self._send_fn(f"❌ Sub-agent{label_str} failed: {e}")

    # ------------------------------------------------------------------
    # Registry queries
    # ------------------------------------------------------------------

    def list_runs(self, status: str | None = None) -> list[SubagentRun]:
        runs = list(self._registry.values())
        if status:
            runs = [r for r in runs if r.status == status]
        return runs

    def get_run(self, run_id: str) -> SubagentRun | None:
        return self._registry.get(run_id)

    async def cancel(self, run_id: str) -> str:
        run = self._registry.get(run_id)
        if not run:
            return f"No sub-agent found with run_id '{run_id}'"
        if run.status != "running":
            return f"Sub-agent '{run_id}' is not running (status: {run.status})"
        if run.task_handle and not run.task_handle.done():
            run.task_handle.cancel()
        run.status = "cancelled"
        store = get_session_store()
        await store.update_session_status(run.session_id, "cancelled")
        logger.info("Cancelled sub-agent run_id=%s", run_id)
        return f"Sub-agent '{run_id}' ({run.label}) cancelled."

    def format_list(self) -> str:
        runs = self.list_runs()
        if not runs:
            return "No sub-agents in this session."
        lines = []
        for r in runs:
            lines.append(
                f"• `{r.run_id}` [{r.status}] depth={r.depth} label={r.label}"
            )
        return "\n".join(lines)


# Global instance (set by main.py)
_manager: SubagentManager | None = None


def set_subagent_manager(manager: SubagentManager) -> None:
    global _manager
    _manager = manager


def get_subagent_manager() -> SubagentManager:
    if _manager is None:
        raise RuntimeError("SubagentManager not initialized — call set_subagent_manager() first")
    return _manager
