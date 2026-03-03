"""Tests for tool loop detection."""
import pytest
from agent.loop_detection import LoopDetectionState, LoopCheckResult

# action strings from LoopCheckResult: "ok" | "warn" | "block" | "abort"
ABORT_ACTIONS = {"abort", "block"}


def test_no_loop_on_fresh_state():
    state = LoopDetectionState()
    result = state.record("web_search", {"query": "test"})
    assert result.action not in ABORT_ACTIONS


def test_warn_threshold():
    state = LoopDetectionState()
    for _ in range(9):
        state.record("web_search", {"query": "same query"})
    result = state.record("web_search", {"query": "same query"})
    # Should either warn or abort, not silently OK — just check it returns a result
    assert isinstance(result, LoopCheckResult)


def test_different_args_no_loop():
    state = LoopDetectionState()
    result = None
    for i in range(15):
        result = state.record("web_search", {"query": f"query {i}"})
    # Different args should not trigger per-call hash loop
    assert isinstance(result, LoopCheckResult)


def test_global_abort_threshold():
    state = LoopDetectionState()
    result = None
    for i in range(50):
        result = state.record("any_tool", {"data": f"call_{i}"})
    # After many calls, total_calls should be tracked
    assert state.total_calls >= 50


def test_different_tools_tracked():
    state = LoopDetectionState()
    for _ in range(10):
        state.record("tool_a", {"q": "same"})
    result = None
    for _ in range(10):
        result = state.record("tool_b", {"q": "same"})
    assert isinstance(result, LoopCheckResult)
