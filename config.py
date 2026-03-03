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

    # Tool policy
    tools_allow: list[str] = field(
        default_factory=lambda: _env_list(
            "TOOLS_ALLOW",
            (
                "web_fetch,web_search,cron,memory_search,memory_get,memory_write,"
                "message,tts,pdf,image,"
                "sessions_spawn,sessions_list,sessions_history,sessions_send,"
                "session_status,subagents,agents_list"
            ),
        )
    )
    tools_deny: list[str] = field(
        default_factory=lambda: _env_list(
            "TOOLS_DENY",
            "exec,process,browser,write,edit,apply_patch",
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
    web_search_max_results: int = field(default_factory=lambda: _env_int("WEB_SEARCH_MAX_RESULTS", 5))

    # Exec
    exec_enabled: bool = field(default_factory=lambda: _env_bool("EXEC_ENABLED", False))
    exec_timeout_seconds: int = field(default_factory=lambda: _env_int("EXEC_TIMEOUT_SECONDS", 30))
    exec_working_dir: str = field(default_factory=lambda: _env("EXEC_WORKING_DIR", "~"))
    exec_confirmation_timeout_seconds: int = field(
        default_factory=lambda: _env_int("EXEC_CONFIRMATION_TIMEOUT_SECONDS", 120)
    )

    # Browser
    browser_enabled: bool = field(default_factory=lambda: _env_bool("BROWSER_ENABLED", False))
    browser_headless: bool = field(default_factory=lambda: _env_bool("BROWSER_HEADLESS", True))

    # Memory
    memory_enabled: bool = field(default_factory=lambda: _env_bool("MEMORY_ENABLED", True))
    memory_dir: str = field(default_factory=lambda: _env("MEMORY_DIR", "~/.pygate/memory"))

    # Cron
    cron_enabled: bool = field(default_factory=lambda: _env_bool("CRON_ENABLED", True))
    cron_db_path: str = field(default_factory=lambda: _env("CRON_DB_PATH", "~/.pygate/cron.db"))

    # Heartbeat (periodic agent wake)
    heartbeat_enabled: bool = field(default_factory=lambda: _env_bool("HEARTBEAT_ENABLED", False))
    heartbeat_schedule: str = field(
        default_factory=lambda: _env("HEARTBEAT_SCHEDULE", "0 * * * *")
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

    # General
    data_dir: str = field(default_factory=lambda: _env("DATA_DIR", "~/.pygate"))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))
    max_history_messages: int = field(default_factory=lambda: _env_int("MAX_HISTORY_MESSAGES", 100))
    max_tool_iterations: int = field(default_factory=lambda: _env_int("MAX_TOOL_ITERATIONS", 10))

    def validate(self) -> "Config":
        errors = []
        if not self.telegram_bot_token:
            errors.append("TELEGRAM_BOT_TOKEN is required")
        if not self.telegram_owner_id:
            errors.append("TELEGRAM_OWNER_ID is required")
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


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config().validate()
    return _config
