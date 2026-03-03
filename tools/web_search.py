"""
Web search tool — supports all OpenClaw search providers:
  brave, perplexity, gemini, grok, kimi

Provider selection: WEB_SEARCH_PROVIDER in .env
Fallback chain: if primary fails, tries next available provider.
"""

from __future__ import annotations

import logging

import httpx

from config import get_config
from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

TOOL_DEFINITION = ToolDefinition(
    name="web_search",
    description=(
        "Search the web and return results with titles, URLs, and descriptions.\n"
        "Providers: brave (ranked list), perplexity/gemini/grok/kimi (AI answer).\n\n"
        "Field parity with OpenClaw web-search.ts:\n"
        "  query       — search terms\n"
        "  count       — number of results (1-10, default 5)\n"
        "  freshness   — time filter string: pd|pw|pm|py (also accepted as 'fresh'=bool)\n"
        "  country     — ISO 2-letter country code (e.g. 'us', 'gb', 'de')\n"
        "  search_lang — search result language code (also 'language')\n"
        "  ui_lang     — interface language code\n"
        "  safe_search — 'strict' | 'moderate' | 'off'\n"
        "  time_range  — alias for freshness"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "count": {
                "type": "integer",
                "description": "Number of results (1-10, default 5)",
                "default": 5,
            },
            # Freshness: OpenClaw uses string 'freshness'; PyGate also accepts bool 'fresh'
            "freshness": {
                "type": "string",
                "description": "Time filter: pd (past day) | pw (past week) | pm (past month) | py (past year)",
            },
            "fresh": {
                "type": "boolean",
                "description": "Shorthand for freshness='pd' (recent results). Alias.",
                "default": False,
            },
            "time_range": {
                "type": "string",
                "description": "Alias for freshness: pd | pw | pm | py",
            },
            "country": {
                "type": "string",
                "description": "Country code (e.g. 'us', 'gb', 'de')",
            },
            "search_lang": {
                "type": "string",
                "description": "Search result language code (e.g. 'en', 'es', 'zh'). Also 'language'.",
            },
            "language": {
                "type": "string",
                "description": "Alias for search_lang",
            },
            "ui_lang": {
                "type": "string",
                "description": "UI/interface language code (e.g. 'en-US')",
            },
            "safe_search": {
                "type": "string",
                "description": "Safe search: strict | moderate | off",
                "default": "moderate",
            },
        },
        "required": ["query"],
    },
    fn=lambda **kw: _web_search(**kw),
)


async def _web_search(
    query: str,
    count: int = 5,
    fresh: bool = False,
    freshness: str | None = None,         # OpenClaw field name
    time_range: str | None = None,        # alias for freshness
    country: str | None = None,
    search_lang: str | None = None,       # OpenClaw field name
    language: str | None = None,          # alias for search_lang
    ui_lang: str | None = None,
    safe_search: str = "moderate",
) -> str:
    cfg = get_config()
    count = min(max(1, count), cfg.web_search_max_results)
    provider = cfg.web_search_provider.lower()

    # Resolve freshness: string > time_range alias > fresh bool
    resolved_freshness = freshness or time_range
    if not resolved_freshness and fresh:
        resolved_freshness = "pd"

    # Resolve language aliases
    resolved_lang = search_lang or language

    # Build a filter hint for AI providers that don't have direct filter params
    filter_hints: list[str] = []
    if resolved_freshness:
        label = {"pd": "past day", "pw": "past week", "pm": "past month", "py": "past year"}.get(resolved_freshness, resolved_freshness)
        filter_hints.append(f"Results from: {label}")
    if country:
        filter_hints.append(f"Country: {country.upper()}")
    if resolved_lang:
        filter_hints.append(f"Language: {resolved_lang}")
    if ui_lang:
        filter_hints.append(f"UI lang: {ui_lang}")
    augmented_query = query
    if filter_hints:
        augmented_query = query + " [" + "; ".join(filter_hints) + "]"

    providers = {
        "brave": lambda: _brave(query, count, cfg.brave_api_key, freshness=resolved_freshness, country=country, search_lang=resolved_lang, ui_lang=ui_lang, safe_search=safe_search),
        "perplexity": lambda: _perplexity(augmented_query, cfg.perplexity_api_key),
        "gemini": lambda: _gemini(augmented_query, cfg.gemini_api_key),
        "grok": lambda: _grok(augmented_query, getattr(cfg, "grok_api_key", "")),
        "kimi": lambda: _kimi(augmented_query, getattr(cfg, "kimi_api_key", "")),
    }

    if provider not in providers:
        return f"Error: unknown search provider '{provider}'. Use: brave, perplexity, gemini, grok, kimi"

    # Try primary provider, then fall back to any available one
    tried: list[str] = []
    for p in [provider] + [k for k in providers if k != provider]:
        try:
            result = await providers[p]()
            if not result.startswith("Error:"):
                if p != provider:
                    result = f"[Note: used {p} as fallback]\n\n{result}"
                return result
            tried.append(f"{p}: {result}")
        except Exception as e:
            tried.append(f"{p}: {e}")

    return "All search providers failed:\n" + "\n".join(tried)


# ------------------------------------------------------------------
# Provider implementations
# ------------------------------------------------------------------

async def _brave(
    query: str,
    count: int,
    api_key: str,
    freshness: str | None = None,
    country: str | None = None,
    search_lang: str | None = None,
    ui_lang: str | None = None,
    safe_search: str = "moderate",
) -> str:
    if not api_key:
        return "Error: BRAVE_API_KEY not set in .env"

    params: dict = {"q": query, "count": count}

    # Freshness (OpenClaw field name: freshness)
    if freshness and freshness in ("pd", "pw", "pm", "py"):
        params["freshness"] = freshness

    # Country / language (OpenClaw field names: country, search_lang, ui_lang)
    if country:
        params["country"] = country.lower()
    if search_lang:
        params["search_lang"] = search_lang.lower()
    if ui_lang:
        params["ui_lang"] = ui_lang.lower()
    elif search_lang:
        params["ui_lang"] = search_lang.lower()

    # Safe search
    safe_map = {"strict": "strict", "moderate": "moderate", "off": "off"}
    params["safesearch"] = safe_map.get(safe_search, "moderate")

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params=params,
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        )
        r.raise_for_status()
        data = r.json()

    results = data.get("web", {}).get("results", [])
    if not results:
        return "No results found."

    lines = []
    for item in results[:count]:
        title = item.get("title", "No title")
        url = item.get("url", "")
        desc = item.get("description", "")[:300]
        age = item.get("age", "")
        lines.append(f"**{title}**")
        lines.append(url)
        if desc:
            lines.append(desc)
        if age:
            lines.append(f"_{age}_")
        lines.append("")
    return "\n".join(lines).strip()


async def _perplexity(query: str, api_key: str) -> str:
    if not api_key:
        return "Error: PERPLEXITY_API_KEY not set in .env"

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "sonar-pro",
                "messages": [{"role": "user", "content": query}],
            },
        )
        r.raise_for_status()
        data = r.json()

    content = data["choices"][0]["message"]["content"]
    # Include citations if available
    citations = data.get("citations", [])
    if citations:
        cit_str = "\n".join(f"[{i+1}] {url}" for i, url in enumerate(citations[:5]))
        content += f"\n\nSources:\n{cit_str}"
    return content


async def _gemini(query: str, api_key: str) -> str:
    if not api_key:
        return "Error: GEMINI_API_KEY not set in .env"

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
            json={
                "contents": [{"parts": [{"text": f"Search and answer: {query}"}]}],
                "tools": [{"google_search": {}}],
            },
        )
        r.raise_for_status()
        data = r.json()

    candidate = data.get("candidates", [{}])[0]
    parts = candidate.get("content", {}).get("parts", [])
    text = " ".join(p.get("text", "") for p in parts if "text" in p)
    return text or "No response from Gemini."


async def _grok(query: str, api_key: str) -> str:
    if not api_key:
        return "Error: GROK_API_KEY not set in .env"

    # Grok uses OpenAI-compatible API via x.ai
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.x.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "grok-3",
                "messages": [{"role": "user", "content": query}],
                "search_parameters": {"mode": "auto"},
            },
        )
        r.raise_for_status()
        data = r.json()

    return data["choices"][0]["message"]["content"]


async def _kimi(query: str, api_key: str) -> str:
    if not api_key:
        return "Error: KIMI_API_KEY not set in .env"

    # Kimi (Moonshot AI) uses OpenAI-compatible API
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.moonshot.cn/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "moonshot-v1-auto",
                "messages": [
                    {"role": "system", "content": "You are a helpful search assistant. Search the web and provide relevant information."},
                    {"role": "user", "content": query},
                ],
            },
        )
        r.raise_for_status()
        data = r.json()

    return data["choices"][0]["message"]["content"]
