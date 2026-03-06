"""
Google Workspace OAuth (Phase 2) — token storage and credential loading.

Stores OAuth refresh tokens per account. Tokens are never returned to the UI;
only used internally by Gmail/Calendar tools.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from config import get_config

logger = logging.getLogger(__name__)

# Full Google Workspace scopes — Gmail + Calendar.
# NOTE: adding Calendar scopes requires users to re-connect their Google account
# in the Gateway UI so Google can issue a new consent with the expanded permissions.
WORKSPACE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

# Backward-compat alias used by gateway/server.py OAuth flow
GMAIL_SCOPES = WORKSPACE_SCOPES


def _tokens_path(accounts_file: str | None = None) -> Path:
    """Tokens stored alongside accounts in tokens/ subdir."""
    cfg = get_config()
    base = Path(
        accounts_file or cfg.google_workspace_accounts_file
    ).expanduser().parent
    path = base / "tokens.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_tokens(path: Path) -> dict[str, dict[str, Any]]:
    """Load tokens file: { account_id: { refresh_token, ... } }."""
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read tokens file %s: %s", path, e)
        return {}

    if not isinstance(raw, dict):
        return {}

    return {k: dict(v) for k, v in raw.items() if isinstance(v, dict)}


def _write_tokens(path: Path, data: dict[str, dict[str, Any]]) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def has_oauth_tokens(account_id: str, accounts_file: str | None = None) -> bool:
    """Return True if account has stored OAuth tokens."""
    path = _tokens_path(accounts_file)
    tokens = _read_tokens(path)
    entry = tokens.get(account_id)
    return bool(entry and entry.get("refresh_token"))


def store_oauth_tokens(
    account_id: str,
    refresh_token: str,
    access_token: str | None = None,
    token_expiry: Any = None,
    accounts_file: str | None = None,
) -> None:
    """Store OAuth tokens for an account. Overwrites existing."""
    path = _tokens_path(accounts_file)
    data = _read_tokens(path)
    data[account_id] = {
        "refresh_token": refresh_token,
        "access_token": access_token or "",
        "token_expiry": str(token_expiry) if token_expiry else "",
    }
    _write_tokens(path, data)
    logger.info("Stored OAuth tokens for account %s", account_id[:8])


def clear_oauth_tokens(account_id: str, accounts_file: str | None = None) -> None:
    """Remove stored tokens for an account."""
    path = _tokens_path(accounts_file)
    data = _read_tokens(path)
    if account_id in data:
        del data[account_id]
        _write_tokens(path, data)
        logger.info("Cleared OAuth tokens for account %s", account_id[:8])


def get_credentials_for_account_id(account_id: str, accounts_file: str | None = None):
    """
    Build google.oauth2.credentials.Credentials for an account.
    Returns (Credentials, None) on success, (None, error_msg) on failure.
    """
    from config import get_config

    path = _tokens_path(accounts_file)
    tokens = _read_tokens(path)
    entry = tokens.get(account_id)
    if not entry or not entry.get("refresh_token"):
        return None, "Account not OAuth-connected"

    cfg = get_config()
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = Credentials(
        token=entry.get("access_token") or None,
        refresh_token=entry.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cfg.google_oauth_client_id,
        client_secret=cfg.google_oauth_client_secret,
        scopes=WORKSPACE_SCOPES,
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds, None
