"""
File system tools — mirrors OpenClaw's read/write/edit/apply_patch + extras.

Tools:
  read         — Read file contents with optional line offset/limit
  write        — Create or overwrite a file
  edit         — Exact-string replace in a file
  apply_patch  — Apply a unified diff patch (async subprocess)
  list_dir     — List directory contents
  glob         — Find files matching a pattern
  delete       — Delete a file or empty directory
  move         — Move/rename a file or directory
"""

from __future__ import annotations

import asyncio
import glob as glob_module
from pathlib import Path

from tools.registry import ToolDefinition

# ------------------------------------------------------------------
# Tool definitions
# ------------------------------------------------------------------

READ_TOOL = ToolDefinition(
    name="read",
    description="Read the contents of a file with optional line offset and limit.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or ~ path to the file"},
            "offset": {"type": "integer", "description": "1-indexed line to start from (optional)"},
            "limit": {"type": "integer", "description": "Max lines to read (optional)"},
        },
        "required": ["path"],
    },
    fn=lambda **kw: _read(**kw),
)

WRITE_TOOL = ToolDefinition(
    name="write",
    description="Create or overwrite a file with the given content.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or ~ path to the file"},
            "content": {"type": "string", "description": "File content to write"},
        },
        "required": ["path", "content"],
    },
    fn=lambda **kw: _write(**kw),
)

EDIT_TOOL = ToolDefinition(
    name="edit",
    description=(
        "Replace an exact string in a file with new text. "
        "The old_string must appear exactly once — make it unique enough."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or ~ path to the file"},
            "old_string": {"type": "string", "description": "Exact text to find and replace"},
            "new_string": {"type": "string", "description": "Replacement text"},
        },
        "required": ["path", "old_string", "new_string"],
    },
    fn=lambda **kw: _edit(**kw),
)

APPLY_PATCH_TOOL = ToolDefinition(
    name="apply_patch",
    description="Apply a unified diff patch to files. Requires 'patch' to be installed.",
    parameters={
        "type": "object",
        "properties": {
            "patch": {"type": "string", "description": "Unified diff patch string"},
        },
        "required": ["patch"],
    },
    fn=lambda **kw: _apply_patch(**kw),
)

LIST_DIR_TOOL = ToolDefinition(
    name="list_dir",
    description="List files and directories at a given path.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or ~ directory path"},
            "recursive": {"type": "boolean", "description": "List recursively (default false)", "default": False},
        },
        "required": ["path"],
    },
    fn=lambda **kw: _list_dir(**kw),
)

GLOB_TOOL = ToolDefinition(
    name="glob",
    description="Find files matching a glob pattern (e.g. '**/*.py', '~/docs/*.md').",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern to match"},
            "cwd": {"type": "string", "description": "Base directory for relative patterns (optional)"},
        },
        "required": ["pattern"],
    },
    fn=lambda **kw: _glob(**kw),
)

DELETE_TOOL = ToolDefinition(
    name="delete",
    description="Delete a file or empty directory.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or ~ path to delete"},
            "recursive": {"type": "boolean", "description": "Delete directory recursively (default false)", "default": False},
        },
        "required": ["path"],
    },
    fn=lambda **kw: _delete(**kw),
)

MOVE_TOOL = ToolDefinition(
    name="move",
    description="Move or rename a file or directory.",
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Source path"},
            "destination": {"type": "string", "description": "Destination path"},
        },
        "required": ["source", "destination"],
    },
    fn=lambda **kw: _move(**kw),
)

# ------------------------------------------------------------------
# Implementations
# ------------------------------------------------------------------

async def _read(path: str, offset: int | None = None, limit: int | None = None) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"Error: file not found: {path}"
    if p.is_dir():
        return f"Error: {path} is a directory — use list_dir instead"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, (offset - 1) if offset else 0)
        end = (start + limit) if limit else len(lines)
        selected = lines[start:end]
        numbered = "\n".join(f"{start + i + 1}|{line}" for i, line in enumerate(selected))
        return numbered or "(empty file)"
    except Exception as e:
        return f"Error reading file: {e}"


async def _write(path: str, content: str) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Written {len(content)} bytes to {path}"


async def _edit(path: str, old_string: str, new_string: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"Error: file not found: {path}"
    text = p.read_text(encoding="utf-8")
    if old_string not in text:
        return f"Error: old_string not found in {path}"
    count = text.count(old_string)
    if count > 1:
        return f"Error: old_string appears {count} times — make it more specific to uniquely identify the location"
    p.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
    return f"Edited {path} successfully"


async def _apply_patch(patch: str) -> str:
    """Apply a unified diff patch — runs 'patch' subprocess asynchronously."""
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as f:
        f.write(patch)
        patch_path = f.name

    try:
        proc = await asyncio.create_subprocess_exec(
            "patch", "-p1", "-i", patch_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode == 0:
            return f"Patch applied successfully.\n{stdout.decode()}"
        return f"Patch failed (exit {proc.returncode}):\n{stderr.decode()}"
    except FileNotFoundError:
        return "Error: 'patch' command not found. Install it with: brew install patch"
    except asyncio.TimeoutError:
        return "Error: patch command timed out"
    finally:
        try:
            os.unlink(patch_path)
        except OSError:
            pass


async def _list_dir(path: str, recursive: bool = False) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"Error: path not found: {path}"
    if not p.is_dir():
        return f"Error: {path} is a file — use read instead"
    try:
        if recursive:
            entries = sorted(p.rglob("*"))
        else:
            entries = sorted(p.iterdir())
        lines = []
        for entry in entries:
            kind = "d" if entry.is_dir() else "f"
            rel = entry.relative_to(p) if recursive else entry.name
            lines.append(f"[{kind}] {rel}")
        return "\n".join(lines) if lines else "(empty directory)"
    except PermissionError as e:
        return f"Error: permission denied: {e}"


async def _glob(pattern: str, cwd: str | None = None) -> str:
    base = Path(cwd).expanduser() if cwd else Path.cwd()
    # Expand ~ in pattern
    expanded = str(Path(pattern).expanduser()) if pattern.startswith("~") else pattern
    try:
        if Path(expanded).is_absolute():
            matches = sorted(glob_module.glob(expanded, recursive=True))
        else:
            matches = sorted(glob_module.glob(str(base / expanded), recursive=True))
        if not matches:
            return f"No files matched pattern: {pattern}"
        return "\n".join(matches)
    except Exception as e:
        return f"Error: {e}"


async def _delete(path: str, recursive: bool = False) -> str:
    import shutil
    p = Path(path).expanduser()
    if not p.exists():
        return f"Error: path not found: {path}"
    try:
        if p.is_file() or p.is_symlink():
            p.unlink()
            return f"Deleted file: {path}"
        if p.is_dir():
            if recursive:
                shutil.rmtree(p)
                return f"Deleted directory recursively: {path}"
            p.rmdir()
            return f"Deleted empty directory: {path}"
        return f"Error: unknown path type: {path}"
    except OSError as e:
        return f"Error deleting {path}: {e}"


async def _move(source: str, destination: str) -> str:
    import shutil
    src = Path(source).expanduser()
    dst = Path(destination).expanduser()
    if not src.exists():
        return f"Error: source not found: {source}"
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return f"Moved {source} → {destination}"
    except Exception as e:
        return f"Error moving {source}: {e}"
