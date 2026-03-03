"""Tests for the plugin loader."""
import tempfile
from pathlib import Path

import pytest
from tools.plugin_loader import _load_plugin, load_all_plugins, list_plugins


def _make_plugin(base: Path, name: str, tool_names: list[str]) -> Path:
    plugin_dir = base / name
    plugin_dir.mkdir(parents=True)

    tools_code = "\n".join([
        "from tools.registry import ToolDefinition",
        "",
        "async def _noop(**kw): return 'ok'",
        "",
        "TOOLS = [",
    ] + [
        f"    ToolDefinition(name='{t}', description='Test', parameters={{'type': 'object', 'properties': {{}}, 'required': []}}, fn=lambda **kw: _noop(**kw)),"
        for t in tool_names
    ] + ["]"])

    (plugin_dir / "__init__.py").write_text(tools_code)
    return plugin_dir


def test_load_missing_plugin_file(tmp_path):
    plugin_dir = tmp_path / "empty-plugin"
    plugin_dir.mkdir()
    result = _load_plugin(plugin_dir)
    assert result is None


def test_load_valid_plugin(tmp_path):
    _make_plugin(tmp_path, "weather", ["get_weather"])
    plugin_dir = tmp_path / "weather"
    result = _load_plugin(plugin_dir)
    assert result is not None
    assert result.name == "weather"
    assert len(result.tools) == 1
    assert result.tools[0].name == "get_weather"


def test_load_plugin_with_metadata(tmp_path):
    import json
    plugin_dir = tmp_path / "meta-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text(
        "from tools.registry import ToolDefinition\nasync def _f(**k): return 'x'\nTOOLS=[ToolDefinition(name='t',description='d',parameters={'type':'object','properties':{},'required':[]},fn=lambda **k:_f(**k))]"
    )
    (plugin_dir / "plugin.json").write_text(json.dumps({
        "name": "meta-plugin",
        "description": "A plugin with metadata",
        "version": "2.0.0",
    }))
    result = _load_plugin(plugin_dir)
    assert result is not None
    assert result.description == "A plugin with metadata"
    assert result.version == "2.0.0"


def test_load_plugin_with_syntax_error(tmp_path):
    plugin_dir = tmp_path / "bad-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text("this is not valid python !!!")
    result = _load_plugin(plugin_dir)
    assert result is None  # Should gracefully return None on error


def test_multiple_tools_in_plugin(tmp_path):
    _make_plugin(tmp_path, "multi", ["tool_a", "tool_b", "tool_c"])
    result = _load_plugin(tmp_path / "multi")
    assert result is not None
    assert len(result.tools) == 3
    tool_names = {t.name for t in result.tools}
    assert tool_names == {"tool_a", "tool_b", "tool_c"}
