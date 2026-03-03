"""
Canvas Host — FastAPI + WebSocket server.

Provides a bidirectional channel between the agent (canvas_tool) and the
Next.js frontend. The agent pushes content/commands over HTTP REST; the
frontend connects over WebSocket and receives commands + sends user events back.

Endpoints:
  WS  /ws/{session_id}                     bidirectional agent ↔ frontend
  GET  /api/canvas/sessions                 list all sessions
  GET  /api/canvas/{session_id}             get session state
  POST /api/canvas/{session_id}/present     push HTML/markdown/A2UI/JSON content
  POST /api/canvas/{session_id}/navigate    load a URL in the canvas
  POST /api/canvas/{session_id}/hide        hide the canvas
  POST /api/canvas/{session_id}/show        show/un-hide the canvas
  POST /api/canvas/{session_id}/eval        eval JS in the frontend page
  GET  /api/canvas/{session_id}/snapshot    Playwright screenshot → base64 PNG
  POST /api/canvas/{session_id}/update      incremental content patch
  POST /api/canvas/{session_id}/event       inject a synthetic event (testing)
  DELETE /api/canvas/{session_id}           close/reset session
  GET  /                                    Next.js frontend (static or dev proxy)
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from canvas_host.events import (
    clear_session,
    get_queue,
    put_event,
    register_eval_future,
    wait_eval_result,
    wait_event,
)
from canvas_host.state import CanvasSession, CanvasStateStore, get_canvas_store

logger = logging.getLogger(__name__)

# Active WebSocket connections: session_id → list[WebSocket]
_connections: dict[str, list[WebSocket]] = {}


# ------------------------------------------------------------------
# WebSocket push helper
# ------------------------------------------------------------------

async def _push(session_id: str, message: dict) -> int:
    """Push a JSON message to all connected clients for session_id. Returns count sent."""
    conns = _connections.get(session_id, [])
    text = json.dumps(message)
    dead: list[WebSocket] = []
    sent = 0
    for ws in list(conns):
        try:
            await ws.send_text(text)
            sent += 1
        except Exception:
            dead.append(ws)
    for ws in dead:
        conns.remove(ws)
    return sent


# ------------------------------------------------------------------
# App factory
# ------------------------------------------------------------------

def create_app(frontend_build_dir: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Canvas host started")
        yield
        logger.info("Canvas host stopped")

    app = FastAPI(title="PyGate Canvas Host", version="1.0.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # WebSocket endpoint
    # ------------------------------------------------------------------

    @app.websocket("/ws/{session_id}")
    async def ws_endpoint(websocket: WebSocket, session_id: str):
        await websocket.accept()
        _connections.setdefault(session_id, []).append(websocket)
        logger.info("Canvas WS connected: %s (total: %d)", session_id,
                    len(_connections[session_id]))

        # Send current session state on connect so UI can restore
        store = get_canvas_store()
        session = store.get_session(session_id)
        if session and session.content:
            await websocket.send_text(json.dumps({
                "type": "restore",
                "content": session.content,
                "url": session.url,
                "visible": session.visible,
                "title": session.title,
                "sessionId": session_id,
            }))

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                msg.setdefault("sessionId", session_id)
                logger.debug("Canvas WS <- %s: %s", session_id, msg.get("type"))
                await put_event(session_id, msg)
        except WebSocketDisconnect:
            conns = _connections.get(session_id, [])
            if websocket in conns:
                conns.remove(websocket)
            logger.info("Canvas WS disconnected: %s", session_id)

    # ------------------------------------------------------------------
    # REST: list sessions
    # ------------------------------------------------------------------

    @app.get("/api/canvas/sessions")
    async def list_sessions():
        store = get_canvas_store()
        sessions = store.list_sessions()
        return {
            "sessions": [
                {
                    "sessionId": s.session_id,
                    "title": s.title,
                    "visible": s.visible,
                    "url": s.url,
                    "connected": len(_connections.get(s.session_id, [])),
                    "updatedAt": s.updated_at,
                    "contentKind": s.content.get("kind") if s.content else None,
                }
                for s in sessions
            ]
        }

    # ------------------------------------------------------------------
    # REST: get session state
    # ------------------------------------------------------------------

    @app.get("/api/canvas/{session_id}")
    async def get_session(session_id: str):
        store = get_canvas_store()
        s = store.get_session(session_id)
        if not s:
            raise HTTPException(status_code=404, detail="Session not found")
        return {
            "sessionId": s.session_id,
            "content": s.content,
            "url": s.url,
            "visible": s.visible,
            "title": s.title,
            "metadata": s.metadata,
            "connected": len(_connections.get(session_id, [])),
            "updatedAt": s.updated_at,
        }

    # ------------------------------------------------------------------
    # REST: present — push content to frontend
    # ------------------------------------------------------------------

    @app.post("/api/canvas/{session_id}/present")
    async def present(session_id: str, body: dict):
        """
        Push content to the canvas frontend.

        body: {
          kind: "html" | "markdown" | "url" | "a2ui" | "json",
          html?: string,
          markdown?: string,
          url?: string,
          a2ui?: object,   # A2UI component tree
          data?: any,      # for kind=json
          title?: string,
        }
        """
        store = get_canvas_store()
        kind = body.get("kind", "html")
        title = body.get("title", "")

        content = {"kind": kind, **{k: v for k, v in body.items() if k != "title"}}
        store.set_content(session_id, content)
        if title:
            store.set_title(session_id, title)
        store.log_action(session_id, "present", content)

        sent = await _push(session_id, {
            "type": "present",
            "content": content,
            "title": title,
            "sessionId": session_id,
        })
        return {"ok": True, "sent": sent, "sessionId": session_id}

    # ------------------------------------------------------------------
    # REST: navigate — load a URL in the canvas
    # ------------------------------------------------------------------

    @app.post("/api/canvas/{session_id}/navigate")
    async def navigate(session_id: str, body: dict):
        url = body.get("url", "")
        if not url:
            raise HTTPException(status_code=400, detail="url is required")
        store = get_canvas_store()
        store.set_url(session_id, url)
        store.log_action(session_id, "navigate", {"url": url})
        sent = await _push(session_id, {"type": "navigate", "url": url, "sessionId": session_id})
        return {"ok": True, "sent": sent, "url": url}

    # ------------------------------------------------------------------
    # REST: hide / show
    # ------------------------------------------------------------------

    @app.post("/api/canvas/{session_id}/hide")
    async def hide(session_id: str):
        store = get_canvas_store()
        store.set_visible(session_id, False)
        sent = await _push(session_id, {"type": "hide", "sessionId": session_id})
        return {"ok": True, "sent": sent}

    @app.post("/api/canvas/{session_id}/show")
    async def show(session_id: str):
        store = get_canvas_store()
        store.set_visible(session_id, True)
        sent = await _push(session_id, {"type": "show", "sessionId": session_id})
        return {"ok": True, "sent": sent}

    # ------------------------------------------------------------------
    # REST: eval — run JavaScript in the frontend page
    # ------------------------------------------------------------------

    @app.post("/api/canvas/{session_id}/eval")
    async def eval_js(session_id: str, body: dict):
        script = body.get("script", "")
        if not script:
            raise HTTPException(status_code=400, detail="script is required")
        eval_id = body.get("id") or uuid.uuid4().hex[:8]
        timeout = float(body.get("timeout", 10))

        future = register_eval_future(eval_id)
        sent = await _push(session_id, {
            "type": "eval",
            "script": script,
            "id": eval_id,
            "sessionId": session_id,
        })
        if sent == 0:
            return {"ok": False, "reason": "no_client_connected", "evalId": eval_id}

        result = await wait_eval_result(eval_id, timeout=timeout)
        return {"ok": True, "evalId": eval_id, "result": result}

    # ------------------------------------------------------------------
    # REST: snapshot — Playwright screenshot
    # ------------------------------------------------------------------

    @app.get("/api/canvas/{session_id}/snapshot")
    async def snapshot(
        session_id: str,
        full_page: bool = False,
        width: int = 1280,
        height: int = 800,
    ):
        from canvas_host.snapshot import snapshot_session
        from config import get_config
        cfg = get_config()

        b64 = await snapshot_session(
            session_id,
            cfg.canvas_host_url,
            full_page=full_page,
            width=width,
            height=height,
        )
        if b64 is None:
            raise HTTPException(status_code=500, detail="Snapshot failed")
        return {"ok": True, "base64": b64, "sessionId": session_id}

    # ------------------------------------------------------------------
    # REST: update — incremental content patch
    # ------------------------------------------------------------------

    @app.post("/api/canvas/{session_id}/update")
    async def update(session_id: str, body: dict):
        store = get_canvas_store()
        s = store.get_session(session_id)
        if s and s.content:
            # Deep-merge body into existing content
            merged = {**s.content, **body}
            store.set_content(session_id, merged)
        store.log_action(session_id, "update", body)
        sent = await _push(session_id, {"type": "update", "patch": body, "sessionId": session_id})
        return {"ok": True, "sent": sent}

    # ------------------------------------------------------------------
    # REST: wait_event — long-poll for a user interaction
    # ------------------------------------------------------------------

    @app.get("/api/canvas/{session_id}/wait_event")
    async def wait_event_endpoint(session_id: str, timeout: float = 60.0):
        event = await wait_event(session_id, timeout=timeout)
        if event is None:
            return {"ok": True, "timeout": True}
        return {"ok": True, "timeout": False, "event": event}

    # ------------------------------------------------------------------
    # REST: inject event (for testing / agent-synthesised events)
    # ------------------------------------------------------------------

    @app.post("/api/canvas/{session_id}/event")
    async def inject_event(session_id: str, body: dict):
        body.setdefault("sessionId", session_id)
        await put_event(session_id, body)
        return {"ok": True}

    # ------------------------------------------------------------------
    # REST: delete / close session
    # ------------------------------------------------------------------

    @app.delete("/api/canvas/{session_id}")
    async def delete_session(session_id: str):
        store = get_canvas_store()
        store.delete_session(session_id)
        clear_session(session_id)
        sent = await _push(session_id, {"type": "close", "sessionId": session_id})
        return {"ok": True, "sent": sent}

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "service": "pygate-canvas-host"}

    # ------------------------------------------------------------------
    # Static file serving (Next.js build output in production)
    # ------------------------------------------------------------------

    frontend_dir = frontend_build_dir or (
        Path(__file__).parent.parent / "canvas_frontend" / "out"
    )
    if frontend_dir.exists():
        logger.info("Serving canvas frontend from %s", frontend_dir)
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
    else:
        @app.get("/")
        async def root():
            return JSONResponse({
                "service": "pygate-canvas-host",
                "status": "running",
                "note": (
                    "Frontend not built. "
                    "Run: cd canvas_frontend && npm install && npm run build"
                ),
                "api": "/api/canvas/sessions",
            })

    return app


# ------------------------------------------------------------------
# Uvicorn runner (called from main.py as asyncio task)
# ------------------------------------------------------------------

async def run_server(host: str = "127.0.0.1", port: int = 7681) -> None:
    """Start the canvas host uvicorn server as a coroutine (for asyncio.create_task)."""
    import uvicorn
    app = create_app()
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    await server.serve()
