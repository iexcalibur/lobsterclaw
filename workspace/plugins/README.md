# Plugins

Each plugin is a subdirectory containing a Python module with tool definitions.

## Creating a Plugin

1. Create a directory: `workspace/plugins/my-plugin/`
2. Add an `__init__.py` (or `plugin.py`) that exports `TOOLS`:

```python
# workspace/plugins/my-plugin/__init__.py
from tools.registry import ToolDefinition

async def _my_tool(query: str) -> str:
    return f"Result for: {query}"

TOOLS = [
    ToolDefinition(
        name="my_tool",
        description="What this tool does.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Input"},
            },
            "required": ["query"],
        },
        fn=lambda **kw: _my_tool(**kw),
    )
]
```

3. Optionally add `plugin.json` with metadata:

```json
{
  "name": "my-plugin",
  "description": "A custom plugin",
  "version": "1.0.0"
}
```

## Global Plugins

You can also install plugins globally in `~/.lobsterclaw/plugins/` — they will be
loaded for all LobsterClaw instances on this machine.

## Policy

Plugin tools respect the same `TOOLS_ALLOW`/`TOOLS_DENY` rules in `.env`.
To allow a plugin tool: add its name to `TOOLS_ALLOW`.
To block a plugin tool: add its name to `TOOLS_DENY`.
