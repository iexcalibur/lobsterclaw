<!-- LOBSTERCLAW README -->
<div align="center">

# 🦞 LobsterClaw

**A personal Python reimplementation of OpenClaw — with better security, faster cold-starts, and live plugin reloading.**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-Personal-lightgrey)](LICENSE)
[![Parity](https://img.shields.io/badge/OpenClaw%20Parity-100%25-brightgreen)](docs/parity-report.md)
[![Channels](https://img.shields.io/badge/Channels-Telegram%20%7C%20Discord-5865F2)](channels/)

</div>

---

## What is LobsterClaw?

LobsterClaw is a fully-featured personal AI assistant built in Python. It started as a port of [OpenClaw](https://github.com/openclaw/openclaw) (TypeScript) and has since grown to **100% feature parity** with the original — while fixing security vulnerabilities the original ships with and adding capabilities it doesn't have.

You talk to it through Telegram or Discord. It can browse the web, read and write files, run shell commands, schedule tasks, spawn sub-agents to work in parallel, remember things across sessions, and more — all through a natural conversation interface backed by Claude or GPT-4.

> **Why Python?** The original OpenClaw is TypeScript. LobsterClaw brings the same architecture to the Python ecosystem — making it easier to extend with Python ML libraries, integrate into existing Python tooling, and deploy in environments where Node.js isn't preferred.

---

## Quick Start

```bash
# 1. Clone and set up environment
git clone https://github.com/you/lobsterclaw
cd lobsterclaw
make setup                     # creates venv + installs dependencies

# 2. Configure
cp .env.example .env
# Fill in: ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_OWNER_ID

# 3. Run
make run                       # starts bot + Gateway API on :4400

# Optional: Mission Control dashboard
make all                       # bot + API + Next.js UI on :3001
```

That's it. Message your bot on Telegram.

---

## Features

### 🤖 Agent Engine
- **Multi-provider** — works with Anthropic (Claude) and OpenAI (GPT-4) out of the box
- **Streaming** — live text preview while the agent thinks, delivered as editable messages
- **Extended thinking** — Anthropic's extended reasoning budget supported
- **Context window compaction** — automatically compresses long conversations so they never hit token limits
- **Pre-compaction memory flush** — writes durable memories before compaction so nothing important is lost
- **Tool loop detection** — warns at 10 repeated calls, blocks at 20, aborts at 30 — prevents runaway loops
- **Transcript repair** — automatically removes orphaned tool blocks that would break the API
- **API retry with backoff** — handles rate limits and transient errors gracefully

### 💬 Channels

| Channel | Status | Features |
|---------|--------|----------|
| **Telegram** | ✅ Full | Pairing mode, reactions lifecycle, streaming preview, webhook + polling, sticker vision, voice transcription, multi-account, forum threads |
| **Discord** | ✅ Full | DMs + guild messages, mention gating, slash commands (`/new` `/help`), streaming preview, reactions (⏳→⚙️→✅/❌), file attachments |
| WhatsApp | 🚧 Stub | Schema-compatible; full implementation planned |
| Slack | 🚧 Stub | Schema-compatible; full implementation planned |

### 🧠 Memory
- **Workspace files** — `SOUL.md`, `USER.md`, `MEMORY.md`, `AGENTS.md`, `TOOLS.md`, `HEARTBEAT.md`, `IDENTITY.md`
- **Daily notes** — `workspace/memory/YYYY-MM-DD.md` auto-created per session
- **FTS5 full-text search** — Porter stemmer + BM25 ranking
- **Semantic search** (optional) — OpenAI or Gemini embeddings with RRF merge
- **5-tool memory suite** — `memory_search`, `memory_get`, `memory_write`, `memory_list`, `memory_delete`

### 🔧 Tools (19 total)

| Category | Tools |
|----------|-------|
| Web | `web_fetch`, `web_search` (6 providers: DuckDuckGo, Brave, Perplexity, Gemini, Grok, Kimi) |
| Shell | `exec`, `process` (background jobs with `yield_ms`) |
| Files | `read`, `write`, `edit`, `apply_patch`, `list_dir`, `glob`, `delete`, `move` |
| Browser | `browser` (Playwright — screenshot, navigate, extract) |
| Memory | `memory_search`, `memory_get`, `memory_write`, `memory_list`, `memory_delete` |
| Media | `pdf`, `image`, `tts` (PyMuPDF + edge-tts) |
| Messaging | `message` (full Telegram action suite), `discord` (10 actions) |
| Scheduling | `cron` (APScheduler) |
| UI | `canvas` (interactive surfaces via canvas_host) |
| System | `gateway`, `nodes`, `sessions_*` (7-tool sub-agent suite), `approval` |

### 🔗 Hooks System
Five events fire automatically through the agent lifecycle — wire your own handlers by dropping a `handler.py` into `workspace/hooks/<name>/`:

```
command:new    → new user message received
turn:end       → agent finished responding
tool:result    → any tool call returned
session:spawn  → sub-agent spawned
session:end    → session completed or errored
```

Errors in hook handlers are caught and logged — they never crash the agent.

### 🧩 Plugin System (with Hot-Reload)
Add tools by dropping a folder into `workspace/plugins/`:

```
workspace/plugins/my-plugin/
  __init__.py      ← defines TOOLS = [ToolDefinition(...)]
  plugin.json      ← metadata (optional)
```

With `PLUGIN_HOT_RELOAD=true` (default), **changes take effect immediately** — no restart needed. LobsterClaw uses [watchdog](https://github.com/gorakhargosh/watchdog) to watch for file changes and calls `importlib.reload()` on the modified module.

### 📅 Scheduler & Heartbeat
- **Cron** — schedule agent tasks via the `cron` tool or `HEARTBEAT.md`
- **Heartbeat** — periodic agent wake on a configurable schedule
- **Active hours guard** — suppress overnight wakes with `HEARTBEAT_ACTIVE_HOURS_START/END`
- **Light-bootstrap mode** — `python main.py --light-context` skips browser, canvas, and heavy subsystems for fast cron runs (~0.5s cold-start vs ~3s full)

### 🖥️ Mission Control Dashboard
Built-in Next.js dashboard at `http://localhost:3001` (run `make gateway-ui`):

- **Agents** — view all sessions, sub-agents, token usage
- **Chat** — send messages to the agent from the browser
- **Cron** — manage scheduled tasks
- **Tools** — browse registered tools and their schemas
- **Skills** — view loaded SKILL.md files
- **Settings** — live config view

The dashboard connects to the FastAPI gateway at `:4400` via WebSocket for real-time updates.

### 🏗️ Sub-Agents
The agent can spawn parallel sub-agents to handle long tasks:

```
Max depth:    3  (main → child → grandchild)
Max children: 5  (per parent session)
```

Sub-agents run in isolated asyncio tasks, auto-announce results when done, and support `inherit` or `strict` sandbox modes.

---

## Where LobsterClaw Beats OpenClaw

LobsterClaw isn't just a port — it ships capabilities the original doesn't have.

### 🔒 Gateway Authentication
OpenClaw's gateway has **no authentication**. Any process on the network can call it. This is the root cause of CVE-2026-25253 (CVSS 8.8), which exposed ~135,000 instances.

LobsterClaw ships gateway auth out of the box:
```bash
# .env
GATEWAY_API_KEY=your-secret-key-here
```
Every REST endpoint requires `X-API-Key: <key>`. WebSocket connections require `?api_key=<key>`. Auth is disabled (with a warning) when no key is set, so development still works without configuration.

### 🛡️ CORS Lockdown
OpenClaw uses `allow_origins=["*"]` — any website can make cross-origin requests to the gateway. LobsterClaw defaults to `localhost:3001` only:
```bash
GATEWAY_CORS_ORIGINS=http://localhost:3001,https://your-domain.com
```

### ⚡ Light-Bootstrap Mode
OpenClaw loads all subsystems on every startup — even for a cron job checking the weather, it initialises Playwright. LobsterClaw adds `--light-context`:
```bash
python main.py --light-context
# Skips: browser, canvas, channel stubs, sub-agent manager
# Cold-start: ~0.5s instead of ~3s
```
Ideal for scheduled heartbeat runs and cron jobs that don't need the full stack.

### 📄 Per-Session JSONL Transcripts
OpenClaw stores conversations in SQLite only. LobsterClaw writes a parallel JSONL file for every session:
```
~/.lobsterclaw/transcripts/main.jsonl
~/.lobsterclaw/transcripts/a1b2c3d4.jsonl
```
Human-readable, grep-able, portable. No DB tooling required to inspect history.

### 🔄 Live Plugin Hot-Reload
OpenClaw uses jiti for hot-reload in the TypeScript ecosystem — in practice any plugin change still requires a restart. LobsterClaw uses [watchdog](https://github.com/gorakhargosh/watchdog) to watch `workspace/plugins/` and calls `importlib.reload()` when files change. **Your plugin is live the moment you save.**

---

## Architecture

```
lobsterclaw/
│
├── agent/                  # Core agent engine
│   ├── loop.py             # Main agent loop (1,255 lines) — Anthropic + OpenAI
│   ├── sessions.py         # SQLite session store + JSONL transcripts
│   ├── memory_index.py     # FTS5 + optional semantic search
│   ├── compaction.py       # Context window compaction
│   ├── hooks.py            # Event system (5 events)
│   ├── subagent.py         # Sub-agent spawning + lifecycle
│   ├── heartbeat.py        # Periodic agent wake
│   └── skills.py           # SKILL.md loader
│
├── channels/               # Inbound/outbound channel adapters
│   ├── telegram.py         # Telegram (2,059 lines — full feature set)
│   └── discord.py          # Discord (422 lines — full feature set)
│
├── tools/                  # Tool implementations (19 tools)
│   ├── registry.py         # Tool registry + policy enforcement
│   ├── plugin_loader.py    # Plugin system + watchdog hot-reload
│   └── ...                 # Individual tool files
│
├── gateway/                # REST API + WebSocket event bus
│   └── server.py           # FastAPI + auth + CORS (547 lines)
│
├── gateway_ui/             # Mission Control dashboard (Next.js)
│   └── app/                # 6 pages: agents/chat/cron/settings/skills/tools
│
├── canvas_host/            # Interactive UI surface server
├── canvas_frontend/        # Canvas frontend (Next.js)
├── scheduler/              # APScheduler cron manager
├── tests/                  # 22 test files
├── config.py               # All config via env vars
├── main.py                 # Entry point + wiring
└── workspace/              # Your agent's home
    ├── SOUL.md             # Who the agent is
    ├── USER.md             # Who you are
    ├── MEMORY.md           # Long-term memories (main session only)
    ├── AGENTS.md           # Workspace instructions
    ├── TOOLS.md            # Tool notes
    ├── HEARTBEAT.md        # Scheduled task definitions
    ├── memory/             # Daily notes (YYYY-MM-DD.md)
    ├── skills/             # SKILL.md skill files
    ├── plugins/            # Drop-in tool plugins
    └── hooks/              # Event handler hooks
```

---

## Configuration

All config via `.env`. Full reference in `.env.example`.

```bash
# Minimum to get running
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_OWNER_ID=12345678

# Recommended to also set
GATEWAY_API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
GATEWAY_CORS_ORIGINS=http://localhost:3001
```

Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `anthropic` | `anthropic` or `openai` |
| `LLM_MODEL` | `claude-opus-4-5` | Model name |
| `DISCORD_ENABLED` | `false` | Enable Discord channel |
| `PLUGIN_HOT_RELOAD` | `true` | Live plugin reload via watchdog |
| `LIGHT_CONTEXT` | `false` | Fast startup for cron/heartbeat |
| `GATEWAY_API_KEY` | _(empty)_ | API key for gateway auth (recommended) |
| `GATEWAY_ENABLED` | `true` | Enable Mission Control API |
| `SUBAGENTS_ENABLED` | `true` | Allow sub-agent spawning |
| `HEARTBEAT_ENABLED` | `false` | Periodic agent wake |

---

## Tool Policy

Control exactly what the agent can do:

```bash
# Whitelist — only these tools run
TOOLS_ALLOW=web_fetch,web_search,memory_search,memory_write,message,cron

# Blacklist — always blocked
TOOLS_DENY=exec,browser,delete,gateway

# Confirmation — asks you before running
TOOLS_REQUIRE_CONFIRMATION=exec,browser,write
```

---

## Making a Plugin

```python
# workspace/plugins/weather/__init__.py
from tools.registry import ToolDefinition

async def _weather(city: str = "London", **_) -> str:
    import httpx
    r = await httpx.AsyncClient().get(f"https://wttr.in/{city}?format=3")
    return r.text

TOOLS = [ToolDefinition(
    name="weather",
    description="Get current weather for a city.",
    parameters={"type":"object","properties":{"city":{"type":"string"}},"required":["city"]},
    fn=lambda **kw: _weather(**kw),
)]
```

Drop it in and the agent can use `weather` immediately — no restart if `PLUGIN_HOT_RELOAD=true`.

---

## Making a Hook

```python
# workspace/hooks/log-turns/handler.py
# plugin.json: {"events": ["turn:end"]}

async def handle(event: str, data: dict) -> None:
    with open("turns.log", "a") as f:
        f.write(f"{data['session_id']} | {data['content'][:100]}\n")
```

---

## Running in Light Mode

For scheduled tasks that don't need the full stack:

```bash
# Via CLI flag
python main.py --light-context

# Via env var (e.g. in a cron job)
LIGHT_CONTEXT=true python main.py
```

Skips: Playwright browser, canvas host, multi-channel stubs, sub-agent manager.
Result: ~0.5s cold-start instead of ~3s.

---

## Requirements

```
Python 3.11+
Node.js 18+  (only for Mission Control UI — optional)
```

Python dependencies (installed by `make setup`):
```
anthropic, openai, fastapi, uvicorn, python-telegram-bot,
discord.py, httpx, APScheduler, playwright, PyMuPDF,
edge-tts, watchdog, python-dotenv, and more
```

---

## Project Status

| Milestone | Status |
|-----------|--------|
| 100% OpenClaw feature parity | ✅ Complete |
| Gateway auth + CORS hardening | ✅ Complete |
| Discord channel | ✅ Complete |
| Plugin hot-reload | ✅ Complete |
| Light-bootstrap mode | ✅ Complete |
| Phase 2 security (6 CVEs) | 🔜 In progress |
| WhatsApp + Slack channels | 🔜 Planned |

---

## License

Personal project. Not affiliated with OpenClaw or Anthropic.

---

<div align="center">
Built with 🦞 and too much asyncio
</div>
