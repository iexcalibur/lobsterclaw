from __future__ import annotations

import httpx

from config import get_config
from tools.registry import ToolDefinition

TOOL_DEFINITION = ToolDefinition(
    name="web_search",
    description="Search the web and return a list of results with titles, URLs, and descriptions.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
            "count": {"type": "integer", "description": "Number of results (1-10, default 5)", "default": 5},
        },
        "required": ["query"],
    },
    fn=lambda **kw: _web_search(**kw),
)


async def _web_search(query: str, count: int = 5) -> str:
    cfg = get_config()
    count = min(max(1, count), 10)

    if cfg.web_search_provider == "brave":
        return await _brave(query, count, cfg.brave_api_key)
    if cfg.web_search_provider == "perplexity":
        return await _perplexity(query, cfg.perplexity_api_key)
    if cfg.web_search_provider == "gemini":
        return await _gemini(query, cfg.gemini_api_key)

    return f"Error: unknown search provider '{cfg.web_search_provider}'"


async def _brave(query: str, count: int, api_key: str) -> str:
    if not api_key:
        return "Error: BRAVE_API_KEY not set in .env"

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": count},
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        )
        r.raise_for_status()
        data = r.json()

    results = data.get("web", {}).get("results", [])
    if not results:
        return "No results found."

    lines = []
    for item in results:
        lines.append(f"**{item.get('title', 'No title')}**")
        lines.append(item.get("url", ""))
        lines.append(item.get("description", "")[:300])
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

    return data["choices"][0]["message"]["content"]


async def _gemini(query: str, api_key: str) -> str:
    if not api_key:
        return "Error: GEMINI_API_KEY not set in .env"

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
            json={"contents": [{"parts": [{"text": f"Search the web and answer: {query}"}]}]},
        )
        r.raise_for_status()
        data = r.json()

    return data["candidates"][0]["content"]["parts"][0]["text"]
