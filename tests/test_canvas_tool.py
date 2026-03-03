"""Tests for canvas_tool.py — CRUD lifecycle, table render, schema."""
from __future__ import annotations

import json
import pytest

from tools.canvas_tool import (
    _canvas,
    _canvas_store,
    _render_table,
    TOOL_DEFINITION,
)


@pytest.fixture(autouse=True)
def clear_store():
    _canvas_store.clear()
    yield
    _canvas_store.clear()


@pytest.mark.asyncio
async def test_open_canvas():
    result = await _canvas(action="open", canvas_type="text", title="Test")
    assert "opened" in result.lower()
    cid = result.split("ID: ")[1].split("\n")[0].strip()
    assert cid in _canvas_store


@pytest.mark.asyncio
async def test_list_empty():
    result = await _canvas(action="list")
    assert "no open" in result.lower()


@pytest.mark.asyncio
async def test_render_and_query():
    await _canvas(action="open", canvas_id="c1", canvas_type="text", title="T")
    await _canvas(action="render", canvas_id="c1", content="hello world")
    result = await _canvas(action="query", canvas_id="c1")
    assert "hello world" in result


@pytest.mark.asyncio
async def test_update_append():
    await _canvas(action="open", canvas_id="c2", canvas_type="text", title="T")
    await _canvas(action="render", canvas_id="c2", content="line1")
    await _canvas(action="update", canvas_id="c2", content="line2", mode="append")
    result = await _canvas(action="query", canvas_id="c2")
    assert "line1" in result
    assert "line2" in result


@pytest.mark.asyncio
async def test_clear():
    await _canvas(action="open", canvas_id="c3", canvas_type="text", title="T")
    await _canvas(action="render", canvas_id="c3", content="data")
    await _canvas(action="clear", canvas_id="c3")
    assert _canvas_store["c3"].content == ""


@pytest.mark.asyncio
async def test_close():
    await _canvas(action="open", canvas_id="c4", canvas_type="text", title="T")
    result = await _canvas(action="close", canvas_id="c4")
    assert "closed" in result.lower()
    assert not _canvas_store["c4"].open


@pytest.mark.asyncio
async def test_set_title():
    await _canvas(action="open", canvas_id="c5", canvas_type="text", title="Old")
    await _canvas(action="set", canvas_id="c5", property="title", value="New")
    assert _canvas_store["c5"].title == "New"


def test_render_table_json():
    data = json.dumps([{"name": "Alice", "score": "99"}, {"name": "Bob", "score": "85"}])
    table = _render_table(data)
    assert "Alice" in table
    assert "score" in table.lower() or "Score" in table


def test_render_table_fallback():
    result = _render_table("not json")
    assert result == "not json"


def test_tool_schema():
    assert TOOL_DEFINITION.name == "canvas"
    props = TOOL_DEFINITION.parameters["properties"]
    assert "action" in props
    assert "canvas_id" in props
    assert "content" in props
    assert "canvas_type" in props
