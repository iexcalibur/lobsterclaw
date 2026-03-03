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
        "Spawn a sub-agent to handle a task in isolation.\n\n"
        "Full field parity with OpenClaw sessions-spawn-tool.ts:\n"
        "  task                     — required: full task description\n"
        "  label                    — short name for this run\n"
        "  agentId / agent_id       — agent identifier\n"
        "  model                    — override LLM model\n"
        "  thinking                 — budget: low|medium|high|off\n"
        "  cwd                      — working directory for the sub-agent\n"
        "  runTimeoutSeconds / timeoutSeconds — max runtime in seconds\n"
        "  thread                   — attach to a thread/topic context\n"
        "  mode                     — execution mode (default: agent)\n"
        "  cleanup                  — keep (default) | delete when done\n"
        "  sandbox                  — inherit (default) | strict\n"
        "  runtime                  — default | acp\n"
        "  visibility               — private (default) | shared\n"
        "  a2a                      — agent-to-agent communication\n"
        "  session_key / sessionKey — idempotent session lookup key\n"
        "  attachments              — [{content, name}] objects or file paths"
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Full task description"},
            "label": {"type": "string", "description": "Short name"},
            "agentId": {"type": "string", "description": "Agent ID (also 'agent_id')"},
            "agent_id": {"type": "string", "description": "Alias for agentId"},
            "model": {"type": "string", "description": "Override LLM model"},
            "thinking": {"type": "string", "description": "Thinking budget: low|medium|high|off"},
            "cwd": {"type": "string", "description": "Working directory for sub-agent"},
            "runTimeoutSeconds": {
                "type": "integer",
                "description": "Max runtime seconds (also 'timeoutSeconds')",
            },
            "timeoutSeconds": {"type": "integer", "description": "Alias for runTimeoutSeconds"},
            "thread": {"type": "string", "description": "Thread/topic context identifier"},
            "mode": {"type": "string", "description": "Execution mode (default: agent)"},
            "cleanup": {"type": "string", "description": "keep | delete when done"},
            "sandbox": {"type": "string", "description": "inherit | strict"},
            "runtime": {"type": "string", "description": "default | acp"},
            "visibility": {"type": "string", "description": "private | shared"},
            "a2a": {"type": "boolean", "description": "Agent-to-agent communication"},
            "session_key": {"type": "string", "description": "Idempotent session key (also 'sessionKey')"},
            "sessionKey": {"type": "string", "description": "Alias for session_key"},
            "attachAs": {
                "type": "string",
                "description": (
                    "How to surface attachments in the sub-agent context:\n"
                    "  inline (default) — embed content directly in the task prompt\n"
                    "  files            — reference as file paths only\n"
                    "  omit             — ignore attachments"
                ),
            },
            "attachments": {
                "type": "array",
                "description": "Attachments: file paths (strings) or {content, name} objects",
                "items": {},
            },
        },
        "required": ["task"],
    },
    fn=lambda **kw: _sessions_spawn(**kw),
)


async def _sessions_spawn(
    task: str,
    label: str = "",
    agentId: str | None = None,           # OpenClaw field name
    agent_id: str | None = None,           # alias
    model: str | None = None,
    thinking: str | None = None,
    cwd: str | None = None,
    runTimeoutSeconds: int | None = None,  # OpenClaw field name
    timeoutSeconds: int | None = None,     # alias
    thread: str | None = None,
    mode: str = "agent",
    cleanup: str = "keep",
    sandbox: str = "inherit",
    runtime: str = "default",
    visibility: str = "private",
    a2a: bool = False,
    session_key: str | None = None,
    sessionKey: str | None = None,         # OpenClaw field name
    attachAs: str = "inline",              # inline | files | omit
    attachments=None,
    _session_id: str = "main",
) -> str:
    from agent.subagent import get_subagent_manager

    # Resolve aliases
    effective_agent_id = agentId or agent_id
    effective_timeout = runTimeoutSeconds or timeoutSeconds
    effective_session_key = session_key or sessionKey

    try:
        mgr = get_subagent_manager()

        # Build task with attachments embedded per attachAs mode
        full_task = task
        if attachments and attachAs != "omit":
            from pathlib import Path
            attached_texts = []
            for item in attachments:
                if isinstance(item, dict):
                    name = item.get("name", "attachment")
                    content = item.get("content", "")
                    if attachAs == "files":
                        # files mode: just reference the name, not content
                        attached_texts.append(f"[Attachment file: {name}]")
                    else:
                        attached_texts.append(f"--- Attachment: {name} ---\n{content[:5000]}")
                else:
                    if attachAs == "files":
                        attached_texts.append(f"[Attachment file: {item}]")
                    else:
                        try:
                            content = Path(str(item)).expanduser().read_text(encoding="utf-8", errors="replace")
                            attached_texts.append(f"--- Attachment: {item} ---\n{content[:5000]}")
                        except Exception as e:
                            attached_texts.append(f"--- Attachment: {item} (failed to read: {e}) ---")
            if attached_texts:
                full_task = task + "\n\n" + "\n\n".join(attached_texts)

        # Build extra context note
        extra_info: list[str] = []
        if cwd:
            extra_info.append(f"Working directory: {cwd}")
        if thread:
            extra_info.append(f"Thread: {thread}")
        if effective_timeout:
            extra_info.append(f"Timeout: {effective_timeout}s")
        if extra_info:
            full_task += "\n\n[Context: " + "; ".join(extra_info) + "]"

        # ACP harness: when runtime="acp", wrap the spawn with ACP metadata
        # so the sub-agent runs inside an ACP execution context.
        # This is a stub — the ACP wire protocol is routed through the session
        # inbox once the ACP harness is fully implemented.
        acp_metadata: dict | None = None
        if runtime == "acp":
            acp_metadata = {
                "runtime": "acp",
                "agentId": effective_agent_id or "default",
                "visibility": visibility,
                "a2a": a2a,
                "sessionKey": effective_session_key,
            }
            # Prepend ACP context note to the task
            full_task = (
                "[ACP runtime: agent-to-agent protocol harness]\n"
                + (f"[agentId: {effective_agent_id}]\n" if effective_agent_id else "")
                + full_task
            )
            logger.info(
                "sessions_spawn: ACP harness mode for task=%r agent=%s",
                task[:80],
                effective_agent_id,
            )

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
            lines = [
                "Sub-agent spawned successfully.",
                f"run_id: {result['run_id']}",
                f"session_id: {result['session_id']}",
                f"label: {result['label']}",
                f"depth: {result['depth']}",
            ]
            if effective_session_key:
                lines.append(f"session_key: {effective_session_key}")
            if runtime != "default":
                lines.append(f"runtime: {runtime}")
            if acp_metadata:
                lines.append("acp_harness: active (stub)")
                lines.append(f"acp_agent: {acp_metadata['agentId']}")
            if a2a:
                lines.append(f"a2a: {a2a}")
            if visibility != "private":
                lines.append(f"visibility: {visibility}")
            if effective_agent_id:
                lines.append(f"agent_id: {effective_agent_id}")
            lines.append(f"\n{result['note']}")
            return "\n".join(lines)
        return f"sessions_spawn {result['status']}: {result.get('error', '')}"
    except Exception as e:
        return f"Error spawning sub-agent: {e}"


# ------------------------------------------------------------------
# sessions_list
# ------------------------------------------------------------------

SESSIONS_LIST_TOOL = ToolDefinition(
    name="sessions_list",
    description=(
        "List all sessions (main + sub-agent sessions) with their status.\n\n"
        "Field parity with OpenClaw sessions-list-tool.ts:\n"
        "  kinds           — filter by session kinds (array: 'main'|'subagent'|'all')\n"
        "  activeMinutes   — only show sessions active within N minutes\n"
        "  messageLimit    — max recent messages to include per session\n"
        "  limit           — max sessions to return (default 20)"
    ),
    parameters={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": "Filter by status: active | completed | error | cancelled",
            },
            "kinds": {
                "type": "array",
                "description": "Filter by session kinds: ['main'] | ['subagent'] | ['main','subagent']",
                "items": {"type": "string"},
            },
            "activeMinutes": {
                "type": "integer",
                "description": "Only show sessions with activity in last N minutes",
            },
            "messageLimit": {
                "type": "integer",
                "description": "Max recent messages to include per session in response",
                "default": 0,
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


async def _sessions_list(
    status: str | None = None,
    kinds: list | None = None,
    activeMinutes: int | None = None,
    messageLimit: int = 0,
    limit: int = 20,
) -> str:
    from agent.sessions import get_session_store
    from datetime import datetime, timezone, timedelta
    try:
        store = get_session_store()
        sessions = await store.list_sessions(status=status, limit=limit)
        if not sessions:
            return "No sessions found."

        # Filter by kinds
        if kinds:
            filtered = []
            for s in sessions:
                is_main = (s.depth == 0)
                if "all" in kinds:
                    filtered.append(s)
                elif "main" in kinds and is_main:
                    filtered.append(s)
                elif "subagent" in kinds and not is_main:
                    filtered.append(s)
            sessions = filtered

        # Filter by activeMinutes
        if activeMinutes:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=activeMinutes)
            sessions = [s for s in sessions if s.updated_at >= cutoff.isoformat()[:19]]

        lines = []
        for s in sessions:
            depth_str = f" depth={s.depth}" if s.depth > 0 else " (main)"
            parent_str = f" parent={s.parent_id}" if s.parent_id else ""
            lines.append(
                f"• `{s.id}` [{s.status}]{depth_str}{parent_str} label={s.label} updated={s.updated_at}"
            )
        return "\n".join(lines) if lines else "No sessions matched the given filters."
    except Exception as e:
        return f"Error listing sessions: {e}"


# ------------------------------------------------------------------
# sessions_history
# ------------------------------------------------------------------

SESSIONS_HISTORY_TOOL = ToolDefinition(
    name="sessions_history",
    description=(
        "Read the message history for a specific session.\n\n"
        "Field parity with OpenClaw sessions-history-tool.ts:\n"
        "  sessionId / session_id — target session ID or session key\n"
        "  sessionKey             — look up by session key instead of ID\n"
        "  includeTools           — include tool calls in history (default false)\n"
        "  limit                  — max messages to return (default 50)"
    ),
    parameters={
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Session ID"},
            "sessionId": {"type": "string", "description": "Alias for session_id"},
            "sessionKey": {
                "type": "string",
                "description": "Named session key (alternative to session_id)",
            },
            "includeTools": {
                "type": "boolean",
                "description": "Include tool call/result messages in history (default false)",
                "default": False,
            },
            "limit": {
                "type": "integer",
                "description": "Max messages to return (default 50)",
                "default": 50,
            },
        },
        "required": [],
    },
    fn=lambda **kw: _sessions_history(**kw),
)


async def _sessions_history(
    session_id: str | None = None,
    sessionId: str | None = None,
    sessionKey: str | None = None,
    includeTools: bool = False,
    limit: int = 50,
) -> str:
    from agent.sessions import get_session_store
    effective_id = session_id or sessionId
    try:
        store = get_session_store()
        if not effective_id and sessionKey:
            sessions = await store.list_sessions(limit=100)
            matched = [s for s in sessions if getattr(s, "label", "") == sessionKey]
            if matched:
                effective_id = matched[0].id
        if not effective_id:
            return "Error: 'session_id' (or 'sessionId'/'sessionKey') is required"
        session = await store.get_session(effective_id)
        if not session:
            return f"Session '{effective_id}' not found."
        history = await store.get_messages_formatted(
            effective_id, limit=limit, include_tools=bool(includeTools)
        )
        return f"Session `{effective_id}` ({session.label}) — last {limit} messages:\n\n{history}"
    except Exception as e:
        return f"Error reading session history: {e}"


# ------------------------------------------------------------------
# sessions_send
# ------------------------------------------------------------------

SESSIONS_SEND_TOOL = ToolDefinition(
    name="sessions_send",
    description=(
        "Send a message into a running sub-agent session.\n\n"
        "Field parity with OpenClaw sessions-send-tool.ts:\n"
        "  sessionId / session_id — target session ID\n"
        "  sessionKey / label     — session key as alternative identifier\n"
        "  message                — message to inject\n"
        "  visibility             — private (default) | shared\n"
        "  role                   — user (default) | system\n"
        "  timeoutSeconds         — wait for response before returning (default 0 = no wait)"
    ),
    parameters={
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Target session ID"},
            "sessionId": {"type": "string", "description": "Alias for session_id"},
            "sessionKey": {"type": "string", "description": "Session key alternative"},
            "label": {"type": "string", "description": "Session label alternative"},
            "message": {"type": "string", "description": "Message to inject"},
            "visibility": {"type": "string", "description": "private | shared"},
            "role": {"type": "string", "description": "user | system"},
            "timeoutSeconds": {
                "type": "integer",
                "description": "Wait N seconds for response (default 0 = fire and forget)",
                "default": 0,
            },
        },
        "required": ["message"],
    },
    fn=lambda **kw: _sessions_send(**kw),
)


async def _sessions_send(
    message: str,
    session_id: str | None = None,
    sessionId: str | None = None,
    sessionKey: str | None = None,
    label: str | None = None,
    visibility: str = "private",
    role: str = "user",
    timeoutSeconds: int = 0,
) -> str:
    from agent.sessions import get_session_store
    effective_id = session_id or sessionId
    try:
        store = get_session_store()
        if not effective_id and (sessionKey or label):
            sessions = await store.list_sessions(limit=100)
            key = sessionKey or label
            matched = [s for s in sessions if getattr(s, "label", "") == key]
            if matched:
                effective_id = matched[0].id
        if not effective_id:
            return "Error: 'session_id' (or 'sessionId'/'sessionKey') is required"
        session = await store.get_session(effective_id)
        if not session:
            return f"Session '{effective_id}' not found."
        if session.status != "active":
            return f"Session '{effective_id}' is {session.status} — cannot send to it."
        effective_role = role if role in ("user", "assistant", "system") else "user"

        # If the target is a sub-agent session, route through SubagentManager inbox
        if session.depth > 0:
            try:
                from agent.subagent import get_subagent_manager
                mgr = get_subagent_manager()
                result = await mgr.send_to_run(effective_id, message, role=effective_role)
                return result
            except RuntimeError:
                pass  # SubagentManager not initialized — fall through to direct append

        tag = "[System]" if effective_role == "system" else "[Injected]"
        await store.append_message(effective_id, effective_role, f"{tag}: {message}")
        return (
            f"Message injected into session '{effective_id}' ({session.label}) "
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
        "Return session status: model, depth, active sub-agents, token estimate.\n\n"
        "Field parity with OpenClaw session-status-tool.ts:\n"
        "  sessionId / session_id — specific session (default: current)\n"
        "  sessionKey             — session key alternative\n"
        "  model                  — override model shown in status"
    ),
    parameters={
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Specific session ID (default: current)"},
            "sessionId": {"type": "string", "description": "Alias for session_id"},
            "sessionKey": {"type": "string", "description": "Session key alternative"},
            "model": {"type": "string", "description": "Override model name shown in output"},
        },
        "required": [],
    },
    fn=lambda **kw: _session_status(**kw),
)


async def _session_status(
    session_id: str | None = None,
    sessionId: str | None = None,
    sessionKey: str | None = None,
    model: str | None = None,
    _session_id: str = "main",
) -> str:
    effective_id = session_id or sessionId or _session_id
    from agent.sessions import get_session_store
    from agent.subagent import get_subagent_manager
    from config import get_config
    try:
        store = get_session_store()
        cfg = get_config()
        session = await store.get_session(effective_id)
        if not session and sessionKey:
            sessions = await store.list_sessions(limit=100)
            matched = [s for s in sessions if getattr(s, "label", "") == sessionKey]
            if matched:
                session = matched[0]
                effective_id = session.id

        active_subagents = []
        try:
            mgr = get_subagent_manager()
            active_subagents = mgr.list_runs(status="running")
        except Exception:
            pass

        display_model = model or cfg.llm_model
        lines = [
            f"**Session ID:** {effective_id}",
            f"**Model:** {display_model}",
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
        "List, steer, kill, or cancel running sub-agents.\n\n"
        "Field parity with OpenClaw subagents-tool.ts:\n"
        "  list   — show all sub-agents and their status\n"
        "           optional: target, recentMinutes filter\n"
        "  steer  — inject guidance into a running sub-agent\n"
        "  kill   — forcibly terminate a sub-agent (alias for cancel)\n"
        "  cancel — cancel a sub-agent by run_id"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action: list | steer | kill | cancel",
            },
            "run_id": {
                "type": "string",
                "description": "Sub-agent run_id (required for steer/kill/cancel)",
            },
            "message": {
                "type": "string",
                "description": "Guidance message to inject (required for steer)",
            },
            "target": {
                "type": "string",
                "description": "Filter by target/label for list action",
            },
            "recentMinutes": {
                "type": "integer",
                "description": "Only show sub-agents active in last N minutes (list action)",
            },
        },
        "required": ["action"],
    },
    fn=lambda **kw: _subagents(**kw),
)


async def _subagents(
    action: str,
    run_id: str | None = None,
    message: str | None = None,
    target: str | None = None,
    recentMinutes: int | None = None,
) -> str:
    from agent.subagent import get_subagent_manager
    try:
        mgr = get_subagent_manager()
        if action == "list":
            result = mgr.format_list()
            # Apply target filter
            if target:
                lines = [l for l in result.splitlines() if target in l or l.startswith("No ")]
                result = "\n".join(lines) if lines else f"No sub-agents matching target '{target}'"
            return result
        if action in ("cancel", "kill"):
            if not run_id:
                return "run_id is required to cancel/kill a sub-agent"
            return await mgr.cancel(run_id)
        if action == "steer":
            if not run_id:
                return "run_id is required for steer"
            if not message:
                return "message is required for steer"
            return await mgr.steer(run_id, message)
        return f"Unknown action '{action}'. Use 'list', 'steer', 'kill', or 'cancel'."
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
