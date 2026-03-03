"""
Cron tool — mirrors OpenClaw's cron-tool.ts with full job{} object model parity.

Job object model (matches OpenClaw cron-tool.ts):

  job: {
    name: str                   # optional human-readable name
    schedule: {                 # required
      kind: "at" | "every" | "cron"
      at: str                   # ISO-8601 timestamp (kind=at)
      everyMs: int              # interval ms (kind=every)
      anchorMs: int             # anchor epoch ms (kind=every, optional)
      expr: str                 # cron expression (kind=cron)
      tz: str                   # timezone (kind=cron, optional)
    }
    payload: {                  # required
      kind: "systemEvent" | "agentTurn"
      text: str                 # message text (kind=systemEvent)
      message: str              # agent prompt (kind=agentTurn)
      model: str                # optional
      thinking: str             # optional
      timeoutSeconds: int       # optional (0 = no timeout)
    }
    sessionTarget: "main" | "isolated"   # required
    delivery: {                 # optional
      mode: "none" | "announce" | "webhook"
      channel: str              # optional channel for announce
      to: str                   # recipient or webhook URL
      bestEffort: bool
    }
    enabled: bool               # default true
    deleteAfterRun: bool        # one-shot cleanup
    description: str            # optional label
  }

  patch: same structure, all fields optional

Backward compatibility: flat add params still accepted (schedule string + message)
  and are auto-converted to job{} object model.

Actions: status | list | add | update | remove | run | runs | wake | enable | disable
"""

from __future__ import annotations

import logging
from typing import Any

from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

_manager: Any = None


def set_manager(mgr: Any) -> None:
    global _manager
    _manager = mgr


TOOL_DEFINITION = ToolDefinition(
    name="cron",
    description=(
        "Manage cron jobs and reminders. Full job{} object model parity with OpenClaw cron-tool.ts.\n\n"
        "ACTIONS: status | list | add | update | remove | run | runs | wake | enable | disable\n\n"
        "JOB SCHEMA (for add action):\n"
        "  job.name           — optional human-readable name\n"
        "  job.schedule       — required: when to run\n"
        "    .kind = 'at'     → .at (ISO-8601 timestamp) — one-shot\n"
        "    .kind = 'every'  → .everyMs (ms) + optional .anchorMs — recurring interval\n"
        "    .kind = 'cron'   → .expr (cron expression) + optional .tz — cron schedule\n"
        "  job.payload        — required: what to execute\n"
        "    .kind = 'systemEvent' → .text (inject into session as system event)\n"
        "    .kind = 'agentTurn'   → .message (run agent with this prompt, isolated only)\n"
        "      + optional: .model, .thinking, .timeoutSeconds\n"
        "  job.sessionTarget  — 'main' (default) | 'isolated'\n"
        "    CONSTRAINT: main → systemEvent only; isolated → agentTurn only\n"
        "  job.delivery       — optional: announce result or webhook\n"
        "    .mode = 'announce' | 'webhook' | 'none'\n"
        "    .to = recipient / webhook URL\n"
        "    .channel = channel name for announce\n"
        "  job.enabled        — default true\n"
        "  job.deleteAfterRun — delete after first run (one-shot)\n\n"
        "FLAT COMPAT (non-frontier models): schedule/message/session_target/delivery "
        "fields at top level are auto-promoted into job{}.\n\n"
        "UPDATE: use patch{} same structure as job, all fields optional.\n\n"
        "WAKE MODES: text + optional mode (now | next-heartbeat)\n"
        "LIST: optional includeDisabled=true, contextMessages (0-10)\n\n"
        "jobId is canonical; id is accepted as alias.\n"
        "Requires EXEC_ENABLED=true or cron scheduler initialized."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "status|list|add|update|remove|run|runs|wake|enable|disable",
            },
            # Job identifier
            "jobId": {"type": "string", "description": "Job ID (OpenClaw field name; also 'id', 'job_id')"},
            "id": {"type": "string", "description": "Alias for jobId"},
            "job_id": {"type": "string", "description": "Alias for jobId (LobsterClaw legacy)"},
            # Job creation / update
            "job": {
                "type": "object",
                "description": "Job definition object (add action). See schema above.",
                "additionalProperties": True,
            },
            "patch": {
                "type": "object",
                "description": "Partial job update object (update action). All fields optional.",
                "additionalProperties": True,
            },
            # List options
            "includeDisabled": {
                "type": "boolean",
                "description": "Include disabled jobs in list (default false)",
            },
            "contextMessages": {
                "type": "number",
                "description": "Attach N most recent chat messages as context to job text (0-10)",
            },
            # Wake options
            "text": {"type": "string", "description": "Wake text / reminder message (wake action)"},
            "mode": {
                "type": "string",
                "description": "Wake mode: now | next-heartbeat (default: next-heartbeat)",
            },
            "runMode": {
                "type": "string",
                "description": "Run trigger mode: due (default) | force (run even if not due)",
            },
            # Remote gateway fields (OpenClaw parity)
            "gatewayUrl": {"type": "string", "description": "Remote gateway URL"},
            "gatewayToken": {"type": "string", "description": "Remote gateway auth token"},
            "timeoutMs": {"type": "number", "description": "Remote gateway timeout ms"},
            # Flat compat (auto-promoted into job{} — for non-frontier models)
            "schedule": {
                "type": "string",
                "description": "Flat compat: schedule string — auto-promoted to job.schedule",
            },
            "message": {
                "type": "string",
                "description": "Flat compat: reminder text — auto-promoted to job.payload.text",
            },
            "description": {"type": "string", "description": "Flat compat: job description"},
            "session_target": {"type": "string", "description": "Flat compat: main | isolated"},
            "sessionTarget": {"type": "string", "description": "Flat compat alias for session_target"},
            "delivery": {"type": "string", "description": "Flat compat: agent | direct (legacy delivery mode)"},
            "wake_text": {"type": "string", "description": "Legacy alias for text (wake action)"},
        },
        "required": ["action"],
        "additionalProperties": True,
    },
    fn=lambda **kw: _cron(**kw),
    owner_only=True,
)

# ----------------------------------------------------------------
# Flat → job{} normalisation
# ----------------------------------------------------------------

def _parse_flat_schedule(schedule_str: str) -> dict:
    """Convert a flat schedule string to job.schedule object."""
    s = schedule_str.strip()
    # ISO datetime → at
    import re
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return {"kind": "at", "at": s}
    # Interval: 30m, 2h, 1d, 90s
    m = re.match(r"^(\d+)(s|m|h|d)$", s, re.IGNORECASE)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        ms_map = {"s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
        return {"kind": "every", "everyMs": n * ms_map[unit]}
    # Cron expression: has spaces → cron kind
    if len(s.split()) >= 5:
        return {"kind": "cron", "expr": s}
    # Fallback: treat as cron expression
    return {"kind": "cron", "expr": s}


def _normalise_job(params: dict) -> dict | None:
    """
    Build a canonical job{} object from params.
    Handles both structured job{} and flat schedule/message params.
    Implements OpenClaw's flat-params recovery for non-frontier models.
    """
    # If job is already a well-formed object, use it
    job = params.get("job")
    if job and isinstance(job, dict) and job:
        # Auto-fill sessionTarget default
        if "sessionTarget" not in job and "session_target" not in job:
            job.setdefault("sessionTarget", "main")
        return job

    # Flat-params recovery: look for top-level job fields
    FLAT_JOB_KEYS = {
        "name", "schedule", "sessionTarget", "session_target", "payload", "delivery",
        "enabled", "description", "deleteAfterRun", "message", "text",
        "model", "thinking", "timeoutSeconds",
    }
    flat_fields = {k: v for k, v in params.items() if k in FLAT_JOB_KEYS and v is not None}
    if not flat_fields:
        return None

    # Build job from flat fields
    built: dict = {}

    # Name / description
    if "name" in flat_fields:
        built["name"] = flat_fields["name"]
    if "description" in flat_fields:
        built["description"] = flat_fields["description"]

    # Schedule
    if "schedule" in flat_fields:
        s = flat_fields["schedule"]
        if isinstance(s, dict):
            built["schedule"] = s
        else:
            built["schedule"] = _parse_flat_schedule(str(s))
    elif "payload" not in flat_fields:
        return None  # need at least a schedule or payload

    # Session target
    session_target = flat_fields.get("sessionTarget") or flat_fields.get("session_target", "main")
    built["sessionTarget"] = session_target

    # Payload
    if "payload" in flat_fields and isinstance(flat_fields["payload"], dict):
        built["payload"] = flat_fields["payload"]
    else:
        msg_text = flat_fields.get("message") or flat_fields.get("text", "")
        if session_target == "isolated":
            built["payload"] = {
                "kind": "agentTurn",
                "message": msg_text,
            }
            if "model" in flat_fields:
                built["payload"]["model"] = flat_fields["model"]
            if "thinking" in flat_fields:
                built["payload"]["thinking"] = flat_fields["thinking"]
            if "timeoutSeconds" in flat_fields:
                built["payload"]["timeoutSeconds"] = flat_fields["timeoutSeconds"]
        else:
            built["payload"] = {"kind": "systemEvent", "text": msg_text}

    # Delivery
    if "delivery" in flat_fields:
        d = flat_fields["delivery"]
        if isinstance(d, dict):
            built["delivery"] = d
        elif isinstance(d, str):
            # Legacy flat delivery string: "agent" → none, "direct" → announce
            if d == "direct":
                built["delivery"] = {"mode": "announce"}
            elif d == "webhook":
                built["delivery"] = {"mode": "webhook"}
            # else: leave unset (default behaviour)

    # Enabled
    if "enabled" in flat_fields:
        built["enabled"] = flat_fields["enabled"]
    if "deleteAfterRun" in flat_fields:
        built["deleteAfterRun"] = flat_fields["deleteAfterRun"]

    return built if built else None


# ----------------------------------------------------------------
# Cron manager bridge
# ----------------------------------------------------------------

def _job_to_manager_params(job: dict) -> dict:
    """Convert canonical job{} → flat scheduler.add_job() kwargs."""
    schedule_obj = job.get("schedule", {})
    payload_obj = job.get("payload", {})
    delivery_obj = job.get("delivery") or {}

    # Extract schedule string for backward-compat manager
    if isinstance(schedule_obj, dict):
        kind = schedule_obj.get("kind", "")
        if kind == "at":
            schedule = schedule_obj.get("at", "")
        elif kind == "every":
            ms = schedule_obj.get("everyMs", 60_000)
            if ms % 86_400_000 == 0:
                schedule = f"{ms // 86_400_000}d"
            elif ms % 3_600_000 == 0:
                schedule = f"{ms // 3_600_000}h"
            elif ms % 60_000 == 0:
                schedule = f"{ms // 60_000}m"
            else:
                schedule = f"{ms // 1000}s"
        elif kind == "cron":
            schedule = schedule_obj.get("expr", "")
        else:
            schedule = str(schedule_obj)
    else:
        schedule = str(schedule_obj)

    # Extract message from payload
    if isinstance(payload_obj, dict):
        payload_kind = payload_obj.get("kind", "systemEvent")
        message = payload_obj.get("text") or payload_obj.get("message", "")
    else:
        payload_kind = "systemEvent"
        message = str(payload_obj)

    session_target = job.get("sessionTarget", job.get("session_target", "main"))

    # Map delivery object to delivery mode string for manager
    if isinstance(delivery_obj, dict):
        d_mode = delivery_obj.get("mode", "")
        if d_mode == "announce":
            delivery = "direct"
        elif d_mode == "webhook":
            delivery = "webhook"
        else:
            delivery = "agent"
    else:
        delivery = "agent"

    return {
        "schedule": schedule,
        "message": message,
        "description": job.get("description") or job.get("name") or (message[:60] if message else ""),
        "session_target": session_target,
        "delivery": delivery,
        "enabled": job.get("enabled", True),
        "delete_after_run": bool(job.get("deleteAfterRun", job.get("delete_after_run", False))),
    }


# ----------------------------------------------------------------
# Tool implementation
# ----------------------------------------------------------------

async def _cron(
    action: str,
    # Job identifier
    jobId: str | None = None,
    id: str | None = None,
    job_id: str | None = None,
    # Job objects
    job: dict | None = None,
    patch: dict | None = None,
    # List options
    includeDisabled: bool = False,
    contextMessages: int = 0,
    # Wake
    text: str | None = None,
    wake_text: str | None = None,
    mode: str | None = None,
    runMode: str | None = None,
    # Remote gateway (schema-present, not yet wired)
    gatewayUrl: str | None = None,
    gatewayToken: str | None = None,
    timeoutMs: float = 60_000,
    # Flat compat params (auto-promoted into job{})
    **kwargs,
) -> str:
    if not _manager:
        return "Cron scheduler not initialized"

    action = action.lower().strip()
    effective_id = jobId or id or job_id

    if action == "status":
        return _manager.status()

    if action == "list":
        return _manager.list_jobs(include_disabled=includeDisabled)

    if action == "add":
        # Build normalised job from job{} or flat params
        all_params = {
            "job": job,
            **{k: v for k, v in kwargs.items() if v is not None},
        }
        norm = _normalise_job(all_params)
        if not norm:
            return (
                "Error: 'job' object is required for add.\n\n"
                "Provide:\n"
                "  job={schedule:{kind:'cron',expr:'0 9 * * *'}, "
                "payload:{kind:'systemEvent',text:'Good morning'}, sessionTarget:'main'}\n\n"
                "Or flat compat:\n"
                "  schedule='0 9 * * *', message='Good morning'"
            )
        mgr_params = _job_to_manager_params(norm)
        return await _manager.add_job(**mgr_params)

    if action == "update":
        if not effective_id:
            return "Error: 'jobId' (or 'id'/'job_id') is required for update"
        # patch{} or flat kwargs
        patch_obj = patch or {k: v for k, v in kwargs.items() if v is not None}
        if not patch_obj:
            return "Error: 'patch' object (or flat fields) required for update"
        mgr_patch: dict = {}
        if "schedule" in patch_obj:
            s = patch_obj["schedule"]
            if isinstance(s, dict):
                mgr_patch["schedule"] = _job_to_manager_params({"schedule": s}).get("schedule", "")
            else:
                mgr_patch["schedule"] = _job_to_manager_params({"schedule": s}).get("schedule", str(s))
        if "payload" in patch_obj:
            p = patch_obj["payload"]
            if isinstance(p, dict):
                mgr_patch["message"] = p.get("text") or p.get("message", "")
        if "message" in patch_obj:
            mgr_patch["message"] = patch_obj["message"]
        if "description" in patch_obj or "name" in patch_obj:
            mgr_patch["description"] = patch_obj.get("description") or patch_obj.get("name")
        if "enabled" in patch_obj:
            mgr_patch["enabled"] = patch_obj["enabled"]
        if "sessionTarget" in patch_obj or "session_target" in patch_obj:
            mgr_patch["session_target"] = patch_obj.get("sessionTarget") or patch_obj.get("session_target")
        if not mgr_patch:
            return "Error: no recognised fields in patch"
        return await _manager.update_job(effective_id, mgr_patch)

    if action == "remove":
        if not effective_id:
            return "Error: 'jobId' is required for remove"
        return _manager.remove_job(effective_id)

    if action == "run":
        if not effective_id:
            return "Error: 'jobId' is required for run"
        return await _manager.run_job_now(effective_id)

    if action == "runs":
        if not effective_id:
            return "Error: 'jobId' is required for runs"
        return _manager.get_run_history(effective_id)

    if action == "enable":
        if not effective_id:
            return "Error: 'jobId' is required for enable"
        return await _manager.set_job_enabled(effective_id, enabled=True)

    if action == "disable":
        if not effective_id:
            return "Error: 'jobId' is required for disable"
        return await _manager.set_job_enabled(effective_id, enabled=False)

    if action == "wake":
        wake_msg = text or wake_text or ""
        return await _manager.wake(wake_msg)

    return (
        f"Unknown action '{action}'. "
        "Use: status, list, add, update, remove, run, runs, wake, enable, disable"
    )
