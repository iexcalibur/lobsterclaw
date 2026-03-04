"""
Conversation history manager — persisted to SQLite so it survives restarts.

Mirrors OpenClaw's per-session conversation history with trimming.
Each user has an independent history keyed by user_id.

Table schema:
  history (id INTEGER PK, user_id TEXT, role TEXT, content TEXT, ts TEXT)

On load, the last max_history_messages entries are loaded into memory.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from config import get_config

logger = logging.getLogger(__name__)


@dataclass
class Message:
    role: str   # "user" | "assistant" | "system"
    content: str | list  # str for text, list for structured blocks (tool_use, images, etc.)


class HistoryManager:
    def __init__(self) -> None:
        self.cfg = get_config()
        self._cache: dict[str, list[Message]] = {}
        self._db_path = self.cfg.data_path / "history.db"
        self._init_db()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                role    TEXT NOT NULL,
                content TEXT NOT NULL,
                ts      TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id, id)")
        conn.commit()
        conn.close()

    def _load_from_db(self, user_id: str) -> list[Message]:
        """Load the most recent max_history_messages from DB."""
        limit = self.cfg.max_history_messages
        conn = sqlite3.connect(str(self._db_path))
        rows = conn.execute(
            """
            SELECT role, content FROM history
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        conn.close()
        # Reverse so oldest-first
        messages = []
        for r, c in reversed(rows):
            content: str | list = c
            if c.startswith("["):
                try:
                    parsed = json.loads(c)
                    if isinstance(parsed, list):
                        content = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
            messages.append(Message(role=r, content=content))
        return messages

    def _persist(self, user_id: str, role: str, content: str) -> None:
        try:
            now = datetime.now().isoformat(timespec="seconds")
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                "INSERT INTO history(user_id, role, content, ts) VALUES (?,?,?,?)",
                (user_id, role, content, now),
            )
            # Prune old rows — keep max_history_messages + 20 buffer to avoid excessive DB growth
            # Using the same limit as the in-memory cache prevents them from drifting out of sync.
            max_keep = self.cfg.max_history_messages + 20
            conn.execute(
                """
                DELETE FROM history WHERE id IN (
                    SELECT id FROM history WHERE user_id=?
                    ORDER BY id DESC LIMIT -1 OFFSET ?
                )
                """,
                (user_id, max_keep),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("History persist failed: %s", e)

    def add(self, user_id: str, role: str, content: str | list) -> None:
        if user_id not in self._cache:
            self._cache[user_id] = self._load_from_db(user_id)

        self._cache[user_id].append(Message(role=role, content=content))

        # Trim in-memory to max
        max_msgs = self.cfg.max_history_messages
        if len(self._cache[user_id]) > max_msgs:
            self._cache[user_id] = self._cache[user_id][-max_msgs:]

        db_content = json.dumps(content, ensure_ascii=False) if isinstance(content, list) else content
        self._persist(user_id, role, db_content)

    def get_for_llm(self, user_id: str) -> list[dict]:
        if user_id not in self._cache:
            self._cache[user_id] = self._load_from_db(user_id)
        return [
            {"role": m.role, "content": m.content}
            for m in self._cache[user_id]
        ]

    def clear(self, user_id: str) -> None:
        self._cache[user_id] = []
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute("DELETE FROM history WHERE user_id=?", (user_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("History clear failed: %s", e)

    def last_n(self, user_id: str, n: int) -> list[Message]:
        if user_id not in self._cache:
            self._cache[user_id] = self._load_from_db(user_id)
        return self._cache[user_id][-n:]
