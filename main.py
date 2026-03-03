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
    """Build and return a ToolRegistry with all tools registered and policy applied."""
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
        gateway_tool,
        nodes_tool,
        canvas_tool,
        channel_stubs,
    )

    registry = ToolRegistry()

    # Web
    registry.register(web_fetch.TOOL_DEFINITION)
    registry.register(web_search.TOOL_DEFINITION)

    # Shell
    registry.register(exec_tool.TOOL_DEFINITION)
    registry.register(exec_tool.PROCESS_TOOL_DEFINITION)

    # File system (full suite)
    registry.register(filesystem.READ_TOOL)
    registry.register(filesystem.WRITE_TOOL)
    registry.register(filesystem.EDIT_TOOL)
    registry.register(filesystem.APPLY_PATCH_TOOL)
    registry.register(filesystem.LIST_DIR_TOOL)
    registry.register(filesystem.GLOB_TOOL)
    registry.register(filesystem.DELETE_TOOL)
    registry.register(filesystem.MOVE_TOOL)

    # Browser
    registry.register(browser_tool.TOOL_DEFINITION)

    # Memory (full suite)
    registry.register(memory_tool.MEMORY_SEARCH_TOOL)
    registry.register(memory_tool.MEMORY_GET_TOOL)
    registry.register(memory_tool.MEMORY_WRITE_TOOL)
    registry.register(memory_tool.MEMORY_LIST_TOOL)
    registry.register(memory_tool.MEMORY_DELETE_TOOL)

    # Media
    registry.register(media_tool.PDF_TOOL)
    registry.register(media_tool.IMAGE_TOOL)
    registry.register(media_tool.TTS_TOOL)

    # Messaging + scheduling + gateway
    registry.register(message_tool.TOOL_DEFINITION)
    registry.register(cron_tool.TOOL_DEFINITION)
    registry.register(gateway_tool.TOOL_DEFINITION)

    # Nodes (remote device management)
    registry.register(nodes_tool.TOOL_DEFINITION)

    # Canvas (interactive UI surfaces)
    registry.register(canvas_tool.TOOL_DEFINITION)

    # Multi-channel stubs (Discord, Slack, WhatsApp — schema-compatible, disabled by default)
    registry.register(channel_stubs.DISCORD_TOOL)
    registry.register(channel_stubs.SLACK_TOOL)
    registry.register(channel_stubs.WHATSAPP_TOOL)

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
    # Wire all lazy send / action functions into tools
    # ----------------------------------------------------------------

    from tools import message_tool, media_tool, cron_tool, browser_tool

    # Text message
    message_tool.set_send_fn(telegram.send_message)

    # Extended Telegram actions for message tool
    message_tool.set_telegram_fns(
        send_photo=telegram.send_photo,
        send_document=telegram.send_document,
        send_sticker=telegram.send_sticker,
        edit=telegram.edit_message,
        delete=telegram.delete_message,
        react=telegram.react_to_message,
        send_buttons=telegram.send_with_buttons,
        create_forum_topic=telegram.create_forum_topic,
    )

    # Wire approval gate for canvas tool (so it can send photos via Telegram)
    canvas_tool  # imported above; send fns shared via message_tool globals

    # Audio + TTS
    media_tool.set_send_audio(telegram.send_audio)

    # Browser screenshot → Telegram photo; PDF → Telegram document
    browser_tool.set_send_photo_fn(telegram.send_photo)
    browser_tool.set_send_document_fn(telegram.send_document)

    # Cron manager
    cron_tool.set_manager(cron_mgr)

    # ----------------------------------------------------------------
    # Sub-agent factory — isolated tool registry + all send fns wired
    # ----------------------------------------------------------------

    def subagent_registry_factory():
        sub_registry = build_registry()
        sub_registry.set_approval_gate(approval)
        # Re-wire send functions into the sub-agent's tool modules
        # Note: module-level globals are shared, so setting them here is safe
        message_tool.set_send_fn(telegram.send_message)
        return sub_registry

    subagent_mgr.configure(
        send_fn=telegram.send_message,
        agent_loop_factory=subagent_registry_factory,
    )

    # ----------------------------------------------------------------
    # Background agent runner (cron + heartbeat)
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
        if cfg.heartbeat_enabled:
            heartbeat.start(cron_mgr.scheduler)
            logger.info("Heartbeat runner attached (schedule: %s)", cfg.heartbeat_schedule)
    elif cfg.heartbeat_enabled:
        # Heartbeat without cron: start its own scheduler
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        hb_scheduler = AsyncIOScheduler()
        hb_scheduler.start()
        heartbeat.start(hb_scheduler)
        logger.info("Heartbeat-only scheduler started")

    # ----------------------------------------------------------------
    # Start Telegram bot (blocking)
    # ----------------------------------------------------------------

    # ----------------------------------------------------------------
    # Load plugins and register their tools
    # ----------------------------------------------------------------

    from tools.plugin_loader import register_plugins
    plugin_tools = register_plugins(registry)
    if plugin_tools:
        logger.info("Loaded plugin tools: %s", ", ".join(plugin_tools))

    # ----------------------------------------------------------------
    # Start Telegram bot (blocking)
    # ----------------------------------------------------------------

    all_tools = registry.get_names()
    logger.info("Bot ready. %d tools: %s", len(all_tools), ", ".join(all_tools))
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
