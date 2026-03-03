from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import anthropic
import openai

from config import get_config

if TYPE_CHECKING:
    from tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class AgentLoop:
    """Core agent loop — sends messages to the LLM, handles tool calls, loops until done."""

    def __init__(self, registry: "ToolRegistry") -> None:
        self.cfg = get_config()
        self.registry = registry

        if self.cfg.llm_provider == "anthropic":
            self._anthropic = anthropic.AsyncAnthropic(api_key=self.cfg.anthropic_api_key)
            self._openai = None
        else:
            self._anthropic = None
            self._openai = openai.AsyncOpenAI(api_key=self.cfg.openai_api_key)

    async def run(self, messages: list[dict], system_prompt: str) -> str:
        """Run the agent loop on the given message history and return the final text reply."""
        if self.cfg.llm_provider == "anthropic":
            return await self._run_anthropic(messages, system_prompt)
        return await self._run_openai(messages, system_prompt)

    # ------------------------------------------------------------------
    # Anthropic
    # ------------------------------------------------------------------

    async def _run_anthropic(self, messages: list[dict], system_prompt: str) -> str:
        tools = self.registry.get_anthropic_tools()
        working = list(messages)

        for iteration in range(self.cfg.max_tool_iterations):
            kwargs: dict = {
                "model": self.cfg.llm_model,
                "max_tokens": self.cfg.llm_max_tokens,
                "system": system_prompt,
                "messages": working,
            }
            if tools:
                kwargs["tools"] = tools

            response = await self._anthropic.messages.create(**kwargs)

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        return block.text
                return ""

            if response.stop_reason == "tool_use":
                # Append assistant message with tool calls
                working.append({"role": "assistant", "content": [b.model_dump() for b in response.content]})

                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        logger.info("Tool call: %s %s", block.name, block.input)
                        result = await self.registry.execute(block.name, block.input)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": str(result),
                            }
                        )

                working.append({"role": "user", "content": tool_results})
            else:
                # Unexpected stop reason
                logger.warning("Unexpected stop_reason: %s", response.stop_reason)
                break

        return "I've completed the requested tasks."

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------

    async def _run_openai(self, messages: list[dict], system_prompt: str) -> str:
        tools = self.registry.get_openai_tools()
        working = [{"role": "system", "content": system_prompt}] + list(messages)

        for iteration in range(self.cfg.max_tool_iterations):
            kwargs: dict = {"model": self.cfg.llm_model, "messages": working}
            if tools:
                kwargs["tools"] = tools

            response = await self._openai.chat.completions.create(**kwargs)
            choice = response.choices[0]

            if choice.finish_reason == "stop":
                return choice.message.content or ""

            if choice.finish_reason == "tool_calls":
                working.append(choice.message.model_dump(exclude_unset=True))

                for tool_call in choice.message.tool_calls:
                    args = json.loads(tool_call.function.arguments)
                    logger.info("Tool call: %s %s", tool_call.function.name, args)
                    result = await self.registry.execute(tool_call.function.name, args)
                    working.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": str(result),
                        }
                    )
            else:
                logger.warning("Unexpected finish_reason: %s", choice.finish_reason)
                break

        return "I've completed the requested tasks."
