"""
PyGate — Personal AI Assistant
Entry point: wires all components together and starts the Telegram bot.

Usage:
    cp .env.example .env        # fill in your keys
    pip install -r requirements.txt
    playwright install chromium  # only needed if BROWSER_ENABLED=true
    python main.py
"""

from __future__ import annotations

import logging
import sys

from config import get_config

# Configure logging before anything else
cfg = get_config()
logging.basicConfig(
    level=getattr(logging, cfg.log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def build_registry():
    from tools.registry import ToolRegistry, ToolDefinition
    from tools import (
        web_fetch,
        web_search,
        exec_tool,
        filesystem,
        browser_tool,
        memory_tool,
        media_tool,
        message_tool,
        cron_tool,
    )

    registry = ToolRegistry()

    # Web
    registry.register(web_fetch.TOOL_DEFINITION)
    registry.register(web_search.TOOL_DEFINITION)

    # Shell (only registered if exec_enabled; policy also blocks via tools_deny)
    registry.register(exec_tool.TOOL_DEFINITION)
    registry.register(exec_tool.PROCESS_TOOL_DEFINITION)

    # File system
    registry.register(filesystem.READ_TOOL)
    registry.register(filesystem.WRITE_TOOL)
    registry.register(filesystem.EDIT_TOOL)
    registry.register(filesystem.APPLY_PATCH_TOOL)

    # Browser
    registry.register(browser_tool.TOOL_DEFINITION)

    # Memory
    registry.register(memory_tool.MEMORY_SEARCH_TOOL)
    registry.register(memory_tool.MEMORY_GET_TOOL)
    registry.register(memory_tool.MEMORY_WRITE_TOOL)

    # Media
    registry.register(media_tool.PDF_TOOL)
    registry.register(media_tool.IMAGE_TOOL)
    registry.register(media_tool.TTS_TOOL)

    # Messaging + scheduling
    registry.register(message_tool.TOOL_DEFINITION)
    registry.register(cron_tool.TOOL_DEFINITION)

    return registry


def main() -> None:
    logger.info("Starting PyGate...")

    # Validate config early
    cfg = get_config()
    logger.info("LLM provider: %s / model: %s", cfg.llm_provider, cfg.llm_model)
    logger.info("Owner Telegram ID: %s", cfg.telegram_owner_id)

    # Ensure data directories exist
    cfg.data_path.mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------------------
    # Build components
    # ----------------------------------------------------------------

    from tools.approval import ApprovalGate
    from agent.history import HistoryManager
    from agent.loop import AgentLoop
    from agent.prompt import build_system_prompt
    from agent.heartbeat import HeartbeatRunner
    from scheduler.manager import CronManager
    from channels.telegram import TelegramChannel

    approval = ApprovalGate()
    registry = build_registry()
    registry.set_approval_gate(approval)

    history = HistoryManager()
    agent = AgentLoop(registry)

    cron_mgr = CronManager()
    heartbeat = HeartbeatRunner()

    telegram = TelegramChannel(
        agent=agent,
        history=history,
        approval=approval,
        build_prompt=build_system_prompt,
    )

    # ----------------------------------------------------------------
    # Wire lazy send functions (tools that need to push to Telegram)
    # ----------------------------------------------------------------

    from tools import message_tool, media_tool, cron_tool

    message_tool.set_send_fn(telegram.send_message)
    media_tool.set_send_audio(telegram.send_audio)
    cron_tool.set_manager(cron_mgr)

    # Shared agent runner for background tasks (cron + heartbeat)
    async def agent_for_bg(message: str, system_prompt: str | None = None) -> str:
        system = system_prompt or build_system_prompt(cfg, registry.get_names())
        return await agent.run([{"role": "user", "content": message}], system)

    # Cron fires agent loop so reminders have full tool access
    cron_mgr.configure(
        send_fn=telegram.send_message,
        agent_fn=lambda msg: agent_for_bg(msg),
    )

    # Heartbeat fires agent loop with HEARTBEAT.md context
    heartbeat.configure(
        agent_fn=agent_for_bg,
        send_fn=telegram.send_message,
    )

    # Start cron + heartbeat on a shared scheduler
    if cfg.cron_enabled:
        cron_mgr.start()
        logger.info("Cron scheduler started")
        if getattr(cfg, "heartbeat_enabled", False):
            heartbeat.start(cron_mgr.scheduler)
            logger.info("Heartbeat runner attached")

    # ----------------------------------------------------------------
    # Start Telegram bot (blocking)
    # ----------------------------------------------------------------

    logger.info("Bot is running. Send a message to start.")
    telegram.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Shutting down.")
        sys.exit(0)
    except ValueError as e:
        # Config validation errors
        logger.error("%s", e)
        sys.exit(1)
