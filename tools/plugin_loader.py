"""
tools/plugin_loader.py — Plugin system with hot-reload via watchdog.

Plugins live in:
  1. workspace/plugins/<n>/  (project-local)
  2. ~/.lobsterclaw/plugins/<n>/  (user-global)

Each plugin directory must contain __init__.py (or plugin.py) that exports:
  TOOLS: list[ToolDefinition]

Optional plugin.json metadata:
  {"name": "...", "description": "...", "version": "...", "requires": [...]}

HOT-RELOAD:
  When PLUGIN_HOT_RELOAD=true (default: true), a watchdog file-system observer
  watches workspace/plugins/ for changes. When a plugin file changes:
    - The plugin module is reloaded via importlib.reload()
    - Its tools are re-registered into the live ToolRegistry
    - A log line confirms the reload
  No restart required.

  Requires: watchdog>=3.0.0  (pip install "watchdog>=3.0.0")
  Falls back to static loading if watchdog is not installed.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.registry import ToolDefinition, ToolRegistry

logger = logging.getLogger(__name__)

DEFAULT_PROJECT_PLUGINS_DIR = Path(__file__).parent.parent / "workspace" / "plugins"

# Module name prefix — must be stable so importlib.reload() can find the module
_MODULE_PREFIX = "_lc_plugin_"


@dataclass
class PluginInfo:
    name: str
    path: Path
    description: str = ""
    version: str = ""
    tools: list = field(default_factory=list)
    module_name: str = ""   # sys.modules key for reload


# ── Loading ───────────────────────────────────────────────────────────────────

def _discover_plugin_dirs() -> list[Path]:
    dirs: list[Path] = []
    if DEFAULT_PROJECT_PLUGINS_DIR.exists():
        dirs.append(DEFAULT_PROJECT_PLUGINS_DIR)
    user_dir = Path.home() / ".lobsterclaw" / "plugins"
    if user_dir.exists():
        dirs.append(user_dir)
    return dirs


def _load_plugin(plugin_dir: Path) -> PluginInfo | None:
    if not plugin_dir.is_dir():
        return None

    name = plugin_dir.name
    info = PluginInfo(name=name, path=plugin_dir)

    meta_file = plugin_dir / "plugin.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            info.description = meta.get("description", "")
            info.version = meta.get("version", "")
        except Exception:
            pass

    init_file = plugin_dir / "__init__.py"
    plugin_file = plugin_dir / "plugin.py"
    module_file = init_file if init_file.exists() else (plugin_file if plugin_file.exists() else None)

    if module_file is None:
        logger.debug("Plugin %s has no __init__.py or plugin.py — skipping", name)
        return None

    module_name = f"{_MODULE_PREFIX}{name}"
    try:
        # If already loaded, reload it
        if module_name in sys.modules:
            module = importlib.reload(sys.modules[module_name])
            logger.info("Plugin '%s' reloaded", name)
        else:
            spec = importlib.util.spec_from_file_location(module_name, module_file)
            module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            sys.modules[module_name] = module
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            logger.info(
                "Plugin '%s' loaded (%d tools): %s",
                name,
                len(getattr(module, "TOOLS", []) or []),
                [t.name for t in (getattr(module, "TOOLS", []) or [])],
            )

        tools = getattr(module, "TOOLS", None)
        if tools is None:
            logger.warning("Plugin %s has no TOOLS list — skipping", name)
            return None

        info.tools = list(tools)
        info.module_name = module_name
        return info

    except Exception as exc:
        logger.error("Failed to load plugin '%s': %s", name, exc)
        # Remove broken module from cache so next reload starts clean
        sys.modules.pop(module_name, None)
        return None


def load_all_plugins() -> list[PluginInfo]:
    plugins: list[PluginInfo] = []
    seen: set[str] = set()
    for search_dir in _discover_plugin_dirs():
        for candidate in sorted(search_dir.iterdir()):
            if candidate.is_dir() and candidate.name not in seen:
                plugin = _load_plugin(candidate)
                if plugin and plugin.tools:
                    plugins.append(plugin)
                    seen.add(candidate.name)
    return plugins


def register_plugins(registry: "ToolRegistry") -> list[str]:
    """Load all plugins and register their tools. Returns list of tool names."""
    plugins = load_all_plugins()
    registered: list[str] = []
    for plugin in plugins:
        for tool_def in plugin.tools:
            try:
                registry.register(tool_def)
                registered.append(tool_def.name)
            except Exception as exc:
                logger.error(
                    "Failed to register tool %s from plugin %s: %s",
                    tool_def.name, plugin.name, exc,
                )
    return registered


def list_plugins() -> str:
    plugins = load_all_plugins()
    if not plugins:
        return "No plugins found. Add plugins to workspace/plugins/ or ~/.lobsterclaw/plugins/"
    lines = [f"Installed plugins ({len(plugins)}):"]
    for p in plugins:
        tools_str = ", ".join(t.name for t in p.tools)
        ver = f" v{p.version}" if p.version else ""
        desc = f" — {p.description}" if p.description else ""
        lines.append(f"  {p.name}{ver}{desc}\n    Tools: {tools_str}")
    return "\n".join(lines)


# ── Hot-reload watcher ────────────────────────────────────────────────────────

def start_hot_reload(registry: "ToolRegistry", watch_dir: Path | None = None) -> bool:
    """
    Start a watchdog observer on workspace/plugins/.
    When any file inside a plugin directory changes, the plugin is reloaded
    and its tools are re-registered into the live registry.

    Returns True if watchdog is available and observer started, False otherwise.
    Designed to be called once from main.py after initial plugin load.
    """
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler, FileSystemEvent
    except ImportError:
        logger.info(
            "watchdog not installed — plugin hot-reload disabled. "
            "Run: pip install 'watchdog>=3.0.0'"
        )
        return False

    target_dir = watch_dir or DEFAULT_PROJECT_PLUGINS_DIR
    if not target_dir.exists():
        logger.debug("Plugin hot-reload: watch dir %s does not exist — skipping", target_dir)
        return False

    class _PluginReloadHandler(FileSystemEventHandler):
        def on_modified(self, event: FileSystemEvent) -> None:
            self._handle(event.src_path)

        def on_created(self, event: FileSystemEvent) -> None:
            self._handle(event.src_path)

        def _handle(self, src_path: str) -> None:
            path = Path(src_path)
            # We only care about .py and plugin.json files
            if path.suffix not in (".py", ".json"):
                return
            # Find which plugin dir this belongs to
            try:
                plugin_dir = None
                for part in path.parents:
                    if part.parent == target_dir:
                        plugin_dir = part
                        break
                if plugin_dir is None or not plugin_dir.is_dir():
                    return

                logger.info(
                    "Plugin hot-reload: change detected in %s — reloading plugin '%s'",
                    path.name, plugin_dir.name,
                )
                plugin = _load_plugin(plugin_dir)
                if plugin and plugin.tools:
                    for tool_def in plugin.tools:
                        try:
                            # Re-register — registry.register() overwrites existing by name
                            registry.register(tool_def)
                            logger.info("Hot-reloaded tool: %s", tool_def.name)
                        except Exception as exc:
                            logger.error(
                                "Hot-reload: failed to register %s: %s",
                                tool_def.name, exc,
                            )
                elif plugin is None:
                    logger.warning(
                        "Hot-reload: plugin '%s' failed to load after change",
                        plugin_dir.name,
                    )
            except Exception as exc:
                logger.error("Hot-reload handler error: %s", exc)

    observer = Observer()
    observer.schedule(_PluginReloadHandler(), str(target_dir), recursive=True)
    observer.daemon = True   # dies with main process
    observer.start()
    logger.info("Plugin hot-reload watching: %s", target_dir)
    return True
