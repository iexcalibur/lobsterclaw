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
  - Streaming: stream_callback receives accumulated text per chunk;
               on_tool_start fires when the first tool_use block begins
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Awaitable, Callable

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
        stream_callback: Callable[[str], Awaitable[None]] | None = None,
        on_tool_start: Callable[[], Awaitable[None]] | None = None,
    ) -> str:
        """
        Run the agent loop and return the final text reply.

        Args:
            messages:        conversation history
            system_prompt:   system prompt text
            session_id:      used for logging/tracing
            model_override:  override the default model for this session
            thinking_budget: extended thinking token budget (Anthropic; None = use config)
            stream_callback: called with accumulated text on each streamed chunk
            on_tool_start:   called once when the first tool_use block begins
        """
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
                stream_callback=stream_callback,
                on_tool_start=on_tool_start,
            )
        return await self._run_openai(
            messages, system_prompt,
            session_id=session_id,
            model=effective_model,
            stream_callback=stream_callback,
            on_tool_start=on_tool_start,
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
        stream_callback: Callable[[str], Awaitable[None]] | None = None,
        on_tool_start: Callable[[], Awaitable[None]] | None = None,
    ) -> str:
        tools = self.registry.get_anthropic_tools()
        working = list(messages)
        loop_detector = LoopDetectionState()
        max_retries = getattr(self.cfg, "llm_max_retries", 3)
        effective_model = model or self.cfg.llm_model
        use_streaming = stream_callback is not None and getattr(self.cfg, "llm_streaming", True)

        budget = thinking_budget
        if budget is None:
            budget = getattr(self.cfg, "llm_thinking_budget", 0)

        for iteration in range(self.cfg.max_tool_iterations):
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
            if budget and budget > 0:
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}

            if use_streaming:
                try:
                    response = await self._anthropic_stream_turn(
                        kwargs, stream_callback, on_tool_start, max_retries
                    )
                except Exception as e:
                    logger.error("Anthropic streaming fatal error: %s", e)
                    return f"Sorry, I couldn't reach the AI service: {_sanitize_error(e)}"
            else:
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
                show_thinking = getattr(self.cfg, "llm_show_thinking", False)
                parts = []
                for block in response.content:
                    if hasattr(block, "thinking") and block.type == "thinking":
                        if show_thinking:
                            parts.append(f"<blockquote expandable>{block.thinking}</blockquote>")
                    elif hasattr(block, "text"):
                        parts.append(block.text)
                return "\n".join(p for p in parts if p)

            if response.stop_reason == "tool_use":
                working.append({"role": "assistant", "content": [b.model_dump() for b in response.content]})

                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    check = loop_detector.record(block.name, block.input)
                    if check.action == "abort":
                        logger.error("Aborting due to loop: %s", check.message)
                        return check.message
                    if check.action == "block":
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": check.message,
                        })
                        continue

                    logger.info("Tool call [%d/%d]: %s",
                                iteration + 1, self.cfg.max_tool_iterations, block.name)
                    args_with_ctx = dict(block.input)
                    if session_id:
                        args_with_ctx.setdefault("_session_id", session_id)
                    try:
                        result = await self.registry.execute(block.name, args_with_ctx)
                    except Exception as e:
                        logger.exception("Unhandled tool error: %s", block.name)
                        result = f"Tool error: {_sanitize_error(e)}"

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

    async def _anthropic_stream_turn(
        self,
        kwargs: dict,
        stream_callback: Callable[[str], Awaitable[None]] | None,
        on_tool_start: Callable[[], Awaitable[None]] | None,
        max_retries: int,
    ):
        """Run one Anthropic streaming turn. Returns a Message object."""
        delay = 2.0
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                accumulated_text = ""
                tool_start_fired = False

                async with self._anthropic.messages.stream(**kwargs) as stream:
                    async for event in stream:
                        event_type = getattr(event, "type", None)

                        if event_type == "content_block_delta":
                            delta = getattr(event, "delta", None)
                            if delta:
                                # Text delta
                                if hasattr(delta, "text") and delta.text:
                                    accumulated_text += delta.text
                                    if stream_callback:
                                        try:
                                            await stream_callback(accumulated_text)
                                        except Exception:
                                            pass

                        elif event_type == "content_block_start":
                            block = getattr(event, "content_block", None)
                            if block and getattr(block, "type", None) == "tool_use":
                                if on_tool_start and not tool_start_fired:
                                    tool_start_fired = True
                                    try:
                                        await on_tool_start()
                                    except Exception:
                                        pass

                    return await stream.get_final_message()

            except _ANTHROPIC_RETRY_TYPES as e:
                last_exc = e
                if attempt < max_retries:
                    wait = delay * (2 ** attempt)
                    logger.warning(
                        "Anthropic stream error (attempt %d/%d): %s — retrying in %.1fs",
                        attempt + 1, max_retries + 1, e, wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error("Anthropic stream failed after %d retries: %s", max_retries, e)
            except Exception as e:
                raise e  # non-transient

        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------

    async def _run_openai(
        self,
        messages: list[dict],
        system_prompt: str,
        session_id: str | None = None,
        model: str | None = None,
        stream_callback: Callable[[str], Awaitable[None]] | None = None,
        on_tool_start: Callable[[], Awaitable[None]] | None = None,
    ) -> str:
        tools = self.registry.get_openai_tools()
        working = [{"role": "system", "content": system_prompt}] + list(messages)
        loop_detector = LoopDetectionState()
        max_retries = getattr(self.cfg, "llm_max_retries", 3)
        effective_model = model or self.cfg.llm_model
        use_streaming = stream_callback is not None and getattr(self.cfg, "llm_streaming", True)

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

            if use_streaming:
                try:
                    finish_reason, content_text, assembled_calls = await self._openai_stream_turn(
                        kwargs, stream_callback, on_tool_start, max_retries
                    )
                except Exception as e:
                    logger.error("OpenAI streaming fatal error: %s", e)
                    return f"Sorry, I couldn't reach the AI service: {_sanitize_error(e)}"

                if finish_reason == "stop":
                    return content_text

                if finish_reason == "tool_calls":
                    # Reconstruct a message dict with assembled tool calls
                    assistant_msg = {
                        "role": "assistant",
                        "content": content_text or None,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {"name": tc["name"], "arguments": tc["arguments"]},
                            }
                            for tc in assembled_calls
                        ],
                    }
                    working.append(assistant_msg)

                    for tc in assembled_calls:
                        args = {}
                        try:
                            args = json.loads(tc["arguments"])
                        except Exception:
                            pass

                        check = loop_detector.record(tc["name"], args)
                        if check.action == "abort":
                            return check.message
                        if check.action == "block":
                            working.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": check.message,
                            })
                            continue

                        logger.info("Tool call [%d/%d]: %s",
                                    iteration + 1, self.cfg.max_tool_iterations, tc["name"])
                        args_with_ctx = dict(args)
                        if session_id:
                            args_with_ctx.setdefault("_session_id", session_id)
                        try:
                            result = await self.registry.execute(tc["name"], args_with_ctx)
                        except Exception as e:
                            logger.exception("Unhandled tool error: %s", tc["name"])
                            result = f"Tool error: {_sanitize_error(e)}"

                        if check.action == "warn":
                            result = f"⚠️ {check.message}\n\n{result}"

                        working.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": str(result),
                        })
                    continue
                else:
                    logger.warning("Unexpected finish_reason (stream): %s", finish_reason)
                    break

            else:
                # Non-streaming path
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
                            return check.message
                        if check.action == "block":
                            working.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": check.message,
                            })
                            continue

                        logger.info("Tool call [%d/%d]: %s",
                                    iteration + 1, self.cfg.max_tool_iterations,
                                    tool_call.function.name)
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

    async def _openai_stream_turn(
        self,
        kwargs: dict,
        stream_callback: Callable[[str], Awaitable[None]] | None,
        on_tool_start: Callable[[], Awaitable[None]] | None,
        max_retries: int,
    ) -> tuple[str, str, list[dict]]:
        """
        Run one OpenAI streaming turn.
        Returns (finish_reason, accumulated_text, assembled_tool_calls).
        assembled_tool_calls: [{id, name, arguments}]
        """
        delay = 2.0
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                accumulated_text = ""
                tool_calls_buffer: dict[int, dict] = {}  # index → {id, name, arguments}
                finish_reason = "stop"
                tool_start_fired = False

                stream = await self._openai.chat.completions.create(**kwargs, stream=True)

                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]

                    if choice.delta.content:
                        accumulated_text += choice.delta.content
                        if stream_callback:
                            try:
                                await stream_callback(accumulated_text)
                            except Exception:
                                pass

                    if choice.delta.tool_calls:
                        for tc_delta in choice.delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_calls_buffer:
                                tool_calls_buffer[idx] = {
                                    "id": tc_delta.id or "",
                                    "name": (tc_delta.function.name or "") if tc_delta.function else "",
                                    "arguments": "",
                                }
                                if on_tool_start and not tool_start_fired:
                                    tool_start_fired = True
                                    try:
                                        await on_tool_start()
                                    except Exception:
                                        pass
                            else:
                                if tc_delta.function:
                                    if tc_delta.function.name:
                                        tool_calls_buffer[idx]["name"] += tc_delta.function.name
                                    if tc_delta.function.arguments:
                                        tool_calls_buffer[idx]["arguments"] += tc_delta.function.arguments

                    if choice.finish_reason:
                        finish_reason = choice.finish_reason

                assembled = list(tool_calls_buffer.values())
                return finish_reason, accumulated_text, assembled

            except _OPENAI_RETRY_TYPES as e:
                last_exc = e
                if attempt < max_retries:
                    wait = delay * (2 ** attempt)
                    logger.warning(
                        "OpenAI stream error (attempt %d/%d): %s — retrying in %.1fs",
                        attempt + 1, max_retries + 1, e, wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error("OpenAI stream failed after %d retries: %s", max_retries, e)
            except Exception as e:
                raise e  # non-transient

        raise last_exc  # type: ignore[misc]
