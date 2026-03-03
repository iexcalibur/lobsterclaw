from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from config import get_config

logger = logging.getLogger(__name__)

ToolFn = Callable[..., Awaitable[Any]]

# Hard cap on tool result size sent back to LLM.
# Prevents single huge outputs (e.g. large file reads, web pages) from blowing the context.
DEFAULT_TOOL_RESULT_MAX_CHARS = 50_000
TOOL_RESULT_TRUNCATION_SUFFIX = "\n\n[... output truncated to {limit} chars. Use targeted queries or file offsets to retrieve specific sections ...]"


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict  # JSON Schema object
    fn: ToolFn


def truncate_tool_result(result: str, max_chars: int) -> str:
    """Cap tool result length, appending a truncation notice if cut."""
    if len(result) <= max_chars:
        return result
    suffix = TOOL_RESULT_TRUNCATION_SUFFIX.format(limit=max_chars)
    return result[:max_chars] + suffix


class ToolRegistry:
    """Registers tools, enforces allow/deny policy, runs approval gate, and truncates results."""

    def __init__(self) -> None:
        self.cfg = get_config()
        self._tools: dict[str, ToolDefinition] = {}
        self._approval_gate: Any = None  # set via set_approval_gate()
        self._result_max_chars: int = getattr(
            self.cfg, "tool_result_max_chars", DEFAULT_TOOL_RESULT_MAX_CHARS
        )

    def set_approval_gate(self, gate: Any) -> None:
        self._approval_gate = gate

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool
        logger.debug("Registered tool: %s", tool.name)

    def _is_allowed(self, name: str) -> bool:
        if name in self.cfg.tools_deny:
            return False
        if self.cfg.tools_allow and name not in self.cfg.tools_allow:
            return False
        return True

    def get_available(self) -> list[ToolDefinition]:
        return [t for name, t in self._tools.items() if self._is_allowed(name)]

    def get_names(self) -> list[str]:
        return [t.name for t in self.get_available()]

    def get_anthropic_tools(self) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in self.get_available()
        ]

    def get_openai_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self.get_available()
        ]

    async def execute(self, name: str, args: dict) -> str:
        if not self._is_allowed(name):
            return f"Error: tool '{name}' is blocked by policy"

        tool = self._tools.get(name)
        if not tool:
            return f"Error: tool '{name}' not found"

        # Confirmation gate — pause and ask user before executing
        if name in self.cfg.tools_require_confirmation and self._approval_gate:
            approved = await self._approval_gate.request(name, args)
            if not approved:
                return f"User denied execution of '{name}'"

        try:
            # Pass context keys (_session_id etc.) only to tools that accept them;
            # strip them silently for tools that don't declare them in their signature.
            import inspect
            sig = inspect.signature(tool.fn)
            if "_session_id" in args and "_session_id" not in sig.parameters:
                args = {k: v for k, v in args.items() if k != "_session_id"}
            result = await tool.fn(**args)
            raw = str(result) if not isinstance(result, str) else result
            # Tool result truncation guard
            return truncate_tool_result(raw, self._result_max_chars)
        except TypeError as e:
            logger.warning("Tool %s argument error: %s", name, e)
            return f"Error calling {name}: {e}"
        except Exception as e:
            logger.exception("Tool %s failed", name)
            return f"Error: {e}"
