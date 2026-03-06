"""
AI News Research Agent — continuously fetches and stores trending AI news.

Searches multiple queries every poll interval, stores articles in SQLite with
a configurable TTL (default 3 days). Exposes a read tool for the agent and
a Gateway API endpoint for the dashboard.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

# Queries sent each refresh cycle, mapped to a display category
_SEARCH_QUERIES: list[tuple[str, str]] = [
    ("latest AI news today", "General"),
    ("large language model LLM release update today", "Products"),
    ("OpenAI Anthropic Google DeepMind news today", "Industry"),
    ("AI machine learning research paper breakthrough", "Research"),
    ("artificial intelligence startup funding acquisition", "Business"),
]

# Module-level singletons
_db_instance: AiNewsDB | None = None
_refresh_lock: asyncio.Lock | None = None
_refresh_event: asyncio.Event | None = None   # set to wake watcher loop early


# ── Database ──────────────────────────────────────────────────────────────────

class AiNewsDB:
    """SQLite-backed store for AI news articles with TTL expiry."""

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            from config import get_config
            db_path = Path(get_config().data_dir).expanduser() / "ai_news.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(db_path)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS articles (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    title       TEXT NOT NULL,
                    url         TEXT NOT NULL UNIQUE,
                    summary     TEXT DEFAULT '',
                    category    TEXT DEFAULT 'General',
                    source      TEXT DEFAULT '',
                    found_at    REAL NOT NULL,
                    expires_at  REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_expires ON articles(expires_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_found ON articles(found_at DESC)")

    def purge_expired(self) -> int:
        now = time.time()
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM articles WHERE expires_at < ?", (now,))
            return cur.rowcount

    def store_articles(
        self,
        articles: list[dict[str, str]],
        retention_days: int = 3,
    ) -> int:
        """Insert new articles, skip duplicates (by URL). Returns count inserted."""
        if not articles:
            return 0
        now = time.time()
        expires_at = now + retention_days * 86400
        inserted = 0
        with self._conn() as conn:
            for art in articles:
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO articles
                           (title, url, summary, category, source, found_at, expires_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            art.get("title", "")[:300],
                            art.get("url", ""),
                            art.get("summary", "")[:500],
                            art.get("category", "General"),
                            art.get("source", ""),
                            now,
                            expires_at,
                        ),
                    )
                    inserted += conn.execute("SELECT changes()").fetchone()[0]
                except Exception:
                    pass
        return inserted

    def get_articles(
        self,
        category: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        now = time.time()
        with self._conn() as conn:
            if category:
                rows = conn.execute(
                    "SELECT * FROM articles WHERE expires_at > ? AND category = ? "
                    "ORDER BY found_at DESC LIMIT ?",
                    (now, category, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM articles WHERE expires_at > ? "
                    "ORDER BY found_at DESC LIMIT ?",
                    (now, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def clear_all(self) -> int:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM articles")
            return cur.rowcount

    def stats(self) -> dict:
        now = time.time()
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE expires_at > ?", (now,)
            ).fetchone()[0]
            by_cat = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM articles "
                "WHERE expires_at > ? GROUP BY category ORDER BY cnt DESC",
                (now,),
            ).fetchall()
        return {"total": total, "by_category": {r["category"]: r["cnt"] for r in by_cat}}


def get_db() -> AiNewsDB:
    global _db_instance
    if _db_instance is None:
        _db_instance = AiNewsDB()
    return _db_instance


# ── Search result parser ──────────────────────────────────────────────────────

def _extract_domain(url: str) -> str:
    """Extract domain from URL for display."""
    m = re.match(r"https?://([^/]+)", url)
    if m:
        domain = m.group(1)
        return domain.replace("www.", "")
    return ""


def _parse_search_results(text: str, category: str) -> list[dict[str, str]]:
    """
    Parse _web_search() text output into a list of article dicts.
    Handles both DDG (plain) and Brave (**bold**) output formats.
    """
    articles: list[dict[str, str]] = []
    if not text or text.startswith("Error:") or text.startswith("All search providers"):
        return articles

    # Split on blank lines to get per-article blocks
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if len(lines) < 2:
            continue

        # First line = title (strip markdown bold markers)
        title = lines[0].lstrip("*").rstrip("*").strip()
        if not title or title.startswith("[Note:") or title.startswith("Error:"):
            continue

        url = ""
        summary = ""
        for line in lines[1:]:
            if not url and re.match(r"https?://", line):
                url = line.split()[0]
            elif url and not summary and not line.startswith("_"):
                summary = line[:400]

        if not (title and url and url.startswith("http")):
            continue

        articles.append({
            "title": title,
            "url": url,
            "summary": summary,
            "category": category,
            "source": _extract_domain(url),
        })

    return articles


# ── News refresh ──────────────────────────────────────────────────────────────

async def _do_refresh(db: AiNewsDB, retention_days: int = 3) -> dict[str, int]:
    """Run one refresh cycle: purge expired, search all queries, store results."""
    from tools.web_search import _web_search  # import here to avoid circular at module load

    purged = db.purge_expired()
    total_new = 0

    for query, category in _SEARCH_QUERIES:
        try:
            result = await _web_search(query=query, count=8, freshness="pd")
            articles = _parse_search_results(result, category)
            inserted = db.store_articles(articles, retention_days=retention_days)
            total_new += inserted
            logger.debug("AI news: '%s' → %d results, %d new", query, len(articles), inserted)
        except Exception as e:
            logger.warning("AI news query '%s' failed: %s", query, e)
        await asyncio.sleep(1)  # gentle pacing between queries

    logger.info("AI news refresh: +%d new articles, -%d expired", total_new, purged)
    return {"new": total_new, "purged": purged}


async def refresh_news() -> dict[str, int]:
    """Public entry point for on-demand refresh (called by gateway endpoint)."""
    global _refresh_lock
    if _refresh_lock is None:
        _refresh_lock = asyncio.Lock()

    from config import get_config
    cfg = get_config()
    retention = getattr(cfg, "ai_news_retention_days", 3)

    if _refresh_lock.locked():
        return {"new": 0, "purged": 0, "status": "already_running"}

    async with _refresh_lock:
        return await _do_refresh(get_db(), retention_days=retention)


# ── Background watcher loop ───────────────────────────────────────────────────

async def ai_news_watcher_loop(cfg: Any) -> None:
    """
    Background task: periodically searches for AI news and stores results.
    Runs immediately on startup then sleeps for poll_interval_minutes.
    """
    global _refresh_lock, _refresh_event

    if _refresh_lock is None:
        _refresh_lock = asyncio.Lock()
    if _refresh_event is None:
        _refresh_event = asyncio.Event()

    poll_minutes = getattr(cfg, "ai_news_poll_interval_minutes", 60)
    retention_days = getattr(cfg, "ai_news_retention_days", 3)
    db = get_db()

    logger.info("AI news watcher started (interval=%dm, retention=%dd)", poll_minutes, retention_days)

    while True:
        try:
            async with _refresh_lock:
                await _do_refresh(db, retention_days=retention_days)
        except Exception as e:
            logger.warning("AI news watcher error: %s", e)

        # Sleep for poll_minutes, but wake early if _refresh_event is set
        _refresh_event.clear()
        try:
            await asyncio.wait_for(
                _refresh_event.wait(),
                timeout=poll_minutes * 60,
            )
        except asyncio.TimeoutError:
            pass


def trigger_early_refresh() -> None:
    """Signal the watcher loop to skip its sleep and refresh now."""
    if _refresh_event is not None:
        _refresh_event.set()


# ── Tool definitions ──────────────────────────────────────────────────────────

async def _ai_news_list(
    category: str | None = None,
    limit: int = 20,
    **_kwargs: Any,
) -> str:
    """Return latest AI news from the local cache."""
    db = get_db()
    articles = db.get_articles(category=category or None, limit=min(limit, 50))
    if not articles:
        return "No AI news in cache yet. The watcher runs every hour — try again shortly."

    stats = db.stats()
    lines = [f"📰 *AI News* — {stats['total']} articles cached\n"]
    for art in articles:
        age_secs = time.time() - art["found_at"]
        age_str = _fmt_age(age_secs)
        lines.append(
            f"*{art['title']}*\n"
            f"[{art['source']}]({art['url']}) · {art['category']} · {age_str}\n"
            + (f"_{art['summary'][:200]}_\n" if art.get("summary") else "")
        )
    return "\n".join(lines).strip()


async def _ai_news_refresh(**_kwargs: Any) -> str:
    """Trigger an immediate AI news refresh."""
    result = await refresh_news()
    if result.get("status") == "already_running":
        return "⏳ Refresh already in progress."
    return f"✅ AI news refreshed: +{result['new']} new articles, {result['purged']} expired removed."


async def _ai_news_clear(**_kwargs: Any) -> str:
    """Clear all cached AI news articles."""
    count = get_db().clear_all()
    return f"🗑 Cleared {count} AI news articles from cache."


def _fmt_age(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


AI_NEWS_LIST_TOOL = ToolDefinition(
    name="ai_news_list",
    description=(
        "List the latest cached AI news articles. "
        "Articles are fetched every hour and kept for 3 days. "
        "Optionally filter by category: General, Products, Research, Industry, Business."
    ),
    parameters={
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["General", "Products", "Research", "Industry", "Business"],
                "description": "Filter by category (optional)",
            },
            "limit": {
                "type": "integer",
                "description": "Max articles to return (default 20, max 50)",
                "default": 20,
            },
        },
        "required": [],
    },
    fn=_ai_news_list,
    owner_only=False,
)

AI_NEWS_REFRESH_TOOL = ToolDefinition(
    name="ai_news_refresh",
    description="Trigger an immediate AI news refresh (normally runs every hour automatically).",
    parameters={"type": "object", "properties": {}, "required": []},
    fn=_ai_news_refresh,
    owner_only=True,
)

AI_NEWS_CLEAR_TOOL = ToolDefinition(
    name="ai_news_clear",
    description="Clear all cached AI news articles.",
    parameters={"type": "object", "properties": {}, "required": []},
    fn=_ai_news_clear,
    owner_only=True,
)
