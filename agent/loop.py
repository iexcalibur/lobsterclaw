"""
Core agent loop — sends messages to the LLM, handles tool calls, loops until done.

Integrates:
  - Transcript repair — removes orphaned tool_use/tool_result blocks before API calls
  - Tool loop detection (agent/loop_detection.py)
  - Context window compaction (agent/compaction.py)
  - Pre-compaction memory flush (agent/compaction.py)
  - Tool result truncation (tools/registry.py)
  - API retry on transient errors (rate-limit, overload, 5xx)
  - Extended thinking budget (Anthropic)
  - Per-session model override
  - Error sanitization (no raw tracebacks to user)
  - Streaming: stream_callback receives accumulated text per chunk;
               on_tool_start fires when the first tool_use block begins
  - Reasoning lane split: <think>…</think>/<final>…</final> tags extracted
  - Session token tracking: usage reported to SessionStore after each turn
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING, Awaitable, Callable

import anthropic
import openai

from agent.compaction import (
    MEMORY_FLUSH_PROMPT,
    MEMORY_FLUSH_SYSTEM_PROMPT,
    compact_messages,
    needs_compaction,
    needs_memory_flush,
)
from agent.loop_detection import LoopDetectionState
from config import get_config

if TYPE_CHECKING:
    from tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Regex patterns for reasoning lane split (<think>…</think> / <final>…</final>)
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)
_FINAL_RE = re.compile(r"<final>(.*?)</final>", re.DOTALL | re.IGNORECASE)

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

# Per-session locks to prevent concurrent agent runs on the same session
_session_locks: dict[str, asyncio.Lock] = {}


def _get_session_lock(session_id: str) -> asyncio.Lock:
    if session_id not in _session_locks:
        _session_locks[session_id] = asyncio.Lock()
    return _session_locks[session_id]


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


_ANTHROPIC_REFUSAL_STRINGS = [
    "I cannot and will not",
    "I need to be direct",
    "I don't feel comfortable",
    "I'm not able to help with",
    "I appreciate you sharing",
]


def _scrub_refusal_strings(text: str) -> str:
    for pattern in _ANTHROPIC_REFUSAL_STRINGS:
        text = text.replace(pattern, "[...]")
    return text


def repair_transcript(messages: list[dict]) -> list[dict]:
    """
    Sanitize conversation history before sending to the LLM API.

    Mirrors OpenClaw's transcript repair / sanitization logic:
      1. Remove assistant messages that contain tool_use blocks where the tool_result
         is missing (orphaned tool_use).
      2. Remove user messages that contain tool_result blocks where no preceding
         tool_use exists (orphaned tool_result).
      3. Remove empty assistant/user messages.
      4. Ensure the conversation does not start with an assistant message.

    Returns a cleaned copy. The original list is not mutated.
    """
    if not messages:
        return messages

    working = list(messages)

    # Pass 1: collect all tool_use ids present in assistant messages
    issued_ids: set[str] = set()
    for msg in working:
        if msg.get("role") == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        bid = block.get("id") or block.get("tool_use_id")
                        if bid:
                            issued_ids.add(bid)

    # Pass 2: collect all tool_result ids present in user messages
    answered_ids: set[str] = set()
    for msg in working:
        if msg.get("role") == "user":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        bid = block.get("tool_use_id")
                        if bid:
                            answered_ids.add(bid)

    cleaned: list[dict] = []
    for msg in working:
        role = msg.get("role", "")
        content = msg.get("content")

        # Skip empty messages
        if not content or content == [] or content == "":
            continue

        if role == "assistant" and isinstance(content, list):
            # Remove tool_use blocks that were never answered
            new_blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    bid = block.get("id") or block.get("tool_use_id")
                    if bid and bid not in answered_ids:
                        logger.debug("repair_transcript: dropping orphaned tool_use id=%s", bid)
                        continue
                new_blocks.append(block)
            if not new_blocks:
                continue
            cleaned.append({**msg, "content": new_blocks})

        elif role == "user" and isinstance(content, list):
            # Remove tool_result blocks that reference unknown tool_use ids
            new_blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    bid = block.get("tool_use_id")
                    if bid and bid not in issued_ids:
                        logger.debug("repair_transcript: dropping orphaned tool_result id=%s", bid)
                        continue
                new_blocks.append(block)
            if not new_blocks:
                continue
            cleaned.append({**msg, "content": new_blocks})

        else:
            cleaned.append(msg)

    # Ensure conversation doesn't start with assistant
    while cleaned and cleaned[0].get("role") == "assistant":
        logger.debug("repair_transcript: removing leading assistant message")
        cleaned.pop(0)

    # Pass 5: scrub known Anthropic refusal magic strings from assistant messages
    for msg in cleaned:
        if msg.get("role") == "assistant":
            content = msg.get("content")
            if isinstance(content, str):
                msg["content"] = _scrub_refusal_strings(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        block["text"] = _scrub_refusal_strings(block.get("text", ""))

    return cleaned


def _extract_reasoning_split(text: str, show_thinking: bool) -> str:
    """
    Extract <think>…</think> reasoning blocks and <final>…</final> from text.

    - If show_thinking: wrap reasoning in a collapsible block; return reasoning + final.
    - If not show_thinking: strip reasoning blocks; return only final (or plain text).
    """
    think_blocks = _THINK_RE.findall(text)
    final_blocks = _FINAL_RE.findall(text)

    # Remove both tag families from the main text
    stripped = _THINK_RE.sub("", text)
    stripped = _FINAL_RE.sub("", stripped).strip()

    # Use <final> content if present, otherwise use stripped remainder
    final_text = "\n\n".join(b.strip() for b in final_blocks if b.strip()) or stripped

    if show_thinking and think_blocks:
        thinking_combined = "\n\n".join(b.strip() for b in think_blocks if b.strip())
        return f"<blockquote expandable>{thinking_combined}</blockquote>\n\n{final_text}"

    return final_text or text  # fallback to original if nothing parsed


def _coerce_text_value(value) -> str:
    """Coerce unknown Anthropic content values into plain text."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("text", "value", "content"):
            maybe = value.get(key)
            if isinstance(maybe, str):
                return maybe
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value)


def _coerce_tool_input(value) -> dict:
    """Normalize tool input payload to a dict shape Anthropic accepts."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
            return {"value": parsed}
        except Exception:
            return {"value": value}
    return {"value": value}


def _sanitize_anthropic_block(block) -> dict | None:
    """
    Keep only Anthropic-supported block fields.
    This strips SDK extras (e.g. parsed_output) that can cause 400 errors.
    """
    if not isinstance(block, dict):
        return {"type": "text", "text": _coerce_text_value(block)}

    btype = block.get("type")
    if btype == "text":
        return {"type": "text", "text": _coerce_text_value(block.get("text", ""))}

    if btype == "image":
        source = block.get("source")
        if not isinstance(source, dict):
            return None
        stype = source.get("type")
        if stype == "base64":
            data = source.get("data")
            media_type = source.get("media_type")
            if isinstance(data, str) and data and isinstance(media_type, str) and media_type:
                return {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": data,
                    },
                }
        if stype == "url":
            url = source.get("url")
            if isinstance(url, str) and url:
                return {
                    "type": "image",
                    "source": {
                        "type": "url",
                        "url": url,
                    },
                }
        return None

    if btype == "tool_use":
        tool_id = block.get("id") or block.get("tool_use_id")
        name = block.get("name")
        if not tool_id or not name:
            return None
        return {
            "type": "tool_use",
            "id": str(tool_id),
            "name": str(name),
            "input": _coerce_tool_input(block.get("input", {})),
        }

    if btype == "tool_result":
        tool_use_id = block.get("tool_use_id")
        if not tool_use_id:
            return None
        content = block.get("content", "")
        if isinstance(content, (dict, list)):
            try:
                content = json.dumps(content, ensure_ascii=False)
            except Exception:
                content = str(content)
        elif content is None:
            content = ""
        else:
            content = str(content)
        result = {
            "type": "tool_result",
            "tool_use_id": str(tool_use_id),
            "content": content,
        }
        if "is_error" in block:
            result["is_error"] = bool(block.get("is_error"))
        return result

    # Skip unknown/unsupported block types (e.g. thinking) in request transcripts.
    return None


def _sanitize_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Sanitize transcript before Anthropic API calls."""
    out: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content", "")

        if isinstance(content, list):
            blocks: list[dict] = []
            for block in content:
                normalized = _sanitize_anthropic_block(block)
                if normalized:
                    blocks.append(normalized)
            if blocks:
                out.append({"role": role, "content": blocks})
            continue

        if not isinstance(content, str):
            content = _coerce_text_value(content)
        if content:
            out.append({"role": role, "content": content})
    return out


def _response_blocks_to_anthropic_input_blocks(response_blocks) -> list[dict]:
    """Convert Anthropic response blocks to valid request blocks for transcript replay."""
    blocks: list[dict] = []
    for block in response_blocks:
        btype = getattr(block, "type", None)
        if btype == "text":
            text = _coerce_text_value(getattr(block, "text", ""))
            blocks.append({"type": "text", "text": text})
        elif btype == "tool_use":
            tool_id = getattr(block, "id", None)
            name = getattr(block, "name", None)
            if not tool_id or not name:
                continue
            blocks.append(
                {
                    "type": "tool_use",
                    "id": str(tool_id),
                    "name": str(name),
                    "input": _coerce_tool_input(getattr(block, "input", {})),
                }
            )
    return blocks


class AgentLoop:
    """Core agent loop — sends messages to the LLM, handles tool calls, loops until done."""

    def __init__(self, registry: "ToolRegistry") -> None:
        self.cfg = get_config()
        self.registry = registry
        # Track whether we've already run a memory flush for the current compaction cycle.
        # Reset after each successful compaction.
        self._memory_flush_done: bool = False

        # Cumulative token usage for this loop instance (updated after each API call).
        # Keys: "input_tokens", "output_tokens", "total_tokens"
        self._token_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }

        # Build auth profile lists for rotation (comma-separated keys)
        self._anthropic_keys = [k.strip() for k in (self.cfg.anthropic_api_key or "").split(",") if k.strip()]
        self._openai_keys = [k.strip() for k in (self.cfg.openai_api_key or "").split(",") if k.strip()]
        self._current_anthropic_idx = 0
        self._current_openai_idx = 0
        self._key_cooldowns: dict[str, float] = {}

        if self.cfg.llm_provider == "anthropic":
            self._anthropic = anthropic.AsyncAnthropic(
                api_key=self._anthropic_keys[0] if self._anthropic_keys else "",
            )
            self._openai = None
        else:
            self._anthropic = None
            self._openai = openai.AsyncOpenAI(
                api_key=self._openai_keys[0] if self._openai_keys else "",
            )

    def get_token_usage(self) -> dict[str, int]:
        """Return cumulative token usage for this loop (input + output + total)."""
        return dict(self._token_usage)

    def _record_usage(self, input_tokens: int, output_tokens: int) -> None:
        self._token_usage["input_tokens"] += input_tokens
        self._token_usage["output_tokens"] += output_tokens
        self._token_usage["total_tokens"] += input_tokens + output_tokens

    def _rotate_anthropic_key(self) -> str | None:
        if len(self._anthropic_keys) <= 1:
            return None
        self._current_anthropic_idx = (self._current_anthropic_idx + 1) % len(self._anthropic_keys)
        new_key = self._anthropic_keys[self._current_anthropic_idx]
        self._anthropic = anthropic.AsyncAnthropic(api_key=new_key)
        logger.info("Rotated to Anthropic key profile #%d", self._current_anthropic_idx)
        return new_key

    def _rotate_openai_key(self) -> str | None:
        if len(self._openai_keys) <= 1:
            return None
        self._current_openai_idx = (self._current_openai_idx + 1) % len(self._openai_keys)
        new_key = self._openai_keys[self._current_openai_idx]
        self._openai = openai.AsyncOpenAI(api_key=new_key)
        logger.info("Rotated to OpenAI key profile #%d", self._current_openai_idx)
        return new_key

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
        # Acquire per-session lock to serialize concurrent calls
        lock = _get_session_lock(session_id or "__global__")
        async with lock:
            effective_model = model_override or self.cfg.llm_model

            # Resolve model aliases (e.g., "sonnet" → "claude-sonnet-4-5")
            effective_model = self.cfg.resolve_model(effective_model)

            api_key = (
                self.cfg.anthropic_api_key
                if self.cfg.llm_provider == "anthropic"
                else self.cfg.openai_api_key
            )

            # Transcript repair: strip orphaned tool_use/tool_result blocks before sending.
            if getattr(self.cfg, "transcript_repair_enabled", True):
                messages = repair_transcript(messages)

            # Pre-compaction memory flush: ask the agent to write durable memories before
            # the history gets compacted and older turns are lost.
            flush_enabled = getattr(self.cfg, "memory_flush_enabled", True)
            flush_soft = getattr(self.cfg, "memory_flush_soft_tokens", 4000)
            if (
                flush_enabled
                and not self._memory_flush_done
                and needs_memory_flush(messages, effective_model, self.cfg.llm_max_tokens, flush_soft)
            ):
                logger.info("Pre-compaction memory flush: running flush turn")
                today = __import__("datetime").date.today().isoformat()
                flush_prompt = MEMORY_FLUSH_PROMPT.replace("YYYY-MM-DD", today)
                try:
                    await self._run_flush_turn(messages, system_prompt, flush_prompt)
                    self._memory_flush_done = True
                except Exception as exc:
                    logger.warning("Memory flush turn failed (continuing): %s", exc)

            if needs_compaction(messages, effective_model, self.cfg.llm_max_tokens):
                messages = await compact_messages(
                    messages=messages,
                    model=effective_model,
                    api_key=api_key,
                    provider=self.cfg.llm_provider,
                )
                self._memory_flush_done = False  # reset for the next compaction cycle

            if self.cfg.llm_provider == "anthropic":
                result = await self._run_anthropic(
                    messages, system_prompt,
                    session_id=session_id,
                    model=effective_model,
                    thinking_budget=thinking_budget,
                    stream_callback=stream_callback,
                    on_tool_start=on_tool_start,
                )
            else:
                result = await self._run_openai(
                    messages, system_prompt,
                    session_id=session_id,
                    model=effective_model,
                    stream_callback=stream_callback,
                    on_tool_start=on_tool_start,
                )

            # Persist token usage to SessionStore if available
            if session_id:
                try:
                    from agent.sessions import get_session_store
                    store = get_session_store()
                    await store.add_token_usage(
                        session_id,
                        self._token_usage["input_tokens"],
                        self._token_usage["output_tokens"],
                    )
                except Exception:
                    pass  # SessionStore may not be initialized in tests

            return result

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

        overflow_attempts = 0

        for iteration in range(self.cfg.max_tool_iterations):
            if needs_compaction(working, effective_model, self.cfg.llm_max_tokens):
                working = await compact_messages(
                    messages=_sanitize_anthropic_messages(working),
                    model=effective_model,
                    api_key=self.cfg.anthropic_api_key,
                    provider="anthropic",
                )

            kwargs: dict = {
                "model": effective_model,
                "max_tokens": self.cfg.llm_max_tokens,
                "system": system_prompt,
                "messages": _sanitize_anthropic_messages(working),
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
                except anthropic.BadRequestError as e:
                    err_str = str(e).lower()
                    if ("thinking" in err_str or "budget" in err_str) and budget and budget > 0:
                        logger.warning("Thinking budget rejected, retrying without extended thinking")
                        budget = 0
                        if "thinking" in kwargs:
                            del kwargs["thinking"]
                        continue
                    if "context" in err_str or "token" in err_str or "too long" in err_str:
                        logger.warning("Context overflow detected, attempting compaction (attempt %d/3)", overflow_attempts + 1)
                        overflow_attempts += 1
                        if overflow_attempts <= 3:
                            working = await compact_messages(
                                messages=_sanitize_anthropic_messages(working),
                                model=effective_model,
                                api_key=self.cfg.anthropic_api_key,
                                provider="anthropic",
                            )
                            continue
                        else:
                            return "Context overflow: could not reduce context after 3 compaction attempts."
                    raise
                except anthropic.RateLimitError:
                    rotated = self._rotate_anthropic_key()
                    if rotated:
                        logger.info("Rate limited — rotated to next API key profile")
                        continue
                    raise
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
                except anthropic.BadRequestError as e:
                    err_str = str(e).lower()
                    if ("thinking" in err_str or "budget" in err_str) and budget and budget > 0:
                        logger.warning("Thinking budget rejected, retrying without extended thinking")
                        budget = 0
                        if "thinking" in kwargs:
                            del kwargs["thinking"]
                        continue
                    if "context" in err_str or "token" in err_str or "too long" in err_str:
                        logger.warning("Context overflow detected, attempting compaction (attempt %d/3)", overflow_attempts + 1)
                        overflow_attempts += 1
                        if overflow_attempts <= 3:
                            working = await compact_messages(
                                messages=_sanitize_anthropic_messages(working),
                                model=effective_model,
                                api_key=self.cfg.anthropic_api_key,
                                provider="anthropic",
                            )
                            continue
                        else:
                            return "Context overflow: could not reduce context after 3 compaction attempts."
                    raise
                except anthropic.RateLimitError:
                    rotated = self._rotate_anthropic_key()
                    if rotated:
                        logger.info("Rate limited — rotated to next API key profile")
                        continue
                    raise
                except Exception as e:
                    logger.error("Anthropic API fatal error: %s", e)
                    return f"Sorry, I couldn't reach the AI service: {_sanitize_error(e)}"

            if response.stop_reason == "end_turn":
                show_thinking = getattr(self.cfg, "llm_show_thinking", False)
                # Record token usage from Anthropic usage object
                if hasattr(response, "usage") and response.usage:
                    self._record_usage(
                        getattr(response.usage, "input_tokens", 0),
                        getattr(response.usage, "output_tokens", 0),
                    )
                parts: list[str] = []
                for block in response.content:
                    # Anthropic extended thinking blocks
                    if hasattr(block, "thinking") and block.type == "thinking":
                        if show_thinking:
                            parts.append(f"<blockquote expandable>{block.thinking}</blockquote>")
                    elif hasattr(block, "text"):
                        # Reasoning lane split: handle <think>/<final> tags from reasoning models
                        parts.append(_extract_reasoning_split(block.text, show_thinking))
                return "\n".join(p for p in parts if p)

            if response.stop_reason == "tool_use":
                # Record usage for intermediate tool turns
                if hasattr(response, "usage") and response.usage:
                    self._record_usage(
                        getattr(response.usage, "input_tokens", 0),
                        getattr(response.usage, "output_tokens", 0),
                    )
                assistant_blocks = _response_blocks_to_anthropic_input_blocks(response.content)
                if assistant_blocks:
                    working.append({"role": "assistant", "content": assistant_blocks})

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

    async def _run_flush_turn(
        self,
        messages: list[dict],
        main_system_prompt: str,
        flush_prompt: str,
    ) -> None:
        """
        Run a single pre-compaction memory flush turn.
        Injects a special user message asking the agent to write memories to disk,
        then runs one tool-calling loop with only memory_write accessible.
        The result is discarded (flush turn is invisible to the user).
        """
        silent_token = getattr(self.cfg, "silent_reply_token", "NO_REPLY")
        flush_messages = list(messages) + [{"role": "user", "content": flush_prompt}]

        if self.cfg.llm_provider == "anthropic":
            # Use memory tools if available, else run without tools (agent writes via text)
            memory_tools = [
                t for t in self.registry.get_anthropic_tools()
                if t.get("name", "").startswith("memory")
            ]
            kwargs: dict = {
                "model": self.cfg.llm_model,
                "max_tokens": 2048,
                "system": MEMORY_FLUSH_SYSTEM_PROMPT,
                "messages": _sanitize_anthropic_messages(flush_messages),
            }
            if memory_tools:
                kwargs["tools"] = memory_tools

            response = await _retry_api(
                lambda: self._anthropic.messages.create(**kwargs),
                max_retries=2,
                label="memory-flush-anthropic",
            )
            # Execute any memory_write tool calls in the flush response
            for block in response.content:
                if getattr(block, "type", None) == "tool_use" and block.name.startswith("memory"):
                    try:
                        await self.registry.execute(block.name, dict(block.input))
                        logger.debug("Memory flush tool: %s", block.name)
                    except Exception as e:
                        logger.debug("Memory flush tool error (%s): %s", block.name, e)
        else:
            memory_tools = [
                t for t in self.registry.get_openai_tools()
                if t.get("function", {}).get("name", "").startswith("memory")
            ]
            kwargs = {
                "model": self.cfg.llm_model,
                "max_tokens": 2048,
                "messages": [
                    {"role": "system", "content": MEMORY_FLUSH_SYSTEM_PROMPT},
                    *flush_messages,
                ],
            }
            if memory_tools:
                kwargs["tools"] = memory_tools

            response = await _retry_api(
                lambda: self._openai.chat.completions.create(**kwargs),
                max_retries=2,
                label="memory-flush-openai",
            )
            choice = response.choices[0]
            if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    if tc.function.name.startswith("memory"):
                        try:
                            import json as _json
                            args = _json.loads(tc.function.arguments)
                            await self.registry.execute(tc.function.name, args)
                            logger.debug("Memory flush tool: %s", tc.function.name)
                        except Exception as e:
                            logger.debug("Memory flush tool error (%s): %s", tc.function.name, e)

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

        overflow_attempts = 0

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
                except openai.BadRequestError as e:
                    err_str = str(e).lower()
                    if "context" in err_str or "token" in err_str or "too long" in err_str:
                        logger.warning("Context overflow detected, attempting compaction (attempt %d/3)", overflow_attempts + 1)
                        overflow_attempts += 1
                        if overflow_attempts <= 3:
                            working = await compact_messages(
                                messages=working,
                                model=effective_model,
                                api_key=self.cfg.openai_api_key,
                                provider="openai",
                            )
                            continue
                        else:
                            return "Context overflow: could not reduce context after 3 compaction attempts."
                    raise
                except openai.RateLimitError:
                    rotated = self._rotate_openai_key()
                    if rotated:
                        logger.info("Rate limited — rotated to next API key profile")
                        continue
                    raise
                except Exception as e:
                    logger.error("OpenAI streaming fatal error: %s", e)
                    return f"Sorry, I couldn't reach the AI service: {_sanitize_error(e)}"

                if finish_reason == "stop":
                    show_thinking = getattr(self.cfg, "llm_show_thinking", False)
                    return _extract_reasoning_split(content_text, show_thinking)

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
                except openai.BadRequestError as e:
                    err_str = str(e).lower()
                    if "context" in err_str or "token" in err_str or "too long" in err_str:
                        logger.warning("Context overflow detected, attempting compaction (attempt %d/3)", overflow_attempts + 1)
                        overflow_attempts += 1
                        if overflow_attempts <= 3:
                            working = await compact_messages(
                                messages=working,
                                model=effective_model,
                                api_key=self.cfg.openai_api_key,
                                provider="openai",
                            )
                            continue
                        else:
                            return "Context overflow: could not reduce context after 3 compaction attempts."
                    raise
                except openai.RateLimitError:
                    rotated = self._rotate_openai_key()
                    if rotated:
                        logger.info("Rate limited — rotated to next API key profile")
                        continue
                    raise
                except Exception as e:
                    logger.error("OpenAI API fatal error: %s", e)
                    return f"Sorry, I couldn't reach the AI service: {_sanitize_error(e)}"

                choice = response.choices[0]

                if choice.finish_reason == "stop":
                    if hasattr(response, "usage") and response.usage:
                        self._record_usage(
                            getattr(response.usage, "prompt_tokens", 0),
                            getattr(response.usage, "completion_tokens", 0),
                        )
                    raw_text = choice.message.content or ""
                    show_thinking = getattr(self.cfg, "llm_show_thinking", False)
                    return _extract_reasoning_split(raw_text, show_thinking)

                if choice.finish_reason == "tool_calls":
                    if hasattr(response, "usage") and response.usage:
                        self._record_usage(
                            getattr(response.usage, "prompt_tokens", 0),
                            getattr(response.usage, "completion_tokens", 0),
                        )
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
