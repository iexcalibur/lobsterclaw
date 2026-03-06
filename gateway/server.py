"""
Gateway API — FastAPI server powering the Mission Control dashboard.

SECURITY FIXES (v2):
  - API key authentication on all endpoints (GATEWAY_API_KEY in .env)
  - CORS locked to GATEWAY_CORS_ORIGINS (default: localhost only)
  - WebSocket auth via ?api_key= query param
  - /api/gateway/health exempt from auth (for uptime monitors)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import APIKeyHeader

from agent.google_workspace_accounts import (
    add_account,
    list_accounts,
    remove_account,
)
from agent.google_workspace_oauth import (
    GMAIL_SCOPES,
    clear_oauth_tokens,
    has_oauth_tokens,
    store_oauth_tokens,
)
from config import get_config
from gateway.events import event_bus

logger = logging.getLogger(__name__)

_registry: Any = None
_cron_mgr: Any = None
_agent_fn: Callable[[str], Awaitable[str]] | None = None
_send_fn: Callable[[str], Awaitable[None]] | None = None
_history_mgr: Any = None
_start_time: float = time.time()

# ── Auth ──────────────────────────────────────────────────────────────────────

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def _get_api_key() -> str | None:
    """Return the configured gateway API key, or None if not set (auth disabled)."""
    cfg = get_config()
    key = getattr(cfg, "gateway_api_key", "").strip()
    return key if key else None


async def require_auth(api_key: str | None = Depends(_API_KEY_HEADER)) -> None:
    """
    FastAPI dependency — call on every protected endpoint.

    If GATEWAY_API_KEY is set in .env, the request must include:
        X-API-Key: <your-key>
    If GATEWAY_API_KEY is empty/unset, auth is disabled (dev mode).
    """
    expected = _get_api_key()
    if expected is None:
        # Auth not configured — dev mode, allow all
        logger.debug("Gateway auth: disabled (GATEWAY_API_KEY not set)")
        return
    if not api_key or not secrets.compare_digest(api_key.strip(), expected):
        logger.warning("Gateway auth: rejected request with invalid or missing API key")
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


async def _ws_auth(websocket: WebSocket) -> bool:
    """
    WebSocket auth — check ?api_key= query param.
    Returns True if allowed, False if rejected.

    IMPORTANT: call this BEFORE websocket.accept(). If called after accept(),
    closing the socket raises AttributeError on websockets>=12 because the
    protocol's transfer_data_task is not yet initialised.
    When this returns False, the caller should NOT call websocket.accept() —
    the WebSocket framework will drop the connection automatically.
    """
    expected = _get_api_key()
    if expected is None:
        return True  # dev mode — no key configured
    provided = websocket.query_params.get("api_key", "")
    if not provided or not secrets.compare_digest(provided.strip(), expected):
        logger.warning("Gateway WS: rejected connection — invalid or missing api_key param")
        # Do NOT call websocket.close() here — the connection has not been
        # accepted yet, so close() crashes on websockets>=12.
        # Returning False tells ws_events to skip accept() and return early,
        # which causes the framework to cleanly drop the connection.
        return False
    return True


# ── Wiring ────────────────────────────────────────────────────────────────────

def configure(
    *,
    registry: Any = None,
    cron_mgr: Any = None,
    agent_fn: Callable[[str], Awaitable[str]] | None = None,
    send_fn: Callable[[str], Awaitable[None]] | None = None,
    history_mgr: Any = None,
) -> None:
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


# ── App factory ───────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = get_config()
        key_set = bool(getattr(cfg, "gateway_api_key", "").strip())
        if key_set:
            logger.info("Gateway API started — authentication ENABLED")
        else:
            logger.warning(
                "Gateway API started — authentication DISABLED "
                "(set GATEWAY_API_KEY in .env to enable)"
            )
        yield
        logger.info("Gateway API stopped")

    cfg = get_config()

    # ── CORS ─────────────────────────────────────────────────────────────────
    # Read allowed origins from config; default to localhost only.
    allowed_origins: list[str] = getattr(cfg, "gateway_cors_origins", [])
    if not allowed_origins:
        # Sensible secure default: only the local UI dev server
        allowed_origins = ["http://localhost:3001", "http://127.0.0.1:3001"]
    logger.info("Gateway CORS allowed origins: %s", allowed_origins)

    app = FastAPI(title="LobsterClaw Gateway", version="2.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["X-API-Key", "Content-Type", "Authorization"],
    )

    # ── Health (no auth — safe for uptime monitors) ───────────────────────────
    @app.get("/api/gateway/health")
    async def health():
        return {"status": "ok", "service": "lobsterclaw-gateway", "version": "2.0.0"}

    # ── Everything below requires auth ────────────────────────────────────────
    Auth = Depends(require_auth)

    @app.get("/api/gateway/status", dependencies=[Auth])
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
                cron_total  = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
                cron_active = conn.execute("SELECT COUNT(*) FROM jobs WHERE enabled=1").fetchone()[0]
                conn.close()
            except Exception:
                pass

        return {
            "agent_id":       cfg.agent_id,
            "provider":       cfg.llm_provider,
            "model":          cfg.llm_model,
            "uptime_seconds": round(time.time() - _start_time),
            "auth_enabled":   bool(getattr(cfg, "gateway_api_key", "").strip()),
            "components": {
                "cron":      cfg.cron_enabled,
                "heartbeat": cfg.heartbeat_enabled,
                "memory":    cfg.memory_enabled,
                "browser":   cfg.browser_enabled,
                "canvas":    cfg.canvas_host_enabled,
                "streaming": cfg.llm_streaming,
                "subagents": cfg.subagents_enabled,
                "exec":      cfg.exec_enabled,
                "tts":       cfg.tts_enabled,
            },
            "counts": {
                "tools":           tools_count,
                "skills":          skills_count,
                "sessions":        sessions_count,
                "active_sessions": active_sessions,
                "cron_total":      cron_total,
                "cron_active":     cron_active,
            },
            "ws_subscribers": event_bus.subscriber_count,
        }

    @app.get("/api/gateway/sessions", dependencies=[Auth])
    async def list_sessions():
        try:
            from agent.sessions import get_session_store
            store = get_session_store()
            sessions = await store.list_sessions(limit=100)
            return {
                "sessions": [
                    {
                        "id":            s.id,
                        "label":         s.label,
                        "parent_id":     s.parent_id,
                        "status":        s.status,
                        "model":         s.model,
                        "depth":         s.depth,
                        "token_usage":   s.token_usage,
                        "input_tokens":  s.input_tokens,
                        "output_tokens": s.output_tokens,
                        "created_at":    s.created_at,
                        "updated_at":    s.updated_at,
                        "error":         s.error,
                    }
                    for s in sessions
                ]
            }
        except Exception as e:
            return {"sessions": [], "error": str(e)}

    @app.get("/api/gateway/sessions/{session_id}", dependencies=[Auth])
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
                    "id":            session.id,
                    "label":         session.label,
                    "parent_id":     session.parent_id,
                    "status":        session.status,
                    "model":         session.model,
                    "depth":         session.depth,
                    "token_usage":   session.token_usage,
                    "input_tokens":  session.input_tokens,
                    "output_tokens": session.output_tokens,
                    "created_at":    session.created_at,
                    "updated_at":    session.updated_at,
                    "error":         session.error,
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

    @app.get("/api/gateway/skills", dependencies=[Auth])
    async def list_skills():
        try:
            from agent.skills import load_skills
            skills = load_skills()
            return {
                "skills": [
                    {
                        "name":            s.name,
                        "description":     s.description,
                        "always":          s.always,
                        "path":            str(s.path),
                        "content_preview": (s.content[:300] + "...") if len(s.content) > 300 else s.content,
                    }
                    for s in skills
                ],
                "total": len(skills),
            }
        except Exception as e:
            return {"skills": [], "total": 0, "error": str(e)}

    @app.get("/api/gateway/cron", dependencies=[Auth])
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
                    "id":              r["id"],
                    "description":     r["description"],
                    "schedule":       r["schedule"],
                    "message":        r["message"],
                    "enabled":        bool(r["enabled"]),
                    "created_at":     r["created_at"],
                    "run_count":      r["run_count"],
                    "last_run":       r["last_run"],
                    "next_run":       next_run,
                    "session_target": r["session_target"],
                    "delivery":       r["delivery"],
                    "delete_after_run":bool(r["delete_after_run"]),
                })
            return {"jobs": jobs, "total": len(jobs)}
        except Exception as e:
            return {"jobs": [], "total": 0, "error": str(e)}

    @app.post("/api/gateway/cron/{job_id}/toggle", dependencies=[Auth])
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

    @app.get("/api/gateway/tools", dependencies=[Auth])
    async def list_tools():
        if not _registry:
            return {"tools": [], "total": 0}
        cfg = get_config()
        tools = []
        for name, tool in _registry._tools.items():
            tools.append({
                "name":                 name,
                "description":          tool.description[:300] if len(tool.description) > 300 else tool.description,
                "owner_only":           tool.owner_only,
                "depth_limit":          tool.depth_limit,
                "denied":               name in cfg.tools_deny,
                "allowed":              not cfg.tools_allow or name in cfg.tools_allow,
                "requires_confirmation":name in cfg.tools_require_confirmation,
            })
        return {"tools": sorted(tools, key=lambda t: t["name"]), "total": len(tools)}

    @app.get("/api/gateway/config", dependencies=[Auth])
    async def get_config_view():
        cfg = get_config()
        return {
            "llm": {
                "provider":       cfg.llm_provider,
                "model":          cfg.llm_model,
                "max_tokens":     cfg.llm_max_tokens,
                "streaming":      cfg.llm_streaming,
                "thinking_budget":cfg.llm_thinking_budget,
                "reasoning_mode": cfg.reasoning_mode,
                "max_retries":    cfg.llm_max_retries,
            },
            "telegram": {
                "dm_policy":          cfg.telegram_dm_policy,
                "group_policy":       cfg.telegram_group_policy,
                "link_preview":       cfg.telegram_link_preview,
                "reactions_enabled":  cfg.telegram_reactions_enabled,
                "mention_required":   cfg.telegram_mention_required,
            },
            "features": {
                "cron":      cfg.cron_enabled,
                "heartbeat": cfg.heartbeat_enabled,
                "memory":    cfg.memory_enabled,
                "browser":   cfg.browser_enabled,
                "canvas":    cfg.canvas_host_enabled,
                "subagents": cfg.subagents_enabled,
                "exec":      cfg.exec_enabled,
                "tts":       cfg.tts_enabled,
            },
            "limits": {
                "max_history_messages":  cfg.max_history_messages,
                "max_tool_iterations":   cfg.max_tool_iterations,
                "subagents_max_depth":   cfg.subagents_max_depth,
                "subagents_max_children":cfg.subagents_max_children,
                "tool_result_max_chars": cfg.tool_result_max_chars,
            },
            "identity": {
                "agent_id":    cfg.agent_id,
                "prompt_mode": cfg.prompt_mode,
            },
            "security": {
                "auth_enabled":  bool(getattr(cfg, "gateway_api_key", "").strip()),
                "cors_origins":  getattr(cfg, "gateway_cors_origins", []),
                "bind_address":  cfg.gateway_bind,
            },
        }

    @app.post("/api/gateway/chat", dependencies=[Auth])
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

    @app.get("/api/gateway/history/{session_id}", dependencies=[Auth])
    async def get_history(session_id: str, limit: int = 50):
        try:
            from agent.sessions import get_session_store
            store = get_session_store()
            messages = await store.get_messages(session_id, limit=limit)
            return {"messages": messages, "session_id": session_id}
        except Exception as e:
            return {"messages": [], "error": str(e)}

    @app.get("/api/gateway/metrics", dependencies=[Auth])
    async def get_metrics():
        try:
            from agent.sessions import get_session_store
            store = get_session_store()
            main_usage = await store.get_token_usage("main")
            sessions = await store.list_sessions(limit=200)
            total_in  = sum(s.input_tokens  for s in sessions)
            total_out = sum(s.output_tokens for s in sessions)
            return {
                "main_session": main_usage,
                "all_sessions": {
                    "input_tokens":  total_in,
                    "output_tokens": total_out,
                    "total_tokens":  total_in + total_out,
                    "session_count": len(sessions),
                },
            }
        except Exception as e:
            return {
                "main_session": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "all_sessions": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "session_count": 0},
                "error": str(e),
            }

    @app.get("/api/gateway/workspace", dependencies=[Auth])
    async def list_workspace_files():
        ws_dir = Path(__file__).parent.parent / "workspace"
        files = []
        if ws_dir.exists():
            for f in sorted(ws_dir.iterdir()):
                if f.suffix == ".md":
                    content = f.read_text(encoding="utf-8", errors="replace")
                    files.append({
                        "name":    f.name,
                        "size":    len(content),
                        "preview": (content[:500] + "...") if len(content) > 500 else content,
                    })
        return {"files": files}

    # OAuth state store: nonce -> (account_id, expires_at) — in-memory, single process
    _oauth_state_store: dict[str, tuple[str, float]] = {}

    def _oauth_cleanup_expired():
        now = time.time()
        expired = [k for k, v in _oauth_state_store.items() if v[1] < now]
        for k in expired:
            del _oauth_state_store[k]

    @app.get("/api/gateway/google-accounts", dependencies=[Auth])
    async def list_google_workspace_accounts():
        try:
            accounts = list_accounts(redact=True)
            for acct in accounts:
                acct["oauth_connected"] = has_oauth_tokens(acct.get("id", ""))
            return {"accounts": accounts, "total": len(accounts)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/gateway/google-accounts", dependencies=[Auth])
    async def add_google_workspace_account(body: dict):
        label = str(body.get("label", "")).strip()
        email = str(body.get("email", "")).strip()
        source = str(body.get("source", "manual")).strip() or "manual"
        status = str(body.get("status", "active")).strip() or "active"

        if not label or not email:
            raise HTTPException(status_code=400, detail="Both 'label' and 'email' are required")

        try:
            account = add_account(label=label, email=email, source=source, status=status)
            return {"ok": True, "account": account, "message": "Account added"}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/gateway/google-accounts/{email_or_label}", dependencies=[Auth])
    async def remove_google_workspace_account(email_or_label: str):
        try:
            removed = remove_account(email_or_label)
            if removed is None:
                raise HTTPException(status_code=404, detail="Account not found")
            aid = removed.get("id", "")
            if aid:
                clear_oauth_tokens(aid)
            return {"ok": True, "account": removed, "message": "Account removed"}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── Google OAuth (Phase 2) ────────────────────────────────────────────────

    @app.get("/api/gateway/google-oauth/start", dependencies=[Auth])
    async def google_oauth_start(account_id: str):
        """Return authorization URL for OAuth. UI redirects user to Google."""
        cfg = get_config()
        cid = getattr(cfg, "google_oauth_client_id", "").strip()
        csec = getattr(cfg, "google_oauth_client_secret", "").strip()
        redirect_uri = getattr(cfg, "google_oauth_redirect_uri", "").strip()
        if not cid or not csec or not redirect_uri:
            raise HTTPException(
                status_code=503,
                detail="Google OAuth not configured. Set GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET in .env",
            )
        if not account_id or not account_id.strip():
            raise HTTPException(status_code=400, detail="account_id is required")

        # Build state: nonce -> we lookup account_id on callback
        nonce = secrets.token_hex(16)
        # PKCE: generate code_verifier here so we can pass it during token exchange.
        # google_auth_oauthlib auto-adds code_challenge to the auth URL, so Google
        # requires the matching code_verifier at fetch_token time. Since the Flow
        # object is recreated in the callback, we must persist the verifier ourselves.
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b"=").decode()

        _oauth_cleanup_expired()
        # Store (account_id, expires_at, code_verifier) — 10 min window
        _oauth_state_store[nonce] = (account_id.strip(), time.time() + 600, code_verifier)

        def _build_url():
            from google_auth_oauthlib.flow import Flow

            client_config = {
                "web": {
                    "client_id": cid,
                    "client_secret": csec,
                    "redirect_uris": [redirect_uri],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
            flow = Flow.from_client_config(
                client_config,
                scopes=GMAIL_SCOPES,
                redirect_uri=redirect_uri,
            )
            auth_url, _ = flow.authorization_url(
                access_type="offline",
                prompt="consent",
                state=nonce,
                code_challenge=code_challenge,
                code_challenge_method="S256",
            )
            return auth_url

        try:
            url = await asyncio.to_thread(_build_url)
            return {"authorization_url": url}
        except Exception as e:
            logger.exception("Google OAuth start failed: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/gateway/google-oauth/callback")
    async def google_oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
        """OAuth callback — no auth (Google redirects user here)."""
        ui_base = _gateway_ui_base()
        if error:
            return RedirectResponse(url=f"{ui_base}/google-accounts?oauth_error={error}")
        if not code or not state:
            return RedirectResponse(url=f"{ui_base}/google-accounts?oauth_error=missing_params")

        _oauth_cleanup_expired()
        entry = _oauth_state_store.pop(state, None)
        if not entry:
            return RedirectResponse(url=f"{ui_base}/google-accounts?oauth_error=invalid_state")
        account_id, _, code_verifier = entry

        cfg = get_config()
        cid = getattr(cfg, "google_oauth_client_id", "").strip()
        csec = getattr(cfg, "google_oauth_client_secret", "").strip()
        redirect_uri = getattr(cfg, "google_oauth_redirect_uri", "").strip()
        if not cid or not csec:
            return RedirectResponse(url=f"{ui_base}/google-accounts?oauth_error=config")

        def _exchange():
            import os as _os
            # Google appends `openid` to returned scopes even when not requested.
            # oauthlib raises a Warning-as-exception on any scope mismatch;
            # this env var suppresses that check.
            _os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

            from google_auth_oauthlib.flow import Flow

            client_config = {
                "web": {
                    "client_id": cid,
                    "client_secret": csec,
                    "redirect_uris": [redirect_uri],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
            flow = Flow.from_client_config(
                client_config,
                scopes=GMAIL_SCOPES,
                redirect_uri=redirect_uri,
                state=state,
            )
            flow.fetch_token(code=code, code_verifier=code_verifier)
            creds = flow.credentials
            return creds.refresh_token, creds.token, creds.expiry

        try:
            refresh_token, access_token, expiry = await asyncio.to_thread(_exchange)
            store_oauth_tokens(account_id, refresh_token or "", access_token, expiry)
            return RedirectResponse(url=f"{ui_base}/google-accounts?oauth_connected=1")
        except Exception as e:
            logger.exception("Google OAuth callback failed: %s", e)
            return RedirectResponse(url=f"{ui_base}/google-accounts?oauth_error=exchange")

    def _gateway_ui_base() -> str:
        """Base URL for redirecting back to the Gateway UI."""
        origins = getattr(get_config(), "gateway_cors_origins", []) or []
        if origins:
            first = str(origins[0] if isinstance(origins, list) else origins).strip()
            if first:
                return first.rstrip("/")
        return "http://localhost:3001"

    @app.post("/api/gateway/google-oauth/disconnect", dependencies=[Auth])
    async def google_oauth_disconnect(body: dict):
        """Clear OAuth tokens for an account."""
        account_id = str(body.get("account_id", "")).strip()
        if not account_id:
            raise HTTPException(status_code=400, detail="account_id is required")
        clear_oauth_tokens(account_id)
        return {"ok": True, "message": "OAuth disconnected"}

    # ── AI News ───────────────────────────────────────────────────────────────

    @app.get("/api/gateway/ai-news", dependencies=[Auth])
    async def get_ai_news(category: str | None = None, limit: int = 100):
        """Return cached AI news articles."""
        from tools.ai_news_tool import get_db
        db = get_db()
        articles = db.get_articles(category=category, limit=min(limit, 200))
        stats = db.stats()
        return {
            "articles": articles,
            "total": len(articles),
            "stats": stats,
        }

    @app.post("/api/gateway/ai-news/refresh", dependencies=[Auth])
    async def trigger_ai_news_refresh():
        """Trigger an immediate AI news refresh cycle."""
        from tools.ai_news_tool import refresh_news, trigger_early_refresh
        trigger_early_refresh()
        result = await refresh_news()
        return {"ok": True, **result}

    @app.delete("/api/gateway/ai-news", dependencies=[Auth])
    async def clear_ai_news():
        """Clear all cached AI news articles and content ideas."""
        from tools.ai_news_tool import get_db
        count = get_db().clear_all()
        return {"ok": True, "cleared": count}

    @app.get("/api/gateway/ai-news/content-ideas", dependencies=[Auth])
    async def get_content_ideas():
        """Return AI-scored content ideas for short-form video creation."""
        from tools.ai_news_tool import get_db
        db = get_db()
        ideas = db.get_content_ideas(limit=10)
        return {"ideas": ideas, "total": len(ideas)}

    # ── WebSocket — auth via ?api_key= query param ────────────────────────────
    @app.websocket("/ws/gateway")
    async def ws_events(websocket: WebSocket):
        # Auth check MUST happen before accept() — calling websocket.close()
        # on an un-accepted connection crashes on websockets>=12.
        # (AttributeError: 'WebSocketProtocol' object has no attribute
        #  'transfer_data_task')
        if not await _ws_auth(websocket):
            return  # connection dropped cleanly — no accept() called

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
    import uvicorn
    app = create_app()
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    await server.serve()
