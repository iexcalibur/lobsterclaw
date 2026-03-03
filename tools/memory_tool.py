"""
Memory tools — mirrors OpenClaw's memory_search / memory_get / memory_write.

Storage: workspace/memory/*.md files (human-readable, editable by user).
The directory is resolved from config (MEMORY_DIR) or falls back to workspace/memory/.

FTS5 index is rebuilt in-memory per search query from the current file state.

Tools:
  memory_search — full-text search across all memory MD files
  memory_get    — read a specific memory file by key
  memory_write  — write/update/append a memory file
  memory_list   — list all memory keys
  memory_delete — delete a memory entry
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from config import get_config
from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Tool definitions
# ------------------------------------------------------------------

MEMORY_SEARCH_TOOL = ToolDefinition(
    name="memory_search",
    description=(
        "Search long-term memory for relevant notes and facts. "
        "Searches MEMORY.md and all files in workspace/memory/. "
        "Run this BEFORE answering questions about the user's past preferences, projects, or decisions."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Keywords to search for"},
            "limit": {"type": "integer", "description": "Max results (default 5)", "default": 5},
        },
        "required": ["query"],
    },
    fn=lambda **kw: _memory_search(**kw),
)

MEMORY_GET_TOOL = ToolDefinition(
    name="memory_get",
    description=(
        "Read a specific memory file by key. "
        "Key is the filename without .md (e.g. 'preferences', 'projects'). "
        "Use 'MEMORY' to read the top-level MEMORY.md. "
        "Use memory_list to see all available keys."
    ),
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Memory key (filename without .md)"},
        },
        "required": ["key"],
    },
    fn=lambda **kw: _memory_get(**kw),
)

MEMORY_WRITE_TOOL = ToolDefinition(
    name="memory_write",
    description=(
        "Save or update a memory entry as a markdown file. "
        "Key becomes the filename (e.g. 'preferences' → memory/preferences.md). "
        "Set append=true to add to an existing entry instead of replacing it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Short unique name (use underscores/hyphens, no spaces)"},
            "content": {"type": "string", "description": "Markdown content to remember"},
            "append": {"type": "boolean", "description": "Append to existing file (default false)", "default": False},
        },
        "required": ["key", "content"],
    },
    fn=lambda **kw: _memory_write(**kw),
)

MEMORY_LIST_TOOL = ToolDefinition(
    name="memory_list",
    description="List all available memory keys.",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    fn=lambda **kw: _memory_list(**kw),
)

MEMORY_DELETE_TOOL = ToolDefinition(
    name="memory_delete",
    description="Delete a memory entry by key.",
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Memory key to delete"},
        },
        "required": ["key"],
    },
    fn=lambda **kw: _memory_delete(**kw),
)

# ------------------------------------------------------------------
# Path resolution — uses config.memory_path, not hardcoded workspace/
# ------------------------------------------------------------------

def _get_memory_dir() -> Path:
    """Returns the memory directory, creating it if needed."""
    cfg = get_config()
    # Use the configured path; fall back to workspace/memory/ next to this project
    if cfg.memory_dir and cfg.memory_dir != "~/.pygate/memory":
        directory = Path(cfg.memory_dir).expanduser()
    else:
        # Default: workspace/memory/ relative to project root
        directory = Path(__file__).parent.parent / "workspace" / "memory"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _get_memory_md_path() -> Path:
    """Top-level MEMORY.md in workspace root."""
    return Path(__file__).parent.parent / "workspace" / "MEMORY.md"


# ------------------------------------------------------------------
# FTS index — rebuilt in-memory each query (always fresh)
# ------------------------------------------------------------------

def _build_fts_index() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE VIRTUAL TABLE memories USING fts5(
            key UNINDEXED,
            filepath UNINDEXED,
            content,
            tokenize='porter ascii'
        )
        """
    )
    entries: list[tuple[str, str, str]] = []

    # Top-level MEMORY.md
    memory_md = _get_memory_md_path()
    if memory_md.exists():
        content = memory_md.read_text(encoding="utf-8").strip()
        if content:
            entries.append(("MEMORY", str(memory_md), content))

    # workspace/memory/*.md
    memory_dir = _get_memory_dir()
    for md_file in sorted(memory_dir.glob("*.md")):
        if md_file.name == "README.md":
            continue
        content = md_file.read_text(encoding="utf-8").strip()
        if content:
            entries.append((md_file.stem, str(md_file), content))

    if entries:
        conn.executemany(
            "INSERT INTO memories(key, filepath, content) VALUES (?, ?, ?)", entries
        )
        conn.commit()
    return conn


def _sanitize_fts_query(query: str) -> str:
    """Make FTS5 query safe by escaping special chars."""
    # Remove FTS5 special syntax characters that cause parse errors
    sanitized = re.sub(r'[^a-zA-Z0-9\s\-_\']', ' ', query)
    return sanitized.strip() or query[:50]


# ------------------------------------------------------------------
# Implementations
# ------------------------------------------------------------------

async def _memory_search(query: str, limit: int = 5) -> str:
    cfg = get_config()
    if not cfg.memory_enabled:
        return "Memory is disabled (MEMORY_ENABLED=false)"

    try:
        conn = _build_fts_index()
        safe_query = _sanitize_fts_query(query)
        rows = conn.execute(
            "SELECT key, content FROM memories WHERE memories MATCH ? ORDER BY rank LIMIT ?",
            (safe_query, limit),
        ).fetchall()
        conn.close()

        if not rows:
            return f"No memory entries matched '{query}'."

        results = []
        for key, content in rows:
            preview = content[:800] + ("…" if len(content) > 800 else "")
            results.append(f"### [{key}]\n{preview}")
        return "\n\n---\n\n".join(results)
    except sqlite3.OperationalError as e:
        # FTS query syntax error — fall back to substring search
        logger.warning("FTS query failed (%s), falling back to substring search", e)
        return await _memory_search_fallback(query, limit)
    except Exception as e:
        logger.exception("memory_search failed")
        return f"Error searching memory: {e}"


async def _memory_search_fallback(query: str, limit: int) -> str:
    """Simple case-insensitive substring search as FTS fallback."""
    query_lower = query.lower()
    results = []

    memory_md = _get_memory_md_path()
    if memory_md.exists():
        content = memory_md.read_text(encoding="utf-8")
        if query_lower in content.lower():
            results.append(("MEMORY", content[:800]))

    memory_dir = _get_memory_dir()
    for md_file in sorted(memory_dir.glob("*.md")):
        if md_file.name == "README.md":
            continue
        content = md_file.read_text(encoding="utf-8")
        if query_lower in content.lower():
            results.append((md_file.stem, content[:800]))
        if len(results) >= limit:
            break

    if not results:
        return f"No memory entries matched '{query}'."
    return "\n\n---\n\n".join(f"### [{k}]\n{v}" for k, v in results)


async def _memory_get(key: str) -> str:
    cfg = get_config()
    if not cfg.memory_enabled:
        return "Memory is disabled (MEMORY_ENABLED=false)"

    if key.upper() == "MEMORY":
        path = _get_memory_md_path()
    else:
        path = _get_memory_dir() / f"{key}.md"

    if not path.exists():
        available = _list_keys()
        return f"No memory file found for key '{key}'.\nAvailable keys: {available}"

    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        return f"Error reading memory '{key}': {e}"


async def _memory_write(key: str, content: str, append: bool = False) -> str:
    cfg = get_config()
    if not cfg.memory_enabled:
        return "Memory is disabled (MEMORY_ENABLED=false)"

    safe_key = key.replace("/", "_").replace("\\", "_").strip(". ")
    if not safe_key:
        return "Invalid key"

    path = _get_memory_dir() / f"{safe_key}.md"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        if append and path.exists():
            existing = path.read_text(encoding="utf-8")
            updated = f"{existing.rstrip()}\n\n_Updated {now}_\n\n{content}"
            path.write_text(updated, encoding="utf-8")
            return f"Memory '{safe_key}' updated (appended)"
        else:
            header = f"# {safe_key}\n\n_Last updated: {now}_\n\n"
            path.write_text(header + content, encoding="utf-8")
            return f"Memory '{safe_key}' saved → workspace/memory/{safe_key}.md"
    except Exception as e:
        return f"Error saving memory '{safe_key}': {e}"


async def _memory_list() -> str:
    cfg = get_config()
    if not cfg.memory_enabled:
        return "Memory is disabled (MEMORY_ENABLED=false)"
    keys = _list_keys()
    return f"Available memory keys: {keys}" if keys != "(none yet)" else "No memory entries yet."


async def _memory_delete(key: str) -> str:
    cfg = get_config()
    if not cfg.memory_enabled:
        return "Memory is disabled (MEMORY_ENABLED=false)"

    path = _get_memory_dir() / f"{key}.md"
    if not path.exists():
        return f"No memory file found for key '{key}'"
    try:
        path.unlink()
        return f"Memory '{key}' deleted."
    except Exception as e:
        return f"Error deleting memory '{key}': {e}"


def _list_keys() -> str:
    memory_dir = _get_memory_dir()
    keys = [f.stem for f in sorted(memory_dir.glob("*.md")) if f.name != "README.md"]
    if _get_memory_md_path().exists():
        keys = ["MEMORY"] + keys
    return ", ".join(keys) if keys else "(none yet)"
