"""
Gateway tool — mirrors OpenClaw's gateway tool.

Actions:
  restart      — restart the LobsterClaw process (sends SIGTERM to self)
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
        "Control the LobsterClaw process and remote gateways.\n\n"
        "Field parity with OpenClaw gateway-tool.ts:\n"
        "  action       — required: see actions below\n"
        "  gatewayUrl   — remote gateway base URL (for remote ops)\n"
        "  gatewayToken — auth token for remote gateway\n"
        "  timeoutMs    — request timeout for remote ops (default 10000ms)\n"
        "  baseHash     — expected config hash for write-safety checks\n"
        "  sessionKey   — associate update with a session key\n"
        "  note         — human-readable note for config changes\n"
        "  restartDelayMs — milliseconds to wait before restart (default 1000ms)\n\n"
        "Actions:\n"
        "  status        — show uptime and process info\n"
        "  restart       — restart the bot process\n"
        "  config.get    — view current config (sensitive values redacted)\n"
        "  config.schema — show all config keys and types\n"
        "  config.set    — set a single key=value in .env\n"
        "  config.apply  — replace .env with provided raw content\n"
        "  config.patch  — merge KEY=VALUE lines into .env\n"
        "  update.run    — git pull and restart\n"
        "  policy        — show tool allow/deny policy"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action to perform",
            },
            "key": {"type": "string", "description": "Config key (config.set)"},
            "value": {"type": "string", "description": "Config value (config.set)"},
            "raw": {
                "type": "string",
                "description": "Raw .env content (config.apply) or KEY=VALUE lines (config.patch)",
            },
            # Remote gateway fields (OpenClaw parity)
            "gatewayUrl": {
                "type": "string",
                "description": "Remote gateway base URL for remote operations",
            },
            "gatewayToken": {
                "type": "string",
                "description": "Auth token for remote gateway",
            },
            "timeoutMs": {
                "type": "integer",
                "description": "Request timeout for remote gateway ops (ms, default 10000)",
                "default": 10000,
            },
            # Write-safety fields (OpenClaw parity)
            "baseHash": {
                "type": "string",
                "description": "Expected config hash to detect concurrent edits (write-safety check)",
            },
            "sessionKey": {
                "type": "string",
                "description": "Session key to associate with this config change",
            },
            "note": {
                "type": "string",
                "description": "Human-readable note logged with config changes",
            },
            "restartDelayMs": {
                "type": "integer",
                "description": "Milliseconds to wait before restart (default 1000)",
                "default": 1000,
            },
        },
        "required": ["action"],
    },
    fn=lambda **kw: _gateway(**kw),
    owner_only=True,
)


async def _gateway(
    action: str,
    key: str | None = None,
    value: str | None = None,
    raw: str | None = None,
    # Remote gateway fields (OpenClaw parity)
    gatewayUrl: str | None = None,
    gatewayToken: str | None = None,
    timeoutMs: int = 10000,
    # Write-safety fields
    baseHash: str | None = None,
    sessionKey: str | None = None,
    note: str | None = None,
    restartDelayMs: int = 1000,
) -> str:
    action = action.lower().strip()

    if action == "status":
        uptime = datetime.now() - _start_time
        hours, rem = divmod(int(uptime.total_seconds()), 3600)
        mins, secs = divmod(rem, 60)
        return (
            f"LobsterClaw status:\n"
            f"PID: {os.getpid()}\n"
            f"Python: {sys.version.split()[0]}\n"
            f"Uptime: {hours}h {mins}m {secs}s\n"
            f"Started: {_start_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    if action == "restart":
        logger.info("Gateway restart requested by agent (note=%s, sessionKey=%s)", note, sessionKey)
        # If remote gateway, delegate via HTTP
        if gatewayUrl:
            return await _remote_gateway_call(gatewayUrl, gatewayToken, "restart", timeoutMs=timeoutMs)
        import asyncio
        delay_s = restartDelayMs / 1000.0
        loop = asyncio.get_running_loop()
        loop.call_later(delay_s, lambda: os.kill(os.getpid(), signal.SIGTERM))
        return f"LobsterClaw restart scheduled in {restartDelayMs}ms... ✅"

    if action == "config.schema":
        import dataclasses
        from config import Config
        lines = ["LobsterClaw configuration schema (.env keys):"]
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
        if gatewayUrl:
            return await _remote_gateway_call(gatewayUrl, gatewayToken, "config.set",
                                               params={"key": key, "value": value}, timeoutMs=timeoutMs)
        env_path = Path(".env")
        if not env_path.exists():
            return "Error: .env file not found"
        content = env_path.read_text(encoding="utf-8")
        # baseHash safety check: verify current content hash matches expected
        if baseHash:
            import hashlib
            current_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
            if current_hash != baseHash:
                return (f"Error: baseHash mismatch — expected {baseHash}, got {current_hash}. "
                        "Config may have been changed by another process.")
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
        if note:
            new_lines.append(f"# {note}")
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        note_str = f" (note: {note})" if note else ""
        return f"Config updated: {key}={value}{note_str}\nRestart required to apply."

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

    return (
        f"Unknown action '{action}'. Use: "
        "status, restart, config.get, config.schema, config.set, config.apply, config.patch, update.run, policy"
    )


async def _remote_gateway_call(
    gateway_url: str,
    token: str | None,
    action: str,
    params: dict | None = None,
    timeoutMs: int = 10000,
) -> str:
    """Delegate an action to a remote LobsterClaw instance via HTTP (OpenClaw remote gateway parity)."""
    try:
        import httpx
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = gateway_url.rstrip("/") + "/gateway"
        payload = {"action": action, **(params or {})}
        async with httpx.AsyncClient(timeout=timeoutMs / 1000) as client:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            return data.get("result", str(data))
    except Exception as e:
        return f"Remote gateway error ({gateway_url}): {e}"
