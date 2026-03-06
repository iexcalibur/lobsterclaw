"""
Memory tools — mirrors OpenClaw's memory_search / memory_get / memory_write.

Storage: workspace/memory/*.md files (human-readable, editable by user).
The directory is resolved from config (MEMORY_DIR) or falls back to workspace/memory/.

FTS5 index is rebuilt in-memory per search query from the current file state.

Tools:
  memory_search — full-text search across all memory MD files
  memory_get    — read a specific memory file by key
  memory_write  — write/update/append a memory file
  memory_list   — list all memory keys
  memory_delete — delete a memory entry
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from config import get_config
from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Tool definitions
# ------------------------------------------------------------------

MEMORY_SEARCH_TOOL = ToolDefinition(
    name="memory_search",
    description=(
        "Search long-term memory for relevant notes and facts.\n\n"
        "Field parity with OpenClaw memory-tool.ts:\n"
        "  query      — search terms or natural language\n"
        "  maxResults — max results to return (also 'limit', default 5)\n"
        "  minScore   — minimum relevance score 0-1 (default 0.0, no filter)\n"
        "  mode       — 'fts' (keyword) or 'semantic' (requires MEMORY_SEMANTIC=true)"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query (keywords or natural language)"},
            "maxResults": {
                "type": "integer",
                "description": "Max results — OpenClaw field name (also 'limit', default 5)",
                "default": 5,
            },
            "limit": {
                "type": "integer",
                "description": "Alias for maxResults",
                "default": 5,
            },
            "minScore": {
                "type": "number",
                "description": "Minimum relevance score 0.0-1.0 to filter results (default 0.0)",
                "default": 0.0,
            },
            "mode": {
                "type": "string",
                "description": "Search mode: fts (keyword, default) | semantic (requires MEMORY_SEMANTIC=true)",
                "default": "fts",
            },
        },
        "required": ["query"],
    },
    fn=lambda **kw: _memory_search(**kw),
)

MEMORY_GET_TOOL = ToolDefinition(
    name="memory_get",
    description=(
        "Read a specific memory file by key or path.\n\n"
        "Field parity with OpenClaw memory-tool.ts:\n"
        "  path / key  — memory key (filename without .md) or full path\n"
        "  from        — 1-indexed line number to start reading from\n"
        "  lines       — max lines to return (default: all)"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Memory key or path (OpenClaw field name; also 'key')",
            },
            "key": {
                "type": "string",
                "description": "Alias for path (e.g. 'preferences', 'MEMORY')",
            },
            "from": {
                "type": "integer",
                "description": "1-indexed start line (OpenClaw field name; for reading sections)",
            },
            "lines": {
                "type": "integer",
                "description": "Max lines to return from 'from' offset (default: all)",
            },
        },
        "required": [],
    },
    fn=lambda **kw: _memory_get(**kw),
)

MEMORY_WRITE_TOOL = ToolDefinition(
    name="memory_write",
    description=(
        "Save or update a memory entry as a markdown file. "
        "Key becomes the filename (e.g. 'preferences' → memory/preferences.md). "
        "Use 'memory/YYYY-MM-DD' for the daily log. "
        "Set append=true to add to an existing entry instead of replacing it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": (
                    "Key or path without extension (e.g. 'preferences', 'memory/2026-03-06', "
                    "'notes/project')."
                ),
            },
            "content": {"type": "string", "description": "Markdown content to remember"},
            "append": {"type": "boolean", "description": "Append to existing file (default false)", "default": False},
        },
        "required": ["key", "content"],
    },
    fn=lambda **kw: _memory_write(**kw),
)

MEMORY_LIST_TOOL = ToolDefinition(
    name="memory_list",
    description="List all available memory keys.",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    fn=lambda **kw: _memory_list(**kw),
)

MEMORY_DELETE_TOOL = ToolDefinition(
    name="memory_delete",
    description="Delete a memory entry by key.",
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Memory key to delete"},
        },
        "required": ["key"],
    },
    fn=lambda **kw: _memory_delete(**kw),
)

# ------------------------------------------------------------------
# Path resolution — uses config.memory_path, not hardcoded workspace/
# ------------------------------------------------------------------

def _get_memory_dir() -> Path:
    """Returns the memory directory, creating it if needed."""
    cfg = get_config()
    # Use the configured path; fall back to workspace/memory/ next to this project
    if cfg.memory_dir and cfg.memory_dir != "~/.lobsterclaw/memory":
        directory = Path(cfg.memory_dir).expanduser()
    else:
        # Default: workspace/memory/ relative to project root
        directory = Path(__file__).parent.parent / "workspace" / "memory"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _get_memory_md_path() -> Path:
    """Top-level MEMORY.md — checked in config memory_dir first, then workspace root."""
    cfg = get_config()
    # Check config directory first (non-default path)
    if cfg.memory_dir and cfg.memory_dir != "~/.lobsterclaw/memory":
        candidate = Path(cfg.memory_dir).expanduser().parent / "MEMORY.md"
        if candidate.exists():
            return candidate
    # Default: workspace/MEMORY.md relative to project root
    return Path(__file__).parent.parent / "workspace" / "MEMORY.md"


def _normalize_key(raw_key: str) -> str:
    """Normalize a user-provided memory key into a stable internal form."""
    if not raw_key:
        return ""

    key = raw_key.strip().replace("\\", "/").strip().strip("/")
    if key.lower().startswith("memory/"):
        key = key[len("memory/") :]
    if key.lower().endswith(".md"):
        key = key[:-3]
    return key.strip()


def _memory_path_from_key(raw_key: str) -> tuple[str, Path]:
    """Resolve a key/path into a concrete file path under the memory directory."""
    key = _normalize_key(raw_key)
    if not key:
        raise ValueError("Invalid key")

    parts = key.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("Invalid key")

    memory_dir = _get_memory_dir()
    rel_path = Path(*parts)
    if rel_path.suffix != ".md":
        rel_path = rel_path.with_suffix(".md")
    return key, memory_dir / rel_path


def _is_daily_log_key(key: str) -> bool:
    """Return True when the key looks like YYYY-MM-DD."""
    try:
        datetime.strptime(key, "%Y-%m-%d")
        return True
    except ValueError:
        return False


# ------------------------------------------------------------------
# Implementations
# ------------------------------------------------------------------

async def _memory_search(
    query: str,
    maxResults: int = 5,       # OpenClaw field name
    limit: int = 5,             # alias
    minScore: float = 0.0,      # OpenClaw field name
    mode: str = "fts",
) -> str:
    cfg = get_config()
    if not cfg.memory_enabled:
        return "Memory is disabled (MEMORY_ENABLED=false)"

    effective_limit = maxResults if maxResults != 5 else limit

    try:
        from agent.memory_index import MemoryIndex

        memory_dir = _get_memory_dir()
        memory_md = _get_memory_md_path()
        index_db = memory_dir / ".memory_index.db"
        idx = MemoryIndex(memory_dir=memory_dir, memory_md=memory_md, index_db_path=index_db)

        use_semantic = (
            mode == "semantic"
            and getattr(cfg, "memory_semantic", False)
            and cfg.openai_api_key
        )

        results = idx.search(query, limit=effective_limit, semantic=use_semantic, api_key=cfg.openai_api_key)

        if not results:
            return f"No memory entries matched '{query}'."

        # Apply minScore filter if specified
        if minScore > 0.0:
            results = [r for r in results if getattr(r, "score", 1.0) >= minScore]
            if not results:
                return f"No memory entries above minScore={minScore} for '{query}'."

        formatted = []
        for r in results:
            score_str = f" (score={r.score:.2f})" if hasattr(r, "score") and r.score < 1.0 else ""
            formatted.append(f"### [{r.key}]{score_str}\n{r.snippet}")
        return "\n\n---\n\n".join(formatted)

    except Exception as e:
        logger.exception("memory_search failed")
        return f"Error searching memory: {e}"


async def _memory_get(
    path: str | None = None,   # OpenClaw field name
    key: str | None = None,    # alias
    **kwargs,                   # absorb 'from' keyword (reserved word)
) -> str:
    cfg = get_config()
    if not cfg.memory_enabled:
        return "Memory is disabled (MEMORY_ENABLED=false)"

    effective_key = path or key
    # 'from' is a Python reserved word so it comes through kwargs
    from_line: int | None = kwargs.get("from")
    lines: int | None = kwargs.get("lines")

    if not effective_key:
        return "Error: 'path' (or 'key') is required for memory_get"

    if effective_key.upper() == "MEMORY":
        file_path = _get_memory_md_path()
    else:
        try:
            normalized_key, memory_file = _memory_path_from_key(effective_key)
        except ValueError:
            return "Invalid key"

        workspace_root = Path(__file__).parent.parent / "workspace"
        workspace_key_path = workspace_root / f"{normalized_key}.md"
        file_path = workspace_key_path if workspace_key_path.exists() else memory_file

    if not file_path.exists():
        available = _list_keys()
        return f"No memory file found for key '{effective_key}'.\nAvailable keys: {available}"

    try:
        content_lines = file_path.read_text(encoding="utf-8").splitlines()
        start = max(0, (from_line - 1) if from_line else 0)
        end = (start + lines) if lines else len(content_lines)
        selected = content_lines[start:end]
        result = "\n".join(selected).strip()
        if from_line or lines:
            result = f"[Lines {start+1}–{start+len(selected)} of {len(content_lines)}]\n\n{result}"
        return result
    except Exception as e:
        return f"Error reading memory '{effective_key}': {e}"


async def _memory_write(key: str, content: str, append: bool = False) -> str:
    cfg = get_config()
    if not cfg.memory_enabled:
        return "Memory is disabled (MEMORY_ENABLED=false)"

    try:
        normalized_key, memory_path = _memory_path_from_key(key)
    except ValueError:
        return "Invalid key"

    if _is_daily_log_key(normalized_key) and memory_path.exists():
        append = True

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Check workspace root first — update existing files like IDENTITY.md,
    # USER.md, SOUL.md in-place instead of creating duplicates in memory/.
    workspace_root = Path(__file__).parent.parent / "workspace"
    workspace_file = workspace_root / f"{normalized_key}.md"
    if "/" in normalized_key:
        workspace_file = None

    path = workspace_file if workspace_file is not None and workspace_file.exists() else memory_path
    memory_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if (append or (workspace_file is not None and workspace_file.exists())) and path.exists():
            existing = path.read_text(encoding="utf-8")
            if append:
                updated = f"{existing.rstrip()}\n\n_Updated {now}_\n\n{content}"
            else:
                updated = f"# {normalized_key}\n\n_Last updated: {now}_\n\n{content}"
            path.write_text(updated, encoding="utf-8")
            if path == workspace_file:
                loc = f"workspace/{path.name}"
            else:
                rel = path.relative_to(_get_memory_dir())
                loc = f"memory/{rel.as_posix()}"
            return f"Memory '{normalized_key}' updated → {loc}"
        else:
            header = f"# {normalized_key}\n\n_Last updated: {now}_\n\n"
            path.write_text(header + content, encoding="utf-8")
            rel = path.relative_to(_get_memory_dir())
            return f"Memory '{normalized_key}' saved → memory/{rel.as_posix()}"
    except Exception as e:
        return f"Error saving memory '{normalized_key}': {e}"


async def _memory_list() -> str:
    cfg = get_config()
    if not cfg.memory_enabled:
        return "Memory is disabled (MEMORY_ENABLED=false)"
    keys = _list_keys()
    return f"Available memory keys: {keys}" if keys != "(none yet)" else "No memory entries yet."


async def _memory_delete(key: str) -> str:
    cfg = get_config()
    if not cfg.memory_enabled:
        return "Memory is disabled (MEMORY_ENABLED=false)"

    try:
        _, path = _memory_path_from_key(key)
    except ValueError:
        return "Invalid key"
    if not path.exists():
        return f"No memory file found for key '{key}'"
    try:
        path.unlink()
        return f"Memory '{key}' deleted."
    except Exception as e:
        return f"Error deleting memory '{key}': {e}"


def _list_keys() -> str:
    memory_dir = _get_memory_dir()
    keys = [
        str(path.relative_to(memory_dir).with_suffix("").as_posix())
        for path in sorted(memory_dir.rglob("*.md"))
        if path.name != "README.md"
    ]
    if _get_memory_md_path().exists():
        keys = ["MEMORY"] + keys
    return ", ".join(keys) if keys else "(none yet)"
