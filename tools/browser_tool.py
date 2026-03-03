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
        "Actions:\n"
        "  status     — check browser status and list tabs\n"
        "  start      — start the browser\n"
        "  stop       — close the browser\n"
        "  profiles   — list available browser profiles\n"
        "  tabs       — list all open tabs\n"
        "  open       — open a new tab (optionally navigate to url)\n"
        "  focus      — focus a tab by tab_id\n"
        "  close      — close a tab (tab_id) or browser (no tab_id)\n"
        "  navigate   — go to a URL in current tab\n"
        "  snapshot   — get structured page content (headings/links/inputs/buttons)\n"
        "  screenshot — capture screenshot and send as Telegram photo\n"
        "  console    — evaluate JS and capture console output\n"
        "  pdf        — render page as PDF and send as Telegram document\n"
        "  upload     — upload a local file to a file input\n"
        "  dialog     — handle a browser dialog (alert/confirm/prompt)\n"
        "  act        — sub-actions: click|type|press|hover|select|fill|scroll|wait|evaluate|drag"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "Browser action"},
            "url": {"type": "string", "description": "URL (for navigate, open)"},
            "tab_id": {"type": "integer", "description": "Tab ID (for focus, close, or target-specific actions)"},
            "selector": {"type": "string", "description": "CSS selector for target element"},
            "value": {"type": "string", "description": "Value to fill/type/select"},
            "key": {"type": "string", "description": "Keyboard key (for press; e.g. Enter, Tab, Escape)"},
            "script": {"type": "string", "description": "JavaScript to evaluate"},
            "file_path": {"type": "string", "description": "Local file path (for upload)"},
            "direction": {"type": "string", "description": "Scroll direction: up|down|left|right (default: down)"},
            "amount": {"type": "integer", "description": "Scroll pixels (default 500)"},
            "width": {"type": "integer", "description": "Viewport width in pixels (for resize action)"},
            "height": {"type": "integer", "description": "Viewport height in pixels (for resize action)"},
            "sub_action": {"type": "string", "description": "act sub-action: click|type|press|hover|select|fill|scroll|wait|evaluate|drag|resize|close"},
            "dialog_action": {"type": "string", "description": "Dialog action: accept|dismiss (default: accept)"},
            "prompt_text": {"type": "string", "description": "Text to enter in a prompt dialog"},
            "wait_ms": {"type": "integer", "description": "Milliseconds to wait (for sub_action=wait)"},
            "timeout": {"type": "integer", "description": "Action timeout in milliseconds (default 10000)"},
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
    url: str | None = None,
    tab_id: int | None = None,
    selector: str | None = None,
    value: str | None = None,
    key: str | None = None,
    script: str | None = None,
    file_path: str | None = None,
    direction: str = "down",
    amount: int = 500,
    sub_action: str | None = None,
    dialog_action: str = "accept",
    prompt_text: str | None = None,
    wait_ms: int = 1000,
    timeout: int = 10_000,
    width: int | None = None,
    height: int | None = None,
) -> str:
    global _playwright_instance, _browser_instance, _tabs, _active_tab_id

    cfg = get_config()
    if not cfg.browser_enabled:
        return "Error: browser is disabled (set BROWSER_ENABLED=true in .env)"

    action = action.lower().strip()

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
        if url:
            try:
                await tab.page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            except Exception as e:
                return f"Tab {tab.tab_id} opened but navigation failed: {e}"
        return f"Tab {tab.tab_id} opened ✅" + (f" — {url}" if url else "")

    if action == "focus":
        if tab_id is None:
            return "Error: 'tab_id' is required for focus action"
        t = _tabs.get(tab_id)
        if not t:
            return f"No tab with id {tab_id}"
        await t.page.bring_to_front()
        _active_tab_id = tab_id
        return f"Tab {tab_id} focused ✅"

    if action == "close":
        if tab_id is not None:
            t = _tabs.pop(tab_id, None)
            if not t:
                return f"No tab with id {tab_id}"
            try:
                await t.page.close()
            except Exception:
                pass
            if _active_tab_id == tab_id:
                _active_tab_id = next(iter(_tabs), None)
            return f"Tab {tab_id} closed ✅"
        else:
            await _close_all()
            return "Browser closed ✅"

    # ------------------------------------------------------------------
    # Get active page (auto-create if needed)
    # ------------------------------------------------------------------

    page = await _get_page(tab_id)
    if page is None:
        return "Error: no active tab. Use action=open first."

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    if action == "navigate":
        if not url:
            return "Error: 'url' is required for navigate action"
        try:
            await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            title = await page.title()
            return f"Navigated to: {url}\nTitle: {title}"
        except Exception as e:
            return f"Navigation error: {e}"

    # ------------------------------------------------------------------
    # Content reading
    # ------------------------------------------------------------------

    if action == "snapshot":
        try:
            content = await page.evaluate("""() => {
                const els = document.querySelectorAll('h1,h2,h3,h4,p,a,li,td,th,label,button,input,select,textarea,[role="button"],[role="link"]');
                return Array.from(els).map(el => {
                    const tag = el.tagName.toLowerCase();
                    const text = (el.innerText || el.placeholder || el.value || el.getAttribute('aria-label') || '').trim();
                    if (!text) return null;
                    const href = el.href || '';
                    const type = el.type || '';
                    const name = el.name || el.id || '';
                    let desc = `[${tag}`;
                    if (name) desc += `#${name}`;
                    if (type) desc += ` type=${type}`;
                    desc += `] ${text.slice(0, 200)}`;
                    if (href && !href.startsWith('javascript')) desc += ` (${href})`;
                    return desc;
                }).filter(Boolean).join('\\n');
            }""")
            tab_url = page.url
            title = await page.title()
            header = f"Page: {title}\nURL: {tab_url}\n\n"
            body = content[:8000] if content else "(no readable content found)"
            return header + body
        except Exception as e:
            return f"Snapshot error: {e}"

    if action == "screenshot":
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            await page.screenshot(path=tmp_path, full_page=False)
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
                return f"PDF sent ✅"
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
        if not script:
            return "Error: 'script' is required for console action"
        logs: list[str] = []

        def _on_console(msg):
            logs.append(f"[{msg.type}] {msg.text}")

        page.on("console", _on_console)
        try:
            result = await page.evaluate(script)
            return f"Result: {result}\nConsole:\n" + "\n".join(logs[-20:]) if logs else f"Result: {result}"
        except Exception as e:
            return f"Console eval error: {e}\nConsole:\n" + "\n".join(logs[-20:])
        finally:
            page.remove_listener("console", _on_console)

    # ------------------------------------------------------------------
    # Interactions
    # ------------------------------------------------------------------

    if action == "upload":
        if not selector:
            return "Error: 'selector' is required for upload action"
        if not file_path:
            return "Error: 'file_path' is required for upload action"
        from pathlib import Path
        fp = Path(file_path).expanduser()
        if not fp.exists():
            return f"Error: file not found: {file_path}"
        try:
            await page.set_input_files(selector, str(fp), timeout=timeout)
            return f"File uploaded to '{selector}' ✅"
        except Exception as e:
            return f"Upload error: {e}"

    if action == "dialog":
        # Pre-register the dialog handler before the triggering action
        import asyncio as _asyncio

        async def _handle_dialog(dialog):
            if dialog_action == "dismiss":
                await dialog.dismiss()
            else:
                if prompt_text:
                    await dialog.accept(prompt_text)
                else:
                    await dialog.accept()

        page.on("dialog", _handle_dialog)
        return f"Dialog handler registered (action={dialog_action}). Trigger the action that opens the dialog."

    # ------------------------------------------------------------------
    # act sub-actions
    # ------------------------------------------------------------------

    if action == "act":
        if not sub_action:
            return "Error: 'sub_action' is required for act action"
        return await _act(page, sub_action, selector, value, key, script, direction, amount, wait_ms, timeout, width, height)

    # Convenience aliases (direct action names map to act sub-actions)
    if action in ("click", "fill", "type", "press", "hover", "select", "scroll", "wait", "evaluate", "drag", "resize", "close"):
        return await _act(page, action, selector, value, key, script, direction, amount, wait_ms, timeout, width, height)

    return (
        f"Unknown browser action '{action}'. "
        "Use: status, start, stop, profiles, tabs, open, focus, close, navigate, snapshot, screenshot, console, pdf, upload, dialog, act"
    )


# ------------------------------------------------------------------
# Act sub-dispatcher
# ------------------------------------------------------------------

async def _act(
    page, sub_action: str,
    selector: str | None, value: str | None, key: str | None,
    script: str | None, direction: str, amount: int, wait_ms: int, timeout: int,
    width: int | None = None, height: int | None = None,
) -> str:
    sub_action = sub_action.lower().strip()

    if sub_action == "click":
        if not selector:
            return "Error: 'selector' required for click"
        try:
            await page.click(selector, timeout=timeout)
            return f"Clicked: {selector}"
        except Exception as e:
            return f"Click error on '{selector}': {e}"

    if sub_action == "fill":
        if not selector:
            return "Error: 'selector' required for fill"
        if value is None:
            return "Error: 'value' required for fill"
        try:
            await page.fill(selector, value, timeout=timeout)
            return f"Filled '{selector}'"
        except Exception as e:
            return f"Fill error: {e}"

    if sub_action == "type":
        if value is None:
            return "Error: 'value' required for type"
        try:
            await page.keyboard.type(value, delay=20)
            return "Typed text"
        except Exception as e:
            return f"Type error: {e}"

    if sub_action == "press":
        if not key:
            return "Error: 'key' required for press"
        try:
            await page.keyboard.press(key)
            return f"Pressed: {key}"
        except Exception as e:
            return f"Press error: {e}"

    if sub_action == "hover":
        if not selector:
            return "Error: 'selector' required for hover"
        try:
            await page.hover(selector, timeout=timeout)
            return f"Hovered: {selector}"
        except Exception as e:
            return f"Hover error: {e}"

    if sub_action == "select":
        if not selector:
            return "Error: 'selector' required for select"
        if value is None:
            return "Error: 'value' required for select"
        try:
            await page.select_option(selector, value, timeout=timeout)
            return f"Selected '{value}' in '{selector}'"
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
        import asyncio
        await asyncio.sleep(wait_ms / 1000)
        return f"Waited {wait_ms}ms"

    if sub_action == "evaluate":
        if not script:
            return "Error: 'script' required for evaluate"
        try:
            result = await page.evaluate(script)
            return f"Result: {result}"
        except Exception as e:
            return f"Evaluate error: {e}"

    if sub_action == "drag":
        if not selector or not value:
            return "Error: 'selector' (source) and 'value' (target selector) required for drag"
        try:
            src = await page.query_selector(selector)
            dst = await page.query_selector(value)
            if not src or not dst:
                return f"Drag error: selector not found"
            src_box = await src.bounding_box()
            dst_box = await dst.bounding_box()
            if not src_box or not dst_box:
                return "Drag error: could not get element bounding boxes"
            await page.mouse.move(src_box["x"] + src_box["width"] / 2, src_box["y"] + src_box["height"] / 2)
            await page.mouse.down()
            await page.mouse.move(dst_box["x"] + dst_box["width"] / 2, dst_box["y"] + dst_box["height"] / 2)
            await page.mouse.up()
            return f"Dragged from '{selector}' to '{value}'"
        except Exception as e:
            return f"Drag error: {e}"

    if sub_action == "resize":
        w = width or 1280
        h = height or 800
        try:
            await page.set_viewport_size({"width": w, "height": h})
            return f"Viewport resized to {w}x{h}"
        except Exception as e:
            return f"Resize error: {e}"

    if sub_action == "close":
        # Close the current tab
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

    return f"Unknown act sub_action '{sub_action}'. Use: click, fill, type, press, hover, select, scroll, wait, evaluate, drag, resize, close"


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
