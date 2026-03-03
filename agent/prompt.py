from __future__ import annotations

from datetime import datetime
from pathlib import Path

from config import Config


def build_system_prompt(
    cfg: Config,
    tool_names: list[str] | None = None,
    workspace_dir: Path | None = None,
    include_heartbeat: bool = False,
) -> str:
    from agent.workspace import load_workspace_context
    from agent.skills import format_skills_for_prompt

    now = datetime.now().strftime("%A, %B %d, %Y %I:%M %p")

    tools_section = ""
    if tool_names:
        tools_section = f"\n\nAvailable tools: {', '.join(tool_names)}"

    confirmation_note = ""
    if cfg.tools_require_confirmation:
        tools_str = ", ".join(cfg.tools_require_confirmation)
        confirmation_note = (
            f"\n\nIMPORTANT: The following tools require explicit user approval before "
            f"running: {tools_str}. You will be paused and the user will be shown an "
            f"Approve/Deny button. Wait for their decision."
        )

    # Memory instruction
    memory_instruction = (
        "\n\nBefore answering questions about the user's preferences, past decisions, "
        "ongoing projects, or personal details: run memory_search on MEMORY.md and memory/ files. "
        "Use memory_get to read specific entries. Use memory_list to see all keys. "
        "If unsure after searching, say so."
    )

    # Workspace MD files (SOUL, USER, MEMORY, IDENTITY, TOOLS, optionally HEARTBEAT)
    workspace_context = load_workspace_context(
        workspace_dir=workspace_dir,
        include_heartbeat=include_heartbeat,
    )
    workspace_section = f"\n\n---\n\n{workspace_context}" if workspace_context else ""

    # Skills from workspace/skills/
    skills_text = format_skills_for_prompt()
    skills_section = f"\n\n---\n\n{skills_text}" if skills_text else ""

    return (
        f"You are a personal AI assistant running privately for one person only.\n\n"
        f"Current time: {now}"
        f"{tools_section}"
        f"{confirmation_note}"
        f"{memory_instruction}"
        f"{workspace_section}"
        f"{skills_section}"
    )
