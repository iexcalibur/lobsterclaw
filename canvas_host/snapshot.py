"""
Canvas snapshot — Playwright-based screenshot of a canvas session.

The canvas host serves the Next.js frontend at http://localhost:{port}.
For a snapshot, we navigate Playwright to /canvas/{session_id} and screenshot.

Also provides a fallback HTML renderer for when the canvas host isn't running.
"""

from __future__ import annotations

import base64
import logging
import tempfile
import os
from pathlib import Path

logger = logging.getLogger(__name__)


async def snapshot_session(
    session_id: str,
    canvas_host_url: str,
    *,
    full_page: bool = False,
    width: int = 1280,
    height: int = 800,
    timeout_ms: int = 10_000,
) -> str | None:
    """
    Take a Playwright screenshot of canvas session page.
    Returns base64-encoded PNG string, or None on failure.
    """
    url = f"{canvas_host_url}/canvas/{session_id}"
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": width, "height": height})
            await page.goto(url, timeout=timeout_ms, wait_until="networkidle")
            screenshot_bytes = await page.screenshot(type="png", full_page=full_page)
            await browser.close()
        return base64.standard_b64encode(screenshot_bytes).decode()
    except Exception as e:
        logger.warning("Canvas snapshot failed (url=%s): %s", url, e)
        return None


async def snapshot_to_file(
    session_id: str,
    canvas_host_url: str,
    *,
    full_page: bool = False,
    width: int = 1280,
    height: int = 800,
) -> str | None:
    """
    Snapshot and save to a temp PNG file. Returns file path, or None on failure.
    Caller is responsible for deleting the file.
    """
    b64 = await snapshot_session(
        session_id, canvas_host_url, full_page=full_page, width=width, height=height
    )
    if not b64:
        return None
    data = base64.standard_b64decode(b64)
    fd, path = tempfile.mkstemp(suffix=".png")
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return path


async def snapshot_html_fallback(html: str, title: str = "") -> str | None:
    """
    Render raw HTML with Playwright and return base64 PNG.
    Used when canvas_host isn't running but browser is available.
    """
    try:
        from playwright.async_api import async_playwright
        full_html = f"""<!DOCTYPE html>
<html>
<head>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0f0f11; color: #e2e8f0; padding: 24px; min-height: 100vh; }}
  h1 {{ color: #a78bfa; margin-bottom: 16px; font-size: 1.5rem; }}
  pre {{ background: #1e1e2e; border-radius: 6px; padding: 16px; overflow: auto; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th {{ background: #1e1e2e; color: #a78bfa; padding: 8px 12px; text-align: left; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #2d2d3e; }}
</style>
</head>
<body>
  {('<h1>' + title + '</h1>') if title else ''}
  {html}
</body>
</html>"""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1280, "height": 800})
            await page.set_content(full_html, wait_until="networkidle")
            screenshot_bytes = await page.screenshot(type="png", full_page=True)
            await browser.close()
        return base64.standard_b64encode(screenshot_bytes).decode()
    except Exception as e:
        logger.warning("HTML snapshot fallback failed: %s", e)
        return None
