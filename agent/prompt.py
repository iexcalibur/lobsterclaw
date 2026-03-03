"""
System prompt builder — mirrors OpenClaw's buildAgentSystemPrompt.

Sections (in order, same as OpenClaw's full-mode prompt):
  ## Tooling              — tool names + one-line summaries, ordered by importance
  ## Tool Call Style      — when to narrate, when to just call the tool
  ## Safety               — AI safety constraints
  ## Skills               — loaded from workspace/skills/
  ## Memory               — memory search guidance + citations mode
  ## Self-Update          — gateway restart/update guidance (if gateway tool present)
  ## Workspace            — working directory, workspace notes
  ## Project Context      — workspace MD files (SOUL, USER, MEMORY, IDENTITY, TOOLS, …)
  ## Silent Replies       — NO_REPLY token
  ## Heartbeats           — HEARTBEAT_OK ack contract
  ## Reply Tags           — [[reply_to_current]] / [[reply_to:<id>]] threading hints
  ## Reasoning Format     — <think>…</think>/<final>…</final> (reasoning models only)
  ## Voice (TTS)          — how to request a TTS reply (if tts in tools)
  ## Model Aliases        — short name → full model id table (for sessions_spawn)
  ## Authorized Senders   — owner identity + multi-user note
  ## Messaging            — Telegram routing / message tool guidance
  ## Reactions            — reaction guidance (minimal/extensive)
  ## Runtime              — OS/Python/model/channel/agent_id metadata
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import Config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------
# Reasoning-model name patterns (trigger ## Reasoning Format)
# ---------------------------------------------------------------
_REASONING_MODEL_RE = re.compile(
    r"(?i)(deepseek[-_]r\d|o1|o3|o4|gemini[-_].*thinking|claude.*3-7|"
    r"qwq|skywork-o|step[-_]r)"
)

# ---------------------------------------------------------------
# Tool summaries — matches OpenClaw's coreToolSummaries dict
# ---------------------------------------------------------------

CORE_TOOL_SUMMARIES: dict[str, str] = {
    "read": "Read file contents",
    "write": "Create or overwrite files",
    "edit": "Make precise edits to files",
    "apply_patch": "Apply multi-file patches",
    "glob": "Find files by glob pattern",
    "list_dir": "List directory contents",
    "delete": "Delete files",
    "move": "Move or rename files",
    "exec": "Run shell commands (supports background via yield_ms)",
    "process": "Manage background exec sessions",
    "web_search": "Search the web",
    "web_fetch": "Fetch and extract readable content from a URL",
    "browser": "Control web browser",
    "canvas": "Present/eval/snapshot the Canvas",
    "nodes": "List/describe/notify/camera on paired nodes",
    "cron": (
        "Manage cron jobs and wake events (use for reminders; when scheduling a reminder, "
        "write the systemEvent text as something that will read like a reminder when it fires; "
        "include recent context in reminder text if appropriate)"
    ),
    "message": "Send messages and channel actions",
    "gateway": "Restart, apply config, or run updates on the running LobsterClaw process",
    "agents_list": "List agent ids allowed for sessions_spawn",
    "sessions_list": "List other sessions with filters",
    "sessions_history": "Fetch history for another session",
    "sessions_send": "Send a message to another session",
    "sessions_spawn": "Spawn an isolated sub-agent session",
    "subagents": "List, steer, or kill sub-agent runs",
    "session_status": "Show session status (usage, time, model); use for model-use questions (📊 session_status)",
    "memory_search": "Search memory files (FTS + optional semantic)",
    "memory_get": "Read a specific memory file",
    "memory_write": "Write a memory file",
    "memory_list": "List all memory files",
    "memory_delete": "Delete a memory file",
    "pdf": "Extract text and metadata from PDF files",
    "image": "Analyze an image with vision model",
    "tts": "Convert text to speech and send as voice",
}

# Canonical tool order (lower index = more important)
TOOL_ORDER = [
    "read", "write", "edit", "apply_patch", "glob", "list_dir", "delete", "move",
    "exec", "process",
    "web_search", "web_fetch", "browser",
    "canvas", "nodes", "cron", "message", "gateway",
    "agents_list", "sessions_list", "sessions_history", "sessions_send",
    "sessions_spawn", "subagents", "session_status",
    "memory_search", "memory_get", "memory_write", "memory_list", "memory_delete",
    "pdf", "image", "tts",
]


def build_system_prompt(
    cfg: "Config",
    tool_names: list[str] | None = None,
    workspace_dir: Path | None = None,
    include_heartbeat: bool = False,
    prompt_mode: str | None = None,
    runtime_info: dict | None = None,
    extra_system_prompt: str = "",
) -> str:
    """
    Build the full agent system prompt.

    Args:
        cfg:               Config instance
        tool_names:        list of active tool names (used for ## Tooling section)
        workspace_dir:     override workspace directory for MD files
        include_heartbeat: include HEARTBEAT.md in context (heartbeat runs only)
        prompt_mode:       "full" (default) | "minimal" (sub-agents) | "none"
        runtime_info:      dict with channel/capabilities overrides for ## Runtime
        extra_system_prompt: injected as ## Subagent Context (minimal) or ## Group Chat Context (full)
    """
    from agent.workspace import load_workspace_context
    from agent.skills import format_skills_for_prompt

    mode = prompt_mode or getattr(cfg, "prompt_mode", "full")
    is_minimal = mode in ("minimal", "none")

    if mode == "none":
        return "You are a personal assistant running inside LobsterClaw."

    silent_token = getattr(cfg, "silent_reply_token", "NO_REPLY")
    heartbeat_ok = getattr(cfg, "heartbeat_ok_token", "HEARTBEAT_OK")
    reaction_level = getattr(cfg, "reaction_guidance_level", "off")
    memory_citations = getattr(cfg, "memory_citations_mode", "off")

    # Runtime metadata
    rt = runtime_info or {}
    channel = rt.get("channel", "telegram").lower()
    capabilities = rt.get("capabilities", ["reactions", "inline_buttons"])
    agent_id = rt.get("agent_id") or getattr(cfg, "agent_id", "")
    repo_root = rt.get("repo_root", "")

    # Tool feature flags
    tool_names_lower = [t.lower() for t in (tool_names or [])]
    has_gateway = "gateway" in tool_names_lower
    has_sessions_spawn = "sessions_spawn" in tool_names_lower
    has_memory_tools = any(t.startswith("memory") for t in tool_names_lower)
    has_tts = "tts" in tool_names_lower

    # Reasoning mode
    reasoning_mode = getattr(cfg, "reasoning_mode", "auto")
    effective_model = cfg.llm_model
    if reasoning_mode == "auto":
        include_reasoning = bool(_REASONING_MODEL_RE.search(effective_model))
    elif reasoning_mode == "on":
        include_reasoning = True
    else:
        include_reasoning = False

    # Model aliases
    model_aliases: dict[str, str] = getattr(cfg, "model_aliases", {})

    # Workspace MD files
    workspace_context = load_workspace_context(
        workspace_dir=workspace_dir,
        include_heartbeat=include_heartbeat,
    )

    # Skills
    skills_text = format_skills_for_prompt()

    # Timestamps
    now = datetime.now()
    now_str = now.strftime("%A, %B %d, %Y %I:%M %p")
    tz = _get_tz(cfg)

    # Owner identity (hashed for display)
    owner_line = _build_owner_line(cfg)

    lines: list[str] = [
        "You are a personal assistant running inside LobsterClaw.",
        "",
        f"Current time: {now_str}{(' (' + tz + ')') if tz else ''}",
        "",
    ]

    # ----------------------------------------------------------------
    # ## Tooling
    # ----------------------------------------------------------------
    tool_lines = _build_tool_lines(tool_names or [])
    if tool_lines:
        lines += [
            "## Tooling",
            "Tool availability (filtered by policy):",
            "Tool names are case-sensitive. Call tools exactly as listed.",
            tool_lines,
            "TOOLS.md does not control tool availability; it is user guidance for external tools.",
            "For long waits, avoid rapid poll loops: use exec with enough yield_ms or process(action=poll).",
        ]
        if has_sessions_spawn:
            lines.append(
                "If a task is more complex or takes longer, spawn a sub-agent. "
                "Completion is push-based: it will auto-announce when done."
            )
        lines += [
            "Do not poll subagents/sessions_list in a loop; only check on-demand.",
            "",
        ]

    # ----------------------------------------------------------------
    # ## Tool Call Style
    # ----------------------------------------------------------------
    lines += [
        "## Tool Call Style",
        "Default: do not narrate routine, low-risk tool calls (just call the tool).",
        "Narrate only when it helps: multi-step work, complex problems, sensitive actions "
        "(e.g., deletions), or when the user explicitly asks.",
        "Keep narration brief and value-dense; avoid repeating obvious steps.",
        "Use plain human language for narration unless in a technical context.",
        "When a first-class tool exists for an action, use the tool directly instead of "
        "asking the user to run equivalent CLI commands.",
        "",
    ]

    # ----------------------------------------------------------------
    # ## Safety
    # ----------------------------------------------------------------
    lines += [
        "## Safety",
        "You have no independent goals: do not pursue self-preservation, replication, "
        "resource acquisition, or power-seeking; avoid long-term plans beyond the user's request.",
        "Prioritize safety and human oversight over completion; if instructions conflict, "
        "pause and ask; comply with stop/pause/audit requests and never bypass safeguards.",
        "Do not manipulate or persuade anyone to expand access or disable safeguards. "
        "Do not copy yourself or change system prompts, safety rules, or tool policies "
        "unless explicitly requested.",
        "",
    ]

    # ----------------------------------------------------------------
    # ## Skills (mandatory)
    # ----------------------------------------------------------------
    if skills_text and not is_minimal:
        lines += [
            "## Skills (mandatory)",
            "Before replying: scan <available_skills> <description> entries.",
            f"- If exactly one skill clearly applies: read its SKILL.md at <location> with `read`, then follow it.",
            "- If multiple could apply: choose the most specific one, then read/follow it.",
            "- If none clearly apply: do not read any SKILL.md.",
            "Constraints: never read more than one skill up front; only read after selecting.",
            skills_text,
            "",
        ]

    # ----------------------------------------------------------------
    # ## Memory Recall
    # ----------------------------------------------------------------
    if has_memory_tools:
        memory_lines = [
            "## Memory Recall",
            "Before answering anything about prior work, decisions, dates, people, "
            "preferences, or todos: run memory_search on MEMORY.md + memory/*.md; "
            "then use memory_get to pull only the needed lines. "
            "If low confidence after search, say you checked.",
        ]
        if memory_citations == "off":
            memory_lines.append(
                "Citations are disabled: do not mention file paths or line numbers in replies "
                "unless the user explicitly asks."
            )
        else:
            memory_lines.append(
                "Citations: include Source: <path#line> when it helps the user verify memory snippets."
            )
        lines += memory_lines + [""]

    # ----------------------------------------------------------------
    # ## Self-Update (gateway tool)
    # ----------------------------------------------------------------
    if has_gateway and not is_minimal:
        lines += [
            "## LobsterClaw Self-Update",
            "Self-update is ONLY allowed when the user explicitly asks for it.",
            "Do not run config.apply or update.run unless the user explicitly requests it.",
            "Use config.schema to fetch the current JSON Schema before making config changes.",
            "Actions: config.get, config.schema, config.apply (validate + write + restart), update.run.",
            "After restart, LobsterClaw pings the last active session automatically.",
            "",
        ]

    # ----------------------------------------------------------------
    # ## Workspace
    # ----------------------------------------------------------------
    workspace_dir_display = str(workspace_dir or Path.home() / ".lobsterclaw" / "workspace")
    if repo_root:
        workspace_dir_display = repo_root
    lines += [
        "## Workspace",
        f"Your working directory is: {workspace_dir_display}",
        "Treat this directory as the single global workspace for file operations "
        "unless explicitly instructed otherwise.",
        "These user-editable files are loaded by LobsterClaw and included below in Project Context.",
        "",
    ]

    # ----------------------------------------------------------------
    # ## Project Context (workspace MD files)
    # ----------------------------------------------------------------
    if workspace_context:
        soul_loaded = "SOUL.md" in workspace_context
        lines += ["## Project Context", ""]
        if soul_loaded:
            lines.append(
                "If SOUL.md is present, embody its persona and tone. "
                "Avoid stiff, generic replies; follow its guidance unless "
                "higher-priority instructions override it."
            )
        lines += ["The following workspace context files have been loaded:", "", workspace_context, ""]

    # ----------------------------------------------------------------
    # ## Silent Replies
    # ----------------------------------------------------------------
    if not is_minimal:
        lines += [
            "## Silent Replies",
            f"When you have nothing to say, respond with ONLY: {silent_token}",
            "",
            "⚠️ Rules:",
            "- It must be your ENTIRE message — nothing else",
            f'- Never append it to an actual response (never include "{silent_token}" in real replies)',
            "- Never wrap it in markdown or code blocks",
            "",
            f'❌ Wrong: "Here\'s help... {silent_token}"',
            f'❌ Wrong: `{silent_token}`',
            f"✅ Right: {silent_token}",
            "",
        ]

    # ----------------------------------------------------------------
    # ## Heartbeats
    # ----------------------------------------------------------------
    if not is_minimal:
        heartbeat_schedule = getattr(cfg, "heartbeat_schedule", "0 * * * *")
        lines += [
            "## Heartbeats",
            f"Heartbeat schedule: {heartbeat_schedule}",
            "If you receive a heartbeat poll and there is nothing that needs attention, reply exactly:",
            heartbeat_ok,
            f'LobsterClaw treats a leading/trailing "{heartbeat_ok}" as a heartbeat ack (and may discard it).',
            f"If something needs attention, do NOT include \"{heartbeat_ok}\"; "
            "reply with the alert text instead.",
            "",
        ]

    # ----------------------------------------------------------------
    # ## Reply Tags  (mirrors OpenClaw's replyTags section)
    # ----------------------------------------------------------------
    if channel == "telegram" and not is_minimal:
        lines += [
            "## Reply Tags",
            "To thread a reply to a specific message in the conversation, prefix your reply text with:",
            "  [[reply_to_current]]        — reply to the message that triggered this turn",
            "  [[reply_to:<message_id>]]   — reply to a specific Telegram message ID",
            "",
            "Rules:",
            "- The tag must appear at the very start of the reply text.",
            "- Omit the tag for standalone messages (no threading).",
            '- Example: [[reply_to_current]] Here is the answer you asked for.',
            '- Example: [[reply_to:12345]] Done — see the attached file.',
            "",
        ]

    # ----------------------------------------------------------------
    # ## Reasoning Format  (for reasoning-capable models)
    # ----------------------------------------------------------------
    if include_reasoning and not is_minimal:
        lines += [
            "## Reasoning Format",
            "This model supports extended reasoning. When solving complex problems:",
            "- Wrap internal reasoning in <think>…</think> tags.",
            "- Place the final answer in <final>…</final> tags (or just return it directly).",
            "- The reasoning block is shown to the user only when SHOW_THINKING is enabled; "
            "the final answer is always delivered.",
            "- Keep reasoning blocks focused; avoid restating the problem verbatim.",
            "",
        ]

    # ----------------------------------------------------------------
    # ## Voice (TTS)
    # ----------------------------------------------------------------
    if has_tts and getattr(cfg, "tts_hint_in_prompt", True) and not is_minimal:
        lines += [
            "## Voice (TTS)",
            "You can send a voice message instead of (or in addition to) text by calling the "
            "`tts` tool with your reply text.",
            "Use TTS when the user asks to 'say', 'read aloud', 'voice', or prefers audio output.",
            f"Default voice: {getattr(cfg, 'tts_voice', 'en-US-GuyNeural')}.",
            "",
        ]

    # ----------------------------------------------------------------
    # ## Model Aliases  (for sessions_spawn model= parameter)
    # ----------------------------------------------------------------
    if model_aliases and has_sessions_spawn and not is_minimal:
        alias_lines = [f"  {alias} → {model_id}" for alias, model_id in sorted(model_aliases.items())]
        lines += (
            ["## Model Aliases",
             "Short names accepted in sessions_spawn model= parameter:"]
            + alias_lines
            + [""]
        )

    # ----------------------------------------------------------------
    # ## Authorized Senders  (replaces "User Identity")
    # ----------------------------------------------------------------
    if owner_line:
        owner_display = getattr(cfg, "owner_display_name", "") or ""
        lines += ["## Authorized Senders"]
        if owner_display:
            lines.append(f"Owner: {owner_display} ({owner_line})")
        else:
            lines.append(owner_line)
        # Multi-user note
        allow_from: list = getattr(cfg, "telegram_allow_from", [])
        dm_policy: str = getattr(cfg, "telegram_dm_policy", "owner")
        if allow_from or dm_policy in ("open", "allowlist"):
            lines.append(
                "Other users may also message you. "
                "Do not assume every sender is the owner; "
                "adapt your response to who is actually writing."
            )
        lines += [""]

    # ----------------------------------------------------------------
    # ## Messaging (Telegram-specific)
    # ----------------------------------------------------------------
    if channel == "telegram" and not is_minimal:
        inline_buttons = "inline_buttons" in [str(c).lower() for c in capabilities]
        lines += _build_telegram_messaging_section(inline_buttons, silent_token)

    # ----------------------------------------------------------------
    # ## Reactions
    # ----------------------------------------------------------------
    if reaction_level != "off" and not is_minimal:
        lines += _build_reaction_section(reaction_level, channel)

    # ----------------------------------------------------------------
    # ## Tool Confirmation
    # ----------------------------------------------------------------
    if getattr(cfg, "tools_require_confirmation", []):
        tools_str = ", ".join(cfg.tools_require_confirmation)
        lines += [
            "## Tool Confirmation",
            f"The following tools require explicit user approval before running: {tools_str}.",
            "You will be paused and the user will be shown an Approve/Deny button. "
            "Wait for their decision.",
            "",
        ]

    # ----------------------------------------------------------------
    # Extra system prompt (sub-agent context or group chat context)
    # ----------------------------------------------------------------
    if extra_system_prompt and extra_system_prompt.strip():
        header = "## Subagent Context" if is_minimal else "## Group Chat Context"
        lines += [header, extra_system_prompt.strip(), ""]

    # ----------------------------------------------------------------
    # ## Runtime (always last before return)
    # ----------------------------------------------------------------
    lines += [
        "## Runtime",
        _build_runtime_line(cfg, channel, capabilities, agent_id, repo_root),
        "",
    ]

    return "\n".join(line for line in lines if line is not None)


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

def _build_tool_lines(tool_names: list[str]) -> str:
    if not tool_names:
        return ""
    available = {t.lower().strip() for t in tool_names if t.strip()}

    # Build ordered list: canonical order first, then extras alphabetically
    seen: set[str] = set()
    ordered: list[str] = []
    for canonical in TOOL_ORDER:
        if canonical in available:
            ordered.append(canonical)
            seen.add(canonical)
    for extra in sorted(available - seen):
        ordered.append(extra)

    # Build lines with summaries
    result_lines = []
    # Map lowercase → original casing from caller
    orig_casing = {t.lower().strip(): t for t in tool_names if t.strip()}
    for name in ordered:
        display = orig_casing.get(name, name)
        summary = CORE_TOOL_SUMMARIES.get(name)
        if summary:
            result_lines.append(f"- {display}: {summary}")
        else:
            result_lines.append(f"- {display}")

    return "\n".join(result_lines)


def _build_owner_line(cfg: "Config") -> str:
    try:
        owner_id = getattr(cfg, "telegram_owner_id", 0)
        if not owner_id:
            return ""
        h = hashlib.sha256(str(owner_id).encode()).hexdigest()[:8]
        return f"user#{h} (Telegram owner ID hidden for privacy)"
    except Exception:
        return ""


def _build_runtime_line(
    cfg: "Config",
    channel: str,
    capabilities: list[str],
    agent_id: str,
    repo_root: str,
) -> str:
    parts = []
    if agent_id:
        parts.append(f"agent={agent_id}")
    try:
        import socket
        parts.append(f"host={socket.gethostname()}")
    except Exception:
        pass
    parts.append(f"os={platform.system().lower()} ({platform.machine()})")
    parts.append(f"python={sys.version_info.major}.{sys.version_info.minor}")

    # Shell detection: config > env $SHELL > "unknown"
    shell_name = getattr(cfg, "shell", "") or ""
    if not shell_name:
        env_shell = os.environ.get("SHELL", "")
        shell_name = Path(env_shell).name if env_shell else ""
    if shell_name:
        parts.append(f"shell={shell_name}")

    if repo_root:
        parts.append(f"repo={repo_root}")
    parts.append(f"model={cfg.llm_model}")
    parts.append(f"channel={channel}")
    caps_str = ",".join(str(c) for c in capabilities) if capabilities else "none"
    parts.append(f"capabilities={caps_str}")
    return "Runtime: " + " ".join(p for p in parts if p)


def _build_telegram_messaging_section(
    inline_buttons: bool,
    silent_token: str = "NO_REPLY",
) -> list[str]:
    lines = [
        "## Messaging",
        f"- Reply in current session → automatically routes to the source channel (Telegram)",
        f"- Cross-session messaging → use sessions_send(sessionKey, message)",
        f"- Sub-agent orchestration → use subagents(action=list|steer|kill)",
        f"- Runtime-generated completion events may ask for a user update. Rewrite those in your "
        f"normal assistant voice and send the update (do not forward raw internal metadata or "
        f"default to {silent_token}).",
        f"- Never use exec/curl for provider messaging; LobsterClaw handles all routing internally.",
        "",
        "### message tool",
        "- Use `message` for proactive sends + channel actions (polls, reactions, etc.).",
        "- For `action=send`, include `to` and `message`.",
        f"- If you use `message` (`action=send`) to deliver your user-visible reply, "
        f"respond with ONLY: {silent_token} (avoid duplicate replies).",
        "- Target routing: send without 'to' → owner's DM. Set 'to' for a different chat_id.",
        "- For @username or t.me/ links, pass them as 'to' — resolved automatically.",
        "- Media: action=send_photo (path/URL), send_document (file), send_audio (MP3/OGG).",
        "- Stickers: action=sticker (file_id) or sticker_search (query).",
        "- Edit/delete: action=edit (message_id + text) or delete (message_id).",
        "- Reactions: action=react (message_id + emoji). Supported emojis only.",
        "- Pin/unpin: action=pin (message_id) or unpin (message_id).",
        "- Forum threads: pass message_thread_id to scope a message to a topic.",
    ]
    if inline_buttons:
        lines += [
            "- Inline buttons supported. Use `action=send` with `buttons=[[{text,callback_data}]]`.",
        ]
    lines += [""]
    return lines


def _build_reaction_section(level: str, channel: str) -> list[str]:
    if level == "minimal":
        guidance = "\n".join([
            f"Reactions are enabled for {channel} in MINIMAL mode.",
            "React ONLY when truly relevant:",
            "- Acknowledge important user requests or confirmations",
            "- Express genuine sentiment (humor, appreciation) sparingly",
            "- Avoid reacting to routine messages or your own replies",
            "Guideline: at most 1 reaction per 5-10 exchanges.",
        ])
    else:
        guidance = "\n".join([
            f"Reactions are enabled for {channel} in EXTENSIVE mode.",
            "Feel free to react liberally:",
            "- Acknowledge messages with appropriate emojis",
            "- Express sentiment and personality through reactions",
            "- React to interesting content, humor, or notable events",
            "- Use reactions to confirm understanding or agreement",
            "Guideline: react whenever it feels natural.",
        ])
    return ["## Reactions", guidance, ""]


def _get_tz(cfg: "Config | None" = None) -> str:
    """Return timezone abbreviation: config USER_TIMEZONE → local strftime → ''."""
    if cfg is not None:
        tz_name = getattr(cfg, "user_timezone", "")
        if tz_name:
            return tz_name
    try:
        return datetime.now().astimezone().strftime("%Z")
    except Exception:
        return ""
