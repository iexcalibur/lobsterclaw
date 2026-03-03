"""Tests for web_fetch.py and web_search.py — schema shape and parameter parity."""
from __future__ import annotations

import pytest

from tools.web_fetch import TOOL_DEFINITION as WEB_FETCH_TOOL
from tools.web_search import TOOL_DEFINITION as WEB_SEARCH_TOOL


# ------------------------------------------------------------------
# web_fetch schema parity (OpenClaw web-fetch.ts)
# ------------------------------------------------------------------

def test_web_fetch_has_render_js():
    props = WEB_FETCH_TOOL.parameters["properties"]
    assert "render_js" in props


def test_web_fetch_has_selector():
    props = WEB_FETCH_TOOL.parameters["properties"]
    assert "selector" in props


def test_web_fetch_has_max_chars():
    props = WEB_FETCH_TOOL.parameters["properties"]
    assert "max_chars" in props


def test_web_fetch_has_headers():
    props = WEB_FETCH_TOOL.parameters["properties"]
    assert "headers" in props


def test_web_fetch_has_timeout():
    props = WEB_FETCH_TOOL.parameters["properties"]
    assert "timeout" in props


def test_web_fetch_url_required():
    required = WEB_FETCH_TOOL.parameters.get("required", [])
    assert "url" in required


# ------------------------------------------------------------------
# web_search schema parity (OpenClaw web-search.ts)
# ------------------------------------------------------------------

def test_web_search_has_time_range():
    props = WEB_SEARCH_TOOL.parameters["properties"]
    assert "time_range" in props


def test_web_search_has_country():
    props = WEB_SEARCH_TOOL.parameters["properties"]
    assert "country" in props


def test_web_search_has_language():
    props = WEB_SEARCH_TOOL.parameters["properties"]
    assert "language" in props


def test_web_search_has_safe_search():
    props = WEB_SEARCH_TOOL.parameters["properties"]
    assert "safe_search" in props


def test_web_search_query_required():
    required = WEB_SEARCH_TOOL.parameters.get("required", [])
    assert "query" in required


def test_web_search_tool_name():
    assert WEB_SEARCH_TOOL.name == "web_search"


def test_web_fetch_tool_name():
    assert WEB_FETCH_TOOL.name == "web_fetch"
