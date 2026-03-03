"""
Core agent loop — sends messages to the LLM, handles tool calls, loops until done.

Integrates:
  - Tool loop detection (agent/loop_detection.py)
  - Context window compaction (agent/compaction.py)
  - Tool result truncation (tools/registry.py)
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import anthropic
import openai

from agent.compaction import compact_messages, needs_compaction
from agent.loop_detection import LoopDetectionState
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

    async def run(
        self,
        messages: list[dict],
        system_prompt: str,
        session_id: str | None = None,
    ) -> str:
        """
        Run the agent loop on the given message history and return the final text reply.
        session_id is used for logging/tracing only.
        """
        # Compact history if it's grown too large for the context window
        if needs_compaction(messages, self.cfg.llm_model, self.cfg.llm_max_tokens):
            messages = await compact_messages(
                messages=messages,
                model=self.cfg.llm_model,
                api_key=self.cfg.anthropic_api_key
                if self.cfg.llm_provider == "anthropic"
                else self.cfg.openai_api_key,
                provider=self.cfg.llm_provider,
            )

        if self.cfg.llm_provider == "anthropic":
            return await self._run_anthropic(messages, system_prompt)
        return await self._run_openai(messages, system_prompt)

    # ------------------------------------------------------------------
    # Anthropic
    # ------------------------------------------------------------------

    async def _run_anthropic(self, messages: list[dict], system_prompt: str) -> str:
        tools = self.registry.get_anthropic_tools()
        working = list(messages)
        loop_detector = LoopDetectionState()

        for iteration in range(self.cfg.max_tool_iterations):
            # Re-check compaction on each iteration — tool results can balloon history
            if needs_compaction(working, self.cfg.llm_model, self.cfg.llm_max_tokens):
                working = await compact_messages(
                    messages=working,
                    model=self.cfg.llm_model,
                    api_key=self.cfg.anthropic_api_key,
                    provider="anthropic",
                )

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
                working.append({"role": "assistant", "content": [b.model_dump() for b in response.content]})

                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    # Loop detection check before executing
                    check = loop_detector.record(block.name, block.input)
                    if check.action == "abort":
                        logger.error("Aborting agent turn due to loop: %s", check.message)
                        return check.message
                    if check.action == "block":
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": check.message,
                        })
                        continue

                    logger.info("Tool call [%d/%d]: %s", iteration + 1, self.cfg.max_tool_iterations, block.name)
                    # Inject _session_id so session-aware tools (sessions_spawn, session_status) know their context
                    args_with_ctx = dict(block.input)
                    if session_id:
                        args_with_ctx.setdefault("_session_id", session_id)
                    result = await self.registry.execute(block.name, args_with_ctx)

                    # Append loop warning as a prefix to the result if in warn state
                    if check.action == "warn":
                        result = f"⚠️ {check.message}\n\n{result}"

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

                working.append({"role": "user", "content": tool_results})
            else:
                logger.warning("Unexpected stop_reason: %s", response.stop_reason)
                break

        logger.warning("Max tool iterations (%d) reached", self.cfg.max_tool_iterations)
        return "I've reached the tool call limit for this turn. Here's what I accomplished so far."

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------

    async def _run_openai(self, messages: list[dict], system_prompt: str) -> str:
        tools = self.registry.get_openai_tools()
        working = [{"role": "system", "content": system_prompt}] + list(messages)
        loop_detector = LoopDetectionState()

        for iteration in range(self.cfg.max_tool_iterations):
            if needs_compaction(working, self.cfg.llm_model, self.cfg.llm_max_tokens):
                working = await compact_messages(
                    messages=working,
                    model=self.cfg.llm_model,
                    api_key=self.cfg.openai_api_key,
                    provider="openai",
                )

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

                    check = loop_detector.record(tool_call.function.name, args)
                    if check.action == "abort":
                        logger.error("Aborting agent turn due to loop: %s", check.message)
                        return check.message
                    if check.action == "block":
                        working.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": check.message,
                        })
                        continue

                    logger.info("Tool call [%d/%d]: %s", iteration + 1, self.cfg.max_tool_iterations, tool_call.function.name)
                    args_with_ctx = dict(args)
                    if session_id:
                        args_with_ctx.setdefault("_session_id", session_id)
                    result = await self.registry.execute(tool_call.function.name, args_with_ctx)

                    if check.action == "warn":
                        result = f"⚠️ {check.message}\n\n{result}"

                    working.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": str(result),
                    })
            else:
                logger.warning("Unexpected finish_reason: %s", choice.finish_reason)
                break

        logger.warning("Max tool iterations (%d) reached", self.cfg.max_tool_iterations)
        return "I've reached the tool call limit for this turn. Here's what I accomplished so far."
