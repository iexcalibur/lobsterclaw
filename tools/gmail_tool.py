"""
Gmail tools — search, send, read, reply, archive, label, mark-read, VIP management.

Requires OAuth-connected accounts from the Google Workspace registry.
"""

from __future__ import annotations

import asyncio
import base64
import html
import logging
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Awaitable, Callable

from config import get_config
from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)


# ── Credential helpers ────────────────────────────────────────────────────────

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


# ── Body extraction helpers ───────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    """Very simple HTML → plain text strip."""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _extract_body(payload: dict) -> str:
    """Recursively extract plain text body from a Gmail message payload."""
    mime = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if mime == "text/plain" and body_data:
        return base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")

    if mime == "text/html" and body_data:
        raw = base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")
        return _strip_html(raw)

    # multipart — recurse
    for part in payload.get("parts", []):
        result = _extract_body(part)
        if result:
            return result

    return ""


def _extract_attachments(payload: dict) -> list[str]:
    """Return list of 'filename (size KB)' strings for attachments."""
    attachments = []

    def _walk(p: dict) -> None:
        filename = p.get("filename", "")
        size = p.get("body", {}).get("size", 0)
        if filename:
            attachments.append(f"{filename} ({size // 1024} KB)" if size else filename)
        for part in p.get("parts", []):
            _walk(part)

    _walk(payload)
    return attachments


# ── Tool implementations ──────────────────────────────────────────────────────

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
        output_lines.append(
            f"{i}. ID:{m['id']}\n"
            f"   [{subject}]\n"
            f"   From: {from_addr} | {date}\n"
            f"   {snippet}"
        )

    return "\n\n".join(output_lines)


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


async def _gmail_read(
    message_id: str,
    account: str | None = None,
    **_kwargs: Any,
) -> str:
    """Read the full body of an email by message ID."""
    creds, err = _get_credentials_for_account(account)
    if err:
        return f"Error: {err}"

    try:
        client = _build_client(creds)
        msg = (
            client.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
    except Exception as e:
        logger.exception("Gmail read failed: %s", e)
        return f"Gmail API error: {e}"

    payload = msg.get("payload", {})
    headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
    subject = headers.get("Subject", "(no subject)")
    from_addr = headers.get("From", "(unknown)")
    to_addr = headers.get("To", "")
    date = headers.get("Date", "")
    body = _extract_body(payload) or "(no body)"
    attachments = _extract_attachments(payload)

    lines = [
        f"From: {from_addr}",
        f"To: {to_addr}",
        f"Subject: {subject}",
        f"Date: {date}",
        "",
        body[:4000],  # cap at 4K chars
    ]
    if attachments:
        lines += ["", f"Attachments: {', '.join(attachments)}"]

    return "\n".join(lines)


async def _gmail_reply(
    message_id: str,
    body: str,
    account: str | None = None,
    **_kwargs: Any,
) -> str:
    """Reply to an email."""
    creds, err = _get_credentials_for_account(account)
    if err:
        return f"Error: {err}"

    try:
        client = _build_client(creds)
        orig = (
            client.users()
            .messages()
            .get(userId="me", id=message_id, format="metadata")
            .execute()
        )
    except Exception as e:
        return f"Gmail API error fetching original: {e}"

    headers = {h["name"]: h["value"] for h in orig.get("payload", {}).get("headers", [])}
    subject = headers.get("Subject", "")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    to_addr = headers.get("Reply-To") or headers.get("From", "")
    message_id_header = headers.get("Message-ID", "")
    thread_id = orig.get("threadId", "")

    try:
        msg = MIMEMultipart()
        msg["To"] = to_addr
        msg["Subject"] = subject
        if message_id_header:
            msg["In-Reply-To"] = message_id_header
            msg["References"] = message_id_header
        msg.attach(MIMEText(body, "plain"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8").rstrip("=")
        sent = (
            client.users()
            .messages()
            .send(userId="me", body={"raw": raw, "threadId": thread_id})
            .execute()
        )
        return f"Reply sent. Message ID: {sent.get('id', 'N/A')}"
    except Exception as e:
        logger.exception("Gmail reply failed: %s", e)
        return f"Gmail reply error: {e}"


async def _gmail_archive(
    message_id: str,
    account: str | None = None,
    **_kwargs: Any,
) -> str:
    """Archive an email (remove from INBOX)."""
    creds, err = _get_credentials_for_account(account)
    if err:
        return f"Error: {err}"

    try:
        client = _build_client(creds)
        client.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["INBOX"]},
        ).execute()
        return f"Email {message_id} archived."
    except Exception as e:
        logger.exception("Gmail archive failed: %s", e)
        return f"Gmail archive error: {e}"


async def _gmail_label(
    message_id: str,
    label_name: str,
    action: str = "add",
    account: str | None = None,
    **_kwargs: Any,
) -> str:
    """Add or remove a label on an email."""
    creds, err = _get_credentials_for_account(account)
    if err:
        return f"Error: {err}"

    if action not in ("add", "remove"):
        return "Error: action must be 'add' or 'remove'"

    try:
        client = _build_client(creds)
        # Resolve label name → label ID
        labels_resp = client.users().labels().list(userId="me").execute()
        label_id = None
        for lbl in labels_resp.get("labels", []):
            if lbl.get("name", "").lower() == label_name.lower():
                label_id = lbl["id"]
                break

        if not label_id:
            return f"Label '{label_name}' not found. Check Gmail label names."

        body: dict[str, list[str]] = {}
        if action == "add":
            body["addLabelIds"] = [label_id]
        else:
            body["removeLabelIds"] = [label_id]

        client.users().messages().modify(userId="me", id=message_id, body=body).execute()
        return f"Label '{label_name}' {action}ed on message {message_id}."
    except Exception as e:
        logger.exception("Gmail label failed: %s", e)
        return f"Gmail label error: {e}"


async def _gmail_mark_read(
    message_id: str,
    read: bool = True,
    account: str | None = None,
    **_kwargs: Any,
) -> str:
    """Mark an email as read or unread."""
    creds, err = _get_credentials_for_account(account)
    if err:
        return f"Error: {err}"

    try:
        client = _build_client(creds)
        if read:
            body = {"removeLabelIds": ["UNREAD"]}
        else:
            body = {"addLabelIds": ["UNREAD"]}
        client.users().messages().modify(userId="me", id=message_id, body=body).execute()
        status = "read" if read else "unread"
        return f"Email {message_id} marked as {status}."
    except Exception as e:
        logger.exception("Gmail mark_read failed: %s", e)
        return f"Gmail mark_read error: {e}"


async def _gmail_vip(
    action: str,
    email: str | None = None,
    **_kwargs: Any,
) -> str:
    """Manage the VIP sender list."""
    from tools.gmail_vip import add_vip_sender, load_vip_senders, remove_vip_sender

    action = action.strip().lower()

    if action == "list":
        senders = load_vip_senders()
        if not senders:
            return "VIP sender list is empty. Use action='add' to add senders."
        return "VIP senders:\n" + "\n".join(f"  • {s}" for s in senders)

    if action == "add":
        if not email:
            return "Error: email is required for action='add'"
        ok, msg = add_vip_sender(email)
        return msg

    if action == "remove":
        if not email:
            return "Error: email is required for action='remove'"
        ok, msg = remove_vip_sender(email)
        return msg

    return "Error: action must be one of: list, add, remove"


# ── Tool definitions ──────────────────────────────────────────────────────────

GMAIL_SEARCH_TOOL = ToolDefinition(
    name="gmail_search",
    description=(
        "Search Gmail for messages matching a query.\n\n"
        "Uses Gmail search operators (from:, to:, subject:, is:unread, newer_than:, etc.). "
        "Returns message IDs usable with gmail_read and gmail_reply. "
        "Requires a Google Workspace account connected via OAuth in the Gateway UI."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Gmail search query (e.g. 'from:boss@company.com is:unread')"},
            "max_results": {"type": "integer", "description": "Max messages to return (default 10)", "default": 10},
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
        "Send a new email via Gmail. "
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

GMAIL_READ_TOOL = ToolDefinition(
    name="gmail_read",
    description=(
        "Read the full body of an email by its message ID (obtained from gmail_search). "
        "Also lists attachment names and sizes."
    ),
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Gmail message ID from gmail_search"},
            "account": {"type": "string", "description": "Account label or id (optional)"},
        },
        "required": ["message_id"],
    },
    fn=_gmail_read,
    owner_only=True,
)

GMAIL_REPLY_TOOL = ToolDefinition(
    name="gmail_reply",
    description=(
        "Reply to an email by its message ID. "
        "Automatically sets Re: subject, In-Reply-To, and keeps the thread."
    ),
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Gmail message ID to reply to"},
            "body": {"type": "string", "description": "Reply body (plain text)"},
            "account": {"type": "string", "description": "Account label or id (optional)"},
        },
        "required": ["message_id", "body"],
    },
    fn=_gmail_reply,
    owner_only=True,
)

GMAIL_ARCHIVE_TOOL = ToolDefinition(
    name="gmail_archive",
    description="Archive an email (remove it from Inbox without deleting).",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Gmail message ID"},
            "account": {"type": "string", "description": "Account label or id (optional)"},
        },
        "required": ["message_id"],
    },
    fn=_gmail_archive,
    owner_only=True,
)

GMAIL_LABEL_TOOL = ToolDefinition(
    name="gmail_label",
    description="Add or remove a Gmail label on an email.",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Gmail message ID"},
            "label_name": {"type": "string", "description": "Gmail label name (case-insensitive)"},
            "action": {"type": "string", "enum": ["add", "remove"], "description": "add or remove the label"},
            "account": {"type": "string", "description": "Account label or id (optional)"},
        },
        "required": ["message_id", "label_name", "action"],
    },
    fn=_gmail_label,
    owner_only=True,
)

GMAIL_MARK_READ_TOOL = ToolDefinition(
    name="gmail_mark_read",
    description="Mark an email as read or unread.",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Gmail message ID"},
            "read": {"type": "boolean", "description": "True to mark read, False to mark unread (default True)"},
            "account": {"type": "string", "description": "Account label or id (optional)"},
        },
        "required": ["message_id"],
    },
    fn=_gmail_mark_read,
    owner_only=True,
)

GMAIL_VIP_TOOL = ToolDefinition(
    name="gmail_vip",
    description=(
        "Manage the VIP sender list. Emails from VIP senders trigger proactive "
        "Telegram notifications. Actions: list, add, remove."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "add", "remove"], "description": "list / add / remove"},
            "email": {"type": "string", "description": "Email address (required for add/remove)"},
        },
        "required": ["action"],
    },
    fn=_gmail_vip,
    owner_only=True,
)


# ── Gmail watcher background loop ────────────────────────────────────────────

async def gmail_watcher_loop(
    send_fn: Callable[[str], Awaitable[None]],
    cfg: Any,
) -> None:
    """
    Background task: polls Gmail every GMAIL_WATCH_INTERVAL_MINUTES for new
    emails from VIP senders and sends a Telegram notification for each new one.
    """
    from tools.gmail_vip import load_vip_senders

    interval_minutes = getattr(cfg, "gmail_watch_interval_minutes", 30)
    seen_ids: set[str] = set()

    logger.info("Gmail watcher started (interval=%dm)", interval_minutes)

    while True:
        try:
            vip_senders = load_vip_senders()
            if vip_senders:
                creds, err = _get_credentials_for_account()
                if not err:
                    client = _build_client(creds)
                    from_query = " OR ".join(f"from:{s}" for s in vip_senders)
                    results = (
                        client.users()
                        .messages()
                        .list(
                            userId="me",
                            q=f"({from_query}) is:unread newer_than:2h",
                            maxResults=10,
                        )
                        .execute()
                    )
                    for m in results.get("messages", []):
                        mid = m["id"]
                        if mid in seen_ids:
                            continue
                        seen_ids.add(mid)
                        try:
                            msg = (
                                client.users()
                                .messages()
                                .get(userId="me", id=mid, format="metadata")
                                .execute()
                            )
                            headers = {
                                h["name"]: h["value"]
                                for h in msg.get("payload", {}).get("headers", [])
                            }
                            subject = headers.get("Subject", "(no subject)")
                            from_addr = headers.get("From", "(unknown)")
                            date = headers.get("Date", "")
                            snippet = msg.get("snippet", "")[:300]

                            notification = (
                                f"📧 *New email from VIP sender*\n\n"
                                f"*From:* {from_addr}\n"
                                f"*Subject:* {subject}\n"
                                f"*Time:* {date}\n\n"
                                f"{snippet}\n\n"
                                f'_To reply, say: "reply to email {mid}"_'
                            )
                            await send_fn(notification)
                        except Exception as e:
                            logger.warning("Gmail watcher: failed to fetch message %s: %s", mid, e)
        except Exception as e:
            logger.warning("Gmail watcher error: %s", e)

        await asyncio.sleep(interval_minutes * 60)
