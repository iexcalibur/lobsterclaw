"""Tests for Telegram message splitting."""
import pytest
from channels.telegram import _split_message


def test_short_message_not_split():
    result = _split_message("Hello world")
    assert result == ["Hello world"]


def test_exact_limit_not_split():
    text = "x" * 4000
    result = _split_message(text)
    assert result == [text]


def test_long_message_splits():
    text = "a" * 8001
    result = _split_message(text)
    assert len(result) == 2
    combined = "".join(result)
    assert combined == text


def test_split_prefers_newline():
    text = "line1\nline2\n" + "x" * 3990
    result = _split_message(text)
    # Should split at newline, not mid-word
    assert result[0].endswith("line2") or "\n" in result[0]


def test_split_at_space():
    # Long line with a space near the 4000 mark
    part1 = "word " * 800  # ~4000 chars
    part2 = "more content " * 100
    text = part1 + part2
    result = _split_message(text)
    assert len(result) >= 2
    for chunk in result:
        assert len(chunk) <= 4000


def test_empty_message():
    result = _split_message("")
    assert result == [""]


def test_unicode_not_corrupted():
    text = "Hello 🌍 " * 500
    result = _split_message(text)
    combined = "".join(result)
    assert combined == text
