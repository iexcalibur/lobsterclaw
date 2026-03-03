from __future__ import annotations

import json
import sqlite3
import logging
from pathlib import Path

from config import get_config
from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

MEMORY_SEARCH_TOOL = ToolDefinition(
    name="memory_search",
    description="Search long-term memory for relevant notes/facts using keyword search.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Keywords to search for"},
            "limit": {"type": "integer", "description": "Max results to return (default 5)", "default": 5},
        },
        "required": ["query"],
    },
    fn=lambda **kw: _memory_search(**kw),
)

MEMORY_GET_TOOL = ToolDefinition(
    name="memory_get",
    description="Read a specific memory entry by its key/name.",
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "The memory key/name to retrieve"},
        },
        "required": ["key"],
    },
    fn=lambda **kw: _memory_get(**kw),
)

MEMORY_WRITE_TOOL = ToolDefinition(
    name="memory_write",
    description="Save or update a memory entry. Use to remember facts about the user or ongoing tasks.",
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Short unique name for this memory"},
            "content": {"type": "string", "description": "The content to remember"},
        },
        "required": ["key", "content"],
    },
    fn=lambda **kw: _memory_write(**kw),
)


def _get_db() -> sqlite3.Connection:
    cfg = get_config()
    db_path = cfg.memory_path / "memory.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memories USING fts5(
            key UNINDEXED,
            content,
            updated_at UNINDEXED,
            tokenize='porter ascii'
        )
    """)
    conn.commit()
    return conn


async def _memory_search(query: str, limit: int = 5) -> str:
    cfg = get_config()
    if not cfg.memory_enabled:
        return "Memory is disabled (MEMORY_ENABLED=false)"

    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT key, content, updated_at FROM memories WHERE memories MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
        conn.close()

        if not rows:
            return "No matching memories found."
        lines = []
        for key, content, updated_at in rows:
            lines.append(f"**{key}** (updated: {updated_at})\n{content}")
        return "\n\n---\n\n".join(lines)
    except Exception as e:
        return f"Error searching memory: {e}"


async def _memory_get(key: str) -> str:
    cfg = get_config()
    if not cfg.memory_enabled:
        return "Memory is disabled (MEMORY_ENABLED=false)"

    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT content, updated_at FROM memories WHERE key = ?", (key,)
        ).fetchone()
        conn.close()
        if not row:
            return f"No memory found with key '{key}'"
        content, updated_at = row
        return f"**{key}** (updated: {updated_at})\n{content}"
    except Exception as e:
        return f"Error reading memory: {e}"


async def _memory_write(key: str, content: str) -> str:
    cfg = get_config()
    if not cfg.memory_enabled:
        return "Memory is disabled (MEMORY_ENABLED=false)"

    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")

    try:
        conn = _get_db()
        # Delete existing entry if present, then insert
        conn.execute("DELETE FROM memories WHERE key = ?", (key,))
        conn.execute("INSERT INTO memories(key, content, updated_at) VALUES (?, ?, ?)", (key, content, now))
        conn.commit()
        conn.close()
        return f"Memory saved: '{key}'"
    except Exception as e:
        return f"Error saving memory: {e}"
