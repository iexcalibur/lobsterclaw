"""Tests for the approval gate."""
import asyncio
import pytest
from unittest.mock import AsyncMock
from tools.approval import ApprovalGate


@pytest.mark.asyncio
async def test_approval_auto_deny_without_send_fn():
    gate = ApprovalGate()
    result = await gate.request("exec", {"command": "rm -rf /"})
    assert result is False


@pytest.mark.asyncio
async def test_approval_resolves_true():
    gate = ApprovalGate()
    sent_messages = []

    async def mock_send(text, request_id):
        sent_messages.append((text, request_id))
        # Auto-approve in the background
        asyncio.get_running_loop().call_soon(lambda: gate.resolve(request_id, True))

    gate.configure(send_fn=mock_send, timeout=5)
    result = await gate.request("exec", {"command": "ls"})
    assert result is True
    assert len(sent_messages) == 1


@pytest.mark.asyncio
async def test_approval_resolves_false():
    gate = ApprovalGate()

    async def mock_send(text, request_id):
        asyncio.get_running_loop().call_soon(lambda: gate.resolve(request_id, False))

    gate.configure(send_fn=mock_send, timeout=5)
    result = await gate.request("browser", {"action": "navigate"})
    assert result is False


@pytest.mark.asyncio
async def test_approval_timeout():
    gate = ApprovalGate()
    called = []

    async def mock_send(text, request_id):
        called.append(request_id)
        # Don't resolve — let it timeout

    gate.configure(send_fn=mock_send, timeout=0.1)
    result = await gate.request("exec", {"command": "test"})
    assert result is False
    assert len(called) == 1


def test_resolve_unknown_id():
    gate = ApprovalGate()
    # Should return False gracefully for unknown request_id
    result = gate.resolve("nonexistent", True)
    assert result is False
