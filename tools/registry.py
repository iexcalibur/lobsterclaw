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
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from config import get_config

logger = logging.getLogger(__name__)

# Patterns that might leak sensitive info in error strings — redact before LLM sees them
_REDACT_PATTERNS = [
    # API keys / bearer tokens (long hex/base64 strings)
    re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9_\-\.]{20,}", re.ASCII),
    re.compile(r"(?i)(sk-|pk-|ant-|xoxb-|xoxp-)[A-Za-z0-9_\-]{10,}"),
    # Telegram bot token
    re.compile(r"\d{8,12}:[A-Za-z0-9_\-]{30,}"),
    # URLs containing credentials (user:pass@host)
    re.compile(r"[A-Za-z0-9_\-%.]+:[A-Za-z0-9_\-%.@]+@[a-zA-Z0-9.\-]+"),
    # Absolute paths leaking username (macOS /Users/<name>, Linux /home/<name>)
    re.compile(r"/(?:Users|home)/[^/\s]{1,32}/"),
]
_REDACT_REPLACE = "[REDACTED]"


def _redact_error_string(s: str) -> str:
    """Strip common secret/path patterns from error strings before returning to LLM."""
    for pat in _REDACT_PATTERNS:
        s = pat.sub(lambda m: m.group(0)[:3] + _REDACT_REPLACE, s)
    return s

ToolFn = Callable[..., Awaitable[Any]]

# Hook function types — fired before / after every tool execution.
# pre_hook(name, args)               — called before execution; may raise to abort
# post_hook(name, args, result, ms)  — called after execution with result and duration
PreHookFn = Callable[[str, dict], Awaitable[None]]
PostHookFn = Callable[[str, dict, str, float], Awaitable[None]]

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
        # Pre/post execution hooks (fired for every tool call if hooks_enabled)
        self._pre_hooks: list[PreHookFn] = []
        self._post_hooks: list[PostHookFn] = []

    def set_approval_gate(self, gate: Any) -> None:
        self._approval_gate = gate

    def add_pre_hook(self, fn: PreHookFn) -> None:
        """Register a hook called before every tool execution."""
        self._pre_hooks.append(fn)

    def add_post_hook(self, fn: PostHookFn) -> None:
        """Register a hook called after every tool execution (name, args, result, duration_ms)."""
        self._post_hooks.append(fn)

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
    # Overflow truncation
    # ------------------------------------------------------------------

    def truncate_for_overflow(self, result: str, aggressive: bool = False) -> str:
        """Truncate a tool result more aggressively when context is overflowing."""
        limit = self._result_max_chars
        if aggressive:
            limit = min(limit, 10_000)
        return truncate_tool_result(result, limit)

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

        # Build tool context from args and explicit context
        tool_context = dict(context or {})
        for ctx_key in ("_session_id", "_session_depth", "_caller_depth", "_channel", "_account_id", "_thread_id"):
            if ctx_key in args:
                tool_context[ctx_key.lstrip("_")] = args[ctx_key]

        hooks_enabled = getattr(self.cfg, "hooks_enabled", True)

        # Pre-execution hooks
        if hooks_enabled:
            for hook in self._pre_hooks:
                try:
                    await hook(name, args)
                except Exception as hook_exc:
                    logger.warning("pre_hook error for tool %s: %s", name, hook_exc)

        t_start = time.monotonic()
        result_str: str
        try:
            # Pass context keys (_session_id etc.) only to tools that accept them
            import inspect
            sig = inspect.signature(tool.fn)
            # Filter out internal context keys not in the tool signature
            _internal_keys = {"_session_id", "_session_depth", "_caller_depth", "_channel", "_account_id", "_thread_id"}
            clean_args = {
                k: v for k, v in args.items()
                if k not in _internal_keys or k in sig.parameters
            }
            result = await tool.fn(**clean_args)
            raw = str(result) if not isinstance(result, str) else result
            result_str = truncate_tool_result(raw, self._result_max_chars)
        except TypeError as e:
            logger.warning("Tool %s argument error: %s", name, e)
            result_str = f"Error calling {name}: {_redact_error_string(str(e))}"
        except Exception as e:
            logger.exception("Tool %s failed", name)
            result_str = f"Error: {_redact_error_string(str(e))}"

        elapsed_ms = (time.monotonic() - t_start) * 1000.0

        # Post-execution hooks
        if hooks_enabled:
            for hook in self._post_hooks:
                try:
                    await hook(name, args, result_str, elapsed_ms)
                except Exception as hook_exc:
                    logger.warning("post_hook error for tool %s: %s", name, hook_exc)

        return result_str
