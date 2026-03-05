"""
agent/hooks.py — Event-driven hooks system for LobsterClaw.

Mirrors OpenClaw's hooks/ event system.

HOW IT WORKS:
  On startup, HookRegistry scans workspace/hooks/*/handler.py.
  Each handler.py must define:  async def handle(event: str, data: dict) -> None
  Optionally: plugin.json with {"events": ["turn:end"]} to filter to specific events.

EVENTS:
  command:new     — new user message received (before agent processes it)
  turn:end        — agent finished a full response turn
  tool:result     — a tool call returned (any tool, any result)
  session:spawn   — a sub-agent session was spawned
  session:end     — a session completed (success or error)

USAGE EXAMPLE (workspace/hooks/my-hook/handler.py):
  async def handle(event: str, data: dict) -> None:
      if event == "turn:end":
          print(data["content"])

WIRING:
  In main.py after plugin load:
      from agent.hooks import init_hooks
      init_hooks(Path("workspace"))

  Then in agent loop / subagent, call:
      from agent import hooks
      await hooks.fire("turn:end", {"session_id": sid, "content": reply})
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

VALID_EVENTS = frozenset({
    "command:new",
    "turn:end",
    "tool:result",
    "session:spawn",
    "session:end",
})

# type alias
HandlerFn = Callable[[str, dict], Coroutine[Any, Any, None]]


class HookRegistry:
    """Discovers, loads, and fires workspace hooks. Errors never crash the agent."""

    def __init__(self) -> None:
        # event → list of (hook_name, handler_fn)
        self._handlers: dict[str, list[tuple[str, HandlerFn]]] = {e: [] for e in VALID_EVENTS}
        self._loaded: list[str] = []

    def load(self, workspace_dir: Path) -> None:
        """Scan workspace/hooks/ and register all valid handler.py files."""
        hooks_dir = workspace_dir / "hooks"
        if not hooks_dir.exists():
            logger.debug("hooks: no workspace/hooks/ directory — skipping")
            return

        for hook_dir in sorted(hooks_dir.iterdir()):
            if not hook_dir.is_dir():
                continue
            handler_file = hook_dir / "handler.py"
            if not handler_file.exists():
                logger.debug("hooks: %s has no handler.py — skipping", hook_dir.name)
                continue

            # Determine which events this hook subscribes to
            subscribed: set[str] = set(VALID_EVENTS)  # default = all events
            meta_path = hook_dir / "plugin.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    if "events" in meta:
                        subscribed = set(meta["events"]) & VALID_EVENTS
                        if not subscribed:
                            logger.warning(
                                "hooks: %s plugin.json 'events' has no valid event names — skipping",
                                hook_dir.name,
                            )
                            continue
                except Exception as exc:
                    logger.warning("hooks: %s bad plugin.json (%s) — using all events", hook_dir.name, exc)

            # Load the module
            try:
                spec = importlib.util.spec_from_file_location(
                    f"lobsterclaw_hooks.{hook_dir.name}", handler_file
                )
                mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
            except Exception as exc:
                logger.error("hooks: failed to load %s — %s", hook_dir.name, exc)
                continue

            if not hasattr(mod, "handle"):
                logger.warning("hooks: %s has no handle() function — skipping", hook_dir.name)
                continue

            fn: HandlerFn = mod.handle
            for event in subscribed:
                self._handlers[event].append((hook_dir.name, fn))

            self._loaded.append(hook_dir.name)
            logger.info("hooks: loaded %s (events: %s)", hook_dir.name, sorted(subscribed))

        if self._loaded:
            logger.info("hooks: %d hook(s) ready — %s", len(self._loaded), self._loaded)
        else:
            logger.debug("hooks: no hooks loaded")

    async def fire(self, event: str, data: dict | None = None) -> None:
        """
        Fire all handlers registered for *event*.
        Exceptions are caught and logged — a broken hook never crashes the agent.
        """
        if event not in VALID_EVENTS:
            logger.warning("hooks.fire: unknown event %r (ignored)", event)
            return

        handlers = self._handlers.get(event, [])
        if not handlers:
            return

        payload: dict = {"event": event, "ts": time.time(), **(data or {})}

        for name, fn in handlers:
            try:
                result = fn(event, payload)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                # Hooks must never take down the agent
                logger.error("hooks: %s raised on event %r — %s", name, event, exc)

    @property
    def loaded(self) -> list[str]:
        return list(self._loaded)


# ── Module-level singleton ────────────────────────────────────────────────────

_registry: HookRegistry | None = None


def init_hooks(workspace_dir: Path) -> HookRegistry:
    """
    Create and load the global HookRegistry.
    Call once from main.py after plugin load.
    """
    global _registry
    _registry = HookRegistry()
    _registry.load(workspace_dir)
    return _registry


async def fire(event: str, data: dict | None = None) -> None:
    """
    Convenience shortcut — fire an event on the global registry.
    Safe to call even before init_hooks() (no-ops if not initialised).
    """
    if _registry is not None:
        await _registry.fire(event, data)


def get_registry() -> HookRegistry | None:
    return _registry
