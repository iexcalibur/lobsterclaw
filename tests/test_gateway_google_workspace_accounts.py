"""Gateway API tests for Google Workspace account management endpoints."""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def _client_with_registry(tmp_path, monkeypatch):
    accounts_file = tmp_path / "google_workspace" / "accounts.json"
    accounts_file.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("GOOGLE_WORKSPACE_ACCOUNTS_FILE", str(accounts_file))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test:token")
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "123456789")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("GATEWAY_API_KEY", "")

    import config as config_mod
    config_mod._config = None
    importlib.reload(config_mod)

    from gateway.server import create_app

    client = TestClient(create_app())
    return client, accounts_file


def test_list_google_accounts_empty(monkeypatch, tmp_path):
    client, _ = _client_with_registry(tmp_path, monkeypatch)
    res = client.get("/api/gateway/google-accounts")
    assert res.status_code == 200
    payload = res.json()
    assert payload["total"] == 0
    assert payload["accounts"] == []


def test_add_list_and_remove_google_account(monkeypatch, tmp_path):
    client, _ = _client_with_registry(tmp_path, monkeypatch)

    add_res = client.post(
        "/api/gateway/google-accounts",
        json={"label": "Main", "email": "me@example.com"},
    )
    assert add_res.status_code == 200
    account = add_res.json()["account"]
    assert account["label"] == "Main"
    assert account["email_masked"] != "me@example.com"

    list_res = client.get("/api/gateway/google-accounts")
    assert list_res.status_code == 200
    payload = list_res.json()
    assert payload["total"] == 1
    assert payload["accounts"][0]["label"] == "Main"

    del_res = client.delete(f"/api/gateway/google-accounts/{account['id']}")
    assert del_res.status_code == 200

    list_after = client.get("/api/gateway/google-accounts").json()
    assert list_after["accounts"] == []


def test_add_rejects_duplicate_and_validation_errors(monkeypatch, tmp_path):
    client, _ = _client_with_registry(tmp_path, monkeypatch)

    first = client.post(
        "/api/gateway/google-accounts",
        json={"label": "Main", "email": "main@example.com"},
    )
    assert first.status_code == 200

    duplicate = client.post(
        "/api/gateway/google-accounts",
        json={"label": "main", "email": "MAIN@example.com"},
    )
    assert duplicate.status_code == 400

    invalid = client.post("/api/gateway/google-accounts", json={"label": "NoEmail"})
    assert invalid.status_code == 400


def test_remove_missing_account_returns_404(monkeypatch, tmp_path):
    client, _ = _client_with_registry(tmp_path, monkeypatch)
    res = client.delete("/api/gateway/google-accounts/not-there")
    assert res.status_code == 404
