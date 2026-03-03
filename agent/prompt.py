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

    # Memory instruction — always remind agent to check memory before answering
    memory_instruction = (
        "\n\nBefore answering questions about the user's preferences, past decisions, "
        "ongoing projects, or personal details: run memory_search on MEMORY.md and memory/ files "
        "first. Use memory_get to pull specific entries. If unsure after searching, say so."
    )

    # Load workspace MD files (SOUL, USER, MEMORY, IDENTITY, TOOLS)
    workspace_context = load_workspace_context(
        workspace_dir=workspace_dir,
        include_heartbeat=include_heartbeat,
    )
    workspace_section = f"\n\n---\n\n{workspace_context}" if workspace_context else ""

    return (
        f"You are a personal AI assistant running privately for one person only.\n\n"
        f"Current time: {now}"
        f"{tools_section}"
        f"{confirmation_note}"
        f"{memory_instruction}"
        f"{workspace_section}"
    )
