"""Google Workspace account registry (Phase 1).

This module stores a small JSON inventory used by the Gateway UI/API.

The registry supports:
- add/remove/list operations for connected accounts
- strict duplicate prevention (label + email)
- safe, masked output for dashboard reads

No token values are emitted from this module; callers must handle token
storage/loading separately in a later phase.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import get_config


_SENSITIVE_HINTS = ("token", "secret", "api_key", "password", "oauth", "refresh", "id_token")


@dataclass
class GoogleWorkspaceAccount:
    """Lightweight account record persisted to accounts.json."""

    label: str
    email: str
    source: str
    status: str
    added_at: str
    id: str

    def normalize_label(self) -> str:
        return " ".join(self.label.strip().split()).casefold()

    def normalize_email(self) -> str:
        return self.email.strip().casefold()

    def normalize_id(self) -> str:
        return self.id.strip().casefold()


def _accounts_path(accounts_file: str | None = None) -> Path:
    cfg = get_config()
    path = Path(accounts_file or cfg.google_workspace_accounts_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_identifier(identifier: str) -> str:
    """Normalization used for label/email/id comparisons."""
    return " ".join(identifier.strip().split()).casefold()


def _mask_email(email: str) -> str:
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if not local:
        return "***@***"

    if len(local) <= 2:
        masked_local = local[0] + "***" if local else "***"
    else:
        masked_local = f"{local[:2]}***{local[-1]}"

    if not domain:
        return f"{masked_local}@***"

    parts = domain.split(".")
    if len(parts) >= 2 and parts[0]:
        masked_domain = f"{parts[0][:2]}***.{parts[-1]}"
    else:
        masked_domain = "***"

    return f"{masked_local}@{masked_domain}"


def _sanitize_record(record: dict[str, Any]) -> dict[str, str]:
    """Redact sensitive-looking keys and ensure stable output keys."""
    sanitized: dict[str, str] = {}
    for key, value in record.items():
        lower_key = key.lower()
        if any(hint in lower_key for hint in _SENSITIVE_HINTS):
            continue
        sanitized[key] = value if value is not None else ""
    return sanitized


def _read_accounts_file(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in accounts file: {path}") from exc

    if isinstance(raw, dict):
        items = raw.get("accounts", [])
    else:
        items = raw

    if not isinstance(items, list):
        raise ValueError(f"Invalid accounts file format: {path}")

    return [dict(item) for item in items if isinstance(item, dict)]


def _write_accounts_file(path: Path, accounts: list[dict[str, str]]) -> None:
    payload = {
        "version": 1,
        "accounts": accounts,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _coerce_account(raw: dict[str, Any]) -> GoogleWorkspaceAccount:
    label = str(raw.get("label", "")).strip()
    email = str(raw.get("email", "")).strip()
    source = str(raw.get("source", "manual")).strip() or "manual"
    status = str(raw.get("status", "active")).strip() or "active"
    added_at = str(raw.get("added_at", "")).strip()
    account_id = str(raw.get("id", "")).strip() or uuid.uuid4().hex

    if not label:
        raise ValueError("Account 'label' is required")
    if not email or "@" not in email:
        raise ValueError("Account 'email' must be a valid email address")

    return GoogleWorkspaceAccount(
        label=label,
        email=email,
        source=source,
        status=status,
        added_at=added_at,
        id=account_id,
    )


def load_accounts(accounts_file: str | None = None) -> list[dict[str, str]]:
    """Load raw accounts from file and normalize shape."""
    path = _accounts_path(accounts_file)
    loaded = _read_accounts_file(path)
    return [
        {
            **{
                "id": acct.id,
                "label": acct.label,
                "email": acct.email,
                "source": acct.source,
                "status": acct.status,
                "added_at": acct.added_at,
            }
        }
        for acct in [_coerce_account(entry) for entry in loaded]
    ]


def _to_display_record(account: dict[str, str]) -> dict[str, str]:
    safe = _sanitize_record(_coerce_account(account).__dict__)
    safe["email_masked"] = _mask_email(account.get("email", ""))
    if "email" in safe:
        safe["email"] = _mask_email(safe["email"])  # backward-safe, still never raw
    return safe


def list_accounts(
    *,
    redact: bool = True,
    accounts_file: str | None = None,
) -> list[dict[str, str]]:
    """Return accounts for read/write paths.

    - redact=True: masked output for UI/API
    - redact=False: internal use only (still filters sensitive keys)
    """
    accounts = load_accounts(accounts_file=accounts_file)
    if not redact:
        return [_sanitize_record(a) for a in accounts]
    return [_to_display_record(a) for a in accounts]


def _load_lookup_maps(accounts: list[dict[str, str]]) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    by_label = {}
    by_email = {}
    by_id = {}
    for idx, acct in enumerate(accounts):
        label_key = _normalize_identifier(acct["label"])
        email_key = _normalize_identifier(acct["email"])
        id_key = _normalize_identifier(acct["id"])
        by_label[label_key] = by_label.get(label_key, idx)
        by_email[email_key] = by_email.get(email_key, idx)
        by_id[id_key] = by_id.get(id_key, idx)
    return by_label, by_email, by_id


def add_account(
    *,
    label: str,
    email: str,
    source: str = "manual",
    status: str = "active",
    accounts_file: str | None = None,
) -> dict[str, str]:
    accounts = load_accounts(accounts_file=accounts_file)
    by_label, by_email, _ = _load_lookup_maps(accounts)

    normalized_label = _normalize_identifier(label)
    normalized_email = _normalize_identifier(email)

    if not normalized_label:
        raise ValueError("label is required")
    if not normalized_email or "@" not in normalized_email:
        raise ValueError("email is required and must include '@'")

    if normalized_label in by_label:
        existing = accounts[by_label[normalized_label]]
        raise ValueError(f"Account with label '{existing['label']}' already exists")
    if normalized_email in by_email:
        existing = accounts[by_email[normalized_email]]
        raise ValueError(f"Account with email '{_mask_email(existing['email'])}' already exists")

    acct = GoogleWorkspaceAccount(
        label=label.strip(),
        email=email.strip(),
        source=source.strip() or "manual",
        status=status.strip() or "active",
        added_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        id=uuid.uuid4().hex,
    )

    accounts.append(_sanitize_record(acct.__dict__))
    path = _accounts_path(accounts_file)
    _write_accounts_file(path, accounts)
    return _to_display_record(acct.__dict__)


def remove_account(
    identifier: str,
    *,
    accounts_file: str | None = None,
) -> dict[str, str] | None:
    accounts = load_accounts(accounts_file=accounts_file)
    normalized = _normalize_identifier(identifier)
    if not normalized:
        raise ValueError("identifier is required")

    by_label, by_email, by_id = _load_lookup_maps(accounts)

    idx = by_label.get(normalized)
    if idx is None:
        idx = by_email.get(normalized)
    if idx is None:
        idx = by_id.get(normalized)
    if idx is None:
        return None

    removed = accounts.pop(idx)
    path = _accounts_path(accounts_file)
    _write_accounts_file(path, accounts)
    return _to_display_record(removed)


def sync_from_cli(*, accounts_file: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    """Prepare a future CLI sync command without executing it.

    Returns the command template and current target path so this phase can stay
    dependency-free while preserving extensibility for Phase 2.
    """
    cfg = get_config()
    command = cfg.google_workspace_cli.strip() or "googleworkspace account list"
    return {
        "enabled": bool(cfg.google_workspace_cli_enabled),
        "dry_run": dry_run,
        "command": command,
        "accounts_file": str(_accounts_path(accounts_file)),
        "message": "CLI sync is planned for a later phase (no execution in Phase 1).",
    }
