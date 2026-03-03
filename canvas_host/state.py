"""
Canvas state — SQLite-backed store for canvas session content.

Each canvas session tracks:
  - content: the most recent content pushed by the agent
  - url: the current navigated URL (for navigate action)
  - visible: whether the canvas is shown (present/hide)
  - history: list of content items (for undo/replay)
  - metadata: arbitrary key-value pairs (title, theme, etc.)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CanvasSession:
    session_id: str
    content: dict | None = None      # most recent content pushed
    url: str | None = None           # current URL (navigate action)
    visible: bool = True
    title: str = ""
    created_at: str = ""
    updated_at: str = ""
    metadata: dict = field(default_factory=dict)


class CanvasStateStore:
    """SQLite-backed store for canvas session state."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS canvas_sessions (
                session_id   TEXT PRIMARY KEY,
                content      TEXT,
                url          TEXT,
                visible      INTEGER NOT NULL DEFAULT 1,
                title        TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                metadata     TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS canvas_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   TEXT NOT NULL,
                action       TEXT NOT NULL,
                payload      TEXT NOT NULL,
                created_at   TEXT NOT NULL
            );
        """)
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    def upsert_session(self, session: CanvasSession) -> None:
        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        if not session.created_at:
            session.created_at = now
        session.updated_at = now
        conn = self._connect()
        conn.execute(
            """
            INSERT OR REPLACE INTO canvas_sessions
            (session_id, content, url, visible, title, created_at, updated_at, metadata)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                session.session_id,
                json.dumps(session.content) if session.content is not None else None,
                session.url,
                int(session.visible),
                session.title,
                session.created_at,
                session.updated_at,
                json.dumps(session.metadata),
            ),
        )
        conn.commit()
        conn.close()

    def get_session(self, session_id: str) -> CanvasSession | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM canvas_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        conn.close()
        if not row:
            return None
        return CanvasSession(
            session_id=row["session_id"],
            content=json.loads(row["content"]) if row["content"] else None,
            url=row["url"],
            visible=bool(row["visible"]),
            title=row["title"] or "",
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
            metadata=json.loads(row["metadata"] or "{}"),
        )

    def list_sessions(self) -> list[CanvasSession]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM canvas_sessions ORDER BY updated_at DESC"
        ).fetchall()
        conn.close()
        sessions = []
        for row in rows:
            sessions.append(CanvasSession(
                session_id=row["session_id"],
                content=json.loads(row["content"]) if row["content"] else None,
                url=row["url"],
                visible=bool(row["visible"]),
                title=row["title"] or "",
                created_at=row["created_at"] or "",
                updated_at=row["updated_at"] or "",
                metadata=json.loads(row["metadata"] or "{}"),
            ))
        return sessions

    def delete_session(self, session_id: str) -> bool:
        conn = self._connect()
        affected = conn.execute(
            "DELETE FROM canvas_sessions WHERE session_id=?", (session_id,)
        ).rowcount
        conn.commit()
        conn.close()
        return affected > 0

    # ------------------------------------------------------------------
    # Helpers: update individual fields
    # ------------------------------------------------------------------

    def set_content(self, session_id: str, content: dict) -> None:
        s = self.get_session(session_id) or CanvasSession(session_id=session_id)
        s.content = content
        s.visible = True
        self.upsert_session(s)

    def set_url(self, session_id: str, url: str) -> None:
        s = self.get_session(session_id) or CanvasSession(session_id=session_id)
        s.url = url
        s.content = {"kind": "url", "url": url}
        s.visible = True
        self.upsert_session(s)

    def set_visible(self, session_id: str, visible: bool) -> None:
        s = self.get_session(session_id) or CanvasSession(session_id=session_id)
        s.visible = visible
        self.upsert_session(s)

    def set_title(self, session_id: str, title: str) -> None:
        s = self.get_session(session_id) or CanvasSession(session_id=session_id)
        s.title = title
        self.upsert_session(s)

    # ------------------------------------------------------------------
    # History log
    # ------------------------------------------------------------------

    def log_action(self, session_id: str, action: str, payload: Any) -> None:
        try:
            conn = self._connect()
            now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            conn.execute(
                "INSERT INTO canvas_history(session_id, action, payload, created_at) VALUES(?,?,?,?)",
                (session_id, action, json.dumps(payload), now),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug("canvas history log error: %s", e)


# Module-level singleton
_store: CanvasStateStore | None = None


def get_canvas_store() -> CanvasStateStore:
    global _store
    if _store is None:
        from config import get_config
        _store = CanvasStateStore(get_config().canvas_db)
    return _store
