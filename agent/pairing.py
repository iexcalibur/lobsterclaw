"""
Pairing store — persists user pairings when TELEGRAM_DM_POLICY=pairing.

Flow:
  1. Unknown user sends any message (or /start) to the bot in a DM.
  2. Bot saves a pending record and sends the owner an Approve/Deny message.
  3. Owner presses Approve → user added to approved_pairings table → allowed.
  4. Owner presses Deny  → pending record removed → user stays blocked.

Revocation: call revoke(user_id) to remove an approved user.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class PairingStore:
    """SQLite-backed store for user pairing state."""

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
            CREATE TABLE IF NOT EXISTS approved_pairings (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                first_name TEXT,
                approved_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pending_pairings (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                first_name  TEXT,
                request_id  TEXT NOT NULL UNIQUE,
                requested_at TEXT NOT NULL
            );
        """)
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def is_approved(self, user_id: int) -> bool:
        conn = self._connect()
        row = conn.execute(
            "SELECT 1 FROM approved_pairings WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()
        return bool(row)

    def is_pending(self, user_id: int) -> bool:
        conn = self._connect()
        row = conn.execute(
            "SELECT 1 FROM pending_pairings WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()
        return bool(row)

    def get_pending_by_request_id(self, request_id: str) -> dict | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT user_id, username, first_name FROM pending_pairings WHERE request_id=?",
            (request_id,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def list_approved(self) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT user_id, username, first_name, approved_at FROM approved_pairings"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def list_pending(self) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT user_id, username, first_name, requested_at FROM pending_pairings"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def add_pending(
        self, user_id: int, username: str, first_name: str, request_id: str
    ) -> None:
        conn = self._connect()
        now = datetime.utcnow().isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO pending_pairings"
            "(user_id, username, first_name, request_id, requested_at)"
            " VALUES (?,?,?,?,?)",
            (user_id, username, first_name, request_id, now),
        )
        conn.commit()
        conn.close()

    def approve(self, request_id: str) -> dict | None:
        """Approve pending request. Returns user info dict or None if not found."""
        user = self.get_pending_by_request_id(request_id)
        if not user:
            return None
        conn = self._connect()
        now = datetime.utcnow().isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO approved_pairings"
            "(user_id, username, first_name, approved_at) VALUES (?,?,?,?)",
            (user["user_id"], user["username"], user["first_name"], now),
        )
        conn.execute(
            "DELETE FROM pending_pairings WHERE request_id=?", (request_id,)
        )
        conn.commit()
        conn.close()
        logger.info("Pairing approved: user_id=%s (%s)", user["user_id"], user["username"])
        return user

    def deny(self, request_id: str) -> dict | None:
        """Deny pending request. Returns user info dict or None if not found."""
        user = self.get_pending_by_request_id(request_id)
        if not user:
            return None
        conn = self._connect()
        conn.execute(
            "DELETE FROM pending_pairings WHERE request_id=?", (request_id,)
        )
        conn.commit()
        conn.close()
        logger.info("Pairing denied: user_id=%s (%s)", user["user_id"], user["username"])
        return user

    def revoke(self, user_id: int) -> bool:
        """Remove an approved user. Returns True if a row was deleted."""
        conn = self._connect()
        affected = conn.execute(
            "DELETE FROM approved_pairings WHERE user_id=?", (user_id,)
        ).rowcount
        conn.commit()
        conn.close()
        if affected:
            logger.info("Pairing revoked: user_id=%s", user_id)
        return affected > 0


# Module-level singleton — created lazily by get_pairing_store()
_store: PairingStore | None = None


def get_pairing_store() -> PairingStore:
    global _store
    if _store is None:
        from config import get_config
        cfg = get_config()
        db_path = cfg.data_path / "pairing.db"
        _store = PairingStore(db_path)
    return _store
