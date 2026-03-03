"""
Shell execution tools — mirrors OpenClaw's exec and process tools.

exec field parity with OpenClaw bash-tools.exec-runtime.ts:
  command     — exact
  workdir     — OpenClaw field name; also 'cwd' (alias)
  timeout     — exact
  yieldMs     — OpenClaw field name; also 'yield_ms' (alias)
  env         — extra environment variables dict
  background  — run in background without yieldMs (returns pid immediately)
  pty         — allocate a pseudo-tty (stub: noted but not implemented on asyncio subprocess)
  elevated    — run with elevated privileges via sudo (stub, requires EXEC_ELEVATED_ENABLED)
  node        — target node name for remote exec (delegates to nodes_tool ssh exec)

process action parity with OpenClaw bash-tools.process.ts:
  Actions: start, list, poll, log, write, send-keys/send_keys, submit, paste, kill, clear, remove
  sessionId   — OpenClaw field; also 'pid' (alias)
  data        — OpenClaw field name for write; also 'text' (alias)
  keys/hex/literal/bracketed/eof — send-keys sub-params
  offset/limit  — for log action (OpenClaw pagination)
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
        "Run a shell command and return its output.\n\n"
        "Field parity with OpenClaw bash-tools.exec-runtime.ts:\n"
        "  command    — shell command to run\n"
        "  workdir    — working directory (also 'cwd')\n"
        "  timeout    — max seconds to wait (default 30)\n"
        "  yieldMs    — background mode: yield partial output after N ms (also 'yield_ms')\n"
        "  env        — extra environment variables dict\n"
        "  background — run in background without yieldMs (returns PID immediately)\n"
        "  pty        — allocate pseudo-tty (schema-present; asyncio subprocess limitation applies)\n"
        "  elevated   — run with sudo elevation (requires EXEC_ELEVATED_ENABLED=true)\n"
        "  node       — target node name for remote SSH exec (delegates to nodes tool)\n\n"
        "Requires EXEC_ENABLED=true in .env."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run"},
            "workdir": {
                "type": "string",
                "description": "Working directory — OpenClaw field name (also 'cwd')",
            },
            "cwd": {
                "type": "string",
                "description": "Alias for workdir",
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds to wait (default 30)",
                "default": 30,
            },
            "yieldMs": {
                "type": "integer",
                "description": "Background mode: return partial output after N ms — OpenClaw field (also 'yield_ms')",
                "default": 0,
            },
            "yield_ms": {
                "type": "integer",
                "description": "Alias for yieldMs",
                "default": 0,
            },
            "env": {
                "type": "object",
                "description": "Extra environment variables to set for this command",
                "additionalProperties": {"type": "string"},
            },
            "background": {
                "type": "boolean",
                "description": "Run in background and return PID immediately (no wait)",
                "default": False,
            },
            "pty": {
                "type": "boolean",
                "description": "Allocate pseudo-TTY (schema-present; best-effort on asyncio subprocess)",
                "default": False,
            },
            "elevated": {
                "type": "boolean",
                "description": "Run with sudo elevation (requires EXEC_ELEVATED_ENABLED=true in .env)",
                "default": False,
            },
            "node": {
                "type": "string",
                "description": "Target node name for remote SSH exec (delegates to nodes tool)",
            },
        },
        "required": ["command"],
    },
    fn=lambda **kw: _exec(**kw),
    owner_only=True,
)

# ------------------------------------------------------------------
# process tool
# ------------------------------------------------------------------

PROCESS_TOOL_DEFINITION = ToolDefinition(
    name="process",
    description=(
        "Manage background shell processes.\n\n"
        "Field parity with OpenClaw bash-tools.process.ts:\n"
        "  sessionId  — process identifier (also 'pid')\n"
        "  data       — text to write to stdin (also 'text')\n"
        "  keys       — keys string for send-keys (e.g. 'q', '\\n', 'C-c')\n"
        "  hex        — hex-encoded bytes for send-keys\n"
        "  literal    — send keys without interpretation\n"
        "  bracketed  — use bracketed paste mode\n"
        "  eof        — send EOF (Ctrl-D) to process\n"
        "  offset     — line offset for log pagination\n"
        "  limit      — line count for log (also 'lines')\n"
        "  timeout    — timeout for submit action\n\n"
        "Actions: start | list | poll | log | write | send-keys/send_keys |\n"
        "         submit | paste | kill | clear | remove | status"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action: start|list|poll|log|write|send-keys|submit|paste|kill|clear|remove|status",
            },
            "command": {"type": "string", "description": "Command to run (start action)"},
            "sessionId": {
                "type": "integer",
                "description": "Process ID — OpenClaw field name (also 'pid')",
            },
            "pid": {
                "type": "integer",
                "description": "Alias for sessionId",
            },
            "workdir": {"type": "string", "description": "Working directory (start action; also 'cwd')"},
            "cwd": {"type": "string", "description": "Alias for workdir"},
            "data": {
                "type": "string",
                "description": "Text to write to stdin — OpenClaw field name (also 'text')",
            },
            "text": {"type": "string", "description": "Alias for data"},
            # send-keys sub-params
            "keys": {
                "type": "string",
                "description": "Key sequence to send (e.g. 'q', 'Enter', 'C-c')",
            },
            "hex": {
                "type": "string",
                "description": "Hex-encoded bytes to send (e.g. '0a' for newline)",
            },
            "literal": {
                "type": "boolean",
                "description": "Send keys without tmux interpretation",
            },
            "bracketed": {
                "type": "boolean",
                "description": "Use bracketed paste mode",
            },
            "eof": {
                "type": "boolean",
                "description": "Send EOF (Ctrl-D) to stdin",
            },
            # log pagination
            "offset": {
                "type": "integer",
                "description": "Line offset for log pagination (default 0)",
                "default": 0,
            },
            "limit": {
                "type": "integer",
                "description": "Max lines to return for log (also 'lines', default 50)",
                "default": 50,
            },
            "lines": {
                "type": "integer",
                "description": "Alias for limit",
                "default": 50,
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout for submit action (seconds, default 10)",
                "default": 10,
            },
        },
        "required": ["action"],
    },
    fn=lambda **kw: _process(**kw),
    owner_only=True,
)


# ------------------------------------------------------------------
# exec implementation
# ------------------------------------------------------------------

async def _exec(
    command: str,
    workdir: str | None = None,      # OpenClaw field name
    cwd: str | None = None,           # alias
    timeout: int = 30,
    yieldMs: int = 0,                 # OpenClaw field name
    yield_ms: int = 0,                # alias
    env: dict | None = None,
    background: bool = False,
    pty: bool = False,
    elevated: bool = False,
    node: str | None = None,
) -> str:
    cfg = get_config()
    if not cfg.exec_enabled:
        return "Error: exec is disabled (set EXEC_ENABLED=true in .env)"

    # Resolve aliases
    effective_cwd = workdir or cwd or cfg.exec_working_dir or "~"
    effective_yield_ms = yieldMs if yieldMs > 0 else yield_ms
    work_dir = os.path.expanduser(effective_cwd)
    effective_timeout = min(timeout, cfg.exec_timeout_seconds)

    # Remote node delegation
    if node:
        from tools.nodes_tool import _nodes
        return await _nodes(action="exec", node=node, command=command, timeout=effective_timeout)

    # Sudo elevation
    if elevated:
        if not getattr(cfg, "exec_elevated_enabled", False):
            return "Error: elevated exec requires EXEC_ELEVATED_ENABLED=true in .env"
        command = f"sudo {command}"

    # Build subprocess env
    proc_env = {**os.environ}
    if env:
        proc_env.update(env)

    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.PIPE,
        cwd=work_dir,
        env=proc_env,
    )

    # Background (immediate): no wait at all
    if background:
        bgp = BgProcess(pid=proc.pid, command=command, proc=proc, cwd=work_dir)
        _bg_processes[proc.pid] = bgp
        asyncio.create_task(_read_output(bgp))
        return f"Background process started. PID: {proc.pid}"

    # yieldMs: wait N ms then return partial
    if effective_yield_ms > 0:
        bgp = BgProcess(pid=proc.pid, command=command, proc=proc, cwd=work_dir)
        _bg_processes[proc.pid] = bgp
        asyncio.create_task(_read_output(bgp))
        await asyncio.sleep(effective_yield_ms / 1000)
        partial = "\n".join(bgp.output_lines[-100:])
        bgp.output_lines.clear()
        status = "running" if proc.returncode is None else f"exited({proc.returncode})"
        return (
            f"Background process started (PID: {proc.pid}, status: {status})\n"
            f"Partial output ({effective_yield_ms}ms):\n{partial if partial else '(no output yet)'}\n\n"
            f"Use process poll/log pid={proc.pid} for more output."
        )

    # Foreground
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
    sessionId: int | None = None,    # OpenClaw field name
    pid: int | None = None,           # alias
    workdir: str | None = None,       # OpenClaw field name
    cwd: str | None = None,           # alias
    data: str | None = None,          # OpenClaw field name
    text: str | None = None,          # alias
    keys: str | None = None,
    hex: str | None = None,
    literal: bool = False,
    bracketed: bool = False,
    eof: bool = False,
    offset: int = 0,
    limit: int = 50,
    lines: int = 50,
    timeout: int = 10,
) -> str:
    cfg = get_config()
    # Normalise action name (OpenClaw uses send-keys with hyphen)
    action = action.lower().strip().replace("-", "_")

    # Resolve aliases
    effective_pid = sessionId or pid
    effective_cwd = workdir or cwd
    effective_text = data or text
    effective_lines = limit if limit != 50 else lines

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
        work_dir = os.path.expanduser(effective_cwd or cfg.exec_working_dir or "~")
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
        if effective_pid is None:
            return "Error: 'sessionId' (or 'pid') required for poll"
        bgp = _bg_processes.get(effective_pid)
        if not bgp:
            return f"No process with PID {effective_pid}"
        output = "\n".join(bgp.output_lines[-200:])
        bgp.output_lines.clear()
        returncode = bgp.proc.returncode
        status = "running" if returncode is None else f"exited({returncode})"
        return f"[PID {effective_pid} — {status}]\n{output}" if output else f"[PID {effective_pid} — {status}]\n(no new output)"

    if action == "log":
        if effective_pid is None:
            return "Error: 'sessionId' (or 'pid') required for log"
        bgp = _bg_processes.get(effective_pid)
        if not bgp:
            return f"No process with PID {effective_pid}"
        all_lines = bgp.output_lines
        page = all_lines[offset:offset + effective_lines]
        returncode = bgp.proc.returncode
        status = "running" if returncode is None else f"exited({returncode})"
        output = "\n".join(page)
        total = len(all_lines)
        return (
            f"[PID {effective_pid} — {status}] lines {offset}–{offset+len(page)} of {total}:\n"
            f"{output if output else '(no output yet)'}"
        )

    if action == "kill":
        if not cfg.exec_enabled:
            return "Error: exec is disabled"
        if effective_pid is None:
            return "Error: 'sessionId' (or 'pid') required for kill"
        bgp = _bg_processes.get(effective_pid)
        if not bgp:
            return f"No process with PID {effective_pid}"
        try:
            bgp.proc.kill()
            return f"Process PID {effective_pid} killed ✅"
        except Exception as e:
            return f"Error killing PID {effective_pid}: {e}"

    if action == "status":
        if effective_pid is None:
            return "Error: 'sessionId' (or 'pid') required for status"
        bgp = _bg_processes.get(effective_pid)
        if not bgp:
            return f"No process with PID {effective_pid}"
        returncode = bgp.proc.returncode
        status = "running" if returncode is None else f"exited({returncode})"
        return (
            f"PID {effective_pid} [{status}]\n"
            f"Command: {bgp.command}\n"
            f"CWD: {bgp.cwd}\n"
            f"Buffered lines: {len(bgp.output_lines)}"
        )

    if action == "write":
        if effective_pid is None:
            return "Error: 'sessionId' (or 'pid') required for write"
        if effective_text is None:
            return "Error: 'data' (or 'text') required for write"
        bgp = _bg_processes.get(effective_pid)
        if not bgp:
            return f"No process with PID {effective_pid}"
        if bgp.proc.stdin is None or bgp.proc.returncode is not None:
            return f"Process PID {effective_pid} is not running or has no stdin"
        try:
            bgp.proc.stdin.write(effective_text.encode())
            await bgp.proc.stdin.drain()
            return f"Wrote {len(effective_text)} bytes to PID {effective_pid} stdin"
        except Exception as e:
            return f"Write error: {e}"

    if action == "send_keys":
        if effective_pid is None:
            return "Error: 'sessionId' (or 'pid') required for send_keys"
        bgp = _bg_processes.get(effective_pid)
        if not bgp:
            return f"No process with PID {effective_pid}"
        if bgp.proc.stdin is None or bgp.proc.returncode is not None:
            return f"Process PID {effective_pid} is not running or has no stdin"
        try:
            # Resolve key source: hex > keys > data/text
            if hex:
                import binascii
                payload = binascii.unhexlify(hex)
            elif eof:
                payload = b"\x04"  # Ctrl-D
            else:
                raw = keys or effective_text or ""
                if not literal:
                    raw = (raw.replace("\\n", "\n").replace("\\r", "\r")
                              .replace("\\t", "\t").replace("C-c", "\x03")
                              .replace("Enter", "\n"))
                payload = raw.encode()
            bgp.proc.stdin.write(payload)
            await bgp.proc.stdin.drain()
            return f"Sent keys to PID {effective_pid} ✅ ({len(payload)} bytes)"
        except Exception as e:
            return f"send_keys error: {e}"

    if action == "submit":
        # submit = write text + newline + wait for output
        if effective_pid is None:
            return "Error: 'sessionId' (or 'pid') required for submit"
        if effective_text is None:
            return "Error: 'data' (or 'text') required for submit"
        bgp = _bg_processes.get(effective_pid)
        if not bgp:
            return f"No process with PID {effective_pid}"
        if bgp.proc.stdin is None or bgp.proc.returncode is not None:
            return f"Process PID {effective_pid} is not running"
        try:
            bgp.proc.stdin.write((effective_text + "\n").encode())
            await bgp.proc.stdin.drain()
            await asyncio.sleep(min(timeout, 5))
            output = "\n".join(bgp.output_lines[-50:])
            bgp.output_lines.clear()
            return f"Submitted to PID {effective_pid}. Response:\n{output if output else '(no output yet)'}"
        except Exception as e:
            return f"submit error: {e}"

    if action == "paste":
        # paste = bracketed paste mode write
        if effective_pid is None:
            return "Error: 'sessionId' (or 'pid') required for paste"
        if effective_text is None:
            return "Error: 'data' (or 'text') required for paste"
        bgp = _bg_processes.get(effective_pid)
        if not bgp:
            return f"No process with PID {effective_pid}"
        if bgp.proc.stdin is None or bgp.proc.returncode is not None:
            return f"Process PID {effective_pid} is not running"
        try:
            # Bracketed paste: ESC[200~ text ESC[201~
            payload = b"\x1b[200~" + effective_text.encode() + b"\x1b[201~"
            bgp.proc.stdin.write(payload)
            await bgp.proc.stdin.drain()
            return f"Pasted {len(effective_text)} chars to PID {effective_pid} ✅"
        except Exception as e:
            return f"paste error: {e}"

    if action == "clear":
        if effective_pid is None:
            return "Error: 'sessionId' (or 'pid') required for clear"
        bgp = _bg_processes.get(effective_pid)
        if not bgp:
            return f"No process with PID {effective_pid}"
        cleared = len(bgp.output_lines)
        bgp.output_lines.clear()
        return f"Cleared {cleared} buffered lines from PID {effective_pid} ✅"

    if action == "remove":
        if effective_pid is None:
            return "Error: 'sessionId' (or 'pid') required for remove"
        bgp = _bg_processes.get(effective_pid)
        if not bgp:
            return f"No process with PID {effective_pid}"
        if bgp.proc.returncode is None:
            return f"Process PID {effective_pid} is still running. Kill it first."
        _bg_processes.pop(effective_pid, None)
        return f"Process PID {effective_pid} removed ✅"

    return (
        f"Unknown action '{action}'. Use: "
        "start, list, poll, log, write, send-keys, submit, paste, kill, clear, remove, status"
    )


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
