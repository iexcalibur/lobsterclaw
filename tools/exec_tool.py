"""
Shell execution tools — mirrors OpenClaw's exec and process tools.

exec    — run a foreground shell command, return stdout+stderr
process — manage background shell processes (start, list, kill, status, poll)
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

from config import get_config
from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

# Registry of background processes keyed by pid
@dataclass
class BgProcess:
    pid: int
    command: str
    proc: asyncio.subprocess.Process
    output_lines: list[str] = field(default_factory=list)
    returncode: int | None = None


_bg_processes: dict[int, BgProcess] = {}

# ------------------------------------------------------------------
# exec tool
# ------------------------------------------------------------------

TOOL_DEFINITION = ToolDefinition(
    name="exec",
    description=(
        "Run a shell command and return its output. "
        "Use for system operations, scripts, package installs, etc. "
        "Requires EXEC_ENABLED=true in .env."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run"},
            "cwd": {"type": "string", "description": "Working directory (optional, default ~)"},
            "timeout": {"type": "integer", "description": "Max seconds to wait (default 30)"},
        },
        "required": ["command"],
    },
    fn=lambda **kw: _exec(**kw),
)

# ------------------------------------------------------------------
# process tool
# ------------------------------------------------------------------

PROCESS_TOOL_DEFINITION = ToolDefinition(
    name="process",
    description=(
        "Manage background processes.\n"
        "Actions:\n"
        "  start  — start a background process, returns pid\n"
        "  list   — list all background processes and their status\n"
        "  poll   — read recent output from a process by pid\n"
        "  kill   — kill a background process by pid\n"
        "  status — show status of a specific process by pid"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action: start | list | poll | kill | status",
            },
            "command": {
                "type": "string",
                "description": "Command to run (required for start)",
            },
            "pid": {
                "type": "integer",
                "description": "Process PID (required for poll/kill/status)",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory (optional, for start)",
            },
        },
        "required": ["action"],
    },
    fn=lambda **kw: _process(**kw),
)

# ------------------------------------------------------------------
# Implementations
# ------------------------------------------------------------------

async def _exec(
    command: str,
    cwd: str | None = None,
    timeout: int = 30,
) -> str:
    cfg = get_config()
    if not cfg.exec_enabled:
        return "Error: exec is disabled (set EXEC_ENABLED=true in .env)"

    work_dir = os.path.expanduser(cwd or cfg.exec_working_dir or "~")
    effective_timeout = min(timeout, cfg.exec_timeout_seconds)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=work_dir,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=effective_timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return f"Command timed out after {effective_timeout}s: {command}"

        output = stdout.decode(errors="replace")
        return f"Exit code: {proc.returncode}\n{output}" if output else f"Exit code: {proc.returncode}\n(no output)"
    except FileNotFoundError:
        return f"Error: working directory not found: {work_dir}"
    except Exception as e:
        return f"Error running command: {e}"


async def _process(
    action: str,
    command: str | None = None,
    pid: int | None = None,
    cwd: str | None = None,
) -> str:
    cfg = get_config()

    action = action.lower().strip()

    if action == "list":
        if not _bg_processes:
            return "No background processes."
        lines = []
        for p_pid, bgp in list(_bg_processes.items()):
            # Check if still running
            if bgp.proc.returncode is None:
                try:
                    bgp.proc.returncode = bgp.proc.poll()
                except Exception:
                    pass
            status = "running" if bgp.proc.returncode is None else f"exited({bgp.proc.returncode})"
            lines.append(f"PID {p_pid} [{status}]: {bgp.command}")
        return "\n".join(lines)

    if action == "start":
        if not cfg.exec_enabled:
            return "Error: exec is disabled (set EXEC_ENABLED=true in .env)"
        if not command:
            return "Error: command is required for start action"
        work_dir = os.path.expanduser(cwd or cfg.exec_working_dir or "~")
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=work_dir,
            )
            bgp = BgProcess(pid=proc.pid, command=command, proc=proc)
            _bg_processes[proc.pid] = bgp
            # Start background reader
            asyncio.create_task(_read_output(bgp))
            return f"Background process started. PID: {proc.pid}"
        except Exception as e:
            return f"Error starting background process: {e}"

    if action == "poll":
        if pid is None:
            return "Error: pid is required for poll action"
        bgp = _bg_processes.get(pid)
        if not bgp:
            return f"No background process with PID {pid}"
        output = "\n".join(bgp.output_lines[-50:])  # last 50 lines
        bgp.output_lines.clear()
        return f"Output from PID {pid}:\n{output}" if output else f"No new output from PID {pid}"

    if action == "kill":
        if not cfg.exec_enabled:
            return "Error: exec is disabled"
        if pid is None:
            return "Error: pid is required for kill action"
        bgp = _bg_processes.get(pid)
        if not bgp:
            return f"No background process with PID {pid}"
        try:
            bgp.proc.kill()
            _bg_processes.pop(pid, None)
            return f"Process PID {pid} killed ✅"
        except Exception as e:
            return f"Error killing PID {pid}: {e}"

    if action == "status":
        if pid is None:
            return "Error: pid is required for status action"
        bgp = _bg_processes.get(pid)
        if not bgp:
            return f"No background process with PID {pid}"
        returncode = bgp.proc.returncode
        status = "running" if returncode is None else f"exited({returncode})"
        lines_buffered = len(bgp.output_lines)
        return (
            f"PID {pid} [{status}]\n"
            f"Command: {bgp.command}\n"
            f"Buffered output lines: {lines_buffered}"
        )

    return f"Unknown action '{action}'. Use: start, list, poll, kill, status"


async def _read_output(bgp: BgProcess) -> None:
    """Background task to collect output lines from a bg process."""
    try:
        if bgp.proc.stdout:
            async for line in bgp.proc.stdout:
                bgp.output_lines.append(line.decode(errors="replace").rstrip())
                if len(bgp.output_lines) > 1000:
                    bgp.output_lines = bgp.output_lines[-500:]
        await bgp.proc.wait()
        bgp.returncode = bgp.proc.returncode
    except Exception as e:
        logger.debug("Background reader error for PID %s: %s", bgp.pid, e)
