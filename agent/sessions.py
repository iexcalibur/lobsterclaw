"""
Session store — SQLite-backed registry of all agent sessions.

A session is a named, isolated conversation context. The main user conversation
is session "main". Sub-agent spawns create child sessions with their own history.

This mirrors OpenClaw's session management (sessions.patch, sessions.delete,
sessions.list, session transcript store).

Schema:
  sessions(id, label, parent_id, status, model, created_at, updated_at, depth)
  session_messages(id, session_id, role, content, created_at)
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAIN_SESSION_ID = "main"
MAX_SESSION_DEPTH = 3
MAX_CHILDREN_PER_SESSION = 5


@dataclass
class SessionRecord:
    id: str
    label: str
    parent_id: str | None
    status: str          # "active" | "completed" | "error" | "cancelled"
    model: str
    created_at: str
    updated_at: str
    depth: int
    token_usage: int = 0      # legacy total (input + output)
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


@dataclass
class SessionMessage:
    session_id: str
    role: str
    content: str
    created_at: str


class SessionStore:
    """Thread-safe SQLite-backed session and message store."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(db_path)
        self._lock = asyncio.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id            TEXT PRIMARY KEY,
                label         TEXT NOT NULL,
                parent_id     TEXT,
                status        TEXT NOT NULL DEFAULT 'active',
                model         TEXT NOT NULL DEFAULT '',
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL,
                depth         INTEGER NOT NULL DEFAULT 0,
                token_usage   INTEGER NOT NULL DEFAULT 0,
                input_tokens  INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                error         TEXT
            );
            CREATE TABLE IF NOT EXISTS session_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session ON session_messages(session_id);
        """)
        # Schema migration: add input_tokens/output_tokens columns to existing databases
        for col in ("input_tokens", "output_tokens"):
            try:
                conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
                conn.commit()
            except Exception:
                pass  # column already exists
        conn.commit()
        conn.close()

        # Ensure main session always exists
        self._ensure_main_session()

    def _ensure_main_session(self) -> None:
        conn = self._connect()
        now = _now()
        conn.execute(
            """
            INSERT OR IGNORE INTO sessions (id, label, parent_id, status, model, created_at, updated_at, depth)
            VALUES (?, ?, NULL, 'active', '', ?, ?, 0)
            """,
            (MAIN_SESSION_ID, "Main", now, now),
        )
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    async def create_session(
        self,
        label: str,
        parent_id: str | None = None,
        model: str = "",
        depth: int = 0,
    ) -> SessionRecord:
        async with self._lock:
            session_id = str(uuid.uuid4())[:8]
            now = _now()
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO sessions (id, label, parent_id, status, model, created_at, updated_at, depth)
                VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (session_id, label, parent_id, model, now, now, depth),
            )
            conn.commit()
            conn.close()
            logger.info("Created session %s (label=%s depth=%d)", session_id, label, depth)
            return SessionRecord(
                id=session_id, label=label, parent_id=parent_id,
                status="active", model=model,
                created_at=now, updated_at=now, depth=depth,
            )

    async def get_session(self, session_id: str) -> SessionRecord | None:
        conn = self._connect()
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        conn.close()
        return _row_to_session(row) if row else None

    async def update_session_status(
        self,
        session_id: str,
        status: str,
        error: str | None = None,
        token_usage: int | None = None,
    ) -> None:
        async with self._lock:
            conn = self._connect()
            if token_usage is not None:
                conn.execute(
                    "UPDATE sessions SET status=?, error=?, token_usage=?, updated_at=? WHERE id=?",
                    (status, error, token_usage, _now(), session_id),
                )
            else:
                conn.execute(
                    "UPDATE sessions SET status=?, error=?, updated_at=? WHERE id=?",
                    (status, error, _now(), session_id),
                )
            conn.commit()
            conn.close()

    async def list_sessions(
        self,
        status: str | None = None,
        parent_id: str | None = None,
        limit: int = 20,
    ) -> list[SessionRecord]:
        conn = self._connect()
        if status and parent_id:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE status=? AND parent_id=? ORDER BY updated_at DESC LIMIT ?",
                (status, parent_id, limit),
            ).fetchall()
        elif status:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE status=? ORDER BY updated_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        elif parent_id:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE parent_id=? ORDER BY updated_at DESC LIMIT ?",
                (parent_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        conn.close()
        return [_row_to_session(r) for r in rows]

    async def count_active_children(self, parent_id: str) -> int:
        conn = self._connect()
        count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE parent_id=? AND status='active'",
            (parent_id,),
        ).fetchone()[0]
        conn.close()
        return count

    # ------------------------------------------------------------------
    # Message history per session
    # ------------------------------------------------------------------

    async def append_message(self, session_id: str, role: str, content: str) -> None:
        async with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT INTO session_messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, _now()),
            )
            conn.execute(
                "UPDATE sessions SET updated_at=? WHERE id=?", (_now(), session_id)
            )
            conn.commit()
            conn.close()

    async def get_messages(self, session_id: str, limit: int = 100) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT role, content FROM session_messages
            WHERE session_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        conn.close()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    async def get_messages_formatted(
        self,
        session_id: str,
        limit: int = 50,
        include_tools: bool = True,
    ) -> str:
        messages = await self.get_messages(session_id, limit)
        if not messages:
            return "No messages found."
        lines = []
        for msg in messages:
            role = msg["role"].upper()
            content = msg["content"]
            # Skip tool-use / tool-result blocks when include_tools is False
            if not include_tools and role in ("TOOL", "TOOL_RESULT"):
                continue
            # Also skip assistant blocks that are purely tool_use JSON arrays
            if not include_tools and role == "ASSISTANT":
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, list) and all(
                        isinstance(b, dict) and b.get("type") in ("tool_use", "thinking")
                        for b in parsed
                    ):
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass
            if len(content) > 500:
                content = content[:500] + "…"
            lines.append(f"[{role}]: {content}")
        return "\n\n".join(lines) if lines else "No user/assistant messages found."

    async def add_token_usage(
        self,
        session_id: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """
        Increment token counters for a session.
        Mirrors OpenClaw's session token tracking (totalTokens after each turn).
        """
        async with self._lock:
            conn = self._connect()
            total = input_tokens + output_tokens
            conn.execute(
                """
                UPDATE sessions
                SET input_tokens  = input_tokens  + ?,
                    output_tokens = output_tokens + ?,
                    token_usage   = token_usage   + ?,
                    updated_at    = ?
                WHERE id = ?
                """,
                (input_tokens, output_tokens, total, _now(), session_id),
            )
            conn.commit()
            conn.close()

    async def get_token_usage(self, session_id: str) -> dict[str, int]:
        """Return {input_tokens, output_tokens, total_tokens} for a session."""
        conn = self._connect()
        row = conn.execute(
            "SELECT input_tokens, output_tokens, token_usage FROM sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        conn.close()
        if not row:
            return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        return {
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "total_tokens": row["token_usage"],
        }

    async def delete_session(self, session_id: str) -> None:
        """Delete a session and all its messages."""
        async with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM session_messages WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            conn.commit()
            conn.close()


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _row_to_session(row: sqlite3.Row) -> SessionRecord:
    keys = row.keys()
    return SessionRecord(
        id=row["id"],
        label=row["label"],
        parent_id=row["parent_id"],
        status=row["status"],
        model=row["model"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        depth=row["depth"],
        token_usage=row["token_usage"],
        input_tokens=row["input_tokens"] if "input_tokens" in keys else 0,
        output_tokens=row["output_tokens"] if "output_tokens" in keys else 0,
        error=row["error"],
    )


# Global store instance (set by main.py)
_store: SessionStore | None = None


def set_session_store(store: SessionStore) -> None:
    global _store
    _store = store


def get_session_store() -> SessionStore:
    if _store is None:
        raise RuntimeError("SessionStore not initialized — call set_session_store() first")
    return _store
