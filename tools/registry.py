from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from config import get_config

logger = logging.getLogger(__name__)

ToolFn = Callable[..., Awaitable[Any]]


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict  # JSON Schema object
    fn: ToolFn


class ToolRegistry:
    """Registers tools, enforces allow/deny policy, and runs the approval gate."""

    def __init__(self) -> None:
        self.cfg = get_config()
        self._tools: dict[str, ToolDefinition] = {}
        self._approval_gate: Any = None  # set via set_approval_gate()

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

    async def execute(self, name: str, args: dict) -> Any:
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
            result = await tool.fn(**args)
            return result
        except TypeError as e:
            # Argument mismatch — return helpful error
            logger.warning("Tool %s argument error: %s", name, e)
            return f"Error calling {name}: {e}"
        except Exception as e:
            logger.exception("Tool %s failed", name)
            return f"Error: {e}"
