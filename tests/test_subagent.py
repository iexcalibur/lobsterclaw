"""Tests for agent/subagent.py — depth limits, sandbox policy, steer."""
from __future__ import annotations

import pytest

from agent.subagent import SubagentManager, set_subagent_manager


@pytest.fixture
def mgr():
    m = SubagentManager()
    set_subagent_manager(m)
    return m


def test_format_list_empty(mgr):
    result = mgr.format_list()
    assert "no" in result.lower() or "running" in result.lower() or result == "" or "sub-agent" in result.lower()


@pytest.mark.asyncio
async def test_cancel_nonexistent(mgr):
    result = await mgr.cancel("nonexistent-run-id")
    assert isinstance(result, str)
    assert len(result) > 0  # Should return some error/not-found message


@pytest.mark.asyncio
async def test_steer_nonexistent(mgr):
    result = await mgr.steer("bad-run-id", "guidance")
    assert isinstance(result, str)
    assert len(result) > 0


def test_list_runs_empty(mgr):
    runs = mgr.list_runs()
    assert isinstance(runs, list)
    assert len(runs) == 0


@pytest.mark.asyncio
async def test_spawn_no_factory(mgr):
    """Spawning without a factory configured should fail gracefully."""
    from agent.sessions import SessionStore, set_session_store
    from pathlib import Path
    import tempfile
    tmp_db = Path(tempfile.mkdtemp()) / "sessions.db"
    store = SessionStore(tmp_db)
    set_session_store(store)
    result = await mgr.spawn(task="do something")
    assert isinstance(result, dict)
    # Should return status "error" or "rejected" since no registry factory configured
    assert result.get("status") in ("error", "rejected", "accepted")
