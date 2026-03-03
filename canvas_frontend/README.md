# LobsterClaw Canvas Frontend

Next.js 14 interactive canvas UI for LobsterClaw agent.

## Setup

```bash
cd canvas_frontend
cp .env.local.example .env.local
npm install
```

## Development

Run the Next.js dev server alongside the LobsterClaw canvas host:

```bash
# Terminal 1 — LobsterClaw with canvas host enabled
CANVAS_HOST_ENABLED=true python main.py

# Terminal 2 — Next.js dev server
npm run dev
```

Then open `http://localhost:3000/canvas/<session_id>` in your browser.
The canvas host runs at `http://localhost:7681`.

## Production build

```bash
npm run build
```

This exports a static site to `out/`. LobsterClaw's canvas host automatically serves it
when `out/` exists, so the full UI is available at `http://localhost:7681`.

## Architecture

```
LobsterClaw Agent
    │
    │  canvas(action="present", kind="markdown", markdown="# Hello")
    ▼
canvas_tool.py  ──  HTTP POST  ──►  canvas_host/server.py (FastAPI :7681)
                                           │
                                           │  WebSocket push
                                           ▼
                                    Next.js frontend (/canvas/{sessionId})
                                           │
                                     User clicks button
                                           │
                                    WebSocket message ──►  canvas_host
                                                                │
                                    canvas(action="wait_event") ◄──  event queue
```

## Supported content kinds

| Kind       | Description                          |
|------------|--------------------------------------|
| `html`     | Raw HTML (sandboxed iframe)          |
| `markdown` | Markdown with GFM (react-markdown)   |
| `url`      | External URL in iframe               |
| `a2ui`     | A2UI component tree (React renderer) |
| `json`     | Formatted JSON viewer                |

## A2UI component types

`card`, `text`, `heading`, `table`, `list`, `button`, `input`, `form`,
`image`, `code`, `divider`, `badge`, `progress`, `json`, `row`, `col`
