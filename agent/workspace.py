"""
Workspace MD file loader — mirrors OpenClaw's bootstrap context system.

Files loaded from the workspace/ directory and injected into the system prompt:
  SOUL.md      — agent personality and core principles (always loaded)
  USER.md      — facts about the human (always loaded)
  MEMORY.md    — persistent key facts (always loaded)
  IDENTITY.md  — agent name/role override (always loaded)
  TOOLS.md     — tool usage guidelines (always loaded)
  HEARTBEAT.md — periodic task list (loaded only during heartbeat runs)

Each file is optional. Missing files are silently skipped.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Order matters — SOUL first, USER second, MEMORY third, then the rest
ALWAYS_LOADED = ["SOUL.md", "USER.md", "MEMORY.md", "IDENTITY.md", "TOOLS.md"]
HEARTBEAT_ONLY = ["HEARTBEAT.md"]

# Where the workspace files live relative to the project root
DEFAULT_WORKSPACE_DIR = Path(__file__).parent.parent / "workspace"


def load_workspace_context(
    workspace_dir: Path | None = None,
    include_heartbeat: bool = False,
) -> str:
    """
    Load all relevant MD files and return them as a single formatted string
    to be appended to the system prompt.
    """
    directory = workspace_dir or DEFAULT_WORKSPACE_DIR
    filenames = list(ALWAYS_LOADED)
    if include_heartbeat:
        filenames += HEARTBEAT_ONLY

    sections: list[str] = []
    for filename in filenames:
        path = directory / filename
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8").strip()
            if not content or _is_effectively_empty(content):
                continue
            sections.append(f"## [{filename}]\n\n{content}")
            logger.debug("Loaded workspace file: %s (%d chars)", filename, len(content))
        except Exception as e:
            logger.warning("Failed to read workspace file %s: %s", filename, e)

    if not sections:
        return ""

    return "\n\n---\n\n".join(sections)


def _is_effectively_empty(content: str) -> bool:
    """Return True if the file contains only comments or whitespace."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return False
    return True


def load_memory_md(workspace_dir: Path | None = None) -> str:
    """Load just MEMORY.md — used to inject into cron/heartbeat agent runs."""
    directory = workspace_dir or DEFAULT_WORKSPACE_DIR
    path = directory / "MEMORY.md"
    if not path.exists():
        return ""
    try:
        content = path.read_text(encoding="utf-8").strip()
        return content if not _is_effectively_empty(content) else ""
    except Exception as e:
        logger.warning("Failed to read MEMORY.md: %s", e)
        return ""
