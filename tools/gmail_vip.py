"""
Gmail VIP sender list — persistent storage helpers.

VIP senders are email addresses that trigger proactive Telegram notifications
when new unread emails arrive. Stored at:
  ~/.lobsterclaw/google_workspace/vip_senders.json

Falls back to GMAIL_VIP_SENDERS in .env if the file is empty on first load.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from config import get_config

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _vip_path() -> Path:
    cfg = get_config()
    base = Path(cfg.google_workspace_accounts_file).expanduser().parent
    path = base / "vip_senders.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_raw(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(e).strip().lower() for e in data if e]
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read vip_senders.json: %s", e)
    return []


def _write(path: Path, senders: list[str]) -> None:
    path.write_text(json.dumps(sorted(senders), indent=2), encoding="utf-8")


def load_vip_senders() -> list[str]:
    """Return current VIP sender list. Seeds from .env on first use."""
    path = _vip_path()
    senders = _read_raw(path)
    if not senders:
        # Seed from config (GMAIL_VIP_SENDERS env var)
        cfg = get_config()
        env_senders = getattr(cfg, "gmail_vip_senders", [])
        senders = [e.strip().lower() for e in env_senders if e.strip()]
        if senders:
            _write(path, senders)
            logger.info("Seeded VIP senders from .env: %s", senders)
    return senders


def add_vip_sender(email: str) -> tuple[bool, str]:
    """Add email to VIP list. Returns (added, message)."""
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        return False, f"'{email}' is not a valid email address."
    path = _vip_path()
    senders = _read_raw(path)
    if email in senders:
        return False, f"{email} is already in the VIP list."
    senders.append(email)
    _write(path, senders)
    return True, f"Added {email} to VIP list."


def remove_vip_sender(email: str) -> tuple[bool, str]:
    """Remove email from VIP list. Returns (removed, message)."""
    email = email.strip().lower()
    path = _vip_path()
    senders = _read_raw(path)
    if email not in senders:
        return False, f"{email} is not in the VIP list."
    senders.remove(email)
    _write(path, senders)
    return True, f"Removed {email} from VIP list."
