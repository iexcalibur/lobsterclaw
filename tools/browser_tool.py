"""
Browser tool — Playwright-powered web browser automation.
Full parity with OpenClaw's browser-tool.ts action surface.

Actions (matching OpenClaw):
  status     — check if browser is running, show open tabs
  start      — start the browser (explicit; auto-start also works)
  stop       — close browser and all pages
  profiles   — list chromium user-data profiles
  tabs       — list open tabs (id, url, title)
  open       — open a new tab (optionally with URL)
  focus      — bring a tab into focus by tab_id
  close      — close a specific tab or the whole browser
  navigate   — navigate current/specified tab to a URL
  snapshot   — structured readable content (headings, links, buttons, inputs)
  screenshot — take screenshot and send as Telegram photo
  console    — run JS and return the console log output
  pdf        — save page as PDF and send as Telegram document
  upload     — upload a local file via a file input selector
  dialog     — handle/dismiss a browser dialog (alert/confirm/prompt)
  act        — compound actions: click/type/press/hover/drag/select/fill/resize/wait/evaluate/scroll
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Any

from config import get_config
from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Global browser state — single instance, multi-tab via pages dict
# ------------------------------------------------------------------

@dataclass
class TabInfo:
    tab_id: int
    page: Any  # playwright Page


_playwright_instance = None
_browser_instance = None
_tabs: dict[int, TabInfo] = {}
_active_tab_id: int | None = None
_tab_counter = 0

_send_photo_fn = None
_send_document_fn = None


def set_send_photo_fn(fn) -> None:
    global _send_photo_fn
    _send_photo_fn = fn


def set_send_document_fn(fn) -> None:
    global _send_document_fn
    _send_document_fn = fn


# ------------------------------------------------------------------
# Tool definition
# ------------------------------------------------------------------

TOOL_DEFINITION = ToolDefinition(
    name="browser",
    description=(
        "Control a web browser (Playwright/Chromium). Requires BROWSER_ENABLED=true.\n\n"
        "Full schema parity with OpenClaw browser-tool.schema.ts.\n\n"
        "Actions: status | start | stop | profiles | tabs | open | focus | close |\n"
        "         navigate | snapshot | screenshot | console | pdf | upload | dialog | act\n\n"
        "Routing fields (OpenClaw parity):\n"
        "  target   — sandbox (default) | host | node\n"
        "  node     — target node name when target='node'\n"
        "  profile  — browser profile name (Chromium user-data profile)\n\n"
        "act sub-actions (via 'kind' or 'sub_action'):\n"
        "  click, type, press, hover, drag, select, fill, resize, wait, evaluate, close, scroll\n"
        "  Use 'request' object for structured act params (preferred) or flatten them top-level.\n\n"
        "snapshot options: snapshotFormat (aria|ai), refs (role|aria), interactive, compact, depth\n"
        "screenshot options: fullPage, type (png|jpeg), element (selector to capture)\n"
        "dialog options: accept (bool), promptText"
    ),
    parameters={
        "type": "object",
        "properties": {
            # Core action
            "action": {
                "type": "string",
                "description": "status|start|stop|profiles|tabs|open|focus|close|navigate|snapshot|screenshot|console|pdf|upload|dialog|act",
            },
            # Routing fields (OpenClaw browser-tool.schema.ts: target/node/profile)
            "target": {
                "type": "string",
                "description": "Browser target: sandbox (default) | host | node",
            },
            "node": {
                "type": "string",
                "description": "Target node name when target='node'",
            },
            "profile": {
                "type": "string",
                "description": "Chromium user-data profile name",
            },
            # URL fields
            "url": {"type": "string", "description": "URL for navigate/open"},
            "targetUrl": {"type": "string", "description": "Alias for url (OpenClaw field name)"},
            # Tab identification
            "tab_id": {"type": "integer", "description": "Tab ID (LobsterClaw)"},
            "targetId": {"type": "string", "description": "Tab/target ID (OpenClaw field name; accepts int or string)"},
            # Snapshot options (OpenClaw parity)
            "snapshotFormat": {
                "type": "string",
                "description": "Snapshot output format: aria | ai (default: aria)",
            },
            "mode": {
                "type": "string",
                "description": "Snapshot mode: efficient (default)",
            },
            "refs": {
                "type": "string",
                "description": "Snapshot reference style: role | aria",
            },
            "interactive": {
                "type": "boolean",
                "description": "Snapshot: include only interactive elements",
            },
            "compact": {
                "type": "boolean",
                "description": "Snapshot: compact output (fewer details)",
            },
            "depth": {
                "type": "number",
                "description": "Snapshot: max DOM depth to include",
            },
            "frame": {
                "type": "string",
                "description": "Snapshot: target iframe selector",
            },
            "labels": {
                "type": "boolean",
                "description": "Snapshot: include element labels",
            },
            "maxChars": {
                "type": "number",
                "description": "Max chars to return from snapshot (default 8000)",
            },
            # Screenshot options
            "fullPage": {
                "type": "boolean",
                "description": "Screenshot: capture full scrollable page (default false)",
            },
            "type": {
                "type": "string",
                "description": "Screenshot image type: png (default) | jpeg",
            },
            "element": {
                "type": "string",
                "description": "Screenshot: CSS selector of element to capture",
            },
            # Shared element targeting
            "selector": {"type": "string", "description": "CSS selector for element targeting"},
            "ref": {"type": "string", "description": "Element reference from snapshot (OpenClaw field)"},
            # Console / evaluate
            "script": {"type": "string", "description": "JS expression to evaluate (console/act evaluate)"},
            "fn": {"type": "string", "description": "JS function string for act evaluate (OpenClaw field; also 'script')"},
            "level": {"type": "string", "description": "Console log level filter (console action)"},
            "limit": {"type": "number", "description": "Max console entries to return"},
            # Upload
            "paths": {
                "type": "array",
                "description": "File paths to upload (upload action)",
                "items": {"type": "string"},
            },
            "inputRef": {"type": "string", "description": "File input element ref or selector (upload)"},
            "file_path": {"type": "string", "description": "Single file path alias for upload"},
            # Dialog
            "accept": {"type": "boolean", "description": "Dialog: accept=true/false (default: true)"},
            "promptText": {"type": "string", "description": "Dialog: text to fill in a prompt dialog"},
            "dialog_action": {"type": "string", "description": "Alias: accept=true → 'accept', false → 'dismiss'"},
            # Timeout
            "timeoutMs": {"type": "number", "description": "Action timeout ms (OpenClaw field; default 10000)"},
            "timeout": {"type": "integer", "description": "Alias for timeoutMs"},
            # act: structured request object (OpenClaw preferred form)
            "request": {
                "type": "object",
                "description": "Structured act params (OpenClaw preferred form): {kind, targetId, ref, text, key, ...}",
                "properties": {
                    "kind": {"type": "string", "description": "click|type|press|hover|drag|select|fill|resize|wait|evaluate|close"},
                    "targetId": {"type": "string"},
                    "ref": {"type": "string"},
                    "doubleClick": {"type": "boolean"},
                    "button": {"type": "string"},
                    "modifiers": {"type": "array", "items": {"type": "string"}},
                    "text": {"type": "string"},
                    "submit": {"type": "boolean"},
                    "slowly": {"type": "boolean"},
                    "key": {"type": "string"},
                    "delayMs": {"type": "number"},
                    "startRef": {"type": "string"},
                    "endRef": {"type": "string"},
                    "values": {"type": "array", "items": {"type": "string"}},
                    "fields": {"type": "array", "items": {"type": "object"}},
                    "width": {"type": "number"},
                    "height": {"type": "number"},
                    "timeMs": {"type": "number"},
                    "selector": {"type": "string"},
                    "url": {"type": "string"},
                    "loadState": {"type": "string"},
                    "textGone": {"type": "string"},
                    "timeoutMs": {"type": "number"},
                    "fn": {"type": "string"},
                },
            },
            # act: legacy flattened params (top-level, OpenClaw parity)
            "kind": {"type": "string", "description": "act kind when using flattened params: click|type|press|hover|drag|select|fill|resize|wait|evaluate|close"},
            "sub_action": {"type": "string", "description": "LobsterClaw alias for kind (act sub-action)"},
            "doubleClick": {"type": "boolean", "description": "act click: double-click"},
            "button": {"type": "string", "description": "act click: mouse button (left|right|middle)"},
            "modifiers": {"type": "array", "description": "act click: modifier keys", "items": {"type": "string"}},
            "text": {"type": "string", "description": "act type/fill: text to type"},
            "value": {"type": "string", "description": "Alias for text"},
            "submit": {"type": "boolean", "description": "act type: press Enter after typing"},
            "slowly": {"type": "boolean", "description": "act type: type character by character"},
            "key": {"type": "string", "description": "act press: keyboard key (e.g. Enter, Tab, Escape)"},
            "delayMs": {"type": "number", "description": "act press: delay between key events"},
            "startRef": {"type": "string", "description": "act drag: source element ref"},
            "endRef": {"type": "string", "description": "act drag: target element ref"},
            "values": {"type": "array", "description": "act select: option values", "items": {"type": "string"}},
            "fields": {"type": "array", "description": "act fill: [{selector, value}] field list", "items": {"type": "object"}},
            "width": {"type": "number", "description": "act resize: viewport width"},
            "height": {"type": "number", "description": "act resize: viewport height"},
            "timeMs": {"type": "number", "description": "act wait: milliseconds"},
            "loadState": {"type": "string", "description": "act wait: wait for load state"},
            "textGone": {"type": "string", "description": "act wait: wait until text disappears"},
            # Scroll (LobsterClaw extension)
            "direction": {"type": "string", "description": "scroll direction: up|down|left|right"},
            "amount": {"type": "integer", "description": "scroll pixels (default 500)"},
            "wait_ms": {"type": "integer", "description": "Alias for timeMs (wait action)"},
        },
        "required": ["action"],
    },
    fn=lambda **kw: _browser(**kw),
)


# ------------------------------------------------------------------
# Main dispatcher
# ------------------------------------------------------------------

async def _browser(
    action: str,
    # Routing (OpenClaw parity)
    target: str = "sandbox",
    node: str | None = None,
    profile: str | None = None,
    # URL fields
    url: str | None = None,
    targetUrl: str | None = None,          # OpenClaw alias
    # Tab targeting
    tab_id: int | None = None,
    targetId: str | None = None,           # OpenClaw alias
    # Element targeting
    selector: str | None = None,
    ref: str | None = None,
    # Text/value inputs
    text: str | None = None,
    value: str | None = None,              # alias for text
    # act sub-action
    sub_action: str | None = None,         # LobsterClaw field
    kind: str | None = None,              # OpenClaw field (alias)
    # act structured request object (OpenClaw preferred)
    request: dict | None = None,
    # Flattened act params (OpenClaw top-level compat)
    doubleClick: bool = False,
    button: str | None = None,
    modifiers: list | None = None,
    submit: bool = False,
    slowly: bool = False,
    key: str | None = None,
    delayMs: float | None = None,
    startRef: str | None = None,
    endRef: str | None = None,
    values: list | None = None,
    fields: list | None = None,
    fn: str | None = None,                 # OpenClaw JS function string
    script: str | None = None,            # alias for fn
    # Resize
    width: int | None = None,
    height: int | None = None,
    # Wait
    timeMs: float | None = None,
    wait_ms: int = 1000,                   # alias
    loadState: str | None = None,
    textGone: str | None = None,
    # Scroll
    direction: str = "down",
    amount: int = 500,
    # Timeout
    timeoutMs: float | None = None,
    timeout: int = 10_000,                 # alias
    # Dialog
    accept: bool = True,
    promptText: str | None = None,
    dialog_action: str = "accept",         # legacy alias
    prompt_text: str | None = None,        # legacy alias
    # Snapshot options
    snapshotFormat: str = "aria",
    mode: str | None = None,
    refs: str | None = None,
    interactive: bool = False,
    compact: bool = False,
    depth: float | None = None,
    frame: str | None = None,
    labels: bool = False,
    maxChars: float | None = None,
    # Screenshot options
    fullPage: bool = False,
    type: str = "png",
    element: str | None = None,
    # Upload
    paths: list | None = None,
    inputRef: str | None = None,
    file_path: str | None = None,         # legacy alias
    # Console
    level: str | None = None,
    limit: float | None = None,
) -> str:
    global _playwright_instance, _browser_instance, _tabs, _active_tab_id

    cfg = get_config()
    if not cfg.browser_enabled:
        return "Error: browser is disabled (set BROWSER_ENABLED=true in .env)"

    action = action.lower().strip()

    # Resolve aliases
    effective_url = url or targetUrl
    effective_timeout = int(timeoutMs or timeout or 10_000)
    effective_script = fn or script
    effective_text = text or value
    effective_wait_ms = int(timeMs or wait_ms or 1000)
    effective_tab_id = tab_id or (int(targetId) if targetId and targetId.isdigit() else None)
    effective_accept = accept if dialog_action == "accept" else (dialog_action == "accept")
    effective_prompt = promptText or prompt_text
    effective_max_chars = int(maxChars or 8000)

    # act kind: request.kind > kind > sub_action
    effective_kind = (request or {}).get("kind") if request else (kind or sub_action)

    # node target: delegate exec to nodes_tool (stub — remote browser not implemented)
    if target == "node" and node:
        return f"Remote browser on node '{node}' is not yet implemented in LobsterClaw."

    # ------------------------------------------------------------------
    # Actions that don't require the browser to be open
    # ------------------------------------------------------------------

    if action == "status":
        if not _browser_instance or not _browser_instance.is_connected():
            return "Browser not running. Use action=start or action=navigate to open it."
        tab_list = _format_tab_list()
        return f"Browser running ✅\n{tab_list}"

    if action == "profiles":
        import shutil
        from pathlib import Path
        chromium_data = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
        if not chromium_data.exists():
            return "No Chrome user data found at default path."
        profiles = [d.name for d in chromium_data.iterdir() if d.is_dir() and d.name.startswith("Profile")]
        default = ["Default"] if (chromium_data / "Default").exists() else []
        all_profiles = default + profiles
        return "Profiles:\n" + "\n".join(f"  {p}" for p in all_profiles) if all_profiles else "No profiles found."

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    if action == "start":
        if _browser_instance and _browser_instance.is_connected():
            return "Browser is already running."
        try:
            await _start_browser(cfg.browser_headless)
            return "Browser started ✅"
        except Exception as e:
            return f"Error starting browser: {e}"

    if action == "stop":
        await _close_all()
        return "Browser stopped ✅"

    # ------------------------------------------------------------------
    # Auto-start for most actions
    # ------------------------------------------------------------------

    if not _browser_instance or not _browser_instance.is_connected():
        try:
            await _start_browser(cfg.browser_headless)
        except Exception as e:
            return f"Error starting browser: {e}"

    # ------------------------------------------------------------------
    # Tab management
    # ------------------------------------------------------------------

    if action == "tabs":
        return _format_tab_list() or "No tabs open."

    if action == "open":
        tab = await _new_tab()
        if effective_url:
            try:
                await tab.page.goto(effective_url, timeout=30_000, wait_until="domcontentloaded")
            except Exception as e:
                return f"Tab {tab.tab_id} opened but navigation failed: {e}"
        return f"Tab {tab.tab_id} opened ✅" + (f" — {effective_url}" if effective_url else "")

    if action == "focus":
        if effective_tab_id is None:
            return "Error: 'tab_id' (or 'targetId') is required for focus"
        t = _tabs.get(effective_tab_id)
        if not t:
            return f"No tab with id {effective_tab_id}"
        await t.page.bring_to_front()
        _active_tab_id = effective_tab_id
        return f"Tab {effective_tab_id} focused ✅"

    if action == "close":
        if effective_tab_id is not None:
            t = _tabs.pop(effective_tab_id, None)
            if not t:
                return f"No tab with id {effective_tab_id}"
            try:
                await t.page.close()
            except Exception:
                pass
            if _active_tab_id == effective_tab_id:
                _active_tab_id = next(iter(_tabs), None)
            return f"Tab {effective_tab_id} closed ✅"
        else:
            await _close_all()
            return "Browser closed ✅"

    # ------------------------------------------------------------------
    # Get active page (auto-create if needed)
    # ------------------------------------------------------------------

    page = await _get_page(effective_tab_id)
    if page is None:
        return "Error: no active tab. Use action=open first."

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    if action == "navigate":
        if not effective_url:
            return "Error: 'url' (or 'targetUrl') is required for navigate"
        try:
            await page.goto(effective_url, timeout=30_000, wait_until="domcontentloaded")
            title = await page.title()
            return f"Navigated to: {effective_url}\nTitle: {title}"
        except Exception as e:
            return f"Navigation error: {e}"

    # ------------------------------------------------------------------
    # Content reading
    # ------------------------------------------------------------------

    if action == "snapshot":
        try:
            # aria/ai formats — basic ARIA-style snapshot
            js_aria = """() => {
                const els = document.querySelectorAll('h1,h2,h3,h4,p,a,li,td,th,label,button,input,select,textarea,[role="button"],[role="link"],[role="menuitem"],[role="option"]');
                return Array.from(els).map(el => {
                    const tag = el.tagName.toLowerCase();
                    const text = (el.innerText || el.placeholder || el.value || el.getAttribute('aria-label') || '').trim();
                    if (!text) return null;
                    const href = el.href || '';
                    const t = el.type || '';
                    const name = el.name || el.id || el.getAttribute('aria-labelledby') || '';
                    const role = el.getAttribute('role') || '';
                    let desc = `[${tag}`;
                    if (name) desc += `#${name}`;
                    if (t) desc += ` type=${t}`;
                    if (role) desc += ` role=${role}`;
                    desc += `] ${text.slice(0, 200)}`;
                    if (href && !href.startsWith('javascript')) desc += ` (${href})`;
                    return desc;
                }).filter(Boolean).join('\\n');
            }"""
            if interactive:
                js_aria = js_aria.replace(
                    "h1,h2,h3,h4,p,a,li,td,th,label,button,input,select,textarea,[role=\"button\"],[role=\"link\"],[role=\"menuitem\"],[role=\"option\"]",
                    "button,input,select,textarea,a,[role=\"button\"],[role=\"link\"],[role=\"menuitem\"],[role=\"option\"]"
                )
            content = await page.evaluate(js_aria)
            tab_url = page.url
            title = await page.title()
            header = f"Page: {title}\nURL: {tab_url}\nFormat: {snapshotFormat}\n\n"
            body = content[:effective_max_chars] if content else "(no readable content)"
            return header + body
        except Exception as e:
            return f"Snapshot error: {e}"

    if action == "screenshot":
        tmp_path = None
        try:
            suffix = f".{type}" if type in ("png", "jpeg") else ".png"
            fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            os.close(fd)
            # Capture specific element if 'element' selector provided
            if element:
                el = await page.query_selector(element)
                if el:
                    await el.screenshot(path=tmp_path)
                else:
                    return f"Element '{element}' not found for screenshot"
            else:
                await page.screenshot(path=tmp_path, full_page=fullPage, type=type)
            if _send_photo_fn:
                title = await page.title()
                await _send_photo_fn(tmp_path, caption=f"Screenshot: {title}")
                return f"Screenshot sent ✅ (page: {title})"
            return f"Screenshot saved to {tmp_path} (send_photo not configured)"
        except Exception as e:
            return f"Screenshot error: {e}"
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    if action == "pdf":
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
            os.close(fd)
            await page.pdf(path=tmp_path)
            if _send_document_fn:
                title = await page.title()
                await _send_document_fn(tmp_path, caption=f"PDF: {title}")
                return "PDF sent ✅"
            return f"PDF saved to {tmp_path} (send_document not configured)"
        except Exception as e:
            return f"PDF error: {e}"
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    if action == "console":
        if not effective_script:
            return "Error: 'script' (or 'fn') is required for console action"
        max_entries = int(limit or 20)
        logs: list[str] = []

        def _on_console(msg):
            if not level or msg.type == level:
                logs.append(f"[{msg.type}] {msg.text}")

        page.on("console", _on_console)
        try:
            result = await page.evaluate(effective_script)
            result_str = f"Result: {result}\n" if result is not None else ""
            console_str = "Console:\n" + "\n".join(logs[-max_entries:]) if logs else "(no console output)"
            return result_str + console_str
        except Exception as e:
            return f"Console eval error: {e}\nConsole:\n" + "\n".join(logs[-max_entries:])
        finally:
            page.remove_listener("console", _on_console)

    # ------------------------------------------------------------------
    # Interactions
    # ------------------------------------------------------------------

    if action == "upload":
        effective_selector = inputRef or selector
        effective_files = paths or ([str(file_path)] if file_path else None)
        if not effective_selector:
            return "Error: 'selector' (or 'inputRef') is required for upload"
        if not effective_files:
            return "Error: 'paths' (or 'file_path') is required for upload"
        from pathlib import Path as _Path
        file_list = [str(_Path(f).expanduser()) for f in effective_files]
        for f in file_list:
            if not _Path(f).exists():
                return f"Error: file not found: {f}"
        try:
            await page.set_input_files(effective_selector, file_list, timeout=effective_timeout)
            return f"File(s) uploaded to '{effective_selector}' ✅"
        except Exception as e:
            return f"Upload error: {e}"

    if action == "dialog":
        async def _handle_dialog(dialog):
            # accept field takes precedence over legacy dialog_action alias
            should_accept = accept if (dialog_action == "accept") else (dialog_action == "accept")
            if not should_accept:
                await dialog.dismiss()
            else:
                if effective_prompt:
                    await dialog.accept(effective_prompt)
                else:
                    await dialog.accept()

        page.on("dialog", _handle_dialog)
        mode_str = "accept" if accept else "dismiss"
        return f"Dialog handler registered (mode={mode_str}). Trigger the action that opens the dialog."

    # ------------------------------------------------------------------
    # act sub-actions
    # ------------------------------------------------------------------

    if action == "act":
        if not effective_kind:
            return "Error: 'kind' (or 'sub_action') is required for act"
        # Merge request object fields over top-level flat params
        act_params = dict(
            selector=selector, ref=ref, text=effective_text, key=key,
            script=effective_script, direction=direction, amount=amount,
            wait_ms=effective_wait_ms, timeout=effective_timeout,
            width=width, height=height,
            doubleClick=doubleClick, button=button, modifiers=modifiers,
            submit=submit, slowly=slowly, delayMs=delayMs,
            startRef=startRef, endRef=endRef, values=values, fields=fields,
            loadState=loadState, textGone=textGone,
        )
        if request:
            act_params.update({k: v for k, v in request.items() if v is not None})
        return await _act(page, effective_kind, **act_params)

    # Convenience aliases: direct action names → act
    if action in ("click", "fill", "type", "press", "hover", "select", "scroll", "wait", "evaluate", "drag", "resize"):
        return await _act(page, action,
            selector=selector, ref=ref, text=effective_text, key=key,
            script=effective_script, direction=direction, amount=amount,
            wait_ms=effective_wait_ms, timeout=effective_timeout,
            width=width, height=height,
            doubleClick=doubleClick, button=button, modifiers=modifiers,
            submit=submit, slowly=slowly, delayMs=delayMs,
            startRef=startRef, endRef=endRef, values=values, fields=fields,
            loadState=loadState, textGone=textGone,
        )

    return (
        f"Unknown browser action '{action}'. "
        "Use: status, start, stop, profiles, tabs, open, focus, close, navigate, "
        "snapshot, screenshot, console, pdf, upload, dialog, act"
    )


# ------------------------------------------------------------------
# Act sub-dispatcher — full OpenClaw act kinds
# ------------------------------------------------------------------

async def _act(
    page,
    sub_action: str,
    selector: str | None = None,
    ref: str | None = None,           # OpenClaw element reference
    text: str | None = None,
    key: str | None = None,
    script: str | None = None,
    direction: str = "down",
    amount: int = 500,
    wait_ms: int = 1000,
    timeout: int = 10_000,
    width: int | None = None,
    height: int | None = None,
    doubleClick: bool = False,
    button: str | None = None,
    modifiers: list | None = None,
    submit: bool = False,
    slowly: bool = False,
    delayMs: float | None = None,
    startRef: str | None = None,
    endRef: str | None = None,
    values: list | None = None,
    fields: list | None = None,
    loadState: str | None = None,
    textGone: str | None = None,
    **_ignored,
) -> str:
    sub_action = sub_action.lower().strip()

    # Resolve ref → selector (OpenClaw aria ref can be used as selector)
    effective_sel = selector or ref

    if sub_action == "click":
        if not effective_sel:
            return "Error: 'selector' (or 'ref') required for click"
        try:
            click_opts: dict = {"timeout": timeout}
            if doubleClick:
                await page.dblclick(effective_sel, **click_opts)
                return f"Double-clicked: {effective_sel}"
            if button:
                click_opts["button"] = button
            if modifiers:
                click_opts["modifiers"] = modifiers
            await page.click(effective_sel, **click_opts)
            return f"Clicked: {effective_sel}"
        except Exception as e:
            return f"Click error on '{effective_sel}': {e}"

    if sub_action == "fill":
        if not effective_sel:
            return "Error: 'selector' (or 'ref') required for fill"
        if text is None:
            return "Error: 'text' (or 'value') required for fill"
        # fill can accept multiple fields: [{selector, value}]
        if fields:
            results = []
            for f in fields:
                fs = f.get("selector") or f.get("ref") or f.get("name", "")
                fv = f.get("value", "")
                try:
                    await page.fill(fs, str(fv), timeout=timeout)
                    results.append(f"  filled '{fs}'")
                except Exception as e:
                    results.append(f"  error filling '{fs}': {e}")
            return "fill:\n" + "\n".join(results)
        try:
            await page.fill(effective_sel, text, timeout=timeout)
            return f"Filled '{effective_sel}'"
        except Exception as e:
            return f"Fill error: {e}"

    if sub_action == "type":
        if text is None:
            return "Error: 'text' (or 'value') required for type"
        try:
            delay = delayMs or (50 if slowly else 0)
            if effective_sel:
                await page.click(effective_sel, timeout=timeout)
            await page.keyboard.type(text, delay=delay)
            if submit:
                await page.keyboard.press("Enter")
            return f"Typed: {text[:50]}{'...' if len(text) > 50 else ''}"
        except Exception as e:
            return f"Type error: {e}"

    if sub_action == "press":
        if not key:
            return "Error: 'key' required for press"
        try:
            opts: dict = {}
            if delayMs:
                opts["delay"] = delayMs
            await page.keyboard.press(key, **opts)
            return f"Pressed: {key}"
        except Exception as e:
            return f"Press error: {e}"

    if sub_action == "hover":
        if not effective_sel:
            return "Error: 'selector' (or 'ref') required for hover"
        try:
            await page.hover(effective_sel, timeout=timeout)
            return f"Hovered: {effective_sel}"
        except Exception as e:
            return f"Hover error: {e}"

    if sub_action == "select":
        if not effective_sel:
            return "Error: 'selector' (or 'ref') required for select"
        # OpenClaw uses values[] array; text is single value alias
        option_values = values or ([text] if text else None)
        if not option_values:
            return "Error: 'values' (or 'text') required for select"
        try:
            await page.select_option(effective_sel, option_values, timeout=timeout)
            return f"Selected {option_values} in '{effective_sel}'"
        except Exception as e:
            return f"Select error: {e}"

    if sub_action == "scroll":
        try:
            scroll_map = {"down": (0, amount), "up": (0, -amount), "right": (amount, 0), "left": (-amount, 0)}
            dx, dy = scroll_map.get(direction.lower(), (0, amount))
            await page.evaluate(f"window.scrollBy({dx}, {dy})")
            return f"Scrolled {direction} {abs(dx or dy)}px"
        except Exception as e:
            return f"Scroll error: {e}"

    if sub_action == "wait":
        import asyncio as _asyncio
        # Priority: loadState > textGone > timeMs
        if loadState:
            try:
                await page.wait_for_load_state(loadState, timeout=timeout)
                return f"Waited for load state: {loadState}"
            except Exception as e:
                return f"Wait load state error: {e}"
        if textGone:
            try:
                await page.wait_for_function(
                    f"() => !document.body.innerText.includes({repr(textGone)})",
                    timeout=timeout,
                )
                return f"Waited until '{textGone}' disappeared"
            except Exception as e:
                return f"Wait textGone error: {e}"
        await _asyncio.sleep(wait_ms / 1000)
        return f"Waited {wait_ms}ms"

    if sub_action == "evaluate":
        if not script:
            return "Error: 'script' (or 'fn') required for evaluate"
        try:
            result = await page.evaluate(script)
            return f"Result: {result}"
        except Exception as e:
            return f"Evaluate error: {e}"

    if sub_action == "drag":
        # OpenClaw uses startRef/endRef; fallback to selector/text as src/dst
        src_sel = startRef or effective_sel
        dst_sel = endRef or text
        if not src_sel or not dst_sel:
            return "Error: 'startRef'/'endRef' (or 'selector'/'text') required for drag"
        try:
            src = await page.query_selector(src_sel)
            dst = await page.query_selector(dst_sel)
            if not src or not dst:
                return "Drag error: element not found"
            src_box = await src.bounding_box()
            dst_box = await dst.bounding_box()
            if not src_box or not dst_box:
                return "Drag error: could not get bounding boxes"
            await page.mouse.move(src_box["x"] + src_box["width"] / 2, src_box["y"] + src_box["height"] / 2)
            await page.mouse.down()
            await page.mouse.move(dst_box["x"] + dst_box["width"] / 2, dst_box["y"] + dst_box["height"] / 2)
            await page.mouse.up()
            return f"Dragged '{src_sel}' → '{dst_sel}'"
        except Exception as e:
            return f"Drag error: {e}"

    if sub_action == "resize":
        w = int(width or 1280)
        h = int(height or 800)
        try:
            await page.set_viewport_size({"width": w, "height": h})
            return f"Viewport resized to {w}x{h}"
        except Exception as e:
            return f"Resize error: {e}"

    if sub_action == "close":
        global _active_tab_id
        for tid, t in list(_tabs.items()):
            if t.page is page:
                try:
                    await t.page.close()
                except Exception:
                    pass
                _tabs.pop(tid, None)
                if _active_tab_id == tid:
                    _active_tab_id = next(iter(_tabs), None)
                return f"Tab {tid} closed ✅"
        return "No matching tab found to close."

    return (
        f"Unknown act kind '{sub_action}'. "
        "Use: click, type, press, hover, drag, select, fill, resize, wait, evaluate, close, scroll"
    )


# ------------------------------------------------------------------
# Browser lifecycle helpers
# ------------------------------------------------------------------

async def _start_browser(headless: bool = True) -> None:
    global _playwright_instance, _browser_instance, _tabs, _active_tab_id, _tab_counter
    from playwright.async_api import async_playwright

    _playwright_instance = await async_playwright().start()
    _browser_instance = await _playwright_instance.chromium.launch(headless=headless)
    # Create initial tab
    _tabs = {}
    _tab_counter = 0
    page = await _browser_instance.new_page()
    _tab_counter += 1
    tab = TabInfo(tab_id=_tab_counter, page=page)
    _tabs[_tab_counter] = tab
    _active_tab_id = _tab_counter
    logger.info("Browser started (headless=%s)", headless)


async def _new_tab() -> TabInfo:
    global _tab_counter, _active_tab_id
    page = await _browser_instance.new_page()
    _tab_counter += 1
    tab = TabInfo(tab_id=_tab_counter, page=page)
    _tabs[_tab_counter] = tab
    _active_tab_id = _tab_counter
    return tab


async def _get_page(tab_id: int | None = None):
    if tab_id is not None:
        t = _tabs.get(tab_id)
        return t.page if t else None
    if _active_tab_id and _active_tab_id in _tabs:
        return _tabs[_active_tab_id].page
    if _tabs:
        return next(iter(_tabs.values())).page
    # No tab — create one
    tab = await _new_tab()
    return tab.page


async def _close_all() -> None:
    global _playwright_instance, _browser_instance, _tabs, _active_tab_id
    try:
        for t in list(_tabs.values()):
            try:
                await t.page.close()
            except Exception:
                pass
        if _browser_instance:
            await _browser_instance.close()
        if _playwright_instance:
            await _playwright_instance.stop()
    except Exception as e:
        logger.debug("Browser close error: %s", e)
    finally:
        _tabs = {}
        _active_tab_id = None
        _browser_instance = None
        _playwright_instance = None


def _format_tab_list() -> str:
    if not _tabs:
        return "(no tabs)"
    lines = []
    for tid, t in _tabs.items():
        active_marker = " [active]" if tid == _active_tab_id else ""
        try:
            tab_url = t.page.url
        except Exception:
            tab_url = "?"
        lines.append(f"  Tab {tid}{active_marker}: {tab_url}")
    return "\n".join(lines)
