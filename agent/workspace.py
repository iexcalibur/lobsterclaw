"""
Workspace MD file loader — mirrors OpenClaw's bootstrap context system.

Files loaded from the workspace/ directory and injected into the system prompt.

Load order (first wins for SOUL.md persona):
  AGENTS.md    — top-level agent instructions (loaded first if present; same role as OpenClaw's AGENTS.md)
  BOOTSTRAP.md — onboarding/bootstrap guidance (loaded before SOUL)
  SOUL.md      — agent personality and core principles
  USER.md      — facts about the human
  MEMORY.md    — persistent key facts
  IDENTITY.md  — agent name/role override
  TOOLS.md     — tool usage guidelines
  HEARTBEAT.md — periodic task list (loaded only during heartbeat runs)

Each file is optional. Missing files are silently skipped.
Front-matter (YAML between --- delimiters) is stripped before injection.
File contents are cached by mtime identity to avoid redundant reads.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Ordered load list — AGENTS/BOOTSTRAP first so they take precedence
ALWAYS_LOADED = [
    "AGENTS.md",
    "BOOTSTRAP.md",
    "SOUL.md",
    "USER.md",
    "MEMORY.md",
    "IDENTITY.md",
    "TOOLS.md",
]
HEARTBEAT_ONLY = ["HEARTBEAT.md"]

# Max file size to load (2 MB — mirrors OpenClaw's MAX_WORKSPACE_BOOTSTRAP_FILE_BYTES)
MAX_FILE_BYTES = 2 * 1024 * 1024

# Where the workspace files live relative to the project root
DEFAULT_WORKSPACE_DIR = Path(__file__).parent.parent / "workspace"

# File content cache: path → {"content": str, "identity": str}
# Identity = f"{size}:{mtime_ns}" — cheap inode-free freshness check
_file_cache: dict[str, dict] = {}


def _file_identity(path: Path) -> str:
    try:
        st = path.stat()
        return f"{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        return ""


def _read_workspace_file(path: Path) -> str | None:
    """
    Read a workspace file with:
    - Size guard (skip files > MAX_FILE_BYTES)
    - Mtime-based content cache
    - Front-matter stripping
    """
    try:
        identity = _file_identity(path)
        key = str(path)
        cached = _file_cache.get(key)
        if cached and cached.get("identity") == identity and identity:
            return cached["content"]

        # Size guard before reading
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            logger.warning(
                "Workspace file too large (%d bytes > %d), skipping: %s",
                size, MAX_FILE_BYTES, path.name,
            )
            return None

        raw = path.read_text(encoding="utf-8")
        content = _strip_front_matter(raw).strip()

        # Cache even empty results so we don't re-stat on every call
        _file_cache[key] = {"content": content, "identity": identity}
        logger.debug("Loaded workspace file: %s (%d chars)", path.name, len(content))
        return content

    except OSError:
        return None
    except Exception as e:
        logger.warning("Failed to read workspace file %s: %s", path.name, e)
        return None


def _strip_front_matter(content: str) -> str:
    """Strip YAML front matter (--- ... ---) from the start of the file."""
    if not content.startswith("---"):
        return content
    end_index = content.find("\n---", 3)
    if end_index == -1:
        return content
    trimmed = content[end_index + len("\n---"):]
    return trimmed.lstrip()


def _is_effectively_empty(content: str) -> bool:
    """Return True if the file contains only comments or whitespace."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return False
    return True


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

        content = _read_workspace_file(path)
        if not content or _is_effectively_empty(content):
            continue

        sections.append(f"## [{filename}]\n\n{content}")

    if not sections:
        return ""

    return "\n\n---\n\n".join(sections)


def load_memory_md(workspace_dir: Path | None = None) -> str:
    """Load just MEMORY.md — used to inject into cron/heartbeat agent runs."""
    directory = workspace_dir or DEFAULT_WORKSPACE_DIR
    path = directory / "MEMORY.md"
    if not path.exists():
        return ""
    content = _read_workspace_file(path)
    if not content or _is_effectively_empty(content):
        return ""
    return content


def invalidate_cache(path: Path | None = None) -> None:
    """Invalidate file cache. Pass a specific path, or None to clear all."""
    if path is None:
        _file_cache.clear()
    else:
        _file_cache.pop(str(path), None)
