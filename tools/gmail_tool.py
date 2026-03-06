"""
Gmail tools (Phase 2) — search and send via Google Gmail API.

Requires OAuth-connected accounts from the Google Workspace registry.
"""

from __future__ import annotations

import base64
import logging
from email.mime.text import MIMEText
from typing import Any

from config import get_config
from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)


def _get_credentials_for_account(account_label_or_id: str | None = None):
    """Load credentials for the given account. Returns (creds, None) or (None, error_msg)."""
    from agent.google_workspace_accounts import load_accounts
    from agent.google_workspace_oauth import get_credentials_for_account_id

    accounts = load_accounts()
    if not accounts:
        return None, "No Google Workspace accounts registered"

    identifier = (account_label_or_id or "").strip().lower()
    target = None
    for acct in accounts:
        aid = (acct.get("id") or "").strip().lower()
        label = (acct.get("label") or "").strip().lower()
        email = (acct.get("email") or "").strip().lower()
        if identifier and identifier in (aid, label, email):
            target = acct
            break
        if not target:
            target = acct  # first as default

    if not target:
        return None, "Account not found"

    account_id = target.get("id", "")
    creds, err = get_credentials_for_account_id(account_id)
    if err:
        return None, f"Account '{target.get('label', '')}' is not OAuth-connected. Connect it in Gateway UI first."
    return creds, None


def _build_client(creds):
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=creds)


async def _gmail_search(
    query: str,
    max_results: int = 10,
    account: str | None = None,
    **_kwargs: Any,
) -> str:
    """Search Gmail for messages matching the query."""
    creds, err = _get_credentials_for_account(account)
    if err:
        return f"Error: {err}"

    try:
        client = _build_client(creds)
        results = (
            client.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
    except Exception as e:
        logger.exception("Gmail search failed: %s", e)
        return f"Gmail API error: {e}"

    messages = results.get("messages", [])
    if not messages:
        return f"No messages found for query: {query}"

    output_lines = []
    for i, m in enumerate(messages[:max_results], 1):
        msg = (
            client.users()
            .messages()
            .get(userId="me", id=m["id"], format="metadata")
            .execute()
        )
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        subject = headers.get("Subject", "(no subject)")
        from_addr = headers.get("From", "(unknown)")
        date = headers.get("Date", "")
        snippet = msg.get("snippet", "")[:200]
        output_lines.append(f"{i}. [{subject}] From: {from_addr} | {date}\n   {snippet}")

    return "\n".join(output_lines)


async def _gmail_send(
    to: str,
    subject: str,
    body: str,
    account: str | None = None,
    **_kwargs: Any,
) -> str:
    """Send an email via Gmail."""
    creds, err = _get_credentials_for_account(account)
    if err:
        return f"Error: {err}"

    try:
        msg = MIMEText(body)
        msg["to"] = to
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8").rstrip("=")

        client = _build_client(creds)
        sent = client.users().messages().send(userId="me", body={"raw": raw}).execute()
        return f"Email sent successfully. Message ID: {sent.get('id', 'N/A')}"
    except Exception as e:
        logger.exception("Gmail send failed: %s", e)
        return f"Gmail send error: {e}"


GMAIL_SEARCH_TOOL = ToolDefinition(
    name="gmail_search",
    description=(
        "Search Gmail for messages matching a query.\n\n"
        "Uses Gmail search operators (from:, to:, subject:, is:unread, etc.). "
        "Requires a Google Workspace account connected via OAuth in the Gateway UI."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Gmail search query (e.g. 'from:boss@company.com is:unread')"},
            "max_results": {"type": "integer", "description": "Max messages to return", "default": 10},
            "account": {"type": "string", "description": "Account label or id (optional; uses first if omitted)"},
        },
        "required": ["query"],
    },
    fn=_gmail_search,
    owner_only=True,
)

GMAIL_SEND_TOOL = ToolDefinition(
    name="gmail_send",
    description=(
        "Send an email via Gmail.\n\n"
        "Requires a Google Workspace account connected via OAuth in the Gateway UI."
    ),
    parameters={
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address"},
            "subject": {"type": "string", "description": "Email subject"},
            "body": {"type": "string", "description": "Email body (plain text)"},
            "account": {"type": "string", "description": "Account label or id (optional)"},
        },
        "required": ["to", "subject", "body"],
    },
    fn=_gmail_send,
    owner_only=True,
)
