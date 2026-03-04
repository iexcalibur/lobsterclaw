"""
Gateway API — FastAPI server powering the Mission Control dashboard.

Provides REST endpoints for reading system state and WebSocket for real-time
event streaming. Runs in the same process as the Telegram bot so it has
direct access to all live Python objects (sessions, tools, cron, etc.).

Start: configured and launched from main.py alongside the Telegram channel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import get_config
from gateway.events import event_bus

logger = logging.getLogger(__name__)

_registry: Any = None
_cron_mgr: Any = None
_agent_fn: Callable[[str], Awaitable[str]] | None = None
_send_fn: Callable[[str], Awaitable[None]] | None = None
_history_mgr: Any = None
_start_time: float = time.time()


def configure(
    *,
    registry: Any = None,
    cron_mgr: Any = None,
    agent_fn: Callable[[str], Awaitable[str]] | None = None,
    send_fn: Callable[[str], Awaitable[None]] | None = None,
    history_mgr: Any = None,
) -> None:
    """Called from main.py after all components are wired."""
    global _registry, _cron_mgr, _agent_fn, _send_fn, _history_mgr, _start_time
    _registry = registry
    _cron_mgr = cron_mgr
    _agent_fn = agent_fn
    _send_fn = send_fn
    _history_mgr = history_mgr
    _start_time = time.time()


def _cron_db_path() -> str | None:
    if _cron_mgr is None:
        return None
    try:
        return str(_cron_mgr.cfg.cron_db)
    except Exception:
        return None


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Gateway API started")
        yield
        logger.info("Gateway API stopped")

    app = FastAPI(title="Pygate Gateway", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------
    @app.get("/api/gateway/health")
    async def health():
        return {"status": "ok", "service": "pygate-gateway"}

    # ------------------------------------------------------------------
    # System status (dashboard overview)
    # ------------------------------------------------------------------
    @app.get("/api/gateway/status")
    async def status():
        cfg = get_config()

        sessions_count = active_sessions = 0
        try:
            from agent.sessions import get_session_store
            store = get_session_store()
            all_sessions = await store.list_sessions(limit=200)
            sessions_count = len(all_sessions)
            active_sessions = sum(1 for s in all_sessions if s.status == "active")
        except Exception:
            pass

        tools_count = len(_registry._tools) if _registry else 0

        skills_count = 0
        try:
            from agent.skills import load_skills
            skills_count = len(load_skills())
        except Exception:
            pass

        cron_total = cron_active = 0
        db = _cron_db_path()
        if db:
            try:
                conn = sqlite3.connect(db)
                cron_total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
                cron_active = conn.execute("SELECT COUNT(*) FROM jobs WHERE enabled=1").fetchone()[0]
                conn.close()
            except Exception:
                pass

        return {
            "agent_id": cfg.agent_id,
            "provider": cfg.llm_provider,
            "model": cfg.llm_model,
            "uptime_seconds": round(time.time() - _start_time),
            "components": {
                "cron": cfg.cron_enabled,
                "heartbeat": cfg.heartbeat_enabled,
                "memory": cfg.memory_enabled,
                "browser": cfg.browser_enabled,
                "canvas": cfg.canvas_host_enabled,
                "streaming": cfg.llm_streaming,
                "subagents": cfg.subagents_enabled,
                "exec": cfg.exec_enabled,
                "tts": cfg.tts_enabled,
            },
            "counts": {
                "tools": tools_count,
                "skills": skills_count,
                "sessions": sessions_count,
                "active_sessions": active_sessions,
                "cron_total": cron_total,
                "cron_active": cron_active,
            },
            "ws_subscribers": event_bus.subscriber_count,
        }

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------
    @app.get("/api/gateway/sessions")
    async def list_sessions():
        try:
            from agent.sessions import get_session_store
            store = get_session_store()
            sessions = await store.list_sessions(limit=100)
            return {
                "sessions": [
                    {
                        "id": s.id,
                        "label": s.label,
                        "parent_id": s.parent_id,
                        "status": s.status,
                        "model": s.model,
                        "depth": s.depth,
                        "token_usage": s.token_usage,
                        "input_tokens": s.input_tokens,
                        "output_tokens": s.output_tokens,
                        "created_at": s.created_at,
                        "updated_at": s.updated_at,
                        "error": s.error,
                    }
                    for s in sessions
                ]
            }
        except Exception as e:
            return {"sessions": [], "error": str(e)}

    @app.get("/api/gateway/sessions/{session_id}")
    async def get_session(session_id: str):
        try:
            from agent.sessions import get_session_store
            store = get_session_store()
            session = await store.get_session(session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
            messages = await store.get_messages(session_id, limit=50)
            children = await store.list_sessions(parent_id=session_id, limit=20)
            return {
                "session": {
                    "id": session.id,
                    "label": session.label,
                    "parent_id": session.parent_id,
                    "status": session.status,
                    "model": session.model,
                    "depth": session.depth,
                    "token_usage": session.token_usage,
                    "input_tokens": session.input_tokens,
                    "output_tokens": session.output_tokens,
                    "created_at": session.created_at,
                    "updated_at": session.updated_at,
                    "error": session.error,
                },
                "messages": messages,
                "children": [
                    {"id": c.id, "label": c.label, "status": c.status, "depth": c.depth}
                    for c in children
                ],
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------
    @app.get("/api/gateway/skills")
    async def list_skills():
        try:
            from agent.skills import load_skills
            skills = load_skills()
            return {
                "skills": [
                    {
                        "name": s.name,
                        "description": s.description,
                        "always": s.always,
                        "path": str(s.path),
                        "content_preview": (s.content[:300] + "...") if len(s.content) > 300 else s.content,
                    }
                    for s in skills
                ],
                "total": len(skills),
            }
        except Exception as e:
            return {"skills": [], "total": 0, "error": str(e)}

    # ------------------------------------------------------------------
    # Cron jobs
    # ------------------------------------------------------------------
    @app.get("/api/gateway/cron")
    async def list_cron_jobs():
        db = _cron_db_path()
        if not db:
            return {"jobs": [], "total": 0}
        try:
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, description, schedule, message, enabled, created_at, "
                "run_count, last_run, session_target, delivery, delete_after_run "
                "FROM jobs ORDER BY created_at DESC"
            ).fetchall()
            conn.close()

            jobs = []
            for r in rows:
                next_run = None
                if _cron_mgr:
                    try:
                        apjob = _cron_mgr.scheduler.get_job(r["id"])
                        if apjob and apjob.next_run_time:
                            next_run = apjob.next_run_time.isoformat()
                    except Exception:
                        pass
                jobs.append({
                    "id": r["id"],
                    "description": r["description"],
                    "schedule": r["schedule"],
                    "message": r["message"],
                    "enabled": bool(r["enabled"]),
                    "created_at": r["created_at"],
                    "run_count": r["run_count"],
                    "last_run": r["last_run"],
                    "next_run": next_run,
                    "session_target": r["session_target"],
                    "delivery": r["delivery"],
                    "delete_after_run": bool(r["delete_after_run"]),
                })
            return {"jobs": jobs, "total": len(jobs)}
        except Exception as e:
            return {"jobs": [], "total": 0, "error": str(e)}

    @app.post("/api/gateway/cron/{job_id}/toggle")
    async def toggle_cron_job(job_id: str):
        db = _cron_db_path()
        if not db:
            raise HTTPException(status_code=503, detail="Cron not available")
        try:
            conn = sqlite3.connect(db)
            row = conn.execute("SELECT enabled, schedule, message FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                conn.close()
                raise HTTPException(status_code=404, detail="Job not found")
            new_state = 0 if row[0] else 1
            conn.execute("UPDATE jobs SET enabled=? WHERE id=?", (new_state, job_id))
            conn.commit()
            conn.close()

            if _cron_mgr:
                if new_state:
                    try:
                        _cron_mgr._schedule(job_id, row[1], row[2])
                    except Exception:
                        pass
                else:
                    try:
                        _cron_mgr.scheduler.remove_job(job_id)
                    except Exception:
                        pass

            await event_bus.publish("cron.toggled", {"job_id": job_id, "enabled": bool(new_state)})
            return {"ok": True, "enabled": bool(new_state)}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------
    @app.get("/api/gateway/tools")
    async def list_tools():
        if not _registry:
            return {"tools": [], "total": 0}
        cfg = get_config()
        tools = []
        for name, tool in _registry._tools.items():
            tools.append({
                "name": name,
                "description": tool.description[:300] if len(tool.description) > 300 else tool.description,
                "owner_only": tool.owner_only,
                "depth_limit": tool.depth_limit,
                "denied": name in cfg.tools_deny,
                "allowed": not cfg.tools_allow or name in cfg.tools_allow,
                "requires_confirmation": name in cfg.tools_require_confirmation,
            })
        return {"tools": sorted(tools, key=lambda t: t["name"]), "total": len(tools)}

    # ------------------------------------------------------------------
    # Config (safe subset — no secrets)
    # ------------------------------------------------------------------
    @app.get("/api/gateway/config")
    async def get_config_view():
        cfg = get_config()
        return {
            "llm": {
                "provider": cfg.llm_provider,
                "model": cfg.llm_model,
                "max_tokens": cfg.llm_max_tokens,
                "streaming": cfg.llm_streaming,
                "thinking_budget": cfg.llm_thinking_budget,
                "reasoning_mode": cfg.reasoning_mode,
                "max_retries": cfg.llm_max_retries,
            },
            "telegram": {
                "dm_policy": cfg.telegram_dm_policy,
                "group_policy": cfg.telegram_group_policy,
                "link_preview": cfg.telegram_link_preview,
                "reactions_enabled": cfg.telegram_reactions_enabled,
                "mention_required": cfg.telegram_mention_required,
            },
            "features": {
                "cron": cfg.cron_enabled,
                "heartbeat": cfg.heartbeat_enabled,
                "memory": cfg.memory_enabled,
                "browser": cfg.browser_enabled,
                "canvas": cfg.canvas_host_enabled,
                "subagents": cfg.subagents_enabled,
                "exec": cfg.exec_enabled,
                "tts": cfg.tts_enabled,
            },
            "limits": {
                "max_history_messages": cfg.max_history_messages,
                "max_tool_iterations": cfg.max_tool_iterations,
                "subagents_max_depth": cfg.subagents_max_depth,
                "subagents_max_children": cfg.subagents_max_children,
                "tool_result_max_chars": cfg.tool_result_max_chars,
            },
            "identity": {
                "agent_id": cfg.agent_id,
                "prompt_mode": cfg.prompt_mode,
            },
        }

    # ------------------------------------------------------------------
    # Chat — send a message to the agent and get a response
    # ------------------------------------------------------------------
    @app.post("/api/gateway/chat")
    async def send_chat(body: dict):
        message = body.get("message", "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="message is required")
        if not _agent_fn:
            raise HTTPException(status_code=503, detail="Agent not available")

        await event_bus.publish("chat.user_message", {"content": message})
        try:
            response = await _agent_fn(message)
            await event_bus.publish("chat.agent_response", {"content": response})
            return {"ok": True, "response": response}
        except Exception as e:
            logger.error("Gateway chat error: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------
    @app.get("/api/gateway/history/{session_id}")
    async def get_history(session_id: str, limit: int = 50):
        try:
            from agent.sessions import get_session_store
            store = get_session_store()
            messages = await store.get_messages(session_id, limit=limit)
            return {"messages": messages, "session_id": session_id}
        except Exception as e:
            return {"messages": [], "error": str(e)}

    # ------------------------------------------------------------------
    # Metrics (token usage)
    # ------------------------------------------------------------------
    @app.get("/api/gateway/metrics")
    async def get_metrics():
        try:
            from agent.sessions import get_session_store
            store = get_session_store()
            main_usage = await store.get_token_usage("main")
            sessions = await store.list_sessions(limit=200)
            total_in = sum(s.input_tokens for s in sessions)
            total_out = sum(s.output_tokens for s in sessions)
            return {
                "main_session": main_usage,
                "all_sessions": {
                    "input_tokens": total_in,
                    "output_tokens": total_out,
                    "total_tokens": total_in + total_out,
                    "session_count": len(sessions),
                },
            }
        except Exception as e:
            return {
                "main_session": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "all_sessions": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "session_count": 0},
                "error": str(e),
            }

    # ------------------------------------------------------------------
    # Workspace files (read-only)
    # ------------------------------------------------------------------
    @app.get("/api/gateway/workspace")
    async def list_workspace_files():
        ws_dir = Path(__file__).parent.parent / "workspace"
        files = []
        if ws_dir.exists():
            for f in sorted(ws_dir.iterdir()):
                if f.suffix == ".md":
                    content = f.read_text(encoding="utf-8", errors="replace")
                    files.append({
                        "name": f.name,
                        "size": len(content),
                        "preview": (content[:500] + "...") if len(content) > 500 else content,
                    })
        return {"files": files}

    # ------------------------------------------------------------------
    # WebSocket — real-time event stream
    # ------------------------------------------------------------------
    @app.websocket("/ws/gateway")
    async def ws_events(websocket: WebSocket):
        await websocket.accept()
        queue = event_bus.subscribe()
        logger.info("Gateway WS connected (subscribers: %d)", event_bus.subscriber_count)

        try:
            init = await status()
            await websocket.send_json({"type": "connected", "data": init, "ts": time.time()})

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    await websocket.send_json(event)
                except asyncio.TimeoutError:
                    await websocket.send_json({"type": "heartbeat", "ts": time.time()})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug("Gateway WS error: %s", e)
        finally:
            event_bus.unsubscribe(queue)
            logger.info("Gateway WS disconnected")

    return app


async def run_server(host: str = "127.0.0.1", port: int = 4400) -> None:
    """Start the gateway API as an asyncio coroutine (for asyncio.create_task)."""
    import uvicorn

    app = create_app()
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    await server.serve()
