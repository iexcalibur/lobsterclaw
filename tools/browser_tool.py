from __future__ import annotations

import logging

from config import get_config
from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

TOOL_DEFINITION = ToolDefinition(
    name="browser",
    description=(
        "Control a web browser. Actions: navigate, click, fill, screenshot, eval, snapshot, close. "
        "Only available when BROWSER_ENABLED=true."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["navigate", "click", "fill", "screenshot", "eval", "snapshot", "close"],
                "description": "Browser action to perform",
            },
            "url": {"type": "string", "description": "URL for 'navigate' action"},
            "selector": {"type": "string", "description": "CSS selector for 'click' or 'fill'"},
            "value": {"type": "string", "description": "Text to type for 'fill' action"},
            "script": {"type": "string", "description": "JavaScript to evaluate for 'eval' action"},
        },
        "required": ["action"],
    },
    fn=lambda **kw: _browser(**kw),
)

_page = None
_browser_instance = None
_playwright_instance = None


async def _browser(
    action: str,
    url: str | None = None,
    selector: str | None = None,
    value: str | None = None,
    script: str | None = None,
) -> str:
    cfg = get_config()
    if not cfg.browser_enabled:
        return "Error: browser is disabled. Set BROWSER_ENABLED=true in .env to enable."

    global _page, _browser_instance, _playwright_instance

    try:
        from playwright.async_api import async_playwright

        if _playwright_instance is None:
            _playwright_instance = await async_playwright().start()
            _browser_instance = await _playwright_instance.chromium.launch(headless=cfg.browser_headless)
            _page = await _browser_instance.new_page()

        if action == "navigate":
            if not url:
                return "Error: 'url' required for navigate"
            await _page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return f"Navigated to: {_page.url}"

        elif action == "click":
            if not selector:
                return "Error: 'selector' required for click"
            await _page.click(selector, timeout=10000)
            return f"Clicked: {selector}"

        elif action == "fill":
            if not selector or value is None:
                return "Error: 'selector' and 'value' required for fill"
            await _page.fill(selector, value, timeout=10000)
            return f"Filled '{selector}' with value"

        elif action == "screenshot":
            import base64
            data = await _page.screenshot(type="png")
            b64 = base64.b64encode(data).decode()
            return f"data:image/png;base64,{b64[:100]}... (screenshot taken, {len(data)} bytes)"

        elif action == "eval":
            if not script:
                return "Error: 'script' required for eval"
            result = await _page.evaluate(script)
            return str(result)[:4000]

        elif action == "snapshot":
            # Return page text content
            content = await _page.inner_text("body")
            return content[:6000]

        elif action == "close":
            if _page:
                await _page.close()
            if _browser_instance:
                await _browser_instance.close()
            if _playwright_instance:
                await _playwright_instance.stop()
            _page = _browser_instance = _playwright_instance = None
            return "Browser closed"

        return f"Error: unknown action '{action}'"

    except Exception as e:
        logger.exception("Browser error")
        return f"Browser error: {e}"
