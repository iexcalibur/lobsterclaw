from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_bool(key: str, default: bool = False) -> bool:
    return _env(key, "true" if default else "false").lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _env_list(key: str, default: str = "") -> list[str]:
    val = _env(key, default)
    return [x.strip() for x in val.split(",") if x.strip()]


def _env_float(key: str, default: float = 0.0) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default


@dataclass
class Config:
    # LLM
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "anthropic"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "claude-opus-4-5"))
    llm_max_tokens: int = field(default_factory=lambda: _env_int("LLM_MAX_TOKENS", 8096))

    # Telegram
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_owner_id: int = field(default_factory=lambda: _env_int("TELEGRAM_OWNER_ID"))
    # Multi-user DM allowlist: comma-separated Telegram user IDs (besides owner)
    telegram_allow_from: list[int] = field(
        default_factory=lambda: [int(x) for x in _env_list("TELEGRAM_ALLOW_FROM") if x.isdigit()]
    )
    # DM policy: "owner" (default, owner only) | "allowlist" (owner + allow_from) | "open" (anyone)
    telegram_dm_policy: str = field(default_factory=lambda: _env("TELEGRAM_DM_POLICY", "owner"))
    # Group policy: "disabled" (default) | "open" (anyone) | "allowlist"
    telegram_group_policy: str = field(default_factory=lambda: _env("TELEGRAM_GROUP_POLICY", "disabled"))
    # Group allowlist: comma-separated chat IDs
    telegram_group_allowlist: list[int] = field(
        default_factory=lambda: [int(x) for x in _env_list("TELEGRAM_GROUP_ALLOWLIST") if x.lstrip("-").isdigit()]
    )
    # Link previews in outgoing messages
    telegram_link_preview: bool = field(default_factory=lambda: _env_bool("TELEGRAM_LINK_PREVIEW", True))
    # Voice transcription (Whisper via OpenAI API)
    telegram_voice_transcription: bool = field(
        default_factory=lambda: _env_bool("TELEGRAM_VOICE_TRANSCRIPTION", False)
    )
    # Mention gating: group messages only trigger agent if bot is @mentioned or reply-to-bot
    telegram_mention_required: bool = field(
        default_factory=lambda: _env_bool("TELEGRAM_MENTION_REQUIRED", False)
    )
    # requireTopic: only respond inside this forum thread_id (0 = disabled)
    telegram_require_topic: int = field(default_factory=lambda: _env_int("TELEGRAM_REQUIRE_TOPIC", 0))
    # Per-chat policy overrides: JSON dict {"chat_id": "policy"} — checked before global policy
    telegram_chat_policies_json: str = field(
        default_factory=lambda: _env("TELEGRAM_CHAT_POLICIES", "{}")
    )
    # Callback auth policy: who can press agent inline buttons
    # "owner" (default) | "allowlist" (owner + allow_from) | "open" (anyone)
    telegram_callback_policy: str = field(
        default_factory=lambda: _env("TELEGRAM_CALLBACK_POLICY", "owner")
    )
    # Global switch for auto reaction lifecycle on inbound messages.
    telegram_reactions_enabled: bool = field(
        default_factory=lambda: _env_bool("TELEGRAM_REACTIONS_ENABLED", True)
    )
    # Reaction lifecycle emojis
    telegram_reaction_thinking: str = field(
        default_factory=lambda: _env("TELEGRAM_REACTION_THINKING", "👀")
    )
    telegram_reaction_working: str = field(
        default_factory=lambda: _env("TELEGRAM_REACTION_WORKING", "⚙")
    )
    telegram_reaction_done: str = field(
        default_factory=lambda: _env("TELEGRAM_REACTION_DONE", "✅")
    )
    telegram_reaction_error: str = field(
        default_factory=lambda: _env("TELEGRAM_REACTION_ERROR", "❌")
    )
    telegram_reaction_done_clear_secs: float = field(
        default_factory=lambda: _env_float("TELEGRAM_REACTION_DONE_CLEAR_SECS", 3.0)
    )
    # Reaction fallback list: comma-separated emojis tried in order on REACTION_INVALID
    telegram_reaction_fallback: list[str] = field(
        default_factory=lambda: _env_list("TELEGRAM_REACTION_FALLBACK", "👍,🔥")
    )
    # Sticker vision: download .webp thumbnail and pass to LLM as image
    telegram_sticker_vision: bool = field(
        default_factory=lambda: _env_bool("TELEGRAM_STICKER_VISION", False)
    )
    # Sticker cache persistence path (SQLite)
    telegram_sticker_cache_db: str = field(
        default_factory=lambda: _env("TELEGRAM_STICKER_CACHE_DB", "~/.lobsterclaw/sticker_cache.db")
    )
    # Webhook mode: set TELEGRAM_WEBHOOK_URL to use webhooks instead of polling
    telegram_webhook_url: str = field(default_factory=lambda: _env("TELEGRAM_WEBHOOK_URL", ""))
    telegram_webhook_port: int = field(default_factory=lambda: _env_int("TELEGRAM_WEBHOOK_PORT", 8443))
    telegram_webhook_secret: str = field(default_factory=lambda: _env("TELEGRAM_WEBHOOK_SECRET", ""))
    # Polling offset persistence: survives restarts without re-processing old messages
    telegram_polling_offset_path: str = field(
        default_factory=lambda: _env("TELEGRAM_POLLING_OFFSET_PATH", "~/.lobsterclaw/telegram_offset.json")
    )
    # Multi-account: JSON array of {label, token, owner_id, dm_policy?, group_policy?}
    telegram_accounts_json: str = field(
        default_factory=lambda: _env("TELEGRAM_ACCOUNTS", "")
    )

    # Tool policy
    tools_allow: list[str] = field(
        default_factory=lambda: _env_list(
            "TOOLS_ALLOW",
            (
                "web_fetch,web_search,cron,read,write,edit,glob,list_dir,"
                "memory_search,memory_get,memory_write,memory_list,memory_delete,"
                "message,tts,pdf,image,"
                "sessions_spawn,sessions_list,sessions_history,sessions_send,"
                "session_status,subagents,agents_list"
            ),
        )
    )
    tools_deny: list[str] = field(
        default_factory=lambda: _env_list(
            "TOOLS_DENY",
            "exec,process,browser,apply_patch,delete,move,gateway",
        )
    )
    tools_require_confirmation: list[str] = field(
        default_factory=lambda: _env_list("TOOLS_REQUIRE_CONFIRMATION", "")
    )

    # Web search
    web_search_provider: str = field(default_factory=lambda: _env("WEB_SEARCH_PROVIDER", "brave"))
    brave_api_key: str = field(default_factory=lambda: _env("BRAVE_API_KEY"))
    perplexity_api_key: str = field(default_factory=lambda: _env("PERPLEXITY_API_KEY"))
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    grok_api_key: str = field(default_factory=lambda: _env("GROK_API_KEY"))
    kimi_api_key: str = field(default_factory=lambda: _env("KIMI_API_KEY"))
    web_search_max_results: int = field(default_factory=lambda: _env_int("WEB_SEARCH_MAX_RESULTS", 10))
    google_search_api_key: str = field(default_factory=lambda: _env("GOOGLE_SEARCH_API_KEY"))
    google_search_cx: str = field(default_factory=lambda: _env("GOOGLE_SEARCH_CX"))

    # Exec
    exec_enabled: bool = field(default_factory=lambda: _env_bool("EXEC_ENABLED", False))
    exec_elevated_enabled: bool = field(default_factory=lambda: _env_bool("EXEC_ELEVATED_ENABLED", False))
    exec_timeout_seconds: int = field(default_factory=lambda: _env_int("EXEC_TIMEOUT_SECONDS", 30))
    exec_working_dir: str = field(default_factory=lambda: _env("EXEC_WORKING_DIR", "~"))
    exec_confirmation_timeout_seconds: int = field(
        default_factory=lambda: _env_int("EXEC_CONFIRMATION_TIMEOUT_SECONDS", 120)
    )

    # Browser
    browser_enabled: bool = field(default_factory=lambda: _env_bool("BROWSER_ENABLED", False))
    browser_headless: bool = field(default_factory=lambda: _env_bool("BROWSER_HEADLESS", True))

    # Additional channels (stub-compatible; disabled by default)
    discord_enabled: bool = field(default_factory=lambda: _env_bool("DISCORD_ENABLED", False))
    slack_enabled: bool = field(default_factory=lambda: _env_bool("SLACK_ENABLED", False))
    whatsapp_enabled: bool = field(default_factory=lambda: _env_bool("WHATSAPP_ENABLED", False))
    # Canvas
    canvas_telegram_render: bool = field(default_factory=lambda: _env_bool("CANVAS_TELEGRAM_RENDER", True))

    # Memory
    memory_enabled: bool = field(default_factory=lambda: _env_bool("MEMORY_ENABLED", True))
    memory_dir: str = field(default_factory=lambda: _env("MEMORY_DIR", "~/.lobsterclaw/memory"))
    memory_semantic: bool = field(
        default_factory=lambda: _env_bool("MEMORY_SEMANTIC", False)
    )  # Enable OpenAI embedding-based semantic search (requires OPENAI_API_KEY)

    # Cron
    cron_enabled: bool = field(default_factory=lambda: _env_bool("CRON_ENABLED", True))
    cron_db_path: str = field(default_factory=lambda: _env("CRON_DB_PATH", "~/.lobsterclaw/cron.db"))

    # Heartbeat (periodic agent wake)
    heartbeat_enabled: bool = field(default_factory=lambda: _env_bool("HEARTBEAT_ENABLED", False))
    heartbeat_schedule: str = field(
        default_factory=lambda: _env("HEARTBEAT_SCHEDULE", "0 * * * *")
    )
    # Active hours: heartbeat/cron wakes only fire in this local hour range (0-23, inclusive)
    heartbeat_active_hours_start: int = field(
        default_factory=lambda: _env_int("HEARTBEAT_ACTIVE_HOURS_START", 0)
    )
    heartbeat_active_hours_end: int = field(
        default_factory=lambda: _env_int("HEARTBEAT_ACTIVE_HOURS_END", 23)
    )

    # TTS
    tts_enabled: bool = field(default_factory=lambda: _env_bool("TTS_ENABLED", True))
    tts_voice: str = field(default_factory=lambda: _env("TTS_VOICE", "en-US-GuyNeural"))

    # Sub-agents
    subagents_enabled: bool = field(default_factory=lambda: _env_bool("SUBAGENTS_ENABLED", True))
    subagents_max_depth: int = field(default_factory=lambda: _env_int("SUBAGENTS_MAX_DEPTH", 3))
    subagents_max_children: int = field(default_factory=lambda: _env_int("SUBAGENTS_MAX_CHILDREN", 5))

    # Tool result truncation
    tool_result_max_chars: int = field(
        default_factory=lambda: _env_int("TOOL_RESULT_MAX_CHARS", 50_000)
    )

    # LLM advanced
    # Extended thinking budget for Anthropic (0 = disabled; >0 = token budget passed to the API)
    llm_thinking_budget: int = field(default_factory=lambda: _env_int("LLM_THINKING_BUDGET", 0))
    # Maximum API retries on transient errors (rate-limit, overload, 5xx)
    llm_max_retries: int = field(default_factory=lambda: _env_int("LLM_MAX_RETRIES", 3))
    # Streaming: send partial text chunks as editable preview messages
    llm_streaming: bool = field(default_factory=lambda: _env_bool("LLM_STREAMING", True))
    # Minimum seconds between streaming preview edits (Telegram rate-limit guard)
    llm_stream_min_edit_interval: float = field(
        default_factory=lambda: _env_float("LLM_STREAM_MIN_EDIT_INTERVAL", 3.0)
    )
    # Show extended thinking blocks in Telegram (shown as spoiler/code if true)
    llm_show_thinking: bool = field(default_factory=lambda: _env_bool("LLM_SHOW_THINKING", False))

    # Canvas host (FastAPI + WebSocket server)
    canvas_host_enabled: bool = field(default_factory=lambda: _env_bool("CANVAS_HOST_ENABLED", False))
    canvas_host_port: int = field(default_factory=lambda: _env_int("CANVAS_HOST_PORT", 7681))
    canvas_host_bind: str = field(default_factory=lambda: _env("CANVAS_HOST_BIND", "127.0.0.1"))
    # Dev mode: Next.js dev server proxied; production: serve built static files
    canvas_frontend_dev: bool = field(default_factory=lambda: _env_bool("CANVAS_FRONTEND_DEV", False))
    canvas_frontend_dev_port: int = field(
        default_factory=lambda: _env_int("CANVAS_FRONTEND_DEV_PORT", 3000)
    )
    # How long wait_event polls for a user interaction (seconds)
    canvas_event_timeout: int = field(default_factory=lambda: _env_int("CANVAS_EVENT_TIMEOUT", 60))

    # Gateway (Mission Control dashboard)
    gateway_enabled: bool = field(default_factory=lambda: _env_bool("GATEWAY_ENABLED", True))
    gateway_port: int = field(default_factory=lambda: _env_int("GATEWAY_PORT", 4400))
    gateway_bind: str = field(default_factory=lambda: _env("GATEWAY_BIND", "127.0.0.1"))

    # Agent identity
    # Human-readable agent identifier shown in the ## Runtime prompt line.
    agent_id: str = field(default_factory=lambda: _env("AGENT_ID", ""))
    # "full" (default), "minimal" (sub-agents), or "none" (raw identity only)
    prompt_mode: str = field(default_factory=lambda: _env("PROMPT_MODE", "full"))

    # Reply tokens — must match the values injected into the prompt
    # Agent uses NO_REPLY when it has nothing to say; Telegram channel suppresses it.
    silent_reply_token: str = field(default_factory=lambda: _env("SILENT_REPLY_TOKEN", "NO_REPLY"))
    # Agent uses HEARTBEAT_OK to acknowledge heartbeat polls with no action needed.
    heartbeat_ok_token: str = field(default_factory=lambda: _env("HEARTBEAT_OK_TOKEN", "HEARTBEAT_OK"))

    # Pre-compaction memory flush (mirrors OpenClaw's memoryFlush feature)
    # Before compacting history, inject a special agent turn to write memories to disk.
    memory_flush_enabled: bool = field(
        default_factory=lambda: _env_bool("MEMORY_FLUSH_ENABLED", True)
    )
    # How close to the compaction threshold (in tokens) before triggering a flush
    memory_flush_soft_tokens: int = field(
        default_factory=lambda: _env_int("MEMORY_FLUSH_SOFT_TOKENS", 4000)
    )

    # Reaction guidance for the agent in the prompt
    # "off" (default) | "minimal" | "extensive"
    reaction_guidance_level: str = field(
        default_factory=lambda: _env("REACTION_GUIDANCE_LEVEL", "off")
    )

    # Memory citations mode in the prompt: "off" | "inline" | "footnote"
    memory_citations_mode: str = field(
        default_factory=lambda: _env("MEMORY_CITATIONS_MODE", "off")
    )

    # Memory index persistence: keep a SQLite index file that is updated incrementally
    # instead of rebuilding from scratch on every search (mtime-based invalidation).
    memory_index_persist: bool = field(
        default_factory=lambda: _env_bool("MEMORY_INDEX_PERSIST", True)
    )
    # Embedding provider for semantic memory search: "openai" | "gemini" | "none"
    memory_embedding_provider: str = field(
        default_factory=lambda: _env("MEMORY_EMBEDDING_PROVIDER", "none")
    )

    # Model aliases: JSON dict mapping short names → full model IDs.
    # e.g. {"sonnet": "claude-sonnet-4-5", "haiku": "claude-haiku-4-5", "gpt4o": "gpt-4o"}
    model_aliases_json: str = field(
        default_factory=lambda: _env("MODEL_ALIASES", "{}")
    )

    # Reasoning format mode — controls ## Reasoning Format section in the prompt.
    # "auto" = detect from model name (e.g., deepseek-r1, o1, o3)
    # "on"   = always include the section
    # "off"  = never include it
    reasoning_mode: str = field(default_factory=lambda: _env("REASONING_MODE", "auto"))

    # User timezone name shown in the prompt (e.g., "America/New_York").
    # Empty = use local system timezone via strftime.
    user_timezone: str = field(default_factory=lambda: _env("USER_TIMEZONE", ""))

    # Shell name shown in the ## Runtime line (e.g., "zsh", "bash", "fish").
    # Empty = auto-detect from $SHELL.
    shell: str = field(default_factory=lambda: _env("SHELL_NAME", ""))

    # Owner display name used in the ## Authorized Senders section (e.g., "Peter").
    # Empty = omit name, show hashed ID only.
    owner_display_name: str = field(
        default_factory=lambda: _env("OWNER_DISPLAY_NAME", "")
    )

    # Transcript repair: strip orphaned tool_use / tool_result blocks before
    # sending conversation history to the LLM API.
    transcript_repair_enabled: bool = field(
        default_factory=lambda: _env_bool("TRANSCRIPT_REPAIR_ENABLED", True)
    )

    # Pre/post tool hooks: fire registered async callbacks before/after every tool execution.
    hooks_enabled: bool = field(
        default_factory=lambda: _env_bool("HOOKS_ENABLED", True)
    )

    # Show TTS hint in the system prompt (how to invoke TTS for voice replies).
    tts_hint_in_prompt: bool = field(
        default_factory=lambda: _env_bool("TTS_HINT_IN_PROMPT", True)
    )

    # General
    data_dir: str = field(default_factory=lambda: _env("DATA_DIR", "~/.lobsterclaw"))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))
    max_history_messages: int = field(default_factory=lambda: _env_int("MAX_HISTORY_MESSAGES", 100))
    max_tool_iterations: int = field(default_factory=lambda: _env_int("MAX_TOOL_ITERATIONS", 10))

    @property
    def model_aliases(self) -> dict[str, str]:
        """Short name → full model ID map from MODEL_ALIASES JSON."""
        import json
        try:
            raw = self.model_aliases_json
            return json.loads(raw) if raw and raw.strip() not in ("", "{}") else {}
        except Exception:
            return {}

    def resolve_model(self, name: str) -> str:
        """Resolve an alias or model name to its canonical ID."""
        return self.model_aliases.get(name, name)

    @property
    def telegram_chat_policies(self) -> dict[str, str]:
        """Per-chat policy overrides: {str(chat_id): policy_name}."""
        import json
        try:
            raw = self.telegram_chat_policies_json
            return json.loads(raw) if raw and raw.strip() not in ("", "{}") else {}
        except Exception:
            return {}

    @property
    def telegram_accounts(self) -> list[dict]:
        """Multi-account list from TELEGRAM_ACCOUNTS JSON."""
        import json
        try:
            raw = self.telegram_accounts_json
            return json.loads(raw) if raw and raw.strip() not in ("", "[]") else []
        except Exception:
            return []

    def validate(self) -> "Config":
        errors = []
        # In multi-account mode, individual accounts may override the global token
        accounts = self.telegram_accounts
        if not accounts and not self.telegram_bot_token:
            errors.append("TELEGRAM_BOT_TOKEN is required (or set TELEGRAM_ACCOUNTS)")
        if not accounts and not self.telegram_owner_id:
            errors.append("TELEGRAM_OWNER_ID is required (or set TELEGRAM_ACCOUNTS)")
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            errors.append("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
        if self.llm_provider == "openai" and not self.openai_api_key:
            errors.append("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        if errors:
            raise ValueError("Config errors:\n" + "\n".join(f"  - {e}" for e in errors))
        return self

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).expanduser()

    @property
    def memory_path(self) -> Path:
        return Path(self.memory_dir).expanduser()

    @property
    def cron_db(self) -> Path:
        return Path(self.cron_db_path).expanduser()

    @property
    def sessions_db(self) -> Path:
        return self.data_path / "sessions.db"

    @property
    def canvas_db(self) -> Path:
        return self.data_path / "canvas.db"

    @property
    def canvas_host_url(self) -> str:
        return f"http://{self.canvas_host_bind}:{self.canvas_host_port}"

    @property
    def canvas_host_ws_url(self) -> str:
        return f"ws://{self.canvas_host_bind}:{self.canvas_host_port}"


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config().validate()
    return _config
