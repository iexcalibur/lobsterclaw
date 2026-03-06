"""
Google Calendar tools — list events, create events with Meet links, RSVP.

Requires OAuth-connected accounts with Calendar scopes.
Re-authenticate in Gateway UI after adding Calendar scopes to your account.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)


# ── Credential + client helpers ───────────────────────────────────────────────

def _get_credentials(account_label_or_id: str | None = None):
    """Load credentials. Returns (creds, None) or (None, error_msg)."""
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
            target = acct

    if not target:
        return None, "Account not found"

    creds, err = get_credentials_for_account_id(target["id"])
    if err:
        return None, (
            f"Account '{target.get('label', '')}' is not OAuth-connected, "
            "or Calendar scopes are missing — re-connect in Gateway UI."
        )
    return creds, None


def _build_client(creds):
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=creds)


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_datetime(dt_str: str) -> str:
    """Format a Google Calendar dateTime or date string for display."""
    if not dt_str:
        return ""
    try:
        # dateTime format: 2026-03-10T14:00:00+05:30
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime("%a %b %-d, %I:%M %p %Z").strip()
    except Exception:
        return dt_str


def _get_meet_link(event: dict) -> str | None:
    """Extract Google Meet URL from an event if present."""
    # Check conferenceData
    conf = event.get("conferenceData", {})
    for ep in conf.get("entryPoints", []):
        if ep.get("entryPointType") == "video":
            return ep.get("uri")
    # Fallback: hangoutLink
    return event.get("hangoutLink")


def _format_event(event: dict, index: int | None = None) -> str:
    """Format a single calendar event for Telegram display."""
    summary = event.get("summary", "(No title)")
    start = event.get("start", {})
    end = event.get("end", {})
    start_str = _fmt_datetime(start.get("dateTime") or start.get("date", ""))
    end_str = _fmt_datetime(end.get("dateTime") or end.get("date", ""))
    organizer = event.get("organizer", {}).get("email", "")
    attendees = [
        a.get("email", "") for a in event.get("attendees", [])
        if not a.get("self")
    ]
    meet_url = _get_meet_link(event)

    lines = []
    prefix = f"{index}. " if index is not None else ""
    lines.append(f"{prefix}*{summary}*")
    lines.append(f"🕐 {start_str}" + (f" – {end_str}" if end_str != start_str else ""))
    if organizer:
        lines.append(f"👤 Organizer: {organizer}")
    if attendees:
        lines.append(f"👥 Attendees: {', '.join(attendees[:5])}" +
                     (f" +{len(attendees)-5} more" if len(attendees) > 5 else ""))
    if meet_url:
        lines.append(f"🔗 [Join Google Meet]({meet_url})")
    lines.append(f"_Event ID: {event.get('id', '')}_")
    return "\n".join(lines)


# ── Tool implementations ──────────────────────────────────────────────────────

async def _calendar_list(
    days: int = 7,
    account: str | None = None,
    **_kwargs: Any,
) -> str:
    """List upcoming calendar events."""
    creds, err = _get_credentials(account)
    if err:
        return f"Error: {err}"

    try:
        client = _build_client(creds)
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        time_max = now + timedelta(days=days)

        events_result = (
            client.events()
            .list(
                calendarId="primary",
                timeMin=now.isoformat(),
                timeMax=time_max.isoformat(),
                maxResults=20,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except Exception as e:
        logger.exception("Calendar list failed: %s", e)
        return f"Calendar API error: {e}"

    events = events_result.get("items", [])
    if not events:
        return f"No events in the next {days} day(s)."

    lines = [f"📅 *Upcoming events — next {days} day(s)*\n"]
    for i, event in enumerate(events, 1):
        lines.append(_format_event(event, i))
        lines.append("")

    return "\n".join(lines).strip()


async def _calendar_create(
    title: str,
    start_datetime: str,
    end_datetime: str,
    attendees: list[str] | None = None,
    description: str | None = None,
    add_meet_link: bool = True,
    account: str | None = None,
    **_kwargs: Any,
) -> str:
    """Create a Google Calendar event, optionally with a Meet link."""
    creds, err = _get_credentials(account)
    if err:
        return f"Error: {err}"

    body: dict[str, Any] = {
        "summary": title,
        "start": {"dateTime": start_datetime, "timeZone": "UTC"},
        "end": {"dateTime": end_datetime, "timeZone": "UTC"},
    }
    if description:
        body["description"] = description
    if attendees:
        body["attendees"] = [{"email": e.strip()} for e in attendees]
    if add_meet_link:
        body["conferenceData"] = {
            "createRequest": {
                "requestId": uuid.uuid4().hex,
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }

    try:
        client = _build_client(creds)
        event = (
            client.events()
            .insert(
                calendarId="primary",
                body=body,
                conferenceDataVersion=1 if add_meet_link else 0,
                sendUpdates="all" if attendees else "none",
            )
            .execute()
        )
    except Exception as e:
        logger.exception("Calendar create failed: %s", e)
        return f"Calendar API error: {e}"

    meet_url = _get_meet_link(event)
    event_link = event.get("htmlLink", "")
    lines = [
        f"✅ Event created: *{title}*",
        f"🕐 {_fmt_datetime(start_datetime)} – {_fmt_datetime(end_datetime)}",
    ]
    if attendees:
        lines.append(f"👥 Invites sent to: {', '.join(attendees)}")
    if meet_url:
        lines.append(f"🔗 [Join Google Meet]({meet_url})")
    if event_link:
        lines.append(f"📅 [View in Calendar]({event_link})")
    lines.append(f"_Event ID: {event.get('id', '')}_")
    return "\n".join(lines)


async def _calendar_rsvp(
    event_id: str,
    response: str,
    account: str | None = None,
    **_kwargs: Any,
) -> str:
    """RSVP to a calendar event invite."""
    creds, err = _get_credentials(account)
    if err:
        return f"Error: {err}"

    response = response.strip().lower()
    valid = ("accepted", "declined", "tentative")
    if response not in valid:
        return f"Error: response must be one of: {', '.join(valid)}"

    try:
        client = _build_client(creds)
        # Get event to find own attendee entry
        event = client.events().get(calendarId="primary", eventId=event_id).execute()
        attendees = event.get("attendees", [])
        updated = False
        for attendee in attendees:
            if attendee.get("self"):
                attendee["responseStatus"] = response
                updated = True
                break

        if not updated:
            # Add self as attendee if not present
            from agent.google_workspace_accounts import load_accounts
            accounts = load_accounts()
            self_email = accounts[0].get("email", "") if accounts else ""
            if self_email:
                attendees.append({"email": self_email, "responseStatus": response, "self": True})
                event["attendees"] = attendees

        client.events().update(
            calendarId="primary",
            eventId=event_id,
            body=event,
            sendUpdates="all",
        ).execute()

        emoji = {"accepted": "✅", "declined": "❌", "tentative": "🤔"}[response]
        return f"{emoji} RSVP set to *{response}* for event `{event_id}`."
    except Exception as e:
        logger.exception("Calendar RSVP failed: %s", e)
        return f"Calendar RSVP error: {e}"


# ── Tool definitions ──────────────────────────────────────────────────────────

CALENDAR_LIST_TOOL = ToolDefinition(
    name="calendar_list",
    description=(
        "List upcoming Google Calendar events. Shows title, time, attendees, "
        "and Google Meet join link if available."
    ),
    parameters={
        "type": "object",
        "properties": {
            "days": {"type": "integer", "description": "Number of days ahead to look (default 7)", "default": 7},
            "account": {"type": "string", "description": "Account label or id (optional)"},
        },
        "required": [],
    },
    fn=_calendar_list,
    owner_only=True,
)

CALENDAR_CREATE_TOOL = ToolDefinition(
    name="calendar_create",
    description=(
        "Create a Google Calendar event. Optionally invite attendees and "
        "auto-generate a Google Meet link. "
        "Datetimes must be ISO 8601 format (e.g. 2026-03-10T14:00:00+05:30)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Event title"},
            "start_datetime": {"type": "string", "description": "Start time (ISO 8601)"},
            "end_datetime": {"type": "string", "description": "End time (ISO 8601)"},
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of attendee email addresses (optional)",
            },
            "description": {"type": "string", "description": "Event description (optional)"},
            "add_meet_link": {"type": "boolean", "description": "Auto-create Google Meet link (default true)", "default": True},
            "account": {"type": "string", "description": "Account label or id (optional)"},
        },
        "required": ["title", "start_datetime", "end_datetime"],
    },
    fn=_calendar_create,
    owner_only=True,
)

CALENDAR_RSVP_TOOL = ToolDefinition(
    name="calendar_rsvp",
    description="RSVP to a Google Calendar event invite (accepted / declined / tentative).",
    parameters={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "Google Calendar event ID (from calendar_list)"},
            "response": {"type": "string", "enum": ["accepted", "declined", "tentative"], "description": "Your RSVP response"},
            "account": {"type": "string", "description": "Account label or id (optional)"},
        },
        "required": ["event_id", "response"],
    },
    fn=_calendar_rsvp,
    owner_only=True,
)


# ── Calendar reminder background loop ────────────────────────────────────────

async def calendar_reminder_loop(
    send_fn: Callable[[str], Awaitable[None]],
    cfg: Any,
) -> None:
    """
    Background task: checks every CALENDAR_REMINDER_MINUTES for events
    starting within that window and sends a Telegram notification with Meet link.
    """
    from datetime import timedelta

    reminder_minutes = getattr(cfg, "calendar_reminder_minutes", 10)
    reminded_ids: set[str] = set()

    logger.info("Calendar reminder started (window=%dm)", reminder_minutes)

    while True:
        try:
            creds, err = _get_credentials()
            if not err:
                client = _build_client(creds)
                now = datetime.now(timezone.utc)
                window_start = now + timedelta(minutes=reminder_minutes - 2)
                window_end = now + timedelta(minutes=reminder_minutes + 2)

                events_result = (
                    client.events()
                    .list(
                        calendarId="primary",
                        timeMin=window_start.isoformat(),
                        timeMax=window_end.isoformat(),
                        maxResults=5,
                        singleEvents=True,
                        orderBy="startTime",
                    )
                    .execute()
                )

                for event in events_result.get("items", []):
                    eid = event.get("id", "")
                    if eid in reminded_ids:
                        continue
                    reminded_ids.add(eid)

                    summary = event.get("summary", "(No title)")
                    start = event.get("start", {})
                    end = event.get("end", {})
                    start_str = _fmt_datetime(start.get("dateTime") or start.get("date", ""))
                    end_str = _fmt_datetime(end.get("dateTime") or end.get("date", ""))
                    organizer = event.get("organizer", {}).get("email", "")
                    attendees = [
                        a.get("displayName") or a.get("email", "")
                        for a in event.get("attendees", [])
                        if not a.get("self")
                    ]
                    meet_url = _get_meet_link(event)

                    lines = [
                        f"📅 *Meeting in {reminder_minutes} minutes*\n",
                        f"*{summary}*",
                        f"🕐 {start_str}" + (f" – {end_str}" if end_str != start_str else ""),
                    ]
                    if organizer:
                        lines.append(f"👤 Organizer: {organizer}")
                    if attendees:
                        lines.append(f"👥 {', '.join(attendees[:5])}")
                    if meet_url:
                        lines.append(f"\n🔗 [Join Google Meet]({meet_url})")
                    else:
                        lines.append("\n_(No Meet link for this event)_")

                    await send_fn("\n".join(lines))

        except Exception as e:
            logger.warning("Calendar reminder error: %s", e)

        await asyncio.sleep(reminder_minutes * 60)
