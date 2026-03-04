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
import secrets
import sqlite3
import string
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)
_PAIRING_CODE_ALPHABET = string.ascii_uppercase + string.digits


def _utc_now() -> datetime:
    return datetime.now(UTC)


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
            CREATE TABLE IF NOT EXISTS pairing_codes (
                code         TEXT PRIMARY KEY,
                created_by   INTEGER,
                created_at   TEXT NOT NULL,
                expires_at   TEXT NOT NULL,
                max_uses     INTEGER NOT NULL DEFAULT 1,
                use_count    INTEGER NOT NULL DEFAULT 0,
                last_used_at TEXT,
                disabled     INTEGER NOT NULL DEFAULT 0
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

    def list_active_codes(self) -> list[dict]:
        """Return non-expired, non-disabled pairing codes with remaining uses."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT code, expires_at, max_uses, use_count FROM pairing_codes WHERE disabled=0"
        ).fetchall()
        conn.close()
        now = _utc_now()
        out: list[dict] = []
        for row in rows:
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            uses_left = int(row["max_uses"]) - int(row["use_count"])
            if expires_at > now and uses_left > 0:
                out.append(
                    {
                        "code": row["code"],
                        "expires_at": row["expires_at"],
                        "uses_left": uses_left,
                    }
                )
        out.sort(key=lambda x: x["expires_at"])
        return out

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def add_pending(
        self, user_id: int, username: str, first_name: str, request_id: str
    ) -> None:
        conn = self._connect()
        now = _utc_now().isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO pending_pairings"
            "(user_id, username, first_name, request_id, requested_at)"
            " VALUES (?,?,?,?,?)",
            (user_id, username, first_name, request_id, now),
        )
        conn.commit()
        conn.close()

    def create_pairing_code(
        self,
        *,
        created_by: int,
        ttl_minutes: int = 15,
        max_uses: int = 1,
        length: int = 8,
    ) -> str:
        """
        Create and persist a one-time pairing code.

        Returns:
            The generated uppercase alphanumeric code.
        """
        now = _utc_now()
        expires_at = now + timedelta(minutes=ttl_minutes)
        conn = self._connect()
        for _ in range(16):
            code = "".join(secrets.choice(_PAIRING_CODE_ALPHABET) for _ in range(length))
            try:
                conn.execute(
                    "INSERT INTO pairing_codes"
                    "(code, created_by, created_at, expires_at, max_uses, use_count, disabled)"
                    " VALUES (?,?,?,?,?,?,0)",
                    (
                        code,
                        created_by,
                        now.isoformat(timespec="seconds"),
                        expires_at.isoformat(timespec="seconds"),
                        max(1, int(max_uses)),
                        0,
                    ),
                )
                conn.commit()
                conn.close()
                return code
            except sqlite3.IntegrityError:
                continue
        conn.close()
        raise RuntimeError("Could not generate a unique pairing code")

    def redeem_code(
        self,
        *,
        code: str,
        user_id: int,
        username: str,
        first_name: str,
    ) -> tuple[bool, str]:
        """
        Redeem a pairing code and approve the user.

        Returns:
            (ok, status) where status is one of:
            approved | already_approved | invalid | expired | used
        """
        norm = "".join(ch for ch in code.upper() if ch.isalnum())
        if not norm:
            return False, "invalid"
        if self.is_approved(user_id):
            return True, "already_approved"

        conn = self._connect()
        row = conn.execute(
            "SELECT code, expires_at, max_uses, use_count, disabled"
            " FROM pairing_codes WHERE code=?",
            (norm,),
        ).fetchone()

        if not row:
            conn.close()
            return False, "invalid"

        now = _utc_now()
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        use_count = int(row["use_count"])
        max_uses = int(row["max_uses"])
        disabled = int(row["disabled"]) == 1

        if expires_at <= now:
            conn.execute("UPDATE pairing_codes SET disabled=1 WHERE code=?", (norm,))
            conn.commit()
            conn.close()
            return False, "expired"

        if use_count >= max_uses or disabled:
            conn.execute("UPDATE pairing_codes SET disabled=1 WHERE code=?", (norm,))
            conn.commit()
            conn.close()
            return False, "used"

        approved_at = now.isoformat(timespec="seconds")
        conn.execute(
            "INSERT OR REPLACE INTO approved_pairings"
            "(user_id, username, first_name, approved_at) VALUES (?,?,?,?)",
            (user_id, username, first_name, approved_at),
        )
        conn.execute("DELETE FROM pending_pairings WHERE user_id=?", (user_id,))

        new_use_count = use_count + 1
        disable = 1 if new_use_count >= max_uses else 0
        conn.execute(
            "UPDATE pairing_codes"
            " SET use_count=?, last_used_at=?, disabled=? WHERE code=?",
            (new_use_count, approved_at, disable, norm),
        )
        conn.commit()
        conn.close()
        logger.info("Pairing approved by code: user_id=%s (%s)", user_id, username)
        return True, "approved"

    def approve(self, request_id: str) -> dict | None:
        """Approve pending request. Returns user info dict or None if not found."""
        user = self.get_pending_by_request_id(request_id)
        if not user:
            return None
        conn = self._connect()
        now = _utc_now().isoformat()
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
