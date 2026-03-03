from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from config import get_config
from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

TOOL_DEFINITION = ToolDefinition(
    name="exec",
    description="Run a shell command and return its output. Only available when EXEC_ENABLED=true.",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run"},
            "cwd": {"type": "string", "description": "Working directory (optional)"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)", "default": 30},
        },
        "required": ["command"],
    },
    fn=lambda **kw: _exec(**kw),
)

PROCESS_TOOL_DEFINITION = ToolDefinition(
    name="process",
    description="Run a shell command in the background and return its process ID.",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run in background"},
            "cwd": {"type": "string", "description": "Working directory (optional)"},
        },
        "required": ["command"],
    },
    fn=lambda **kw: _process(**kw),
)

# Tracks background processes: pid -> process
_bg_processes: dict[int, asyncio.subprocess.Process] = {}


async def _exec(command: str, cwd: str | None = None, timeout: int = 30) -> str:
    cfg = get_config()
    if not cfg.exec_enabled:
        return "Error: exec is disabled. Set EXEC_ENABLED=true in .env to enable."

    working_dir = Path(cwd or cfg.exec_working_dir).expanduser()

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=working_dir,
            env={**os.environ},
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return f"Error: command timed out after {timeout}s"

        output = stdout.decode("utf-8", errors="replace").strip()
        exit_code = proc.returncode
        result = output[:8000] if output else "(no output)"
        return f"Exit code: {exit_code}\n{result}"
    except Exception as e:
        logger.exception("exec failed")
        return f"Error: {e}"


async def _process(command: str, cwd: str | None = None) -> str:
    cfg = get_config()
    if not cfg.exec_enabled:
        return "Error: exec is disabled. Set EXEC_ENABLED=true in .env to enable."

    working_dir = Path(cwd or cfg.exec_working_dir).expanduser()

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=working_dir,
        )
        _bg_processes[proc.pid] = proc
        return f"Background process started with PID {proc.pid}"
    except Exception as e:
        return f"Error: {e}"
