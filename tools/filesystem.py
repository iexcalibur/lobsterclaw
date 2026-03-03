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
    description=(
        "Apply a patch to files. Two formats are supported:\n\n"
        "1. OpenAI patch format (preferred — no external tools needed):\n"
        "   *** Begin Patch\n"
        "   *** Update File: path/to/file.py\n"
        "   @@ -old_line_context\n"
        "   -removed line\n"
        "   +added line\n"
        "    context line\n"
        "   *** End Patch\n\n"
        "2. Unified diff format (requires 'patch' binary):\n"
        "   --- a/path/to/file\n"
        "   +++ b/path/to/file\n"
        "   @@ ... @@\n\n"
        "Auto-detects format from content."
    ),
    parameters={
        "type": "object",
        "properties": {
            "patch": {
                "type": "string",
                "description": "Patch content in OpenAI format (*** Begin Patch) or unified diff format",
            },
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
    """
    Apply a patch. Auto-detects format:
      - OpenAI patch format: starts with '*** Begin Patch'
      - Unified diff: contains '--- ' / '+++ ' headers
    """
    patch = patch.strip()
    if patch.startswith("*** Begin Patch"):
        return _apply_openai_patch(patch)
    return await _apply_unified_patch(patch)


def _apply_openai_patch(patch: str) -> str:
    """
    Apply OpenAI patch format (as described in apply-patch.ts).

    Format:
      *** Begin Patch
      *** Update File: relative/path.ext
      @@ optional context hint
      -deleted line
      +added line
       context line (space prefix)
      *** End Patch

    Other directives:
      *** Add File: path      — create a new file
      *** Delete File: path   — delete a file
      *** Rename File: old -> new
    """
    import re

    lines = patch.splitlines()
    results: list[str] = []
    i = 0

    # Skip '*** Begin Patch'
    if lines and lines[i].strip() == "*** Begin Patch":
        i += 1

    while i < len(lines):
        line = lines[i]

        if line.startswith("*** End Patch"):
            break

        # --- Add File ---
        if line.startswith("*** Add File: "):
            path = line[len("*** Add File: "):].strip()
            i += 1
            content_lines: list[str] = []
            while i < len(lines) and not lines[i].startswith("***"):
                content_lines.append(lines[i][1:] if lines[i].startswith("+") else lines[i])
                i += 1
            p = Path(path).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("\n".join(content_lines), encoding="utf-8")
            results.append(f"Added: {path}")
            continue

        # --- Delete File ---
        if line.startswith("*** Delete File: "):
            path = line[len("*** Delete File: "):].strip()
            i += 1
            p = Path(path).expanduser()
            if p.exists():
                p.unlink()
                results.append(f"Deleted: {path}")
            else:
                results.append(f"Skipped (not found): {path}")
            continue

        # --- Rename File ---
        if line.startswith("*** Rename File: "):
            spec = line[len("*** Rename File: "):].strip()
            if " -> " in spec:
                old, new = spec.split(" -> ", 1)
                import shutil
                src = Path(old.strip()).expanduser()
                dst = Path(new.strip()).expanduser()
                if src.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dst))
                    results.append(f"Renamed: {old} → {new}")
                else:
                    results.append(f"Error: source not found for rename: {old}")
            i += 1
            continue

        # --- Update File ---
        if line.startswith("*** Update File: "):
            path = line[len("*** Update File: "):].strip()
            i += 1
            p = Path(path).expanduser()
            if not p.exists():
                results.append(f"Error: file not found: {path}")
                # Skip to next directive
                while i < len(lines) and not lines[i].startswith("***"):
                    i += 1
                continue

            file_text = p.read_text(encoding="utf-8", errors="replace")
            file_lines = file_text.splitlines()

            # Collect all hunks for this file
            hunks: list[list[str]] = []
            current_hunk: list[str] = []
            while i < len(lines) and not lines[i].startswith("*** "):
                l = lines[i]
                if l.startswith("@@"):
                    if current_hunk:
                        hunks.append(current_hunk)
                    current_hunk = []
                    i += 1
                    continue
                current_hunk.append(l)
                i += 1
            if current_hunk:
                hunks.append(current_hunk)

            # Apply hunks in order
            try:
                patched = _apply_hunks(file_lines, hunks, path)
                p.write_text(patched, encoding="utf-8")
                results.append(f"Updated: {path}")
            except Exception as e:
                results.append(f"Error updating {path}: {e}")
            continue

        i += 1

    return "\n".join(results) if results else "No changes applied."


def _apply_hunks(file_lines: list[str], hunks: list[list[str]], path: str) -> str:
    """
    Apply a list of hunks to file_lines. Each hunk is a list of lines:
      ' ' prefix = context (must match)
      '-' prefix = delete
      '+' prefix = insert
    Returns the patched file as a string.
    """
    result = list(file_lines)
    # Apply hunks with a sliding offset
    offset = 0

    for hunk in hunks:
        context = [l[1:] if l.startswith((" ", "-", "+")) else l for l in hunk]
        search_lines = [l[1:] for l in hunk if not l.startswith("+")]

        # Find the location of the search_lines in result
        pos = _find_hunk_position(result, search_lines, offset)
        if pos < 0:
            raise ValueError(
                f"Could not locate hunk in {path}. "
                f"Expected context: {search_lines[:3]}"
            )

        # Build replacement
        replacement: list[str] = []
        for l in hunk:
            if l.startswith("+"):
                replacement.append(l[1:])
            elif l.startswith("-"):
                pass  # delete
            else:
                replacement.append(l[1:] if l.startswith(" ") else l)

        result[pos:pos + len(search_lines)] = replacement
        offset = pos + len(replacement)

    return "\n".join(result) + ("\n" if file_lines else "")


def _find_hunk_position(lines: list[str], search: list[str], start_hint: int = 0) -> int:
    """Find the first position in lines where search matches, starting near start_hint."""
    if not search:
        return start_hint
    n = len(lines)
    m = len(search)
    # Try from start_hint first, then scan all
    for begin in list(range(start_hint, n)) + list(range(0, start_hint)):
        if begin + m > n:
            continue
        if all(lines[begin + j].rstrip() == search[j].rstrip() for j in range(m)):
            return begin
    return -1


async def _apply_unified_patch(patch: str) -> str:
    """Apply a unified diff patch via the system 'patch' command."""
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
