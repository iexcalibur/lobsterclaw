from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from config import get_config


@dataclass
class Message:
    role: Literal["user", "assistant"]
    content: str


class HistoryManager:
    """Stores per-user conversation history (user/assistant turns only).
    Tool calls are ephemeral within an agent run and not persisted here.
    """

    def __init__(self) -> None:
        self._cfg = get_config()
        self._histories: dict[int, list[Message]] = {}

    def add(self, user_id: int, role: Literal["user", "assistant"], content: str) -> None:
        if user_id not in self._histories:
            self._histories[user_id] = []
        self._histories[user_id].append(Message(role=role, content=content))
        # Trim to max to avoid unbounded memory growth
        max_msgs = self._cfg.max_history_messages
        if len(self._histories[user_id]) > max_msgs:
            self._histories[user_id] = self._histories[user_id][-max_msgs:]

    def get_for_llm(self, user_id: int) -> list[dict]:
        """Return messages in the format expected by LLM providers."""
        msgs = self._histories.get(user_id, [])
        return [{"role": m.role, "content": m.content} for m in msgs]

    def clear(self, user_id: int) -> None:
        self._histories[user_id] = []

    def last_n(self, user_id: int, n: int) -> list[dict]:
        msgs = self._histories.get(user_id, [])
        return [{"role": m.role, "content": m.content} for m in msgs[-n:]]
