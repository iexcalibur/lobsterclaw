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


def build_registry(send_fn=None):
    """Build and return a ToolRegistry with all registered tools."""
    from tools.registry import ToolRegistry
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
        sessions_tool,
    )

    registry = ToolRegistry()

    # Web
    registry.register(web_fetch.TOOL_DEFINITION)
    registry.register(web_search.TOOL_DEFINITION)

    # Shell
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

    # Sessions / sub-agents / orchestration
    registry.register(sessions_tool.SESSIONS_SPAWN_TOOL)
    registry.register(sessions_tool.SESSIONS_LIST_TOOL)
    registry.register(sessions_tool.SESSIONS_HISTORY_TOOL)
    registry.register(sessions_tool.SESSIONS_SEND_TOOL)
    registry.register(sessions_tool.SESSION_STATUS_TOOL)
    registry.register(sessions_tool.SUBAGENTS_TOOL)
    registry.register(sessions_tool.AGENTS_LIST_TOOL)

    return registry


def main() -> None:
    logger.info("Starting PyGate...")

    cfg = get_config()
    logger.info("LLM provider: %s / model: %s", cfg.llm_provider, cfg.llm_model)
    logger.info("Owner Telegram ID: %s", cfg.telegram_owner_id)

    cfg.data_path.mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------------------
    # Build core components
    # ----------------------------------------------------------------

    from tools.approval import ApprovalGate
    from agent.history import HistoryManager
    from agent.loop import AgentLoop
    from agent.prompt import build_system_prompt
    from agent.heartbeat import HeartbeatRunner
    from agent.sessions import SessionStore, set_session_store
    from agent.subagent import SubagentManager, set_subagent_manager
    from scheduler.manager import CronManager
    from channels.telegram import TelegramChannel

    # Session store — SQLite-backed registry of all sessions
    session_store = SessionStore(cfg.sessions_db)
    set_session_store(session_store)
    logger.info("Session store initialized at %s", cfg.sessions_db)

    # Sub-agent manager
    subagent_mgr = SubagentManager()
    set_subagent_manager(subagent_mgr)

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
    # Wire lazy send functions
    # ----------------------------------------------------------------

    from tools import message_tool, media_tool, cron_tool

    message_tool.set_send_fn(telegram.send_message)
    media_tool.set_send_audio(telegram.send_audio)
    cron_tool.set_manager(cron_mgr)

    # ----------------------------------------------------------------
    # Sub-agent factory — creates a fresh registry for each sub-agent
    # (each sub-agent gets its own isolated tool registry + message_tool wired)
    # ----------------------------------------------------------------

    def subagent_registry_factory():
        sub_registry = build_registry()
        sub_registry.set_approval_gate(approval)
        message_tool.set_send_fn(telegram.send_message)
        return sub_registry

    subagent_mgr.configure(
        send_fn=telegram.send_message,
        agent_loop_factory=subagent_registry_factory,
    )

    # ----------------------------------------------------------------
    # Background agent runner (used by cron + heartbeat)
    # ----------------------------------------------------------------

    async def agent_for_bg(message: str, system_prompt: str | None = None) -> str:
        system = system_prompt or build_system_prompt(cfg, registry.get_names())
        return await agent.run([{"role": "user", "content": message}], system)

    cron_mgr.configure(
        send_fn=telegram.send_message,
        agent_fn=lambda msg: agent_for_bg(msg),
    )

    heartbeat.configure(
        agent_fn=agent_for_bg,
        send_fn=telegram.send_message,
    )

    # ----------------------------------------------------------------
    # Start schedulers
    # ----------------------------------------------------------------

    if cfg.cron_enabled:
        cron_mgr.start()
        logger.info("Cron scheduler started")
        if getattr(cfg, "heartbeat_enabled", False):
            heartbeat.start(cron_mgr.scheduler)
            logger.info("Heartbeat runner attached")

    # ----------------------------------------------------------------
    # Start Telegram bot (blocking)
    # ----------------------------------------------------------------

    logger.info(
        "Bot ready. Tools: %s",
        ", ".join(registry.get_names()),
    )
    telegram.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Shutting down.")
        sys.exit(0)
    except ValueError as e:
        logger.error("%s", e)
        sys.exit(1)
