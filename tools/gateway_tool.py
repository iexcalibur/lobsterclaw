"""
Gateway tool — mirrors OpenClaw's gateway tool.

Actions:
  restart      — restart the PyGate process (sends SIGTERM to self)
  config.get   — read current .env config (redacted)
  config.set   — update a config value in .env
  update.run   — pull latest code from git and restart
  status       — show process status and uptime
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

_start_time = datetime.now()

TOOL_DEFINITION = ToolDefinition(
    name="gateway",
    description=(
        "Control the PyGate process itself.\n"
        "Actions:\n"
        "  status      — show uptime and process info\n"
        "  restart     — restart the bot process\n"
        "  config.get  — view current configuration (sensitive values redacted)\n"
        "  config.set  — set a config value in .env (key=value format, requires restart)\n"
        "  update.run  — git pull latest code and restart"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action: status | restart | config.get | config.set | update.run",
            },
            "key": {
                "type": "string",
                "description": "Config key to set (for config.set action)",
            },
            "value": {
                "type": "string",
                "description": "Config value to set (for config.set action)",
            },
        },
        "required": ["action"],
    },
    fn=lambda **kw: _gateway(**kw),
)


async def _gateway(action: str, key: str | None = None, value: str | None = None) -> str:
    action = action.lower().strip()

    if action == "status":
        uptime = datetime.now() - _start_time
        hours, rem = divmod(int(uptime.total_seconds()), 3600)
        mins, secs = divmod(rem, 60)
        return (
            f"PyGate status:\n"
            f"PID: {os.getpid()}\n"
            f"Python: {sys.version.split()[0]}\n"
            f"Uptime: {hours}h {mins}m {secs}s\n"
            f"Started: {_start_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    if action == "restart":
        logger.info("Gateway restart requested by agent")
        # Schedule restart after returning response
        import asyncio
        loop = asyncio.get_running_loop()
        loop.call_later(1.0, lambda: os.kill(os.getpid(), signal.SIGTERM))
        return "PyGate restart scheduled in 1 second... ✅"

    if action == "config.get":
        from config import get_config
        cfg = get_config()
        # Show config with sensitive values redacted
        _SENSITIVE = {"api_key", "token", "secret", "password", "otp"}
        lines = []
        for field_name, field_val in vars(cfg).items():
            display_val = str(field_val)
            if any(s in field_name.lower() for s in _SENSITIVE):
                display_val = "***" if field_val else "(not set)"
            lines.append(f"{field_name} = {display_val}")
        return "\n".join(lines)

    if action == "config.set":
        if not key or value is None:
            return "Error: 'key' and 'value' are required for config.set"
        env_path = Path(".env")
        if not env_path.exists():
            return "Error: .env file not found"
        content = env_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        found = False
        new_lines = []
        for line in lines:
            if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
                new_lines.append(f"{key}={value}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{key}={value}")
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return f"Config updated: {key}={value}\nRestart required to apply (use gateway restart)."

    if action == "update.run":
        import asyncio
        try:
            proc = await asyncio.create_subprocess_shell(
                "git pull --rebase origin HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            output = stdout.decode(errors="replace").strip()
            if proc.returncode == 0:
                # Schedule restart
                loop = asyncio.get_running_loop()
                loop.call_later(2.0, lambda: os.kill(os.getpid(), signal.SIGTERM))
                return f"Update successful:\n{output}\n\nRestarting in 2 seconds... ✅"
            return f"Update failed (exit {proc.returncode}):\n{output}"
        except asyncio.TimeoutError:
            return "Update timed out after 60 seconds"
        except Exception as e:
            return f"Update error: {e}"

    return f"Unknown action '{action}'. Use: status, restart, config.get, config.set, update.run"
