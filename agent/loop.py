"""
Core agent loop — sends messages to the LLM, handles tool calls, loops until done.

Integrates:
  - Tool loop detection (agent/loop_detection.py)
  - Context window compaction (agent/compaction.py)
  - Tool result truncation (tools/registry.py)
  - API retry on transient errors (rate-limit, overload, 5xx)
  - Extended thinking budget (Anthropic)
  - Per-session model override
  - Error sanitization (no raw tracebacks to user)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import anthropic
import openai

from agent.compaction import compact_messages, needs_compaction
from agent.loop_detection import LoopDetectionState
from config import get_config

if TYPE_CHECKING:
    from tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Errors that are transient and safe to retry
_ANTHROPIC_RETRY_TYPES = (
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.APIConnectionError,
)
_OPENAI_RETRY_TYPES = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.InternalServerError,
)


async def _retry_api(coro_factory, max_retries: int, label: str):
    """Retry an API call up to max_retries times with exponential back-off."""
    delay = 2.0
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except (*_ANTHROPIC_RETRY_TYPES, *_OPENAI_RETRY_TYPES) as e:  # type: ignore[misc]
            last_exc = e
            if attempt < max_retries:
                wait = delay * (2 ** attempt)
                logger.warning("%s transient error (attempt %d/%d): %s — retrying in %.1fs",
                               label, attempt + 1, max_retries + 1, e, wait)
                await asyncio.sleep(wait)
            else:
                logger.error("%s failed after %d retries: %s", label, max_retries, e)
        except Exception as e:
            raise e  # non-transient — don't retry
    raise last_exc  # type: ignore[misc]


def _sanitize_error(e: Exception) -> str:
    """Convert exception to a safe user-facing message (no raw tracebacks)."""
    name = type(e).__name__
    msg = str(e)
    # Truncate very long error messages (e.g. full HTTP bodies)
    if len(msg) > 400:
        msg = msg[:400] + "…"
    return f"[{name}] {msg}"


class AgentLoop:
    """Core agent loop — sends messages to the LLM, handles tool calls, loops until done."""

    def __init__(self, registry: "ToolRegistry") -> None:
        self.cfg = get_config()
        self.registry = registry

        if self.cfg.llm_provider == "anthropic":
            self._anthropic = anthropic.AsyncAnthropic(api_key=self.cfg.anthropic_api_key)
            self._openai = None
        else:
            self._anthropic = None
            self._openai = openai.AsyncOpenAI(api_key=self.cfg.openai_api_key)

    async def run(
        self,
        messages: list[dict],
        system_prompt: str,
        session_id: str | None = None,
        model_override: str | None = None,
        thinking_budget: int | None = None,
    ) -> str:
        """
        Run the agent loop on the given message history and return the final text reply.

        Args:
            messages: conversation history
            system_prompt: system prompt text
            session_id: used for logging/tracing
            model_override: override the default model for this session
            thinking_budget: extended thinking token budget (Anthropic only; None = use config)
        """
        # Compact history if it's grown too large for the context window
        effective_model = model_override or self.cfg.llm_model
        if needs_compaction(messages, effective_model, self.cfg.llm_max_tokens):
            messages = await compact_messages(
                messages=messages,
                model=effective_model,
                api_key=self.cfg.anthropic_api_key
                if self.cfg.llm_provider == "anthropic"
                else self.cfg.openai_api_key,
                provider=self.cfg.llm_provider,
            )

        if self.cfg.llm_provider == "anthropic":
            return await self._run_anthropic(
                messages, system_prompt,
                session_id=session_id,
                model=effective_model,
                thinking_budget=thinking_budget,
            )
        return await self._run_openai(
            messages, system_prompt,
            session_id=session_id,
            model=effective_model,
        )

    # ------------------------------------------------------------------
    # Anthropic
    # ------------------------------------------------------------------

    async def _run_anthropic(
        self,
        messages: list[dict],
        system_prompt: str,
        session_id: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
    ) -> str:
        tools = self.registry.get_anthropic_tools()
        working = list(messages)
        loop_detector = LoopDetectionState()
        max_retries = getattr(self.cfg, "llm_max_retries", 3)
        effective_model = model or self.cfg.llm_model

        # Determine extended thinking budget
        budget = thinking_budget
        if budget is None:
            budget = getattr(self.cfg, "llm_thinking_budget", 0)

        for iteration in range(self.cfg.max_tool_iterations):
            # Re-check compaction on each iteration — tool results can balloon history
            if needs_compaction(working, effective_model, self.cfg.llm_max_tokens):
                working = await compact_messages(
                    messages=working,
                    model=effective_model,
                    api_key=self.cfg.anthropic_api_key,
                    provider="anthropic",
                )

            kwargs: dict = {
                "model": effective_model,
                "max_tokens": self.cfg.llm_max_tokens,
                "system": system_prompt,
                "messages": working,
            }
            if tools:
                kwargs["tools"] = tools
            # Extended thinking (Anthropic: claude-3-7 and above)
            if budget and budget > 0:
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}

            try:
                response = await _retry_api(
                    lambda: self._anthropic.messages.create(**kwargs),
                    max_retries=max_retries,
                    label="anthropic",
                )
            except Exception as e:
                logger.error("Anthropic API fatal error: %s", e)
                return f"Sorry, I couldn't reach the AI service: {_sanitize_error(e)}"

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        return block.text
                return ""

            if response.stop_reason == "tool_use":
                working.append({"role": "assistant", "content": [b.model_dump() for b in response.content]})

                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    # Loop detection check before executing
                    check = loop_detector.record(block.name, block.input)
                    if check.action == "abort":
                        logger.error("Aborting agent turn due to loop: %s", check.message)
                        return check.message
                    if check.action == "block":
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": check.message,
                        })
                        continue

                    logger.info("Tool call [%d/%d]: %s", iteration + 1, self.cfg.max_tool_iterations, block.name)
                    args_with_ctx = dict(block.input)
                    if session_id:
                        args_with_ctx.setdefault("_session_id", session_id)
                    try:
                        result = await self.registry.execute(block.name, args_with_ctx)
                    except Exception as e:
                        logger.exception("Unhandled tool error: %s", block.name)
                        result = f"Tool error: {_sanitize_error(e)}"

                    # Append loop warning as prefix if in warn state
                    if check.action == "warn":
                        result = f"⚠️ {check.message}\n\n{result}"

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

                working.append({"role": "user", "content": tool_results})
            else:
                logger.warning("Unexpected stop_reason: %s", response.stop_reason)
                break

        logger.warning("Max tool iterations (%d) reached", self.cfg.max_tool_iterations)
        return "I've reached the tool call limit for this turn. Here's what I accomplished so far."

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------

    async def _run_openai(
        self,
        messages: list[dict],
        system_prompt: str,
        session_id: str | None = None,
        model: str | None = None,
    ) -> str:
        tools = self.registry.get_openai_tools()
        working = [{"role": "system", "content": system_prompt}] + list(messages)
        loop_detector = LoopDetectionState()
        max_retries = getattr(self.cfg, "llm_max_retries", 3)
        effective_model = model or self.cfg.llm_model

        for iteration in range(self.cfg.max_tool_iterations):
            if needs_compaction(working, effective_model, self.cfg.llm_max_tokens):
                working = await compact_messages(
                    messages=working,
                    model=effective_model,
                    api_key=self.cfg.openai_api_key,
                    provider="openai",
                )

            kwargs: dict = {"model": effective_model, "messages": working}
            if tools:
                kwargs["tools"] = tools

            try:
                response = await _retry_api(
                    lambda: self._openai.chat.completions.create(**kwargs),
                    max_retries=max_retries,
                    label="openai",
                )
            except Exception as e:
                logger.error("OpenAI API fatal error: %s", e)
                return f"Sorry, I couldn't reach the AI service: {_sanitize_error(e)}"

            choice = response.choices[0]

            if choice.finish_reason == "stop":
                return choice.message.content or ""

            if choice.finish_reason == "tool_calls":
                working.append(choice.message.model_dump(exclude_unset=True))

                for tool_call in choice.message.tool_calls:
                    args = json.loads(tool_call.function.arguments)

                    check = loop_detector.record(tool_call.function.name, args)
                    if check.action == "abort":
                        logger.error("Aborting agent turn due to loop: %s", check.message)
                        return check.message
                    if check.action == "block":
                        working.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": check.message,
                        })
                        continue

                    logger.info("Tool call [%d/%d]: %s", iteration + 1, self.cfg.max_tool_iterations, tool_call.function.name)
                    args_with_ctx = dict(args)
                    if session_id:
                        args_with_ctx.setdefault("_session_id", session_id)
                    try:
                        result = await self.registry.execute(tool_call.function.name, args_with_ctx)
                    except Exception as e:
                        logger.exception("Unhandled tool error: %s", tool_call.function.name)
                        result = f"Tool error: {_sanitize_error(e)}"

                    if check.action == "warn":
                        result = f"⚠️ {check.message}\n\n{result}"

                    working.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": str(result),
                    })
            else:
                logger.warning("Unexpected finish_reason: %s", choice.finish_reason)
                break

        logger.warning("Max tool iterations (%d) reached", self.cfg.max_tool_iterations)
        return "I've reached the tool call limit for this turn. Here's what I accomplished so far."
