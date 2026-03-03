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
        "Filters (Brave-supported; AI providers will include in their prompt):\n"
        "  count       — number of results (1-10, default 5)\n"
        "  fresh       — recent results only\n"
        "  time_range  — pd (past day) | pw (past week) | pm (past month) | py (past year)\n"
        "  country     — ISO 2-letter country code (e.g. 'us', 'gb', 'de')\n"
        "  language    — language code (e.g. 'en', 'es', 'zh')\n"
        "  safe_search — 'strict' | 'moderate' | 'off' (default 'moderate')"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "count": {"type": "integer", "description": "Number of results (1-10, default 5)", "default": 5},
            "fresh": {"type": "boolean", "description": "Request recent results", "default": False},
            "time_range": {
                "type": "string",
                "description": "Time filter: pd (past day) | pw (past week) | pm (past month) | py (past year)",
            },
            "country": {"type": "string", "description": "Country code (e.g. 'us', 'gb', 'de')"},
            "language": {"type": "string", "description": "Language code (e.g. 'en', 'es', 'zh')"},
            "safe_search": {
                "type": "string",
                "description": "Safe search level: strict | moderate | off",
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
    time_range: str | None = None,
    country: str | None = None,
    language: str | None = None,
    safe_search: str = "moderate",
) -> str:
    cfg = get_config()
    count = min(max(1, count), cfg.web_search_max_results)
    provider = cfg.web_search_provider.lower()

    # Build a filter hint for AI providers that don't have direct filter params
    filter_hints: list[str] = []
    if time_range:
        label = {"pd": "past day", "pw": "past week", "pm": "past month", "py": "past year"}.get(time_range, time_range)
        filter_hints.append(f"Results from: {label}")
    if country:
        filter_hints.append(f"Country: {country.upper()}")
    if language:
        filter_hints.append(f"Language: {language}")
    augmented_query = query
    if filter_hints:
        augmented_query = query + " [" + "; ".join(filter_hints) + "]"

    providers = {
        "brave": lambda: _brave(query, count, cfg.brave_api_key, fresh=fresh, time_range=time_range, country=country, language=language, safe_search=safe_search),
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
    fresh: bool = False,
    time_range: str | None = None,
    country: str | None = None,
    language: str | None = None,
    safe_search: str = "moderate",
) -> str:
    if not api_key:
        return "Error: BRAVE_API_KEY not set in .env"

    params: dict = {"q": query, "count": count}

    # Freshness / time range (time_range takes precedence over fresh flag)
    if time_range and time_range in ("pd", "pw", "pm", "py"):
        params["freshness"] = time_range
    elif fresh:
        params["freshness"] = "pd"

    # Country / language
    if country:
        params["country"] = country.lower()
    if language:
        params["search_lang"] = language.lower()
        params["ui_lang"] = language.lower()

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
