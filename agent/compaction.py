"""
Context window compaction — mirrors OpenClaw's compaction.ts + memoryFlush

When conversation history approaches the LLM's context window limit, this module
summarizes older turns and replaces them with a compact summary, keeping only
the most recent turns verbatim.

Token estimation is approximate (4 chars ≈ 1 token), matching OpenClaw's approach.
The compaction LLM call uses the same provider as the main agent.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Approximate chars per token (conservative)
CHARS_PER_TOKEN = 4
# Safety margin: compact when history exceeds this fraction of context window
COMPACTION_TRIGGER_RATIO = 0.75
# How many recent messages to always keep verbatim (never summarized)
RECENT_KEEP_COUNT = 10
# Overhead budget for system prompt + response
SYSTEM_PROMPT_TOKEN_BUDGET = 4_000

# Context window sizes by model prefix
_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-3-5": 200_000,
    "claude-3-7": 200_000,
    "claude-opus-4": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-haiku": 200_000,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5": 16_385,
    "o1": 200_000,
    "o3": 200_000,
}


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def estimate_messages_tokens(messages: list[dict]) -> int:
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += estimate_tokens(str(block.get("text", "") or block.get("content", "")))
    return total


def resolve_context_window(model: str) -> int:
    model_lower = model.lower()
    for prefix, window in _CONTEXT_WINDOWS.items():
        if model_lower.startswith(prefix):
            return window
    return 100_000  # conservative default


# Pre-compaction memory flush — mirrors OpenClaw's memoryFlush feature.
# Before compacting, the agent is asked to write durable memories to disk.
MEMORY_FLUSH_PROMPT = (
    "Pre-compaction memory flush. "
    "Store durable memories now (use memory/YYYY-MM-DD.md; create memory/ if needed). "
    "IMPORTANT: If the file already exists, APPEND new content only — do not overwrite existing entries. "
    "If nothing to store, reply with NO_REPLY."
)

MEMORY_FLUSH_SYSTEM_PROMPT = (
    "Pre-compaction memory flush turn. "
    "The session is near auto-compaction; capture durable memories to disk now. "
    "You may reply, but usually NO_REPLY is correct."
)


def needs_compaction(messages: list[dict], model: str, max_tokens: int) -> bool:
    """Return True if history is large enough to warrant compaction."""
    context_window = resolve_context_window(model)
    # Available tokens = context window - response budget - system prompt budget
    available = context_window - max_tokens - SYSTEM_PROMPT_TOKEN_BUDGET
    if available <= 0:
        return False
    used = estimate_messages_tokens(messages)
    trigger = int(available * COMPACTION_TRIGGER_RATIO)
    return used > trigger


def needs_memory_flush(
    messages: list[dict],
    model: str,
    max_tokens: int,
    soft_threshold_tokens: int = 4000,
) -> bool:
    """
    Return True if history is close enough to the compaction threshold to
    warrant a pre-compaction memory flush.

    The flush fires when we are within `soft_threshold_tokens` of the compaction trigger,
    i.e.: used > trigger - soft_threshold_tokens.

    This mirrors OpenClaw's shouldRunMemoryFlush soft-threshold logic.
    """
    context_window = resolve_context_window(model)
    available = context_window - max_tokens - SYSTEM_PROMPT_TOKEN_BUDGET
    if available <= 0:
        return False
    used = estimate_messages_tokens(messages)
    trigger = int(available * COMPACTION_TRIGGER_RATIO)
    soft_trigger = max(0, trigger - soft_threshold_tokens)
    return used > soft_trigger


def split_for_compaction(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Split messages into (to_summarize, to_keep).
    Always keep RECENT_KEEP_COUNT recent messages verbatim.
    """
    if len(messages) <= RECENT_KEEP_COUNT:
        return [], list(messages)
    cutoff = len(messages) - RECENT_KEEP_COUNT
    return list(messages[:cutoff]), list(messages[cutoff:])


def _render_messages_for_summary(messages: list[dict]) -> str:
    """Flatten messages to plain text for the summarization prompt."""
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, list):
            # Tool calls / results — extract text portions
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    t = block.get("text") or block.get("content") or ""
                    if t:
                        parts.append(str(t))
            content = " | ".join(parts)
        if content:
            lines.append(f"[{role.upper()}]: {content}")
    return "\n".join(lines)


async def compact_messages(
    messages: list[dict],
    model: str,
    api_key: str,
    provider: str,
    max_tokens: int = 2048,
) -> list[dict]:
    """
    Summarize old messages and return a compacted history.
    Returns original messages unchanged if compaction fails.
    """
    to_summarize, to_keep = split_for_compaction(messages)
    if not to_summarize:
        return messages

    history_text = _render_messages_for_summary(to_summarize)
    prompt = (
        "The following is the beginning of a conversation between a personal AI assistant "
        "and their user. Summarize it concisely, preserving:\n"
        "- All decisions made and actions taken\n"
        "- Key facts the user shared (name, preferences, projects)\n"
        "- Any open tasks or pending items\n"
        "- Important context needed to continue the conversation\n\n"
        "Be dense and specific. Do not omit important details.\n\n"
        f"CONVERSATION TO SUMMARIZE:\n{history_text}"
    )

    logger.info(
        "Compacting %d messages (~%d tokens) into a summary",
        len(to_summarize),
        estimate_messages_tokens(to_summarize),
    )

    try:
        summary_text = await _call_llm_for_summary(
            prompt=prompt, model=model, api_key=api_key, provider=provider, max_tokens=max_tokens
        )
    except Exception as e:
        logger.error("Compaction LLM call failed: %s — keeping original history", e)
        return messages

    summary_message = {
        "role": "user",
        "content": (
            f"[Conversation Summary — {len(to_summarize)} earlier messages compacted]\n\n"
            f"{summary_text}"
        ),
    }
    # Inject a fake assistant ack so the message pair is balanced
    ack_message = {
        "role": "assistant",
        "content": "Understood. I have the summary of our earlier conversation and will continue from there.",
    }

    compacted = [summary_message, ack_message] + to_keep
    logger.info(
        "Compaction complete: %d messages → %d messages",
        len(messages),
        len(compacted),
    )
    return compacted


async def _call_llm_for_summary(
    prompt: str, model: str, api_key: str, provider: str, max_tokens: int
) -> str:
    if provider == "anthropic":
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        return ""
    else:
        import openai
        client = openai.AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""
