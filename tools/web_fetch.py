from __future__ import annotations

import httpx
from readability import Document

from tools.registry import ToolDefinition

TOOL_DEFINITION = ToolDefinition(
    name="web_fetch",
    description="Fetch a URL and return its readable text content. Use for reading articles, docs, or any webpage.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to fetch"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)", "default": 30},
        },
        "required": ["url"],
    },
    fn=lambda **kw: _web_fetch(**kw),
)


async def _web_fetch(url: str, timeout: int = 30) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; PyGate/1.0)"}
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")

        if "text/html" in content_type:
            doc = Document(response.text)
            title = doc.title()
            # Strip HTML tags from summary
            import re
            summary = re.sub(r"<[^>]+>", "", doc.summary())
            summary = re.sub(r"\n{3,}", "\n\n", summary).strip()
            return f"# {title}\n\n{summary[:8000]}"
        elif "application/json" in content_type:
            return response.text[:8000]
        else:
            return response.text[:8000]
