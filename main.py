"""
LobsterClaw — Personal AI Assistant
Entry point: wires all components together and starts the Telegram bot(s).

Usage:
    cp .env.example .env        # fill in your keys
    pip install -r requirements.txt
    playwright install chromium  # only needed if BROWSER_ENABLED=true or CANVAS_HOST_ENABLED=true
    python main.py

Canvas host:
    Set CANVAS_HOST_ENABLED=true to start the FastAPI canvas host at CANVAS_HOST_PORT (default 7681).
    Then run `cd canvas_frontend && npm install && npm run dev` to launch the Next.js UI.
    Or build once with `npm run build` — the host serves the static output automatically.

Multi-account:
    Set TELEGRAM_ACCOUNTS to a JSON array of account objects, e.g.:
    [{"label": "main", "token": "...", "owner_id": 123, "dm_policy": "owner"},
     {"label": "group", "token": "...", "owner_id": 123, "group_policy": "open"}]
"""

from __future__ import annotations

import asyncio
import logging
import signal
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

# Reduce noisy transport logs unless explicitly debugging.
if cfg.log_level.upper() != "DEBUG":
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def build_registry(light: bool = False):
    """Build and return a ToolRegistry with all tools registered and policy applied.

    light=True skips browser, canvas, and multi-channel stubs — for fast cron/heartbeat runs.
    """
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

    # Browser (skipped in light mode — Playwright startup is expensive)
    if not light:
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

    # Canvas (skipped in light mode)
    if not light:
        registry.register(canvas_tool.TOOL_DEFINITION)

    # Multi-channel stubs (skipped in light mode)
    if not light:
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


def _wire_channel(telegram, registry, approval, cron_mgr, build_system_prompt):
    """Wire all lazy send / action functions into tools for a given TelegramChannel."""
    from tools import message_tool, media_tool, cron_tool, browser_tool

    # Set channel reference for target resolution and sticker cache
    message_tool.set_channel_ref(telegram)

    # Text message (owner send)
    message_tool.set_send_fn(telegram.send_message)

    # Extended Telegram actions
    message_tool.set_telegram_fns(
        send_photo=telegram.send_photo,
        send_document=telegram.send_document,
        send_sticker=telegram.send_sticker,
        edit=telegram.edit_message,
        delete=telegram.delete_message,
        react=telegram.react_to_message,
        send_buttons=telegram.send_with_buttons,
        create_forum_topic=telegram.create_forum_topic,
        send_to=telegram.send_to,
        pin=telegram.pin_message,
        unpin=telegram.unpin_message,
        unpin_all=telegram.unpin_all_messages,
    )

    # Audio + TTS
    media_tool.set_send_audio(telegram.send_audio)

    # Browser screenshot → Telegram photo; PDF → Telegram document
    browser_tool.set_send_photo_fn(telegram.send_photo)
    browser_tool.set_send_document_fn(telegram.send_document)

    # Canvas snapshot → Telegram photo
    from tools import canvas_tool
    canvas_tool.set_send_photo_fn(telegram.send_photo)

    # Cron manager (shared)
    cron_tool.set_manager(cron_mgr)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="LobsterClaw AI Assistant")
    parser.add_argument(
        "--light-context", action="store_true",
        help="Light-bootstrap mode: skip browser, canvas, memory index, sub-agents. "
             "Faster cold-start for scheduled/cron runs.",
    )
    args, _ = parser.parse_known_args()

    cfg = get_config()
    # CLI flag overrides env var
    if args.light_context:
        object.__setattr__(cfg, "light_context", True)

    logger.info("Starting LobsterClaw%s...", " [LIGHT MODE]" if cfg.light_context else "")

    cfg = get_config()
    logger.info("LLM provider: %s / model: %s", cfg.llm_provider, cfg.llm_model)
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
    from channels.telegram import TelegramChannel, AccountConfig

    # Session store
    session_store = SessionStore(cfg.sessions_db)
    set_session_store(session_store)
    logger.info("Session store: %s", cfg.sessions_db)

    # Sub-agent manager (skipped in light mode)
    subagent_mgr = SubagentManager()
    if not cfg.light_context:
        set_subagent_manager(subagent_mgr)

    approval = ApprovalGate()
    registry = build_registry(light=cfg.light_context)
    registry.set_approval_gate(approval)

    history = HistoryManager()
    agent = AgentLoop(registry)

    cron_mgr = CronManager()
    heartbeat = HeartbeatRunner()

    # ----------------------------------------------------------------
    # Build Telegram channel(s) — single or multi-account
    # ----------------------------------------------------------------

    accounts_cfg = cfg.telegram_accounts
    channels: list[TelegramChannel] = []

    if accounts_cfg:
        # Multi-account mode
        logger.info("Multi-account mode: %d accounts", len(accounts_cfg))
        for acc in accounts_cfg:
            account = AccountConfig(
                label=acc.get("label", "main"),
                token=acc.get("token", cfg.telegram_bot_token),
                owner_id=int(acc.get("owner_id", cfg.telegram_owner_id)),
                dm_policy=acc.get("dm_policy", ""),
                group_policy=acc.get("group_policy", ""),
                allow_from=[int(x) for x in acc.get("allow_from", []) if str(x).isdigit()],
                group_allowlist=[int(x) for x in acc.get("group_allowlist", [])
                                 if str(x).lstrip("-").isdigit()],
            )
            ch = TelegramChannel(
                agent=agent,
                history=history,
                approval=approval,
                build_prompt=build_system_prompt,
                account=account,
            )
            channels.append(ch)
            logger.info("Registered account: %s (owner=%s)", account.label, account.owner_id)
    else:
        # Single-account mode
        ch = TelegramChannel(
            agent=agent,
            history=history,
            approval=approval,
            build_prompt=build_system_prompt,
        )
        channels.append(ch)

    # ----------------------------------------------------------------
    # Build Discord channel (if enabled)
    # ----------------------------------------------------------------
    discord_channel = None
    if cfg.discord_enabled:
        try:
            from channels.discord import DiscordChannel
            from tools.discord_tool import set_discord_channel, DISCORD_TOOL
            discord_channel = DiscordChannel(
                agent=agent,
                history=history,
                approval=approval,
                build_prompt=build_system_prompt,
            )
            set_discord_channel(discord_channel)
            # Replace stub tool with live implementation
            registry.register(DISCORD_TOOL)
            logger.info("Discord channel enabled")
        except Exception as exc:
            logger.error("Discord channel failed to initialise: %s", exc)
            discord_channel = None

    # Use the primary (first) channel for all tool wiring
    primary = channels[0]
    _wire_channel(primary, registry, approval, cron_mgr, build_system_prompt)

    # ----------------------------------------------------------------
    # Sub-agent factory
    # ----------------------------------------------------------------

    def subagent_registry_factory():
        sub_registry = build_registry()
        sub_registry.set_approval_gate(approval)
        from tools import message_tool
        message_tool.set_send_fn(primary.send_message)
        message_tool.set_channel_ref(primary)
        return sub_registry

    subagent_mgr.configure(
        send_fn=primary.send_message,
        agent_loop_factory=subagent_registry_factory,
    )

    # ----------------------------------------------------------------
    # Background agent runner (cron + heartbeat)
    # ----------------------------------------------------------------

    async def agent_for_bg(message: str, system_prompt: str | None = None) -> str:
        system = system_prompt or build_system_prompt(cfg, registry.get_names())
        return await agent.run([{"role": "user", "content": message}], system, session_id="main")

    cron_mgr.configure(
        send_fn=primary.send_message,
        agent_fn=lambda msg: agent_for_bg(msg),
    )

    heartbeat.configure(
        agent_fn=agent_for_bg,
        send_fn=primary.send_message,
    )

    # Nightly memory consolidation — distils daily log → MEMORY.md each night
    from agent.nightly_memory import get_nightly_memory_runner
    nightly_memory = get_nightly_memory_runner()
    nightly_memory.configure(agent_fn=agent_for_bg)

    # ----------------------------------------------------------------
    # Configure Gateway API (Mission Control dashboard)
    # ----------------------------------------------------------------

    use_gateway = cfg.gateway_enabled
    if use_gateway:
        from gateway.server import configure as configure_gateway
        configure_gateway(
            registry=registry,
            cron_mgr=cron_mgr,
            agent_fn=lambda msg: agent_for_bg(msg),
            send_fn=primary.send_message,
            history_mgr=history,
        )
        logger.info("Gateway API configured (port %d)", cfg.gateway_port)

    # ----------------------------------------------------------------
    # Load plugins
    # ----------------------------------------------------------------

    from tools.plugin_loader import register_plugins, start_hot_reload
    from pathlib import Path as _Path
    plugin_tools = register_plugins(registry)
    if plugin_tools:
        logger.info("Loaded plugin tools: %s", ", ".join(plugin_tools))

    # Plugin hot-reload (watchdog) — watches workspace/plugins/ for changes
    if getattr(cfg, "plugin_hot_reload", True) and not cfg.light_context:
        _plugins_dir = _Path(__file__).parent / "workspace" / "plugins"
        _hr_started = start_hot_reload(registry, watch_dir=_plugins_dir)
        if not _hr_started:
            logger.info("Plugin hot-reload: install watchdog>=3.0.0 to enable")

    # ----------------------------------------------------------------
    # Load hooks (workspace/hooks/*/handler.py)
    # ----------------------------------------------------------------
    from agent.hooks import init_hooks
    from pathlib import Path as _Path
    _workspace = _Path(__file__).parent / "workspace"
    hook_registry = init_hooks(_workspace)
    if hook_registry.loaded:
        logger.info("Loaded hooks: %s", ", ".join(hook_registry.loaded))

    # ----------------------------------------------------------------
    # Start Telegram bot(s)
    # ----------------------------------------------------------------

    all_tools = registry.get_names()
    logger.info("Bot ready. %d tools: %s", len(all_tools), ", ".join(all_tools))

    use_canvas_host = cfg.canvas_host_enabled

    if use_canvas_host:
        logger.info(
            "Canvas host enabled — starting on %s:%d",
            cfg.canvas_host_bind,
            cfg.canvas_host_port,
        )

    # Helper to start schedulers (must be called inside a running event loop)
    def start_schedulers():
        if cfg.cron_enabled:
            cron_mgr.start()
            logger.info("Cron scheduler started")
            if cfg.heartbeat_enabled:
                heartbeat.start(cron_mgr.scheduler)
                logger.info("Heartbeat runner attached (schedule: %s)", cfg.heartbeat_schedule)
            nightly_memory.start(cron_mgr.scheduler)
            logger.info("Nightly memory runner attached")
        elif cfg.heartbeat_enabled:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            hb_scheduler = AsyncIOScheduler()
            hb_scheduler.start()
            heartbeat.start(hb_scheduler)
            nightly_memory.start(hb_scheduler)
            logger.info("Heartbeat-only scheduler started")
        else:
            # No cron, no heartbeat — still need a scheduler for nightly memory
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            nm_scheduler = AsyncIOScheduler()
            nm_scheduler.start()
            nightly_memory.start(nm_scheduler)
            logger.info("Nightly memory scheduler started")

    if len(channels) == 1 and not use_canvas_host and not use_gateway:
        # Simple single-account blocking path (no extra servers)
        # channels[0].run() creates its own event loop internally
        logger.info("Starting single-account bot...")
        channels[0].run()
    else:
        # Async path: multi-account OR canvas host OR gateway (or any combo)
        logger.info(
            "Starting %d account(s) in async mode (canvas_host=%s, gateway=%s)...",
            len(channels),
            use_canvas_host,
            use_gateway,
        )
        asyncio.run(_run_async_main(
            channels,
            start_canvas_host=use_canvas_host,
            start_gateway=use_gateway,
            start_discord=bool(discord_channel),
            discord_ch=discord_channel,
            on_ready=start_schedulers,
        ))


async def _run_async_main(
    channels: list,
    *,
    start_canvas_host: bool = False,
    start_gateway: bool = False,
    start_discord: bool = False,
    discord_ch=None,
    on_ready: callable = None,
) -> None:
    """
    Async entry point for:
    - Multi-account Telegram bots
    - Single account + canvas host / gateway
    - All together
    """
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    # Start schedulers now that the event loop is running
    if on_ready:
        on_ready()

    tasks: list[asyncio.Task] = []

    # Canvas host
    if start_canvas_host:
        from canvas_host.server import run_server
        cfg = get_config()
        canvas_task = asyncio.create_task(
            run_server(host=cfg.canvas_host_bind, port=cfg.canvas_host_port),
            name="canvas-host",
        )
        tasks.append(canvas_task)
        logger.info("Canvas host task started on %s:%d", cfg.canvas_host_bind, cfg.canvas_host_port)

    # Gateway API (Mission Control dashboard)
    if start_gateway:
        from gateway.server import run_server as run_gateway
        cfg = get_config()
        gw_task = asyncio.create_task(
            run_gateway(host=cfg.gateway_bind, port=cfg.gateway_port),
            name="gateway-api",
        )
        tasks.append(gw_task)
        logger.info("Gateway API task started on %s:%d", cfg.gateway_bind, cfg.gateway_port)

    # Telegram channels
    for ch in channels:
        t = asyncio.create_task(ch.run_async(), name=f"telegram-{getattr(ch, 'label', 'main')}")
        tasks.append(t)

    # Discord channel
    if start_discord and discord_ch:
        t = asyncio.create_task(discord_ch.run_async(), name="discord")
        tasks.append(t)
        logger.info("Discord channel task started")

    logger.info("All tasks started (%d). Waiting for shutdown signal...", len(tasks))
    await stop_event.wait()

    # Graceful shutdown
    logger.info("Shutting down...")

    stop_tasks = [asyncio.create_task(ch.stop_async()) for ch in channels]
    if start_discord and discord_ch:
        stop_tasks.append(asyncio.create_task(discord_ch.stop_async()))
    await asyncio.gather(*stop_tasks, return_exceptions=True)

    for t in tasks:
        if not t.done():
            t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Shutting down.")
        sys.exit(0)
    except ValueError as e:
        logger.error("%s", e)
        sys.exit(1)
