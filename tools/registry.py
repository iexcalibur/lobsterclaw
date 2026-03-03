"""
Tool registry — mirrors OpenClaw's pi-tools.ts policy model.

Policy layers (checked in order):
  1. tools_deny          — always blocked, regardless of allow list
  2. tools_allow         — whitelist; if set, only listed names run
  3. owner_only          — tool marked as owner-only: blocked in sub-agents (depth > 0)
  4. depth_denied        — blocked at sub-agent depths matching the rule
  5. tools_require_confirmation — pause and ask user before executing

ToolDefinition flags:
  owner_only: bool        — True = only runs in the main session (depth 0)
  depth_limit: int | None — max depth at which this tool may run (None = unlimited)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from config import get_config

logger = logging.getLogger(__name__)

ToolFn = Callable[..., Awaitable[Any]]

# Hard cap on tool result size sent back to LLM.
DEFAULT_TOOL_RESULT_MAX_CHARS = 50_000
TOOL_RESULT_TRUNCATION_SUFFIX = (
    "\n\n[... output truncated to {limit} chars. "
    "Use targeted queries or file offsets to retrieve specific sections ...]"
)


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict  # JSON Schema object
    fn: ToolFn
    # Policy flags (matching OpenClaw's tool security model)
    owner_only: bool = False      # Only allowed in the main session (depth 0); blocked in sub-agents
    depth_limit: int | None = None  # Max session depth this tool may run at (None = no limit)


def truncate_tool_result(result: str, max_chars: int) -> str:
    """Cap tool result length, appending a truncation notice if cut."""
    if len(result) <= max_chars:
        return result
    suffix = TOOL_RESULT_TRUNCATION_SUFFIX.format(limit=max_chars)
    return result[:max_chars] + suffix


class ToolRegistry:
    """
    Registers tools, enforces layered policy, runs approval gate, and truncates results.

    Policy model (OpenClaw parity):
      deny > allow > owner_only > depth_limit > confirmation
    """

    def __init__(self) -> None:
        self.cfg = get_config()
        self._tools: dict[str, ToolDefinition] = {}
        self._approval_gate: Any = None
        self._result_max_chars: int = getattr(
            self.cfg, "tool_result_max_chars", DEFAULT_TOOL_RESULT_MAX_CHARS
        )
        # Current execution depth (0 = main session, >0 = sub-agent)
        self._session_depth: int = 0

    def set_approval_gate(self, gate: Any) -> None:
        self._approval_gate = gate

    def set_session_depth(self, depth: int) -> None:
        """Set the current session depth — used to enforce owner_only and depth_limit."""
        self._session_depth = depth

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool
        logger.debug("Registered tool: %s (owner_only=%s, depth_limit=%s)", tool.name, tool.owner_only, tool.depth_limit)

    # ------------------------------------------------------------------
    # Policy checks
    # ------------------------------------------------------------------

    def _is_denied(self, name: str) -> str | None:
        """
        Return a human-readable reason if the tool is blocked, or None if it's allowed.
        Checks all policy layers in order.
        """
        # 1. Explicit deny list
        if name in self.cfg.tools_deny:
            return f"tool '{name}' is in TOOLS_DENY"

        # 2. Allow list (if set, name must be present)
        if self.cfg.tools_allow and name not in self.cfg.tools_allow:
            return f"tool '{name}' is not in TOOLS_ALLOW"

        tool = self._tools.get(name)
        if not tool:
            return None  # Not found is handled separately

        # 3. owner_only: only runs at depth 0 (main session)
        if tool.owner_only and self._session_depth > 0:
            return (
                f"tool '{name}' is owner-only and cannot run inside a sub-agent "
                f"(current depth: {self._session_depth})"
            )

        # 4. depth_limit: blocked if current depth exceeds limit
        if tool.depth_limit is not None and self._session_depth > tool.depth_limit:
            return (
                f"tool '{name}' is not allowed at depth {self._session_depth} "
                f"(max depth: {tool.depth_limit})"
            )

        return None

    def _is_allowed(self, name: str) -> bool:
        return self._is_denied(name) is None

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

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

    def policy_summary(self) -> str:
        """Return a human-readable policy summary for debugging."""
        lines = [
            f"Session depth: {self._session_depth}",
            f"TOOLS_ALLOW: {self.cfg.tools_allow or '(all)'}",
            f"TOOLS_DENY: {self.cfg.tools_deny}",
            f"TOOLS_REQUIRE_CONFIRMATION: {self.cfg.tools_require_confirmation}",
            "",
            "Tools and their policy status:",
        ]
        for name, t in sorted(self._tools.items()):
            denial = self._is_denied(name)
            flags = []
            if t.owner_only:
                flags.append("owner_only")
            if t.depth_limit is not None:
                flags.append(f"depth≤{t.depth_limit}")
            if name in (self.cfg.tools_require_confirmation or []):
                flags.append("confirmation_required")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            status = f"✅ allowed{flag_str}" if not denial else f"❌ blocked: {denial}"
            lines.append(f"  {name}: {status}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(self, name: str, args: dict, context: dict | None = None) -> str:
        """
        Execute a tool by name with given args.

        context (optional dict) may contain:
          session_depth: int  — current agent session depth for policy checks
        """
        # Update session depth from context if provided
        if context and "session_depth" in context:
            self._session_depth = context["session_depth"]

        denial = self._is_denied(name)
        if denial:
            return f"Error: {denial} (policy blocked)"

        tool = self._tools.get(name)
        if not tool:
            return f"Error: tool '{name}' not found"

        # Confirmation gate — pause and ask user before executing
        if (
            self.cfg.tools_require_confirmation
            and name in self.cfg.tools_require_confirmation
            and self._approval_gate
        ):
            approved = await self._approval_gate.request(name, args)
            if not approved:
                return f"User denied execution of '{name}'"

        try:
            # Pass context keys (_session_id etc.) only to tools that accept them
            import inspect
            sig = inspect.signature(tool.fn)
            # Filter out internal context keys not in the tool signature
            _internal_keys = {"_session_id", "_session_depth", "_caller_depth"}
            clean_args = {
                k: v for k, v in args.items()
                if k not in _internal_keys or k in sig.parameters
            }
            result = await tool.fn(**clean_args)
            raw = str(result) if not isinstance(result, str) else result
            return truncate_tool_result(raw, self._result_max_chars)
        except TypeError as e:
            logger.warning("Tool %s argument error: %s", name, e)
            return f"Error calling {name}: {e}"
        except Exception as e:
            logger.exception("Tool %s failed", name)
            return f"Error: {e}"
