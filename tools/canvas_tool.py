"""
Canvas tool — full action surface backed by the LobsterClaw Canvas Host.

The canvas host is a FastAPI + WebSocket server (canvas_host/) that streams
content to a Next.js frontend. This tool makes HTTP calls to the host and
returns structured results matching OpenClaw's canvas-tool.ts contract.

Actions:
  present      push HTML / markdown / A2UI / JSON content to canvas
  navigate     load a URL in the canvas iframe
  hide         hide the canvas overlay
  show         show / un-hide the canvas
  eval         evaluate JavaScript in the frontend page (returns result)
  snapshot     Playwright screenshot → base64 PNG (optionally sends to Telegram)
  update       incremental content patch (selector / key updates)
  open         alias for present that also opens the canvas URL in the browser
  close        close / delete a canvas session
  list         list active canvas sessions
  wait_event   block until the user clicks / submits something in the canvas
  a2ui_render  render an A2UI component tree (structured JSON → React UI)
  a2ui_snapshot present A2UI then immediately snapshot

Canvas host must be running (CANVAS_HOST_ENABLED=true in .env).
Falls back gracefully when host is not running.
"""

from __future__ import annotations

import json
import logging
import uuid

import httpx

from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

# Injected by main.py so the tool can send Telegram photos for snapshots
_send_photo_fn = None


def set_send_photo_fn(fn) -> None:
    global _send_photo_fn
    _send_photo_fn = fn


TOOL_DEFINITION = ToolDefinition(
    name="canvas",
    description=(
        "Interactive canvas UI controller — push live content to the web canvas "
        "and receive user interactions.\n\n"
        "The canvas host runs at http://localhost:7681 (CANVAS_HOST_PORT).\n"
        "Open http://localhost:7681/canvas/{sessionId} in any browser to view.\n\n"
        "Actions:\n"
        "  present     — push HTML / markdown / A2UI / JSON content\n"
        "  navigate    — load a URL inside the canvas\n"
        "  hide        — hide the canvas overlay\n"
        "  show        — un-hide the canvas\n"
        "  eval        — evaluate JavaScript and return result\n"
        "  snapshot    — Playwright screenshot → base64 PNG (send_to_telegram=true to also DM)\n"
        "  update      — patch existing content incrementally\n"
        "  open        — present content + open canvas URL in system browser\n"
        "  close       — close/reset a canvas session\n"
        "  list        — list active canvas sessions\n"
        "  wait_event  — wait for user click/submit/input from the canvas\n"
        "  a2ui_render — render a structured A2UI component tree\n"
        "  a2ui_snapshot — render A2UI then snapshot\n\n"
        "Content kinds (for present/a2ui_render):\n"
        "  html      — raw HTML string\n"
        "  markdown  — Markdown text (rendered with GFM)\n"
        "  url       — load this URL in iframe (pass via 'url' field)\n"
        "  a2ui      — A2UI component tree JSON (pass via 'a2ui' field)\n"
        "  json      — formatted JSON viewer (pass via 'data' field)\n\n"
        "A2UI component tree example:\n"
        '  {"type":"card","title":"Results","children":['
        '{"type":"table","headers":["K","V"],"rows":[["a","1"]]},'
        '{"type":"button","label":"OK","action":"confirm"}]}'
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": (
                    "Action: present|navigate|hide|show|eval|snapshot|update|open|"
                    "close|list|wait_event|a2ui_render|a2ui_snapshot"
                ),
            },
            "session_id": {
                "type": "string",
                "description": (
                    "Canvas session ID (defaults to current agent session). "
                    "Use the same ID to update an existing canvas."
                ),
            },
            "kind": {
                "type": "string",
                "description": "Content kind: html | markdown | url | a2ui | json",
            },
            "html": {
                "type": "string",
                "description": "HTML string (for kind=html)",
            },
            "markdown": {
                "type": "string",
                "description": "Markdown text (for kind=markdown)",
            },
            "url": {
                "type": "string",
                "description": "URL to navigate to (for navigate action or kind=url)",
            },
            "a2ui": {
                "type": "object",
                "description": "A2UI component tree (for kind=a2ui or a2ui_render action)",
            },
            "data": {
                "type": "object",
                "description": "JSON data to display (for kind=json)",
            },
            "title": {
                "type": "string",
                "description": "Canvas/page title",
            },
            "script": {
                "type": "string",
                "description": "JavaScript expression to evaluate (for eval action)",
            },
            "patch": {
                "type": "object",
                "description": "Content patch object (for update action)",
            },
            "send_to_telegram": {
                "type": "boolean",
                "description": "Also send snapshot as Telegram photo (for snapshot action)",
            },
            "full_page": {
                "type": "boolean",
                "description": "Full-page screenshot vs viewport only (for snapshot)",
            },
            "timeout": {
                "type": "number",
                "description": "Timeout seconds for wait_event or eval (default 60 / 10)",
            },
            "width": {
                "type": "integer",
                "description": "Viewport width for snapshot (default 1280)",
            },
            "height": {
                "type": "integer",
                "description": "Viewport height for snapshot (default 800)",
            },
        },
        "required": ["action"],
    },
    fn=lambda **kw: _canvas(**kw),
)


# ------------------------------------------------------------------
# Main dispatcher
# ------------------------------------------------------------------

async def _canvas(
    action: str,
    session_id: str | None = None,
    kind: str | None = None,
    html: str | None = None,
    markdown: str | None = None,
    url: str | None = None,
    a2ui: dict | None = None,
    data=None,
    title: str | None = None,
    script: str | None = None,
    patch: dict | None = None,
    send_to_telegram: bool = False,
    full_page: bool = False,
    timeout: float | None = None,
    width: int = 1280,
    height: int = 800,
    **_extra,
) -> str:
    action = action.lower().strip()

    try:
        from config import get_config
        cfg = get_config()
    except Exception:
        return "error: config not available"

    if not cfg.canvas_host_enabled:
        return (
            "Canvas host is not enabled. Set CANVAS_HOST_ENABLED=true in .env and restart."
        )

    # Default session_id to a stable per-call UUID (callers should pass their session)
    sid = session_id or uuid.uuid4().hex[:12]
    base_url = cfg.canvas_host_url

    # ----------------------------------------------------------------
    # list
    # ----------------------------------------------------------------
    if action == "list":
        return await _api_get(base_url, "/api/canvas/sessions", _format_sessions)

    # ----------------------------------------------------------------
    # close
    # ----------------------------------------------------------------
    if action == "close":
        return await _api_delete(base_url, f"/api/canvas/{sid}",
                                  lambda r: f"Canvas session '{sid}' closed.")

    # ----------------------------------------------------------------
    # present / open
    # ----------------------------------------------------------------
    if action in ("present", "open"):
        body = _build_content_body(kind, html, markdown, url, a2ui, data, title)
        result = await _api_post(base_url, f"/api/canvas/{sid}/present", body)
        if action == "open":
            import subprocess
            canvas_url = f"{base_url}/canvas/{sid}"
            try:
                subprocess.Popen(["open", canvas_url])
                extra = f"\nOpened {canvas_url} in browser."
            except Exception:
                extra = f"\nCanvas URL: {canvas_url}"
        else:
            extra = f"\nCanvas URL: {base_url}/canvas/{sid}"
        return result + extra

    # ----------------------------------------------------------------
    # navigate
    # ----------------------------------------------------------------
    if action == "navigate":
        if not url:
            return "error: 'url' is required for navigate action"
        body = {"url": url}
        return await _api_post(base_url, f"/api/canvas/{sid}/navigate",
                                body, lambda r: f"Navigated to {url}")

    # ----------------------------------------------------------------
    # hide / show
    # ----------------------------------------------------------------
    if action == "hide":
        return await _api_post(base_url, f"/api/canvas/{sid}/hide", {},
                                lambda r: f"Canvas '{sid}' hidden.")

    if action == "show":
        return await _api_post(base_url, f"/api/canvas/{sid}/show", {},
                                lambda r: f"Canvas '{sid}' visible.")

    # ----------------------------------------------------------------
    # eval
    # ----------------------------------------------------------------
    if action == "eval":
        if not script:
            return "error: 'script' is required for eval action"
        body = {"script": script, "timeout": timeout or 10.0}
        return await _api_post(base_url, f"/api/canvas/{sid}/eval", body, _format_eval_result)

    # ----------------------------------------------------------------
    # snapshot
    # ----------------------------------------------------------------
    if action in ("snapshot", "a2ui_snapshot"):
        if action == "a2ui_snapshot" and a2ui:
            # First render the A2UI content
            body = _build_content_body("a2ui", None, None, None, a2ui, None, title)
            await _api_post(base_url, f"/api/canvas/{sid}/present", body)

        snap_url = (
            f"/api/canvas/{sid}/snapshot"
            f"?full_page={'true' if full_page else 'false'}"
            f"&width={width}&height={height}"
        )
        resp = await _api_get_raw(base_url, snap_url)
        if not resp:
            return f"Snapshot failed — is the canvas host running at {base_url}?"

        b64 = resp.get("base64", "")
        if not b64:
            return "Snapshot returned no data."

        if send_to_telegram and _send_photo_fn:
            import base64
            import tempfile, os
            data_bytes = base64.standard_b64decode(b64)
            fd, tmp = tempfile.mkstemp(suffix=".png")
            try:
                os.write(fd, data_bytes)
                os.close(fd)
                await _send_photo_fn(tmp, caption=title or f"Canvas {sid}")
                os.unlink(tmp)
            except Exception as e:
                logger.warning("Failed to send snapshot to Telegram: %s", e)
            return f"Canvas snapshot captured and sent to Telegram ✅\n  Session: {sid}"

        return (
            f"Canvas snapshot captured ✅\n"
            f"  Session: {sid}\n"
            f"  Size: {len(b64)} chars (base64 PNG)\n"
            f"  Canvas URL: {base_url}/canvas/{sid}"
        )

    # ----------------------------------------------------------------
    # update
    # ----------------------------------------------------------------
    if action == "update":
        body = patch or {}
        if not body:
            return "error: 'patch' is required for update action"
        return await _api_post(base_url, f"/api/canvas/{sid}/update", body,
                                lambda r: f"Canvas '{sid}' updated.")

    # ----------------------------------------------------------------
    # wait_event
    # ----------------------------------------------------------------
    if action == "wait_event":
        t = timeout or 60.0
        wait_url = f"/api/canvas/{sid}/wait_event?timeout={t}"
        resp = await _api_get_raw(base_url, wait_url)
        if not resp:
            return "wait_event failed — canvas host not reachable"
        if resp.get("timeout"):
            return f"wait_event timed out after {t}s — no user interaction."
        event = resp.get("event", {})
        return _format_event(event)

    # ----------------------------------------------------------------
    # a2ui_render (present with kind=a2ui)
    # ----------------------------------------------------------------
    if action == "a2ui_render":
        if a2ui is None:
            return "error: 'a2ui' component tree is required for a2ui_render"
        body = _build_content_body("a2ui", None, None, None, a2ui, None, title)
        result = await _api_post(base_url, f"/api/canvas/{sid}/present", body)
        return result + f"\nCanvas URL: {base_url}/canvas/{sid}"

    return (
        f"Unknown canvas action '{action}'. "
        "Use: present, navigate, hide, show, eval, snapshot, update, open, "
        "close, list, wait_event, a2ui_render, a2ui_snapshot"
    )


# ------------------------------------------------------------------
# Content body builder
# ------------------------------------------------------------------

def _build_content_body(
    kind: str | None,
    html: str | None,
    markdown: str | None,
    url: str | None,
    a2ui: dict | None,
    data,
    title: str | None,
) -> dict:
    """Infer kind from the provided fields and build the /present body."""
    if not kind:
        if html is not None:
            kind = "html"
        elif markdown is not None:
            kind = "markdown"
        elif url is not None:
            kind = "url"
        elif a2ui is not None:
            kind = "a2ui"
        elif data is not None:
            kind = "json"
        else:
            kind = "html"

    body: dict = {"kind": kind}
    if html is not None:
        body["html"] = html
    if markdown is not None:
        body["markdown"] = markdown
    if url is not None:
        body["url"] = url
    if a2ui is not None:
        body["a2ui"] = a2ui
    if data is not None:
        body["data"] = data
    if title:
        body["title"] = title
    return body


# ------------------------------------------------------------------
# HTTP helpers
# ------------------------------------------------------------------

async def _api_post(
    base_url: str,
    path: str,
    body: dict,
    formatter=None,
) -> str:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{base_url}{path}", json=body)
            r.raise_for_status()
            data = r.json()
            if formatter:
                return formatter(data)
            return f"OK — {json.dumps(data)}"
    except httpx.ConnectError:
        return (
            f"Canvas host not reachable at {base_url}. "
            "Is CANVAS_HOST_ENABLED=true and the host running?"
        )
    except httpx.HTTPStatusError as e:
        return f"Canvas host error {e.response.status_code}: {e.response.text}"
    except Exception as e:
        return f"Canvas tool error: {e}"


async def _api_get(
    base_url: str,
    path: str,
    formatter=None,
) -> str:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{base_url}{path}")
            r.raise_for_status()
            data = r.json()
            if formatter:
                return formatter(data)
            return json.dumps(data, indent=2)
    except httpx.ConnectError:
        return f"Canvas host not reachable at {base_url}."
    except Exception as e:
        return f"Canvas tool error: {e}"


async def _api_get_raw(base_url: str, path: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=70.0) as client:
            r = await client.get(f"{base_url}{path}")
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


async def _api_delete(base_url: str, path: str, formatter=None) -> str:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.delete(f"{base_url}{path}")
            r.raise_for_status()
            data = r.json()
            if formatter:
                return formatter(data)
            return f"OK — {json.dumps(data)}"
    except httpx.ConnectError:
        return f"Canvas host not reachable at {base_url}."
    except Exception as e:
        return f"Canvas tool error: {e}"


# ------------------------------------------------------------------
# Output formatters
# ------------------------------------------------------------------

def _format_sessions(data: dict) -> str:
    sessions = data.get("sessions", [])
    if not sessions:
        return "No active canvas sessions."
    lines = [f"Active canvas sessions ({len(sessions)}):"]
    for s in sessions:
        connected = s.get("connected", 0)
        kind = s.get("contentKind", "—")
        conn_str = f"  {connected} client(s)" if connected else "  no clients"
        lines.append(
            f"  [{s['sessionId']}] {s.get('title') or '(untitled)'} "
            f"  kind={kind}  {conn_str}  updated={s.get('updatedAt','')}"
        )
    return "\n".join(lines)


def _format_eval_result(data: dict) -> str:
    if not data.get("ok"):
        reason = data.get("reason", "unknown")
        return f"Eval failed: {reason}"
    result = data.get("result")
    return f"Eval result: {json.dumps(result)}"


def _format_event(event: dict) -> str:
    etype = event.get("type", "unknown")
    if etype == "click":
        return (
            f"User clicked: id={event.get('elementId')} "
            f"text={json.dumps(event.get('text', ''))}"
        )
    if etype == "input":
        return (
            f"User input: id={event.get('elementId')} "
            f"value={json.dumps(event.get('value', ''))}"
        )
    if etype == "submit":
        form_data = json.dumps(event.get("data", {}), indent=2)
        return f"Form submitted (id={event.get('formId', '?')}):\n{form_data}"
    if etype == "event":
        return (
            f"Custom event '{event.get('name')}': "
            f"{json.dumps(event.get('data'))}"
        )
    return f"User event ({etype}): {json.dumps(event)}"
