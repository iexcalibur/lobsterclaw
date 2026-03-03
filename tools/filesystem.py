from __future__ import annotations

import difflib
from pathlib import Path

from tools.registry import ToolDefinition

READ_TOOL = ToolDefinition(
    name="read",
    description="Read the contents of a file.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or ~ path to the file"},
            "offset": {"type": "integer", "description": "Line number to start from (1-indexed, optional)"},
            "limit": {"type": "integer", "description": "Max number of lines to read (optional)"},
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
    description="Replace an exact string in a file with new text.",
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
    description="Apply a unified diff patch to files.",
    parameters={
        "type": "object",
        "properties": {
            "patch": {"type": "string", "description": "Unified diff patch string"},
        },
        "required": ["patch"],
    },
    fn=lambda **kw: _apply_patch(**kw),
)


async def _read(path: str, offset: int | None = None, limit: int | None = None) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"Error: file not found: {path}"
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
        return f"Error: old_string appears {count} times — make it more specific"
    new_text = text.replace(old_string, new_string, 1)
    p.write_text(new_text, encoding="utf-8")
    return f"Edited {path} successfully"


async def _apply_patch(patch: str) -> str:
    """Apply a unified diff patch. Very basic implementation."""
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as f:
        f.write(patch)
        patch_path = f.name

    try:
        result = subprocess.run(
            ["patch", "-p1", "-i", patch_path],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return f"Patch applied successfully.\n{result.stdout}"
        return f"Patch failed (exit {result.returncode}):\n{result.stderr}"
    except FileNotFoundError:
        return "Error: 'patch' command not found. Install it with: brew install patch"
    finally:
        Path(patch_path).unlink(missing_ok=True)
