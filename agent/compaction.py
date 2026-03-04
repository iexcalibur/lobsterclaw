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

IDENTIFIER_PRESERVATION_INSTRUCTIONS = (
    "IMPORTANT: Preserve all identifiers verbatim in the summary — "
    "UUIDs, commit hashes, file paths, URLs, API keys/tokens (redacted forms), "
    "version numbers, IP addresses, port numbers, and any other machine-readable "
    "identifiers. These are critical for continuity."
)

COMPACTION_CHUNK_TOKEN_BUDGET = 30_000
COMPACTION_SAFETY_MARGIN = 1.2

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


def _chunk_messages_by_tokens(messages: list[dict], budget: int) -> list[list[dict]]:
    """Split messages into chunks, each fitting within the token budget."""
    chunks: list[list[dict]] = []
    current_chunk: list[dict] = []
    current_tokens = 0
    effective_budget = int(budget / COMPACTION_SAFETY_MARGIN)

    for msg in messages:
        msg_tokens = _estimate_single_message_tokens(msg)
        if current_tokens + msg_tokens > effective_budget and current_chunk:
            chunks.append(current_chunk)
            current_chunk = []
            current_tokens = 0
        current_chunk.append(msg)
        current_tokens += msg_tokens

    if current_chunk:
        chunks.append(current_chunk)
    return chunks


def _estimate_single_message_tokens(msg: dict) -> int:
    content = msg.get("content", "")
    if isinstance(content, str):
        return estimate_tokens(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                for key in ("text", "content", "input", "arguments"):
                    val = block.get(key)
                    if isinstance(val, str):
                        total += estimate_tokens(val)
                    elif isinstance(val, dict):
                        total += estimate_tokens(str(val))
        return max(total, 1)
    return estimate_tokens(str(content))


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
    Multi-stage chunked summarization with fallback chain.

    1. Split old messages into token-budget chunks
    2. Summarize each chunk individually
    3. If multiple chunk summaries, merge with another LLM call
    4. Fallback: metadata-only summary on failure

    Returns original messages unchanged only if all strategies fail.
    """
    to_summarize, to_keep = split_for_compaction(messages)
    if not to_summarize:
        return messages

    n_old = len(to_summarize)
    logger.info(
        "Compacting %d messages (~%d tokens) into a summary",
        n_old,
        estimate_messages_tokens(to_summarize),
    )

    summary_text: str | None = None

    # --- Stage 1: chunked summarization ---
    try:
        summary_text = await _chunked_summarize(
            to_summarize, model=model, api_key=api_key,
            provider=provider, max_tokens=max_tokens,
        )
    except Exception as e:
        logger.warning("Full chunked compaction failed: %s — trying metadata fallback", e)

    # --- Stage 2: metadata-only fallback ---
    if not summary_text:
        try:
            summary_text = _metadata_only_summary(to_summarize)
        except Exception as e:
            logger.error("Metadata-only fallback also failed: %s — keeping original history", e)
            return messages

    summary_message = {
        "role": "user",
        "content": (
            f"[Conversation Summary — {n_old} earlier messages compacted]\n\n"
            f"{summary_text}"
        ),
    }
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


async def _chunked_summarize(
    messages: list[dict],
    *,
    model: str,
    api_key: str,
    provider: str,
    max_tokens: int,
) -> str:
    """Summarize messages in token-budget chunks, then merge if needed."""
    chunks = _chunk_messages_by_tokens(messages, COMPACTION_CHUNK_TOKEN_BUDGET)
    logger.info("Split %d messages into %d chunk(s) for summarization", len(messages), len(chunks))

    partial_summaries: list[str] = []
    for i, chunk in enumerate(chunks):
        history_text = _render_messages_for_summary(chunk)
        prompt = (
            "The following is part of a conversation between a personal AI assistant "
            "and their user. Summarize it concisely, preserving:\n"
            "- All decisions made and actions taken\n"
            "- Key facts the user shared (name, preferences, projects)\n"
            "- Any open tasks or pending items\n"
            "- Important context needed to continue the conversation\n\n"
            f"{IDENTIFIER_PRESERVATION_INSTRUCTIONS}\n\n"
            "Be dense and specific. Do not omit important details.\n\n"
            f"CONVERSATION CHUNK {i + 1}/{len(chunks)} TO SUMMARIZE:\n{history_text}"
        )
        summary = await _call_llm_for_summary(
            prompt=prompt, model=model, api_key=api_key,
            provider=provider, max_tokens=max_tokens,
        )
        if summary:
            partial_summaries.append(summary)

    if not partial_summaries:
        raise RuntimeError("All chunk summaries returned empty")

    if len(partial_summaries) == 1:
        return partial_summaries[0]

    merge_prompt = (
        "Merge the following partial conversation summaries into a single cohesive summary. "
        "Preserve all details, avoid redundancy, and keep chronological order.\n\n"
        f"{IDENTIFIER_PRESERVATION_INSTRUCTIONS}\n\n"
    )
    for i, s in enumerate(partial_summaries):
        merge_prompt += f"--- PARTIAL SUMMARY {i + 1} ---\n{s}\n\n"

    return await _call_llm_for_summary(
        prompt=merge_prompt, model=model, api_key=api_key,
        provider=provider, max_tokens=max_tokens,
    )


def _metadata_only_summary(messages: list[dict]) -> str:
    """Last-resort fallback: produce a minimal metadata summary without LLM."""
    roles = set()
    topics: list[str] = []
    for msg in messages:
        roles.add(msg.get("role", "unknown"))
        content = msg.get("content", "")
        if isinstance(content, str) and content:
            first_line = content.strip().split("\n", 1)[0][:120]
            if first_line:
                topics.append(first_line)
    role_str = ", ".join(sorted(roles))
    topic_sample = "; ".join(topics[:10])
    return (
        f"{len(messages)} messages from roles: {role_str}. "
        f"Topics discussed: {topic_sample}..."
    )


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
