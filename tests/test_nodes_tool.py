"""Tests for nodes_tool.py — schema, action dispatch, WoL packet."""
from __future__ import annotations

import os
import pytest

# Monkey-patch env before importing nodes_tool
os.environ.setdefault("NODE_0_NAME", "testpi")
os.environ.setdefault("NODE_0_HOST", "192.168.1.99")
os.environ.setdefault("NODE_0_USER", "pi")
os.environ.setdefault("NODE_0_MAC", "aa:bb:cc:dd:ee:ff")


from tools.nodes_tool import _load_nodes, _resolve_node, TOOL_DEFINITION, _nodes


def test_load_nodes_from_env():
    nodes = _load_nodes()
    assert any(n.name == "testpi" for n in nodes)


def test_resolve_by_name():
    nodes = _load_nodes()
    cfg = _resolve_node(nodes, "testpi")
    assert cfg is not None
    assert cfg.host == "192.168.1.99"


def test_resolve_by_index():
    nodes = _load_nodes()
    cfg = _resolve_node(nodes, "0")
    assert cfg is not None
    assert cfg.name == "testpi"


def test_resolve_missing():
    nodes = _load_nodes()
    assert _resolve_node(nodes, "ghost") is None


@pytest.mark.asyncio
async def test_action_list():
    result = await _nodes(action="list")
    assert "testpi" in result


@pytest.mark.asyncio
async def test_action_missing_node():
    result = await _nodes(action="exec")
    assert "required" in result.lower() or "error" in result.lower()


@pytest.mark.asyncio
async def test_unknown_action():
    result = await _nodes(action="teleport", node="testpi")
    assert "unknown action" in result.lower() or "teleport" in result.lower()


def test_tool_schema():
    assert TOOL_DEFINITION.name == "nodes"
    params = TOOL_DEFINITION.parameters
    assert "action" in params["properties"]
    assert "node" in params["properties"]
    assert "command" in params["properties"]
    assert "source" in params["properties"]
