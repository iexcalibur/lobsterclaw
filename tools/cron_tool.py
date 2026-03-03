from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tools.registry import ToolDefinition

if TYPE_CHECKING:
    from scheduler.manager import CronManager

logger = logging.getLogger(__name__)

_manager: "CronManager | None" = None


def set_manager(mgr: "CronManager") -> None:
    global _manager
    _manager = mgr


TOOL_DEFINITION = ToolDefinition(
    name="cron",
    description=(
        "Manage scheduled jobs and reminders. "
        "Actions: add (schedule a one-time or recurring reminder), "
        "list (show all scheduled jobs), remove (cancel a job by ID), status (check scheduler)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "list", "remove", "status"],
                "description": "What to do",
            },
            "description": {
                "type": "string",
                "description": "Human-readable description of the job (required for 'add')",
            },
            "schedule": {
                "type": "string",
                "description": (
                    "When to run. For one-time: ISO datetime like '2025-03-15T14:30:00'. "
                    "For recurring: cron expression like '0 9 * * 1' (every Monday 9am). "
                    "Required for 'add'."
                ),
            },
            "message": {
                "type": "string",
                "description": "The reminder message text that will be sent when the job fires (required for 'add')",
            },
            "job_id": {
                "type": "string",
                "description": "Job ID to remove (required for 'remove')",
            },
        },
        "required": ["action"],
    },
    fn=lambda **kw: _cron(**kw),
)


async def _cron(
    action: str,
    description: str | None = None,
    schedule: str | None = None,
    message: str | None = None,
    job_id: str | None = None,
) -> str:
    if _manager is None:
        return "Error: cron manager not initialized"

    if action == "add":
        if not all([description, schedule, message]):
            return "Error: 'description', 'schedule', and 'message' are required for add"
        return await _manager.add_job(description=description, schedule=schedule, message=message)

    if action == "list":
        return await _manager.list_jobs()

    if action == "remove":
        if not job_id:
            return "Error: 'job_id' is required for remove"
        return await _manager.remove_job(job_id)

    if action == "status":
        return await _manager.status()

    return f"Error: unknown action '{action}'"
