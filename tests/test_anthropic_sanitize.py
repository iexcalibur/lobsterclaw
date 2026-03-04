from __future__ import annotations

import json
from types import SimpleNamespace

from agent.loop import (
    _response_blocks_to_anthropic_input_blocks,
    _sanitize_anthropic_messages,
)


def test_sanitize_anthropic_messages_strips_extra_fields():
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "hello", "parsed_output": {"foo": "bar"}},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "lookup",
                    "input": {"q": "arsenal"},
                    "parsed_output": {"ignored": True},
                },
                {"type": "thinking", "thinking": "internal"},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": {"ok": True}},
                {"type": "text", "text": {"value": "done", "parsed_output": {"x": 1}}},
            ],
        },
    ]

    sanitized = _sanitize_anthropic_messages(messages)
    assert len(sanitized) == 2

    assistant_blocks = sanitized[0]["content"]
    assert [b["type"] for b in assistant_blocks] == ["text", "tool_use"]
    assert assistant_blocks[0] == {"type": "text", "text": "hello"}
    assert assistant_blocks[1]["id"] == "toolu_1"
    assert "parsed_output" not in assistant_blocks[1]

    user_blocks = sanitized[1]["content"]
    assert user_blocks[0]["type"] == "tool_result"
    assert json.loads(user_blocks[0]["content"]) == {"ok": True}
    assert user_blocks[1] == {"type": "text", "text": "done"}


def test_response_blocks_to_input_blocks_ignores_non_request_fields():
    response_blocks = [
        SimpleNamespace(type="text", text="hi", parsed_output={"ignored": 1}),
        SimpleNamespace(type="tool_use", id="toolu_2", name="search", input='{"q":"test"}'),
        SimpleNamespace(type="thinking", thinking="hidden"),
    ]

    converted = _response_blocks_to_anthropic_input_blocks(response_blocks)
    assert converted == [
        {"type": "text", "text": "hi"},
        {
            "type": "tool_use",
            "id": "toolu_2",
            "name": "search",
            "input": {"q": "test"},
        },
    ]
