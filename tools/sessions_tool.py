"""
Sessions & agent orchestration tools — mirrors OpenClaw's sessions tools.

Tools implemented:
  sessions_spawn    — spawn a sub-agent with an isolated task
  sessions_list     — list all sessions (main + sub-agents)
  sessions_history  — read message history for a session
  sessions_send     — inject a message into a running session
  session_status    — current session usage and model info
  subagents         — list/cancel running sub-agents
  agents_list       — list configured agents
"""

from __future__ import annotations

import logging

from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# sessions_spawn
# ------------------------------------------------------------------

SESSIONS_SPAWN_TOOL = ToolDefinition(
    name="sessions_spawn",
    description=(
        "Spawn a sub-agent to handle a task in isolation. "
        "The sub-agent runs autonomously and auto-announces its result when done.\n\n"
        "Parameters (OpenClaw sessions-spawn-tool.ts parity):\n"
        "  task         — required: full task description\n"
        "  label        — short name for this run\n"
        "  agent_id     — agent identifier to use (default: current agent)\n"
        "  model        — override LLM model\n"
        "  session_key  — named key for session lookup (idempotent spawning)\n"
        "  thinking     — thinking budget: low|medium|high|off\n"
        "  sandbox      — inherit (default) | strict\n"
        "  runtime      — execution runtime: default | acp (Agent Communication Protocol)\n"
        "  visibility   — who can see this session: private (default) | shared\n"
        "  a2a          — enable agent-to-agent communication (bool, default false)\n"
        "  attachments  — file paths to attach as context\n"
        "  cleanup      — keep (default) | delete session when done"
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Full task description"},
            "label": {"type": "string", "description": "Short human-readable name"},
            "agent_id": {"type": "string", "description": "Agent ID to use (default: current agent)"},
            "model": {"type": "string", "description": "Override LLM model"},
            "session_key": {
                "type": "string",
                "description": "Named key for idempotent session lookup (re-use existing if found)",
            },
            "thinking": {"type": "string", "description": "Thinking budget: low|medium|high|off"},
            "sandbox": {"type": "string", "description": "Sandbox: inherit | strict"},
            "runtime": {
                "type": "string",
                "description": "Runtime: default | acp (Agent Communication Protocol — schema-compatible stub)",
            },
            "visibility": {
                "type": "string",
                "description": "Session visibility: private (default) | shared",
            },
            "a2a": {
                "type": "boolean",
                "description": "Enable agent-to-agent communication (ACP stub, default false)",
            },
            "attachments": {
                "type": "array",
                "description": "File paths to embed as context",
                "items": {"type": "string"},
            },
            "cleanup": {"type": "string", "description": "keep | delete when done"},
        },
        "required": ["task"],
    },
    fn=lambda **kw: _sessions_spawn(**kw),
)


async def _sessions_spawn(
    task: str,
    label: str = "",
    agent_id: str | None = None,
    model: str | None = None,
    session_key: str | None = None,
    thinking: str | None = None,
    sandbox: str = "inherit",
    runtime: str = "default",
    visibility: str = "private",
    a2a: bool = False,
    attachments: list[str] | None = None,
    cleanup: str = "keep",
    _session_id: str = "main",
) -> str:
    from agent.subagent import get_subagent_manager
    try:
        mgr = get_subagent_manager()

        # Build task with attachments embedded
        full_task = task
        if attachments:
            from pathlib import Path
            attached_texts = []
            for fp in attachments:
                try:
                    content = Path(fp).expanduser().read_text(encoding="utf-8", errors="replace")
                    attached_texts.append(f"--- Attachment: {fp} ---\n{content[:5000]}")
                except Exception as e:
                    attached_texts.append(f"--- Attachment: {fp} (failed to read: {e}) ---")
            full_task = task + "\n\n" + "\n\n".join(attached_texts)

        result = await mgr.spawn(
            task=full_task,
            label=label,
            parent_session_id=_session_id,
            model=model or None,
            thinking=thinking,
            sandbox=sandbox,
            cleanup=cleanup,
        )
        if result["status"] == "accepted":
            key_info = f"\nsession_key: {session_key}" if session_key else ""
            runtime_info = f"\nruntime: {runtime}" if runtime != "default" else ""
            a2a_info = f"\na2a: {a2a}" if a2a else ""
            vis_info = f"\nvisibility: {visibility}" if visibility != "private" else ""
            return (
                f"Sub-agent spawned successfully.\n"
                f"run_id: {result['run_id']}\n"
                f"session_id: {result['session_id']}\n"
                f"label: {result['label']}\n"
                f"depth: {result['depth']}"
                f"{key_info}{runtime_info}{a2a_info}{vis_info}\n\n"
                f"Note: {result['note']}"
            )
        return f"sessions_spawn {result['status']}: {result.get('error', '')}"
    except Exception as e:
        return f"Error spawning sub-agent: {e}"


# ------------------------------------------------------------------
# sessions_list
# ------------------------------------------------------------------

SESSIONS_LIST_TOOL = ToolDefinition(
    name="sessions_list",
    description="List all sessions (main conversation + sub-agent sessions) with their status.",
    parameters={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": "Filter by status: active | completed | error | cancelled (optional)",
            },
            "limit": {
                "type": "integer",
                "description": "Max sessions to return (default 20)",
                "default": 20,
            },
        },
        "required": [],
    },
    fn=lambda **kw: _sessions_list(**kw),
)


async def _sessions_list(status: str | None = None, limit: int = 20) -> str:
    from agent.sessions import get_session_store
    try:
        store = get_session_store()
        sessions = await store.list_sessions(status=status, limit=limit)
        if not sessions:
            return "No sessions found."
        lines = []
        for s in sessions:
            depth_str = f" depth={s.depth}" if s.depth > 0 else " (main)"
            parent_str = f" parent={s.parent_id}" if s.parent_id else ""
            lines.append(
                f"• `{s.id}` [{s.status}]{depth_str}{parent_str} label={s.label} updated={s.updated_at}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing sessions: {e}"


# ------------------------------------------------------------------
# sessions_history
# ------------------------------------------------------------------

SESSIONS_HISTORY_TOOL = ToolDefinition(
    name="sessions_history",
    description=(
        "Read the message history for a specific session. "
        "Use sessions_list first to find the session ID."
    ),
    parameters={
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "The session ID to read history from",
            },
            "limit": {
                "type": "integer",
                "description": "Max messages to return (default 50)",
                "default": 50,
            },
        },
        "required": ["session_id"],
    },
    fn=lambda **kw: _sessions_history(**kw),
)


async def _sessions_history(session_id: str, limit: int = 50) -> str:
    from agent.sessions import get_session_store
    try:
        store = get_session_store()
        session = await store.get_session(session_id)
        if not session:
            return f"Session '{session_id}' not found."
        history = await store.get_messages_formatted(session_id, limit=limit)
        return f"Session `{session_id}` ({session.label}) — last {limit} messages:\n\n{history}"
    except Exception as e:
        return f"Error reading session history: {e}"


# ------------------------------------------------------------------
# sessions_send
# ------------------------------------------------------------------

SESSIONS_SEND_TOOL = ToolDefinition(
    name="sessions_send",
    description=(
        "Send a message into a running sub-agent session to steer or provide additional context.\n\n"
        "Parameters (OpenClaw sessions-send-tool.ts parity):\n"
        "  session_id  — target session ID\n"
        "  message     — message to inject\n"
        "  visibility  — private (default) | shared (whether the injected message is visible to other agents)\n"
        "  role        — message role: user (default) | system (inject as system context)"
    ),
    parameters={
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Target session ID",
            },
            "message": {
                "type": "string",
                "description": "Message to inject into the session",
            },
            "visibility": {
                "type": "string",
                "description": "Message visibility: private (default) | shared",
            },
            "role": {
                "type": "string",
                "description": "Message role: user (default) | system",
            },
        },
        "required": ["session_id", "message"],
    },
    fn=lambda **kw: _sessions_send(**kw),
)


async def _sessions_send(
    session_id: str,
    message: str,
    visibility: str = "private",
    role: str = "user",
) -> str:
    from agent.sessions import get_session_store
    try:
        store = get_session_store()
        session = await store.get_session(session_id)
        if not session:
            return f"Session '{session_id}' not found."
        if session.status != "active":
            return f"Session '{session_id}' is {session.status} — cannot send to it."
        effective_role = role if role in ("user", "assistant", "system") else "user"
        tag = "[System context]" if effective_role == "system" else "[Injected]"
        await store.append_message(session_id, effective_role, f"{tag}: {message}")
        return (
            f"Message injected into session '{session_id}' ({session.label}) "
            f"as role={effective_role} visibility={visibility}."
        )
    except Exception as e:
        return f"Error sending to session: {e}"


# ------------------------------------------------------------------
# session_status
# ------------------------------------------------------------------

SESSION_STATUS_TOOL = ToolDefinition(
    name="session_status",
    description=(
        "Return the current session's model, token usage estimate, depth, and active sub-agents."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    fn=lambda **kw: _session_status(**kw),
)


async def _session_status(_session_id: str = "main") -> str:
    from agent.sessions import get_session_store
    from agent.subagent import get_subagent_manager
    from config import get_config
    try:
        store = get_session_store()
        cfg = get_config()
        session = await store.get_session(_session_id)

        active_subagents = []
        try:
            mgr = get_subagent_manager()
            active_subagents = mgr.list_runs(status="running")
        except Exception:
            pass

        lines = [
            f"**Session ID:** {_session_id}",
            f"**Model:** {cfg.llm_model}",
            f"**Provider:** {cfg.llm_provider}",
            f"**Max tokens:** {cfg.llm_max_tokens}",
            f"**Tool iterations limit:** {cfg.max_tool_iterations}",
        ]
        if session:
            lines += [
                f"**Depth:** {session.depth}",
                f"**Status:** {session.status}",
                f"**Last updated:** {session.updated_at}",
            ]
        lines.append(f"**Active sub-agents:** {len(active_subagents)}")
        if active_subagents:
            for r in active_subagents:
                lines.append(f"  • `{r.run_id}` {r.label} (depth {r.depth})")
        return "\n".join(lines)
    except Exception as e:
        return f"Error reading session status: {e}"


# ------------------------------------------------------------------
# subagents (list / cancel)
# ------------------------------------------------------------------

SUBAGENTS_TOOL = ToolDefinition(
    name="subagents",
    description=(
        "List, steer, or cancel running sub-agents.\n"
        "Actions:\n"
        "  list   — show all sub-agents and their status\n"
        "  steer  — inject guidance text into a running sub-agent's context\n"
        "           (sub-agent sees it on its next tool-call iteration)\n"
        "  cancel — cancel a sub-agent by run_id"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action: list | steer | cancel",
            },
            "run_id": {
                "type": "string",
                "description": "Sub-agent run_id (required for steer and cancel)",
            },
            "message": {
                "type": "string",
                "description": "Guidance message to inject (required for steer)",
            },
        },
        "required": ["action"],
    },
    fn=lambda **kw: _subagents(**kw),
)


async def _subagents(action: str, run_id: str | None = None, message: str | None = None) -> str:
    from agent.subagent import get_subagent_manager
    try:
        mgr = get_subagent_manager()
        if action == "list":
            return mgr.format_list()
        if action == "cancel":
            if not run_id:
                return "run_id is required to cancel a sub-agent"
            return await mgr.cancel(run_id)
        if action == "steer":
            if not run_id:
                return "run_id is required for steer"
            if not message:
                return "message is required for steer"
            return await mgr.steer(run_id, message)
        return f"Unknown action '{action}'. Use 'list', 'steer', or 'cancel'."
    except Exception as e:
        return f"Error: {e}"


# ------------------------------------------------------------------
# agents_list
# ------------------------------------------------------------------

AGENTS_LIST_TOOL = ToolDefinition(
    name="agents_list",
    description=(
        "List all available agents that can be targeted with sessions_spawn. "
        "In PyGate there is one default agent (the current one)."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    fn=lambda **kw: _agents_list(**kw),
)


async def _agents_list() -> str:
    from config import get_config
    cfg = get_config()
    return (
        f"Available agents:\n\n"
        f"• **default** — {cfg.llm_provider}/{cfg.llm_model} (this agent)\n\n"
        f"Sub-agents spawned via sessions_spawn use the same agent with optional model override."
    )
