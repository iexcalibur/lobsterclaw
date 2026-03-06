"""Tests for Google Workspace account registry persistence and validation."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

from pytest import raises


def _accounts_file_env(monkeypatch, tmp_path: Path) -> Path:
    path = tmp_path / "google_workspace" / "accounts.json"
    monkeypatch.setenv("GOOGLE_WORKSPACE_ACCOUNTS_FILE", str(path))
    # Keep config validation happy in this repo's test env.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test:token")
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "123456789")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

    import config as config_mod
    config_mod._config = None
    importlib.reload(config_mod)

    return path


def test_add_and_list_with_redaction(monkeypatch, tmp_path):
    accounts_file = _accounts_file_env(monkeypatch, tmp_path)
    from agent.google_workspace_accounts import add_account, list_accounts, load_accounts

    created = add_account(label="Personal", email="alice@example.com")

    assert created["label"] == "Personal"
    assert created["email"] != "alice@example.com"
    assert created["email_masked"] != "alice@example.com"

    items = list_accounts(redact=True)
    assert len(items) == 1
    assert items[0]["label"] == "Personal"
    assert items[0]["email_masked"]

    raw = list_accounts(redact=False)
    assert raw[0]["email"] == "alice@example.com"

    stored = json.loads(accounts_file.read_text(encoding="utf-8"))
    assert stored["version"] == 1
    assert stored["accounts"]


def test_duplicate_email_case_insensitive_and_label_guard(monkeypatch, tmp_path):
    _accounts_file_env(monkeypatch, tmp_path)
    from agent.google_workspace_accounts import add_account

    add_account(label="Work", email="ALICE@example.com")

    with raises(ValueError, match="already exists"):
        add_account(label="Work Backup", email="alice@example.com")

    with raises(ValueError, match="already exists"):
        add_account(label="work", email="alice+alias@example.com")


def test_remove_by_email_or_label_id(monkeypatch, tmp_path):
    _accounts_file_env(monkeypatch, tmp_path)
    from agent.google_workspace_accounts import add_account, list_accounts, remove_account

    primary = add_account(label="Primary", email="primary@example.com")
    secondary = add_account(label="Secondary", email="secondary@example.com")

    removed = remove_account("  PRIMARY@Example.Com")
    assert removed is not None
    assert removed["label"] == "Primary"

    remaining = list_accounts()
    assert len(remaining) == 1
    assert remaining[0]["label"] == "Secondary"

    removed = remove_account(secondary["id"])
    assert removed is not None
    assert removed["id"] == secondary["id"]

    assert not list_accounts()


def test_redaction_drops_sensitive_keys(monkeypatch, tmp_path):
    accounts_file = _accounts_file_env(monkeypatch, tmp_path)
    accounts_file.parent.mkdir(parents=True, exist_ok=True)
    accounts_file.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "label": "Legacy",
                        "email": "legacy@example.com",
                        "source": "cli",
                        "status": "active",
                        "added_at": "2026-03-06T00:00:00Z",
                        "id": "legacy-1",
                        "access_token": "abc123",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    from agent.google_workspace_accounts import list_accounts

    items = list_accounts()
    assert len(items) == 1
    assert "access_token" not in items[0]
    assert "email_masked" in items[0]
