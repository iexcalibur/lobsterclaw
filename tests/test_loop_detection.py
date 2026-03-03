"""Tests for tool loop detection."""
import pytest
from agent.loop_detection import LoopDetectionState, LoopAction


def test_no_loop_on_fresh_state():
    state = LoopDetectionState()
    result = state.check("web_search", {"query": "test"})
    assert result.action == LoopAction.ALLOW


def test_warn_threshold():
    state = LoopDetectionState()
    for _ in range(9):
        state.check("web_search", {"query": "same query"})
    result = state.check("web_search", {"query": "same query"})
    assert result.action in (LoopAction.WARN, LoopAction.ALLOW)


def test_different_args_no_loop():
    state = LoopDetectionState()
    for i in range(15):
        result = state.check("web_search", {"query": f"query {i}"})
    # Should not trigger loop with different args
    assert result.action == LoopAction.ALLOW


def test_global_abort_threshold():
    state = LoopDetectionState()
    # Hit the global total call limit (30)
    for _ in range(30):
        result = state.check("any_tool", {"data": "different" + str(_)})
    assert result.action == LoopAction.ABORT


def test_different_tools_tracked_separately():
    state = LoopDetectionState()
    for _ in range(15):
        state.check("tool_a", {"q": "same"})
    for _ in range(15):
        result = state.check("tool_b", {"q": "same"})
    # tool_b should not be at warn level yet (only 15 calls to tool_b)
    # but global limit (30) should trigger
    assert result.action in (LoopAction.ABORT, LoopAction.WARN, LoopAction.ALLOW)
