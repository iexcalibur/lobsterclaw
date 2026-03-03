"""
Tool loop detection — mirrors OpenClaw's tool-loop-detection.ts

Detects when the agent is stuck calling the same tool with the same arguments
repeatedly and breaks the loop before it burns all token budget.

Three thresholds (matching OpenClaw's constants):
  WARNING_THRESHOLD  = 10  → log a warning, add a nudge message to history
  CRITICAL_THRESHOLD = 20  → return a hard stop result from the tool
  GLOBAL_CIRCUIT_BREAKER = 30 → abort the entire agent turn
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

TOOL_CALL_HISTORY_SIZE = 30
WARNING_THRESHOLD = 10
CRITICAL_THRESHOLD = 20
GLOBAL_CIRCUIT_BREAKER_THRESHOLD = 30


def _hash_tool_call(tool_name: str, args: dict) -> str:
    """Stable hash of (tool_name, args) for loop detection."""
    try:
        canonical = json.dumps(args, sort_keys=True, ensure_ascii=False)
    except Exception:
        canonical = str(args)
    raw = f"{tool_name}:{canonical}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class LoopDetectionState:
    """Per-agent-turn state for loop detection. Create fresh each turn."""
    _history: deque[str] = field(default_factory=lambda: deque(maxlen=TOOL_CALL_HISTORY_SIZE))
    _counts: dict[str, int] = field(default_factory=dict)
    total_calls: int = 0

    def record(self, tool_name: str, args: dict) -> "LoopCheckResult":
        """
        Record a tool call and return the loop check result.
        Call this BEFORE executing the tool.
        """
        h = _hash_tool_call(tool_name, args)
        self._history.append(h)
        self._counts[h] = self._counts.get(h, 0) + 1
        self.total_calls += 1

        repeat_count = self._counts[h]

        if self.total_calls >= GLOBAL_CIRCUIT_BREAKER_THRESHOLD:
            logger.error(
                "Global circuit breaker triggered after %d tool calls", self.total_calls
            )
            return LoopCheckResult(
                action="abort",
                message=(
                    f"Agent turn aborted: {self.total_calls} tool calls reached the "
                    f"global circuit breaker limit ({GLOBAL_CIRCUIT_BREAKER_THRESHOLD}). "
                    f"Stop and summarize what was accomplished so far."
                ),
            )

        if repeat_count >= CRITICAL_THRESHOLD:
            logger.error(
                "Critical loop: tool '%s' called %d times with identical args", tool_name, repeat_count
            )
            return LoopCheckResult(
                action="block",
                message=(
                    f"Tool '{tool_name}' has been called {repeat_count} times with identical "
                    f"arguments. This is a loop. Stop calling this tool and try a different "
                    f"approach, or report that you cannot complete the task."
                ),
            )

        if repeat_count >= WARNING_THRESHOLD:
            logger.warning(
                "Loop warning: tool '%s' called %d times with identical args", tool_name, repeat_count
            )
            return LoopCheckResult(
                action="warn",
                message=(
                    f"Warning: '{tool_name}' has been called {repeat_count} times with the "
                    f"same arguments. Consider trying a different approach."
                ),
            )

        return LoopCheckResult(action="ok", message="")


@dataclass
class LoopCheckResult:
    action: str   # "ok" | "warn" | "block" | "abort"
    message: str
