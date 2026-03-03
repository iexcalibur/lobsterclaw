"""Tests for memory tools and memory index."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


def _setup_memory_env(tmp_dir: Path):
    """Patch env and config for memory tests."""
    env = {
        "TELEGRAM_BOT_TOKEN": "test:tok",
        "TELEGRAM_OWNER_ID": "123",
        "ANTHROPIC_API_KEY": "sk-x",
        "LLM_PROVIDER": "anthropic",
        "MEMORY_ENABLED": "true",
        "MEMORY_DIR": str(tmp_dir / "memory"),
    }
    return env


@pytest.fixture
def tmp_workspace(tmp_path):
    (tmp_path / "memory").mkdir()
    (tmp_path / "MEMORY.md").write_text("# Main Memory\n\nUser likes Python and coffee.")
    (tmp_path / "memory" / "projects.md").write_text("# Projects\n\nWorking on PyGate chatbot.")
    return tmp_path


@pytest.mark.asyncio
async def test_memory_search_fts(tmp_workspace):
    env = _setup_memory_env(tmp_workspace)
    with patch.dict(os.environ, env, clear=True):
        import config as cfg_mod
        cfg_mod._config = None
        import importlib
        importlib.reload(cfg_mod)

        from agent.memory_index import MemoryIndex
        idx = MemoryIndex(
            memory_dir=tmp_workspace / "memory",
            memory_md=tmp_workspace / "MEMORY.md",
        )
        results = idx.search("Python coffee", limit=3)
        keys = [r.key for r in results]
        assert "MEMORY" in keys


@pytest.mark.asyncio
async def test_memory_search_no_results(tmp_workspace):
    from agent.memory_index import MemoryIndex
    idx = MemoryIndex(
        memory_dir=tmp_workspace / "memory",
        memory_md=tmp_workspace / "MEMORY.md",
    )
    results = idx.search("xyznomatch12345", limit=3)
    assert results == []


@pytest.mark.asyncio
async def test_memory_index_loads_subdir(tmp_workspace):
    from agent.memory_index import MemoryIndex
    idx = MemoryIndex(
        memory_dir=tmp_workspace / "memory",
        memory_md=tmp_workspace / "MEMORY.md",
    )
    results = idx.search("PyGate chatbot", limit=3)
    keys = [r.key for r in results]
    assert "projects" in keys


@pytest.mark.asyncio
async def test_memory_search_fts_sanitizes_special_chars(tmp_workspace):
    """FTS should not error on special chars in query."""
    from agent.memory_index import MemoryIndex
    idx = MemoryIndex(
        memory_dir=tmp_workspace / "memory",
        memory_md=tmp_workspace / "MEMORY.md",
    )
    # Query with chars that break FTS5 syntax
    results = idx.search("Python (coffee) & more!", limit=5)
    # Should not raise, even if no results


@pytest.mark.asyncio
async def test_memory_write_creates_file(tmp_path):
    env = {
        "TELEGRAM_BOT_TOKEN": "tok",
        "TELEGRAM_OWNER_ID": "1",
        "ANTHROPIC_API_KEY": "sk",
        "MEMORY_ENABLED": "true",
        "MEMORY_DIR": str(tmp_path / "memory"),
    }
    with patch.dict(os.environ, env, clear=True):
        import config as cfg_mod
        cfg_mod._config = None

        # Patch the path resolution in memory_tool
        with patch("tools.memory_tool._get_memory_dir", return_value=tmp_path / "memory"):
            (tmp_path / "memory").mkdir(exist_ok=True)
            from tools.memory_tool import _memory_write
            result = await _memory_write("test_key", "Test content here")
            assert "saved" in result.lower() or "memory" in result.lower()
            assert (tmp_path / "memory" / "test_key.md").exists()


@pytest.mark.asyncio
async def test_memory_get_existing(tmp_workspace):
    env = {
        "TELEGRAM_BOT_TOKEN": "tok",
        "TELEGRAM_OWNER_ID": "1",
        "ANTHROPIC_API_KEY": "sk",
        "MEMORY_ENABLED": "true",
        "MEMORY_DIR": str(tmp_workspace / "memory"),
    }
    with patch.dict(os.environ, env, clear=True):
        import config as cfg_mod
        cfg_mod._config = None
        with patch("tools.memory_tool._get_memory_dir", return_value=tmp_workspace / "memory"):
            from tools.memory_tool import _memory_get
            result = await _memory_get("projects")
            assert "PyGate" in result


@pytest.mark.asyncio
async def test_memory_list(tmp_workspace):
    env = {
        "TELEGRAM_BOT_TOKEN": "tok",
        "TELEGRAM_OWNER_ID": "1",
        "ANTHROPIC_API_KEY": "sk",
        "MEMORY_ENABLED": "true",
        "MEMORY_DIR": str(tmp_workspace / "memory"),
    }
    with patch.dict(os.environ, env, clear=True):
        import config as cfg_mod
        cfg_mod._config = None
        with patch("tools.memory_tool._get_memory_dir", return_value=tmp_workspace / "memory"):
            with patch("tools.memory_tool._get_memory_md_path", return_value=tmp_workspace / "MEMORY.md"):
                from tools.memory_tool import _memory_list
                result = await _memory_list()
                assert "projects" in result
