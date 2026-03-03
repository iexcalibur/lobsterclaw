"""
Browser tool — Playwright-powered web browser automation.
Mirrors OpenClaw's browser tool core actions.

Actions:
  navigate   — load a URL
  click      — click an element
  fill       — fill an input field
  type       — type text character-by-character
  press      — press a keyboard key
  hover      — hover over an element
  select     — select a dropdown option
  screenshot — capture screenshot and send as Telegram photo
  snapshot   — return page text structure (for reading content)
  eval       — run JavaScript and return the result
  scroll     — scroll the page
  close      — close the browser
  status     — check if browser is running
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile

from config import get_config
from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

# Module-level browser state (single global instance)
_playwright_instance = None
_browser_instance = None
_page = None

# Injected by main.py to send screenshots as photos
_send_photo_fn = None


def set_send_photo_fn(fn) -> None:
    global _send_photo_fn
    _send_photo_fn = fn


TOOL_DEFINITION = ToolDefinition(
    name="browser",
    description=(
        "Control a web browser (Playwright/Chromium). "
        "Requires BROWSER_ENABLED=true in .env.\n"
        "Actions:\n"
        "  navigate   — go to a URL\n"
        "  click      — click an element (use CSS selector)\n"
        "  fill       — clear and fill an input\n"
        "  type       — type text into focused element\n"
        "  press      — press a key (e.g. 'Enter', 'Tab')\n"
        "  hover      — hover over an element\n"
        "  select     — choose a dropdown option by value\n"
        "  screenshot — take a screenshot and send as Telegram photo\n"
        "  snapshot   — get readable page text/structure\n"
        "  eval       — run JavaScript, return result\n"
        "  scroll     — scroll up/down\n"
        "  close      — close the browser\n"
        "  status     — check if browser is open"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "Browser action to perform"},
            "url": {"type": "string", "description": "URL to navigate to"},
            "selector": {"type": "string", "description": "CSS selector for target element"},
            "value": {"type": "string", "description": "Value to fill/type/select"},
            "key": {"type": "string", "description": "Keyboard key to press (e.g. Enter)"},
            "script": {"type": "string", "description": "JavaScript to evaluate"},
            "direction": {
                "type": "string",
                "description": "Scroll direction: up or down (default: down)",
            },
            "amount": {
                "type": "integer",
                "description": "Pixels to scroll (default 500)",
            },
        },
        "required": ["action"],
    },
    fn=lambda **kw: _browser(**kw),
)


async def _browser(
    action: str,
    url: str | None = None,
    selector: str | None = None,
    value: str | None = None,
    key: str | None = None,
    script: str | None = None,
    direction: str = "down",
    amount: int = 500,
) -> str:
    global _playwright_instance, _browser_instance, _page

    cfg = get_config()
    if not cfg.browser_enabled:
        return "Error: browser is disabled (set BROWSER_ENABLED=true in .env)"

    action = action.lower().strip()

    if action == "status":
        if _page and _browser_instance and _browser_instance.is_connected():
            try:
                current_url = _page.url
                return f"Browser open ✅ — current URL: {current_url}"
            except Exception:
                return "Browser open ✅"
        return "Browser not running. Use action=navigate to start."

    # Auto-start browser if needed (except for close/status)
    if action not in ("close", "status"):
        if _page is None or _browser_instance is None or not _browser_instance.is_connected():
            try:
                await _start_browser(cfg.browser_headless)
            except Exception as e:
                return f"Error starting browser: {e}"

    if action == "navigate":
        if not url:
            return "Error: 'url' is required for navigate action"
        try:
            await _page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            title = await _page.title()
            return f"Navigated to: {url}\nPage title: {title}"
        except Exception as e:
            return f"Navigation error: {e}"

    if action == "click":
        if not selector:
            return "Error: 'selector' is required for click action"
        try:
            await _page.click(selector, timeout=10_000)
            return f"Clicked: {selector}"
        except Exception as e:
            return f"Click error on '{selector}': {e}"

    if action == "fill":
        if not selector:
            return "Error: 'selector' is required for fill action"
        if value is None:
            return "Error: 'value' is required for fill action"
        try:
            await _page.fill(selector, value, timeout=10_000)
            return f"Filled '{selector}' with value"
        except Exception as e:
            return f"Fill error on '{selector}': {e}"

    if action == "type":
        if value is None:
            return "Error: 'value' is required for type action"
        try:
            await _page.keyboard.type(value)
            return f"Typed text"
        except Exception as e:
            return f"Type error: {e}"

    if action == "press":
        if not key:
            return "Error: 'key' is required for press action"
        try:
            await _page.keyboard.press(key)
            return f"Pressed key: {key}"
        except Exception as e:
            return f"Press error: {e}"

    if action == "hover":
        if not selector:
            return "Error: 'selector' is required for hover action"
        try:
            await _page.hover(selector, timeout=10_000)
            return f"Hovered over: {selector}"
        except Exception as e:
            return f"Hover error on '{selector}': {e}"

    if action == "select":
        if not selector:
            return "Error: 'selector' is required for select action"
        if value is None:
            return "Error: 'value' is required for select action"
        try:
            await _page.select_option(selector, value, timeout=10_000)
            return f"Selected '{value}' in '{selector}'"
        except Exception as e:
            return f"Select error on '{selector}': {e}"

    if action == "screenshot":
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            await _page.screenshot(path=tmp_path, full_page=False)
            # Send as Telegram photo if send_photo_fn is wired
            if _send_photo_fn:
                await _send_photo_fn(tmp_path, caption="Screenshot")
                return "Screenshot taken and sent as photo ✅"
            else:
                # Return a description instead of useless base64
                title = await _page.title()
                current_url = _page.url
                return f"Screenshot taken (Telegram photo send not configured).\nPage: {title} — {current_url}"
        except Exception as e:
            return f"Screenshot error: {e}"
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    if action == "snapshot":
        try:
            # Get structured text content (headings, links, paragraphs)
            content = await _page.evaluate("""() => {
                const elements = document.querySelectorAll('h1,h2,h3,p,a,li,td,th,label,button,input[placeholder]');
                return Array.from(elements).map(el => {
                    const tag = el.tagName.toLowerCase();
                    const text = el.innerText || el.placeholder || el.value || '';
                    if (!text.trim()) return null;
                    const href = el.href || '';
                    return href ? `[${tag}] ${text.trim()} (${href})` : `[${tag}] ${text.trim()}`;
                }).filter(Boolean).join('\\n');
            }""")
            url_now = _page.url
            title = await _page.title()
            header = f"Page: {title}\nURL: {url_now}\n\n"
            return header + (content[:6000] if content else "(no readable content found)")
        except Exception as e:
            return f"Snapshot error: {e}"

    if action == "eval":
        if not script:
            return "Error: 'script' is required for eval action"
        try:
            result = await _page.evaluate(script)
            return f"Result: {result}"
        except Exception as e:
            return f"Eval error: {e}"

    if action == "scroll":
        try:
            px = amount if direction != "up" else -amount
            await _page.evaluate(f"window.scrollBy(0, {px})")
            return f"Scrolled {'down' if px > 0 else 'up'} {abs(px)}px"
        except Exception as e:
            return f"Scroll error: {e}"

    if action == "close":
        await _close_browser()
        return "Browser closed ✅"

    return (
        f"Unknown browser action '{action}'. "
        "Use: navigate, click, fill, type, press, hover, select, screenshot, snapshot, eval, scroll, close, status"
    )


async def _start_browser(headless: bool = True) -> None:
    global _playwright_instance, _browser_instance, _page
    from playwright.async_api import async_playwright

    _playwright_instance = await async_playwright().start()
    _browser_instance = await _playwright_instance.chromium.launch(headless=headless)
    _page = await _browser_instance.new_page()
    logger.info("Browser started (headless=%s)", headless)


async def _close_browser() -> None:
    global _playwright_instance, _browser_instance, _page
    try:
        if _page:
            await _page.close()
        if _browser_instance:
            await _browser_instance.close()
        if _playwright_instance:
            await _playwright_instance.stop()
    except Exception as e:
        logger.debug("Browser close error: %s", e)
    finally:
        _page = None
        _browser_instance = None
        _playwright_instance = None
