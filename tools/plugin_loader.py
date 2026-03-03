"""
Plugin/extension system — mirrors OpenClaw's plugin architecture.

Plugins live in:
  1. workspace/plugins/<name>/  (project-local plugins)
  2. ~/.lobsterclaw/plugins/<name>/  (user-global plugins)

Each plugin directory must contain a __init__.py (or plugin.py) that exports:
  TOOLS: list[ToolDefinition]   — tool definitions provided by this plugin

Optional plugin.json metadata:
  {
    "name": "my-plugin",
    "description": "What this plugin does",
    "version": "1.0.0",
    "requires": ["httpx", "beautifulsoup4"]
  }

Plugin tools are loaded and registered alongside core tools. The allow/deny
policy in .env applies equally to plugin tool names.

Example plugin layout:
  workspace/plugins/weather/
    __init__.py    ← defines TOOLS = [ToolDefinition(...)]
    plugin.json    ← metadata (optional)
    README.md      ← documentation (optional)
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

DEFAULT_PROJECT_PLUGINS_DIR = Path(__file__).parent.parent / "workspace" / "plugins"


@dataclass
class PluginInfo:
    name: str
    path: Path
    description: str = ""
    version: str = ""
    tools: list = None  # list[ToolDefinition]

    def __post_init__(self):
        if self.tools is None:
            self.tools = []


def _discover_plugin_dirs() -> list[Path]:
    """Find all plugin directories from project and user-global locations."""
    dirs: list[Path] = []
    # Project-local plugins
    project_dir = DEFAULT_PROJECT_PLUGINS_DIR
    if project_dir.exists():
        dirs.append(project_dir)
    # User-global plugins
    user_dir = Path.home() / ".lobsterclaw" / "plugins"
    if user_dir.exists():
        dirs.append(user_dir)
    return dirs


def _load_plugin(plugin_dir: Path) -> PluginInfo | None:
    """Load a single plugin from its directory."""
    if not plugin_dir.is_dir():
        return None

    name = plugin_dir.name
    info = PluginInfo(name=name, path=plugin_dir)

    # Load metadata
    meta_file = plugin_dir / "plugin.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text())
            info.description = meta.get("description", "")
            info.version = meta.get("version", "")
        except Exception:
            pass

    # Load Python module
    init_file = plugin_dir / "__init__.py"
    plugin_file = plugin_dir / "plugin.py"
    module_file = init_file if init_file.exists() else (plugin_file if plugin_file.exists() else None)

    if module_file is None:
        logger.debug("Plugin %s has no __init__.py or plugin.py — skipping", name)
        return None

    try:
        module_name = f"_pygate_plugin_{name}"
        spec = importlib.util.spec_from_file_location(module_name, module_file)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        tools = getattr(module, "TOOLS", None)
        if tools is None:
            logger.warning("Plugin %s has no TOOLS list — skipping", name)
            return None

        info.tools = list(tools)
        logger.info(
            "Loaded plugin '%s' (%d tools): %s",
            name, len(info.tools), [t.name for t in info.tools]
        )
        return info
    except Exception as e:
        logger.error("Failed to load plugin '%s': %s", name, e)
        return None


def load_all_plugins() -> list[PluginInfo]:
    """Discover and load all available plugins."""
    plugins: list[PluginInfo] = []
    seen_names: set[str] = set()

    for search_dir in _discover_plugin_dirs():
        for candidate in sorted(search_dir.iterdir()):
            if candidate.is_dir() and candidate.name not in seen_names:
                plugin = _load_plugin(candidate)
                if plugin and plugin.tools:
                    plugins.append(plugin)
                    seen_names.add(candidate.name)

    return plugins


def register_plugins(registry) -> list[str]:
    """
    Load all plugins and register their tools into the given ToolRegistry.
    Returns list of registered tool names.
    """
    plugins = load_all_plugins()
    registered: list[str] = []
    for plugin in plugins:
        for tool_def in plugin.tools:
            try:
                registry.register(tool_def)
                registered.append(tool_def.name)
            except Exception as e:
                logger.error("Failed to register tool %s from plugin %s: %s", tool_def.name, plugin.name, e)
    return registered


def list_plugins() -> str:
    """Return a human-readable list of available plugins."""
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
