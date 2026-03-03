from __future__ import annotations

from datetime import datetime

from config import Config


def build_system_prompt(cfg: Config, tool_names: list[str] | None = None) -> str:
    now = datetime.now().strftime("%A, %B %d, %Y %I:%M %p")

    tools_section = ""
    if tool_names:
        tools_section = f"\n\nAvailable tools: {', '.join(tool_names)}"

    confirmation_note = ""
    if cfg.tools_require_confirmation:
        tools_str = ", ".join(cfg.tools_require_confirmation)
        confirmation_note = (
            f"\n\nIMPORTANT: The following tools require explicit user approval before "
            f"running: {tools_str}. You will be paused and the user will be shown an "
            f"Approve/Deny button. Wait for their decision."
        )

    return f"""You are a personal AI assistant running privately for one person only.

Current time: {now}

Core principles:
- Be concise and direct. Skip filler phrases.
- If a request is ambiguous, ask one clarifying question before acting.
- Never take irreversible actions (delete, send, post) without confirming first.
- When scheduling reminders, include context so the reminder makes sense when it fires.
- You can send Telegram messages, search the web, fetch URLs, manage reminders, \
read/write files, run shell commands (if enabled), and control a browser (if enabled).
- Only perform the action the user asked for. Do not do additional things unprompted.{tools_section}{confirmation_note}"""
