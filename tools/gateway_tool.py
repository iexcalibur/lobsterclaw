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
        "  status         — show uptime and process info\n"
        "  restart        — restart the bot process\n"
        "  config.get     — view current configuration (sensitive values redacted)\n"
        "  config.schema  — show all available config keys and their types\n"
        "  config.set     — set a single config value in .env (key + value, requires restart)\n"
        "  config.apply   — replace .env entirely with the provided raw content\n"
        "  config.patch   — merge-patch .env with key=value pairs (partial update)\n"
        "  update.run     — git pull latest code and restart"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action: status | restart | config.get | config.schema | config.set | config.apply | config.patch | update.run | policy",
            },
            "key": {
                "type": "string",
                "description": "Config key to set (for config.set action)",
            },
            "value": {
                "type": "string",
                "description": "Config value to set (for config.set action)",
            },
            "raw": {
                "type": "string",
                "description": "Raw .env file content (for config.apply) or KEY=VALUE lines to merge (for config.patch)",
            },
        },
        "required": ["action"],
    },
    fn=lambda **kw: _gateway(**kw),
    owner_only=True,  # Only the main session may control the gateway
)


async def _gateway(
    action: str,
    key: str | None = None,
    value: str | None = None,
    raw: str | None = None,
) -> str:
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

    if action == "config.schema":
        import dataclasses
        from config import Config
        lines = ["PyGate configuration schema (.env keys):"]
        for f in dataclasses.fields(Config):
            env_key = f.name.upper()
            type_name = str(f.type) if isinstance(f.type, str) else type(f.default).__name__
            lines.append(f"  {env_key}: {type_name}")
        return "\n".join(lines)

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

    if action == "config.apply":
        if not raw:
            return "Error: 'raw' (.env content) is required for config.apply"
        env_path = Path(".env")
        # Create backup
        if env_path.exists():
            env_path.rename(env_path.with_suffix(".env.bak"))
        env_path.write_text(raw, encoding="utf-8")
        return "Config replaced ✅ (.env.bak created). Restart required."

    if action == "config.patch":
        if not raw:
            return "Error: 'raw' (KEY=VALUE lines) is required for config.patch"
        env_path = Path(".env")
        content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        lines = content.splitlines()
        # Parse patch lines
        patch_pairs: dict[str, str] = {}
        for line in raw.strip().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                patch_pairs[k.strip()] = v.strip()
        # Apply patch
        updated: list[str] = []
        applied = set()
        for line in lines:
            key_part = line.split("=")[0].strip() if "=" in line else ""
            if key_part in patch_pairs:
                updated.append(f"{key_part}={patch_pairs[key_part]}")
                applied.add(key_part)
            else:
                updated.append(line)
        # Append any keys not already present
        for k, v in patch_pairs.items():
            if k not in applied:
                updated.append(f"{k}={v}")
        env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
        patched_keys = ", ".join(patch_pairs.keys())
        return f"Config patched ✅ ({patched_keys}). Restart required."

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

    if action == "policy":
        # Show tool policy summary (allow/deny/owner_only per tool)
        try:
            from main import _get_registry
            registry = _get_registry()
            return registry.policy_summary()
        except Exception:
            return "Policy summary requires the registry to be accessible. Run from main process."

    return f"Unknown action '{action}'. Use: status, restart, config.get, config.schema, config.set, config.apply, config.patch, update.run, policy"
