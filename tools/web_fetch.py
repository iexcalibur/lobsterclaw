"""
Web fetch tool — mirrors OpenClaw's web-fetch.ts.

Fetches a URL and returns readable text content.

Params (matching OpenClaw schema):
  url        — required
  headers    — optional dict of extra headers
  timeout    — request timeout in seconds (default 20)
  render_js  — bool: render JavaScript via Playwright before extracting (requires BROWSER_ENABLED)
  selector   — CSS selector to extract a specific element's text (optional)
  max_chars  — cap output length (default 50000)
"""

from __future__ import annotations

import ipaddress
import logging
import re
from urllib.parse import urlparse

import httpx

from config import get_config
from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

# CVE-2026-26322 — SSRF (Server-Side Request Forgery) guard
# Blocks requests to private/loopback/link-local/metadata IP ranges before
# any DNS resolution happens. Scheme allow-list prevents file://, gopher://, etc.

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# RFC 1918 + loopback + link-local + APIPA + cloud metadata
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),        # RFC 1918
    ipaddress.ip_network("172.16.0.0/12"),      # RFC 1918
    ipaddress.ip_network("192.168.0.0/16"),     # RFC 1918
    ipaddress.ip_network("127.0.0.0/8"),        # Loopback
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("169.254.0.0/16"),     # Link-local / APIPA
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
    ipaddress.ip_network("100.64.0.0/10"),      # Carrier-grade NAT
    ipaddress.ip_network("0.0.0.0/8"),          # Current network
    ipaddress.ip_network("192.0.2.0/24"),       # TEST-NET-1
    ipaddress.ip_network("198.51.100.0/24"),    # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),     # TEST-NET-3
    ipaddress.ip_network("fc00::/7"),           # IPv6 unique local
]

# Blocked hostnames regardless of resolved IP
_BLOCKED_HOSTNAMES = frozenset({
    "localhost",
    "metadata.google.internal",            # GCP metadata
    "169.254.169.254",                     # AWS/Azure/GCP metadata (IP form)
    "fd00:ec2::254",                       # AWS IPv6 metadata
})


def _ssrf_check(url: str, allow_hosts: list[str] | None = None) -> str | None:
    """
    Return an error string if the URL fails the SSRF check, else None.
    Checks: scheme, hostname, and whether the host is a private/blocked IP.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return f"Invalid URL: {url!r}"

    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return (
            f"Security: URL scheme {scheme!r} is not allowed. "
            "Only http and https are permitted."
        )

    host = parsed.hostname or ""
    if not host:
        return f"Security: URL has no hostname: {url!r}"

    # Honour the SSRF_ALLOW_HOSTS override (advanced — empty by default)
    if allow_hosts and host.lower() in {h.lower() for h in allow_hosts}:
        return None  # explicitly allowed — skip remaining checks

    # Block known dangerous hostnames directly
    if host.lower() in _BLOCKED_HOSTNAMES:
        return f"Security: hostname {host!r} is blocked (SSRF protection)."

    # Try to parse host as an IP address — block private ranges directly
    try:
        ip = ipaddress.ip_address(host)
        for net in _BLOCKED_NETWORKS:
            if ip in net:
                return (
                    f"Security: IP address {host!r} is in blocked range "
                    f"{net} (SSRF protection)."
                )
    except ValueError:
        # Not an IP — it's a hostname. We cannot resolve it here without
        # making a blocking DNS call, so we rely on the blocked-hostname list
        # and the metadata IP list above for the most common attack vectors.
        # A post-connect check via httpx event hooks is the ideal hardening,
        # but that requires a custom transport. This covers the primary vectors.
        pass

    return None  # safe to proceed


TOOL_DEFINITION = ToolDefinition(
    name="web_fetch",
    description=(
        "Fetch a URL and return its text content, cleaned of navigation/ads.\n\n"
        "Field parity with OpenClaw web-fetch.ts:\n"
        "  url         — required\n"
        "  extractMode — 'auto' (default) | 'readability' | 'selector' | 'raw' | 'js'\n"
        "                'js' enables Playwright rendering (also: render_js=true)\n"
        "  maxChars    — max characters to return (also 'max_chars', default 50000)\n"
        "  selector    — CSS selector (used when extractMode='selector')\n"
        "  headers     — extra HTTP headers\n"
        "  timeout     — request timeout in seconds (default 20)\n"
        "  render_js   — alias for extractMode='js'"
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "extractMode": {
                "type": "string",
                "description": (
                    "Extraction mode (OpenClaw field name):\n"
                    "  auto        — readability first, raw fallback (default)\n"
                    "  readability — article text extraction\n"
                    "  selector    — CSS selector extraction (requires 'selector' param)\n"
                    "  raw         — raw HTML/text, no processing\n"
                    "  js          — render JavaScript first (requires BROWSER_ENABLED=true)"
                ),
                "default": "auto",
            },
            "render_js": {
                "type": "boolean",
                "description": "Alias for extractMode='js': render JavaScript before reading",
                "default": False,
            },
            "selector": {
                "type": "string",
                "description": "CSS selector to extract a specific element",
            },
            "headers": {
                "type": "object",
                "description": "Extra HTTP headers (e.g. Authorization, User-Agent)",
                "additionalProperties": {"type": "string"},
            },
            "timeout": {
                "type": "integer",
                "description": "Request timeout in seconds (default 20)",
                "default": 20,
            },
            "maxChars": {
                "type": "integer",
                "description": "Max characters to return — OpenClaw field name (also 'max_chars', default 50000)",
                "default": 50000,
            },
            "max_chars": {
                "type": "integer",
                "description": "Alias for maxChars",
                "default": 50000,
            },
        },
        "required": ["url"],
    },
    fn=lambda **kw: _web_fetch(**kw),
)


async def _web_fetch(
    url: str,
    extractMode: str = "auto",      # OpenClaw field name
    render_js: bool = False,         # alias for extractMode="js"
    selector: str | None = None,
    headers: dict | None = None,
    timeout: int = 20,
    maxChars: int = 50000,           # OpenClaw field name
    max_chars: int = 50000,          # alias
) -> str:
    # CVE-2026-26322: SSRF check — must run before any network request
    cfg = get_config()
    _allow = list(getattr(cfg, "ssrf_allow_hosts", []) or [])
    ssrf_err = _ssrf_check(url, allow_hosts=_allow)
    if ssrf_err:
        return f"Error: {ssrf_err}"

    # Resolve aliases
    effective_max_chars = maxChars if maxChars != 50000 else max_chars
    # extractMode="js" or render_js=true both mean JS rendering
    if render_js and extractMode == "auto":
        extractMode = "js"
    # selector mode: if selector provided and extractMode is still auto, use selector mode
    if selector and extractMode == "auto":
        extractMode = "selector"
    # Map extractMode to internal flags
    render_js = (extractMode == "js")
    cfg = get_config()

    if extractMode == "raw":
        return await _fetch_raw(url, headers=headers, timeout=timeout, max_chars=effective_max_chars)

    if render_js:
        if not cfg.browser_enabled:
            return "Error: extractMode='js' requires BROWSER_ENABLED=true in .env"
        return await _fetch_rendered(url, selector=selector, timeout=timeout, max_chars=effective_max_chars)

    return await _fetch_static(url, selector=selector, headers=headers, timeout=timeout, max_chars=effective_max_chars)


async def _fetch_static(
    url: str,
    selector: str | None = None,
    headers: dict | None = None,
    timeout: int = 20,
    max_chars: int = 50000,
) -> str:
    """Fetch via httpx + readability text extraction."""
    extra_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        **(headers or {}),
    }

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=extra_headers,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
    except httpx.HTTPStatusError as e:
        return f"HTTP error {e.response.status_code}: {url}"
    except httpx.TimeoutException:
        return f"Timeout fetching {url} (>{timeout}s)"
    except Exception as e:
        return f"Error fetching {url}: {e}"

    return _extract_text(html, url, selector=selector, max_chars=max_chars)


async def _fetch_rendered(
    url: str,
    selector: str | None = None,
    timeout: int = 20,
    max_chars: int = 50000,
) -> str:
    """Fetch via Playwright (JS rendering), then extract text."""
    from tools.browser_tool import _get_page, _start_browser, _browser_instance

    cfg = get_config()
    if not _browser_instance or not _browser_instance.is_connected():
        try:
            await _start_browser(cfg.browser_headless)
        except Exception as e:
            return f"Error starting browser for render_js: {e}"

    try:
        page = await _get_page()
        await page.goto(url, timeout=timeout * 1000, wait_until="networkidle")

        if selector:
            try:
                element = await page.query_selector(selector)
                if element:
                    text = await element.inner_text()
                    return text[:max_chars]
                return f"Selector '{selector}' not found on page"
            except Exception as e:
                return f"Selector error: {e}"

        # Get rendered HTML and extract
        html = await page.content()
        title = await page.title()
        return _extract_text(html, url, title=title, max_chars=max_chars)
    except Exception as e:
        return f"Render error: {e}"


def _extract_text(
    html: str,
    url: str,
    selector: str | None = None,
    title: str | None = None,
    max_chars: int = 50000,
) -> str:
    """Extract readable text from HTML, optionally restricted to a CSS selector."""
    if selector:
        try:
            from lxml import html as lxml_html
            doc = lxml_html.fromstring(html)
            elements = doc.cssselect(selector)
            if not elements:
                return f"Selector '{selector}' found no elements."
            texts = []
            for el in elements[:5]:
                text = el.text_content().strip()
                if text:
                    texts.append(text)
            combined = "\n\n".join(texts)
            return combined[:max_chars] if combined else f"Selector '{selector}' matched but had no text."
        except Exception:
            pass  # Fall through to readability

    # Readability-based extraction
    try:
        from readability import Document
        doc = Document(html)
        page_title = title or doc.title() or url
        content = doc.summary()

        # Strip HTML tags from summary
        try:
            from lxml import html as lxml_html
            clean = lxml_html.fromstring(content).text_content()
        except Exception:
            import re
            clean = re.sub(r"<[^>]+>", " ", content)

        clean = "\n".join(line.strip() for line in clean.splitlines() if line.strip())
        result = f"# {page_title}\n{url}\n\n{clean}"
        return result[:max_chars]
    except Exception:
        pass

    # Bare fallback: strip all tags
    try:
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        return text[:max_chars]
    except Exception as e:
        return f"Error extracting text: {e}"


async def _fetch_raw(
    url: str,
    headers: dict | None = None,
    timeout: int = 20,
    max_chars: int = 50000,
) -> str:
    """Fetch raw HTML/text without any processing (extractMode='raw')."""
    # CVE-2026-26322: SSRF check (raw path)
    _cfg = get_config()
    _allow = list(getattr(_cfg, "ssrf_allow_hosts", []) or [])
    ssrf_err = _ssrf_check(url, allow_hosts=_allow)
    if ssrf_err:
        return f"Error: {ssrf_err}"
    extra_headers = {"User-Agent": "Mozilla/5.0", **(headers or {})}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=extra_headers) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text[:max_chars]
    except Exception as e:
        return f"Error fetching {url}: {e}"
