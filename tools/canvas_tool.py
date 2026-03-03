"""
Canvas tool — mirrors OpenClaw's canvas-tool.ts full action surface.

In OpenClaw, canvas is a live UI canvas (A2UI) rendered inside the macOS/iOS app
or served through the web interface. It allows the agent to build interactive UIs,
display structured data, render charts, and receive user interactions.

PyGate canvas implementation:
  - Full schema parity with canvas-tool.ts
  - Output is rendered as Telegram messages (photos, HTML, or structured text)
    when CANVAS_TELEGRAM_RENDER=true
  - Internally stores canvas state in a SQLite document store
  - HTML/JSON canvases can be exported to a local file or URL

Canvas types:
  text     — formatted text content (markdown, plain)
  table    — tabular data (renders as ASCII table or sends as file)
  chart    — data chart (exports as image via matplotlib if available)
  form     — interactive form elements
  html     — arbitrary HTML content

Actions (1:1 with canvas-tool.ts):
  open      — open/create a canvas by ID
  close     — close a canvas
  render    — render content into a canvas
  update    — patch canvas content
  query     — read canvas state
  clear     — clear canvas content
  screenshot — capture canvas as image and send via Telegram
  list      — list all open canvases
  set       — set a specific canvas property
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

# Canvas state stored in memory and persisted to SQLite
_canvas_store: dict[str, "Canvas"] = {}
_DB_PATH: Path | None = None


@dataclass
class Canvas:
    canvas_id: str
    canvas_type: str  # text | table | chart | form | html
    title: str
    content: str
    created_at: str
    updated_at: str
    open: bool = True
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


TOOL_DEFINITION = ToolDefinition(
    name="canvas",
    description=(
        "Create and manage interactive canvas UIs that render in the Telegram chat.\n\n"
        "Canvas types: text (markdown), table (structured data), chart (data visualization),\n"
        "  form (interactive input), html (rich content)\n\n"
        "Actions:\n"
        "  open       — create a new canvas (type + title required)\n"
        "  close      — close a canvas by canvas_id\n"
        "  render     — render content into a canvas (canvas_id + content required)\n"
        "  update     — patch existing canvas content (append or replace)\n"
        "  query      — read current canvas state\n"
        "  clear      — clear canvas content (keep canvas open)\n"
        "  screenshot — capture canvas as image and send via Telegram\n"
        "  list       — list all open canvases\n"
        "  set        — set a canvas property (title, type, etc.)"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action: open|close|render|update|query|clear|screenshot|list|set",
            },
            "canvas_id": {
                "type": "string",
                "description": "Canvas ID (auto-generated on open if not provided)",
            },
            "canvas_type": {
                "type": "string",
                "description": "Canvas type: text | table | chart | form | html (required for open)",
            },
            "title": {
                "type": "string",
                "description": "Canvas title (for open/set)",
            },
            "content": {
                "type": "string",
                "description": "Content to render (text, JSON for table/chart, HTML for html type)",
            },
            "mode": {
                "type": "string",
                "description": "Update mode: replace (default) | append | prepend",
                "default": "replace",
            },
            "property": {
                "type": "string",
                "description": "Property name to set (for set action: title, type)",
            },
            "value": {
                "type": "string",
                "description": "Property value (for set action)",
            },
        },
        "required": ["action"],
    },
    fn=lambda **kw: _canvas(**kw),
)


async def _canvas(
    action: str,
    canvas_id: str | None = None,
    canvas_type: str = "text",
    title: str | None = None,
    content: str | None = None,
    mode: str = "replace",
    property: str | None = None,
    value: str | None = None,
) -> str:
    action = action.lower().strip()
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    if action == "list":
        open_canvases = [c for c in _canvas_store.values() if c.open]
        if not open_canvases:
            return "No open canvases."
        lines = [f"Open canvases ({len(open_canvases)}):"]
        for c in open_canvases:
            content_preview = c.content[:80].replace("\n", " ") if c.content else "(empty)"
            lines.append(f"  [{c.canvas_id}] {c.canvas_type} — '{c.title}' — {content_preview}")
        return "\n".join(lines)

    if action == "open":
        cid = canvas_id or uuid.uuid4().hex[:8]
        if cid in _canvas_store and _canvas_store[cid].open:
            return f"Canvas '{cid}' is already open."
        c = Canvas(
            canvas_id=cid,
            canvas_type=canvas_type,
            title=title or f"Canvas {cid}",
            content=content or "",
            created_at=now,
            updated_at=now,
        )
        _canvas_store[cid] = c
        _persist(c)
        return f"Canvas opened ✅\n  ID: {cid}\n  Type: {canvas_type}\n  Title: {c.title}"

    # All other actions require canvas_id
    if not canvas_id:
        # Use the most recently updated open canvas as default
        open_list = sorted(
            [c for c in _canvas_store.values() if c.open],
            key=lambda c: c.updated_at, reverse=True,
        )
        if not open_list:
            return "Error: no open canvas. Use action=open first."
        c = open_list[0]
    else:
        c = _canvas_store.get(canvas_id)
        if not c:
            return f"Canvas '{canvas_id}' not found. Use action=list to see open canvases."
        if not c.open:
            return f"Canvas '{canvas_id}' is closed."

    if action == "close":
        c.open = False
        c.updated_at = now
        _persist(c)
        return f"Canvas '{c.canvas_id}' ({c.title}) closed."

    if action == "clear":
        c.content = ""
        c.updated_at = now
        _persist(c)
        return f"Canvas '{c.canvas_id}' cleared."

    if action == "query":
        content_len = len(c.content)
        preview = c.content[:1000] if c.content else "(empty)"
        return (
            f"Canvas '{c.canvas_id}'\n"
            f"  Type: {c.canvas_type}\n"
            f"  Title: {c.title}\n"
            f"  Content length: {content_len} chars\n"
            f"  Updated: {c.updated_at}\n\n"
            f"--- Content preview ---\n{preview}"
        )

    if action == "render" or action == "update":
        if content is None:
            return "Error: 'content' is required for render/update action"
        if mode == "append":
            c.content = (c.content + "\n" + content).strip()
        elif mode == "prepend":
            c.content = (content + "\n" + c.content).strip()
        else:
            c.content = content
        c.updated_at = now
        _persist(c)

        # Try to render to Telegram
        render_result = await _render_to_telegram(c)
        if render_result:
            return f"Canvas '{c.canvas_id}' updated and sent to Telegram ✅\n{render_result}"
        return f"Canvas '{c.canvas_id}' updated ✅ ({len(c.content)} chars, type={c.canvas_type})"

    if action == "screenshot":
        return await _canvas_screenshot(c)

    if action == "set":
        if not property:
            return "Error: 'property' is required for set action"
        if property == "title":
            c.title = value or c.title
        elif property == "type":
            c.canvas_type = value or c.canvas_type
        else:
            c.metadata[property] = value
        c.updated_at = now
        _persist(c)
        return f"Canvas '{c.canvas_id}' {property}={value} ✅"

    return f"Unknown action '{action}'. Use: open, close, render, update, query, clear, screenshot, list, set"


# ------------------------------------------------------------------
# Render helpers
# ------------------------------------------------------------------

async def _render_to_telegram(c: Canvas) -> str | None:
    """Attempt to render canvas content and send via Telegram."""
    try:
        from config import get_config
        cfg = get_config()
    except Exception:
        return None

    # Only render if CANVAS_TELEGRAM_RENDER=true
    if not getattr(cfg, "canvas_telegram_render", False):
        return None

    from tools.message_tool import _send_fn
    if not _send_fn:
        return None

    if c.canvas_type == "text":
        await _send_fn(f"**{c.title}**\n\n{c.content}")
        return "Sent as text message"

    if c.canvas_type == "table":
        rendered = _render_table(c.content)
        await _send_fn(f"**{c.title}**\n\n```\n{rendered}\n```")
        return "Sent as formatted table"

    if c.canvas_type == "chart":
        img_path = await _render_chart(c)
        if img_path:
            from tools.message_tool import _send_photo_fn
            if _send_photo_fn:
                await _send_photo_fn(img_path, caption=c.title)
                import os; os.unlink(img_path)
                return "Sent as chart image"
        await _send_fn(f"**{c.title}**\n\n{c.content}")
        return "Sent as text (chart render unavailable)"

    if c.canvas_type == "html":
        # Send as document
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write(f"<h1>{c.title}</h1>\n{c.content}")
            tmp_path = f.name
        from tools.message_tool import _send_document_fn
        if _send_document_fn:
            await _send_document_fn(tmp_path, caption=c.title)
            os.unlink(tmp_path)
            return "Sent as HTML document"
        os.unlink(tmp_path)
        return None

    return None


def _render_table(content: str) -> str:
    """Render JSON table data as ASCII table."""
    try:
        data = json.loads(content)
        if isinstance(data, list) and data:
            headers = list(data[0].keys())
            rows = [[str(row.get(h, "")) for h in headers] for row in data]
            col_widths = [max(len(h), max((len(r[i]) for r in rows), default=0)) for i, h in enumerate(headers)]
            sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
            header_row = "|" + "|".join(f" {h:<{col_widths[i]}} " for i, h in enumerate(headers)) + "|"
            data_rows = ["|" + "|".join(f" {r[i]:<{col_widths[i]}} " for i in range(len(headers))) + "|" for r in rows]
            return "\n".join([sep, header_row, sep] + data_rows + [sep])
    except Exception:
        pass
    return content


async def _render_chart(c: Canvas) -> str | None:
    """Render chart data as a PNG image using matplotlib."""
    try:
        import json
        import tempfile
        import os
        data = json.loads(c.content)
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(10, 6))
        ax.set_title(c.title)

        chart_type = data.get("type", "line")
        labels = data.get("labels", [])
        datasets = data.get("datasets", [])

        for ds in datasets:
            values = ds.get("data", [])
            label = ds.get("label", "")
            if chart_type == "bar":
                ax.bar(labels or range(len(values)), values, label=label)
            elif chart_type == "pie":
                ax.pie(values, labels=labels, autopct="%1.1f%%")
            else:
                ax.plot(labels or range(len(values)), values, label=label, marker="o")

        if datasets and chart_type != "pie":
            ax.legend()

        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        plt.savefig(path, bbox_inches="tight", dpi=150)
        plt.close()
        return path
    except ImportError:
        logger.debug("matplotlib not installed — cannot render chart")
        return None
    except Exception as e:
        logger.warning("Chart render error: %s", e)
        return None


async def _canvas_screenshot(c: Canvas) -> str:
    """Take a screenshot of the canvas as rendered HTML."""
    from config import get_config
    cfg = get_config()

    if not cfg.browser_enabled:
        # Fall back to text rendering
        return f"Canvas screenshot requires BROWSER_ENABLED=true.\n\nCanvas content:\n{c.content[:2000]}"

    try:
        import tempfile, os
        from tools.browser_tool import _get_page, _start_browser, _browser_instance, _send_photo_fn

        html = f"""<!DOCTYPE html>
<html><head><style>
  body {{ font-family: sans-serif; padding: 20px; background: white; }}
  h1 {{ color: #333; }} table {{ border-collapse: collapse; width: 100%; }}
  td, th {{ border: 1px solid #ddd; padding: 8px; }}
</style></head>
<body><h1>{c.title}</h1><div id="content">{c.content}</div></body></html>"""

        if not _browser_instance or not _browser_instance.is_connected():
            await _start_browser(cfg.browser_headless)

        page = await _get_page()
        await page.set_content(html)

        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        await page.screenshot(path=tmp_path, full_page=True)

        if _send_photo_fn:
            await _send_photo_fn(tmp_path, caption=f"Canvas: {c.title}")
            os.unlink(tmp_path)
            return f"Canvas screenshot sent ✅"
        return f"Canvas screenshot saved to {tmp_path}"
    except Exception as e:
        return f"Canvas screenshot error: {e}"


# ------------------------------------------------------------------
# Persistence
# ------------------------------------------------------------------

def _get_db_path() -> Path:
    try:
        from config import get_config
        return get_config().data_path / "canvas.db"
    except Exception:
        return Path("/tmp/pygate_canvas_test.db")


def _persist(c: Canvas) -> None:
    try:
        db = _get_db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS canvases (
                canvas_id TEXT PRIMARY KEY,
                canvas_type TEXT, title TEXT, content TEXT,
                created_at TEXT, updated_at TEXT, open INTEGER, metadata TEXT
            )
        """)
        conn.execute("""
            INSERT OR REPLACE INTO canvases VALUES (?,?,?,?,?,?,?,?)
        """, (
            c.canvas_id, c.canvas_type, c.title, c.content,
            c.created_at, c.updated_at, int(c.open),
            json.dumps(c.metadata),
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("Canvas persist error: %s", e)
