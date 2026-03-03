"""
Cron tool — mirrors OpenClaw's cron tool completely.

Actions: status, list, add, update, remove, run, enable, disable, wake
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
        "Schedule and manage reminders and recurring tasks.\n"
        "Actions:\n"
        "  status   — show scheduler status\n"
        "  list     — list all scheduled jobs\n"
        "  add      — schedule a new job (requires schedule + message)\n"
        "  update   — modify an existing job by job_id\n"
        "  remove   — delete a job by job_id\n"
        "  run      — fire a job immediately by job_id\n"
        "  enable   — enable a disabled job\n"
        "  disable  — disable a job without deleting it\n"
        "  wake     — trigger an immediate agent heartbeat-style wake"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action: status | list | add | update | remove | run | enable | disable | wake",
            },
            "job_id": {
                "type": "string",
                "description": "Job ID (required for update/remove/run/enable/disable)",
            },
            "schedule": {
                "type": "string",
                "description": (
                    "When to run. Formats:\n"
                    "  ISO datetime: '2025-03-15T14:30:00' (one-time)\n"
                    "  Interval: '30m', '2h', '1d' (repeating)\n"
                    "  Cron expression: '0 9 * * 1' (every Monday 9am)"
                ),
            },
            "message": {
                "type": "string",
                "description": "Reminder text or task description to run at trigger time",
            },
            "description": {
                "type": "string",
                "description": "Human-readable label for this job",
            },
            "wake_text": {
                "type": "string",
                "description": "Text to pass to the agent on wake (for 'wake' action)",
            },
        },
        "required": ["action"],
    },
    fn=lambda **kw: _cron(**kw),
)


async def _cron(
    action: str,
    job_id: str | None = None,
    schedule: str | None = None,
    message: str | None = None,
    description: str | None = None,
    wake_text: str | None = None,
) -> str:
    if not _manager:
        return "Cron scheduler not initialized"

    action = action.lower().strip()

    if action == "status":
        return _manager.status()

    if action == "list":
        return _manager.list_jobs()

    if action == "add":
        if not schedule:
            return "Error: 'schedule' is required for add action"
        if not message:
            return "Error: 'message' is required for add action"
        return await _manager.add_job(
            schedule=schedule,
            message=message,
            description=description or message[:60],
        )

    if action == "update":
        if not job_id:
            return "Error: 'job_id' is required for update action"
        patch: dict = {}
        if schedule:
            patch["schedule"] = schedule
        if message:
            patch["message"] = message
        if description:
            patch["description"] = description
        if not patch:
            return "Error: provide at least one of schedule, message, or description to update"
        return await _manager.update_job(job_id, patch)

    if action == "remove":
        if not job_id:
            return "Error: 'job_id' is required for remove action"
        return _manager.remove_job(job_id)

    if action == "run":
        if not job_id:
            return "Error: 'job_id' is required for run action"
        return await _manager.run_job_now(job_id)

    if action == "enable":
        if not job_id:
            return "Error: 'job_id' is required for enable action"
        return await _manager.set_job_enabled(job_id, enabled=True)

    if action == "disable":
        if not job_id:
            return "Error: 'job_id' is required for disable action"
        return await _manager.set_job_enabled(job_id, enabled=False)

    if action == "wake":
        return await _manager.wake(wake_text or "")

    return f"Unknown action '{action}'. Use: status, list, add, update, remove, run, enable, disable, wake"
