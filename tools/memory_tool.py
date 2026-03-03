"""
Memory tools — mirrors OpenClaw's memory_search / memory_get / memory_write.

Two storage tiers:
  1. workspace/memory/*.md  — human-readable markdown files (readable + editable by user too)
  2. SQLite FTS5 index       — rebuilt on demand from the MD files for fast full-text search

memory_write  → writes/updates a markdown file in workspace/memory/
memory_get    → reads a specific memory file by key (filename without .md)
memory_search → full-text search across all memory MD files + MEMORY.md
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from config import get_config
from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

MEMORY_SEARCH_TOOL = ToolDefinition(
    name="memory_search",
    description=(
        "Search long-term memory for relevant notes and facts. "
        "Searches across MEMORY.md and all files in workspace/memory/. "
        "Run this before answering questions about the user's past preferences, projects, or decisions."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Keywords to search for"},
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 5)",
                "default": 5,
            },
        },
        "required": ["query"],
    },
    fn=lambda **kw: _memory_search(**kw),
)

MEMORY_GET_TOOL = ToolDefinition(
    name="memory_get",
    description=(
        "Read a specific memory file by its key name. "
        "Key is the filename without .md (e.g. 'preferences', 'projects', 'contacts'). "
        "Use 'MEMORY' to read the top-level MEMORY.md."
    ),
    parameters={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Memory key (filename without .md, e.g. 'preferences')",
            },
        },
        "required": ["key"],
    },
    fn=lambda **kw: _memory_get(**kw),
)

MEMORY_WRITE_TOOL = ToolDefinition(
    name="memory_write",
    description=(
        "Save or update a memory entry as a markdown file. "
        "Use this to remember facts about the user, ongoing tasks, preferences, or research. "
        "Key becomes the filename (e.g. key='preferences' → workspace/memory/preferences.md)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Short unique name (no spaces, use underscores or hyphens)",
            },
            "content": {"type": "string", "description": "Markdown content to remember"},
            "append": {
                "type": "boolean",
                "description": "If true, append to existing file instead of replacing (default false)",
                "default": False,
            },
        },
        "required": ["key", "content"],
    },
    fn=lambda **kw: _memory_write(**kw),
)


def _get_memory_dir() -> Path:
    """workspace/memory/ — where all memory MD files live."""
    base = Path(__file__).parent.parent / "workspace"
    memory_dir = base / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    return memory_dir


def _get_memory_md_path() -> Path:
    """Top-level MEMORY.md in workspace root."""
    return Path(__file__).parent.parent / "workspace" / "MEMORY.md"


def _get_fts_db() -> sqlite3.Connection:
    """In-memory FTS index rebuilt each time — keeps it always fresh from MD files."""
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
    return conn


def _build_fts_index(conn: sqlite3.Connection) -> None:
    """Index all MD files in workspace/memory/ plus the top-level MEMORY.md."""
    entries: list[tuple[str, str, str]] = []

    # Top-level MEMORY.md
    memory_md = _get_memory_md_path()
    if memory_md.exists():
        content = memory_md.read_text(encoding="utf-8").strip()
        if content:
            entries.append(("MEMORY", str(memory_md), content))

    # workspace/memory/*.md files
    memory_dir = _get_memory_dir()
    for md_file in sorted(memory_dir.glob("*.md")):
        if md_file.name == "README.md":
            continue
        key = md_file.stem
        content = md_file.read_text(encoding="utf-8").strip()
        if content:
            entries.append((key, str(md_file), content))

    if entries:
        conn.executemany(
            "INSERT INTO memories(key, filepath, content) VALUES (?, ?, ?)", entries
        )
        conn.commit()


async def _memory_search(query: str, limit: int = 5) -> str:
    cfg = get_config()
    if not cfg.memory_enabled:
        return "Memory is disabled (MEMORY_ENABLED=false)"

    try:
        conn = _get_fts_db()
        _build_fts_index(conn)
        rows = conn.execute(
            "SELECT key, content FROM memories WHERE memories MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
        conn.close()

        if not rows:
            return f"No memory entries matched '{query}'."

        results = []
        for key, content in rows:
            # Show first 800 chars to avoid overwhelming context
            preview = content[:800] + ("…" if len(content) > 800 else "")
            results.append(f"### [{key}]\n{preview}")
        return "\n\n---\n\n".join(results)
    except Exception as e:
        logger.exception("memory_search failed")
        return f"Error searching memory: {e}"


async def _memory_get(key: str) -> str:
    cfg = get_config()
    if not cfg.memory_enabled:
        return "Memory is disabled (MEMORY_ENABLED=false)"

    # Special case: MEMORY → top-level MEMORY.md
    if key.upper() == "MEMORY":
        path = _get_memory_md_path()
    else:
        path = _get_memory_dir() / f"{key}.md"

    if not path.exists():
        return (
            f"No memory file found for key '{key}'. "
            f"Available keys: {_list_keys()}"
        )

    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        return f"Error reading memory '{key}': {e}"


async def _memory_write(key: str, content: str, append: bool = False) -> str:
    cfg = get_config()
    if not cfg.memory_enabled:
        return "Memory is disabled (MEMORY_ENABLED=false)"

    # Sanitize key — no path traversal
    safe_key = key.replace("/", "_").replace("\\", "_").strip(". ")
    if not safe_key:
        return "Invalid key"

    path = _get_memory_dir() / f"{safe_key}.md"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        if append and path.exists():
            existing = path.read_text(encoding="utf-8")
            updated = f"{existing.rstrip()}\n\n<!-- updated {now} -->\n{content}"
            path.write_text(updated, encoding="utf-8")
            return f"Memory '{safe_key}' updated (appended)"
        else:
            header = f"# {safe_key}\n\n_Last updated: {now}_\n\n"
            path.write_text(header + content, encoding="utf-8")
            return f"Memory '{safe_key}' saved → workspace/memory/{safe_key}.md"
    except Exception as e:
        return f"Error saving memory '{safe_key}': {e}"


def _list_keys() -> str:
    memory_dir = _get_memory_dir()
    keys = [f.stem for f in sorted(memory_dir.glob("*.md")) if f.name != "README.md"]
    if _get_memory_md_path().exists():
        keys = ["MEMORY"] + keys
    return ", ".join(keys) if keys else "(none yet)"
