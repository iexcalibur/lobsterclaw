"""
Shell execution tools — mirrors OpenClaw's exec and process tools.

exec    — foreground shell command with output capture
process — background processes with full lifecycle management
          Actions: start, list, poll, log, kill, status, write, send_keys, remove

Key additions vs OpenClaw:
  - yieldMs: background exec that fires after N ms (partial parity for yieldMs pattern)
  - write / send_keys: send stdin to a background process
  - log: get buffered output lines without clearing buffer
  - remove: remove a terminated process from the registry
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

from config import get_config
from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)


@dataclass
class BgProcess:
    pid: int
    command: str
    proc: asyncio.subprocess.Process
    output_lines: list[str] = field(default_factory=list)
    returncode: int | None = None
    cwd: str = ""


_bg_processes: dict[int, BgProcess] = {}

# ------------------------------------------------------------------
# exec tool
# ------------------------------------------------------------------

TOOL_DEFINITION = ToolDefinition(
    name="exec",
    description=(
        "Run a shell command and return its output. "
        "Set yield_ms>0 to run in background and get the PID back after yield_ms milliseconds "
        "(partial output returned, process continues). "
        "Requires EXEC_ENABLED=true in .env."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run"},
            "cwd": {"type": "string", "description": "Working directory (optional, default ~)"},
            "timeout": {"type": "integer", "description": "Max seconds to wait (default 30)"},
            "yield_ms": {
                "type": "integer",
                "description": (
                    "If set, run in background. Return partial output after this many milliseconds "
                    "and the PID. Use process poll/log to get more output later. (default 0 = foreground)"
                ),
                "default": 0,
            },
        },
        "required": ["command"],
    },
    fn=lambda **kw: _exec(**kw),
    owner_only=True,  # Shell execution is owner-only (blocked in sub-agents by default)
)

# ------------------------------------------------------------------
# process tool
# ------------------------------------------------------------------

PROCESS_TOOL_DEFINITION = ToolDefinition(
    name="process",
    description=(
        "Manage background shell processes.\n"
        "Actions:\n"
        "  start     — start a background process, returns pid\n"
        "  list      — list all background processes and their status\n"
        "  poll      — read and clear recent output lines from a process\n"
        "  log       — read recent output without clearing (last N lines)\n"
        "  kill      — send SIGKILL to a process by pid\n"
        "  status    — show status of a specific process by pid\n"
        "  write     — write text to a process's stdin\n"
        "  send_keys — send keystrokes to a process (e.g. '\\n' for Enter)\n"
        "  remove    — remove a terminated process from the registry"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action: start | list | poll | log | kill | status | write | send_keys | remove",
            },
            "command": {"type": "string", "description": "Command to run (required for start)"},
            "pid": {"type": "integer", "description": "Process PID (required for poll/log/kill/status/write/send_keys/remove)"},
            "cwd": {"type": "string", "description": "Working directory (optional, for start)"},
            "text": {"type": "string", "description": "Text to write to stdin (for write/send_keys)"},
            "lines": {"type": "integer", "description": "Number of lines to return for log action (default 50)"},
        },
        "required": ["action"],
    },
    fn=lambda **kw: _process(**kw),
    owner_only=True,  # Process management is owner-only (blocked in sub-agents by default)
)


# ------------------------------------------------------------------
# exec implementation
# ------------------------------------------------------------------

async def _exec(
    command: str,
    cwd: str | None = None,
    timeout: int = 30,
    yield_ms: int = 0,
) -> str:
    cfg = get_config()
    if not cfg.exec_enabled:
        return "Error: exec is disabled (set EXEC_ENABLED=true in .env)"

    work_dir = os.path.expanduser(cwd or cfg.exec_working_dir or "~")
    effective_timeout = min(timeout, cfg.exec_timeout_seconds)

    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.PIPE,
        cwd=work_dir,
    )

    if yield_ms > 0:
        # Background mode: return partial output after yield_ms, register process
        bgp = BgProcess(pid=proc.pid, command=command, proc=proc, cwd=work_dir)
        _bg_processes[proc.pid] = bgp
        asyncio.create_task(_read_output(bgp))

        await asyncio.sleep(yield_ms / 1000)

        # Grab whatever was captured so far
        partial = "\n".join(bgp.output_lines[-100:])
        bgp.output_lines.clear()
        status = "running" if proc.returncode is None else f"exited({proc.returncode})"
        return (
            f"Background process started (PID: {proc.pid}, status: {status})\n"
            f"Partial output ({yield_ms}ms):\n{partial if partial else '(no output yet)'}\n\n"
            f"Use process poll/log pid={proc.pid} for more output."
        )

    # Foreground mode
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=effective_timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return f"Command timed out after {effective_timeout}s: {command}"

    output = stdout.decode(errors="replace")
    return f"Exit code: {proc.returncode}\n{output}" if output else f"Exit code: {proc.returncode}\n(no output)"


# ------------------------------------------------------------------
# process implementation
# ------------------------------------------------------------------

async def _process(
    action: str,
    command: str | None = None,
    pid: int | None = None,
    cwd: str | None = None,
    text: str | None = None,
    lines: int = 50,
) -> str:
    cfg = get_config()
    action = action.lower().strip()

    if action == "list":
        if not _bg_processes:
            return "No background processes."
        result = []
        for p_pid, bgp in list(_bg_processes.items()):
            returncode = bgp.proc.returncode
            status = "running" if returncode is None else f"exited({returncode})"
            result.append(f"PID {p_pid} [{status}]: {bgp.command}")
        return "\n".join(result)

    if action == "start":
        if not cfg.exec_enabled:
            return "Error: exec is disabled (set EXEC_ENABLED=true in .env)"
        if not command:
            return "Error: 'command' required for start"
        work_dir = os.path.expanduser(cwd or cfg.exec_working_dir or "~")
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.PIPE,
            cwd=work_dir,
        )
        bgp = BgProcess(pid=proc.pid, command=command, proc=proc, cwd=work_dir)
        _bg_processes[proc.pid] = bgp
        asyncio.create_task(_read_output(bgp))
        return f"Background process started. PID: {proc.pid}"

    if action == "poll":
        if pid is None:
            return "Error: 'pid' required for poll"
        bgp = _bg_processes.get(pid)
        if not bgp:
            return f"No background process with PID {pid}"
        output = "\n".join(bgp.output_lines[-200:])
        bgp.output_lines.clear()
        returncode = bgp.proc.returncode
        status = "running" if returncode is None else f"exited({returncode})"
        return f"[PID {pid} — {status}]\n{output}" if output else f"[PID {pid} — {status}]\n(no new output)"

    if action == "log":
        if pid is None:
            return "Error: 'pid' required for log"
        bgp = _bg_processes.get(pid)
        if not bgp:
            return f"No background process with PID {pid}"
        tail = bgp.output_lines[-lines:]
        returncode = bgp.proc.returncode
        status = "running" if returncode is None else f"exited({returncode})"
        output = "\n".join(tail)
        return f"[PID {pid} — {status}] last {len(tail)} lines:\n{output}" if output else f"[PID {pid}] No output yet."

    if action == "kill":
        if not cfg.exec_enabled:
            return "Error: exec is disabled"
        if pid is None:
            return "Error: 'pid' required for kill"
        bgp = _bg_processes.get(pid)
        if not bgp:
            return f"No background process with PID {pid}"
        try:
            bgp.proc.kill()
            return f"Process PID {pid} killed ✅"
        except Exception as e:
            return f"Error killing PID {pid}: {e}"

    if action == "status":
        if pid is None:
            return "Error: 'pid' required for status"
        bgp = _bg_processes.get(pid)
        if not bgp:
            return f"No background process with PID {pid}"
        returncode = bgp.proc.returncode
        status = "running" if returncode is None else f"exited({returncode})"
        return (
            f"PID {pid} [{status}]\n"
            f"Command: {bgp.command}\n"
            f"CWD: {bgp.cwd}\n"
            f"Buffered output lines: {len(bgp.output_lines)}"
        )

    if action == "write":
        if pid is None:
            return "Error: 'pid' required for write"
        if text is None:
            return "Error: 'text' required for write"
        bgp = _bg_processes.get(pid)
        if not bgp:
            return f"No background process with PID {pid}"
        if bgp.proc.stdin is None or bgp.proc.returncode is not None:
            return f"Process PID {pid} is not running or has no stdin"
        try:
            bgp.proc.stdin.write(text.encode())
            await bgp.proc.stdin.drain()
            return f"Wrote {len(text)} bytes to PID {pid} stdin"
        except Exception as e:
            return f"Write error: {e}"

    if action == "send_keys":
        if pid is None:
            return "Error: 'pid' required for send_keys"
        if text is None:
            return "Error: 'text' required for send_keys (e.g. '\\n' for Enter, 'q' to quit)"
        bgp = _bg_processes.get(pid)
        if not bgp:
            return f"No background process with PID {pid}"
        if bgp.proc.stdin is None or bgp.proc.returncode is not None:
            return f"Process PID {pid} is not running or has no stdin"
        try:
            # Expand common escape sequences
            text_decoded = text.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")
            bgp.proc.stdin.write(text_decoded.encode())
            await bgp.proc.stdin.drain()
            return f"Sent keys to PID {pid} ✅"
        except Exception as e:
            return f"send_keys error: {e}"

    if action == "remove":
        if pid is None:
            return "Error: 'pid' required for remove"
        bgp = _bg_processes.get(pid)
        if not bgp:
            return f"No background process with PID {pid}"
        if bgp.proc.returncode is None:
            return f"Process PID {pid} is still running. Kill it first."
        _bg_processes.pop(pid, None)
        return f"Process PID {pid} removed from registry ✅"

    return f"Unknown action '{action}'. Use: start, list, poll, log, kill, status, write, send_keys, remove"


async def _read_output(bgp: BgProcess) -> None:
    """Background task: collect stdout/stderr from a bg process into ring buffer."""
    try:
        if bgp.proc.stdout:
            async for line in bgp.proc.stdout:
                bgp.output_lines.append(line.decode(errors="replace").rstrip())
                if len(bgp.output_lines) > 2000:
                    bgp.output_lines = bgp.output_lines[-1000:]
        await bgp.proc.wait()
        bgp.returncode = bgp.proc.returncode
    except Exception as e:
        logger.debug("Background reader error for PID %s: %s", bgp.pid, e)
