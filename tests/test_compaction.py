"""Tests for context window compaction."""
import pytest


def _make_messages(n: int, role_cycle=("user", "assistant")) -> list[dict]:
    msgs = []
    for i in range(n):
        role = role_cycle[i % len(role_cycle)]
        msgs.append({"role": role, "content": f"Message {i} " + "x" * 100})
    return msgs


def test_estimate_tokens_basic():
    from agent.compaction import estimate_messages_tokens
    msgs = [{"role": "user", "content": "Hello world"}]
    tokens = estimate_messages_tokens(msgs)
    assert tokens > 0


def test_resolve_context_window_claude():
    from agent.compaction import resolve_context_window
    size = resolve_context_window("claude-opus-4-5")
    assert size > 0


def test_resolve_context_window_gpt4():
    from agent.compaction import resolve_context_window
    size = resolve_context_window("gpt-4o")
    assert size > 0


def test_resolve_context_window_unknown():
    from agent.compaction import resolve_context_window
    size = resolve_context_window("unknown-model-xyz")
    assert size > 0  # Should return a safe default


def test_needs_compaction_short_history():
    from agent.compaction import needs_compaction, resolve_context_window
    msgs = _make_messages(5)
    max_tokens = resolve_context_window("claude-opus-4-5")
    assert needs_compaction(msgs, "claude-opus-4-5", max_tokens) is False


def test_needs_compaction_huge_history():
    from agent.compaction import needs_compaction
    # 10k messages will always exceed any threshold
    msgs = [{"role": "user", "content": "x" * 1000} for _ in range(10000)]
    assert needs_compaction(msgs, "claude-opus-4-5", 1000) is True


def test_split_for_compaction():
    from agent.compaction import split_for_compaction
    msgs = _make_messages(50)
    # split_for_compaction(messages) returns (old, recent) with a fixed internal split
    old, recent = split_for_compaction(msgs)
    assert len(old) + len(recent) == 50
    assert old + recent == msgs
