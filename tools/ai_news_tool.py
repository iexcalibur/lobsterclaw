"""
AI News Research Agent — v3

Architecture (4-dimension search, inspired by openclaw/skills ai-news-collector):
  Perplexity sonar [day]   → Products: official launches, model releases (recency=day)
  Perplexity sonar [day]   → Research: papers, breakthroughs (recency=day)
  Perplexity sonar [week]  → Viral: GitHub trending, HN, Reddit viral AI (recency=week)
  Perplexity sonar [week]  → Newsletter: The Batch, AI Weekly, Substack roundups (recency=week)
  Gemini + Google Search   → GitHub + HN trending (community signals, real-time)
  Gemini Flash             → Content scoring (top 3 reel-worthy ideas from all sources)
  SQLite                   → Article + content idea storage (3-day TTL)

Key insight: "Don't just search 'AI news today'" — generic searches miss community
viral phenomena (GitHub stars surging, HN frontpage, viral open-source tools).
Multi-dimensional search captures both top-down (official announcements) and
bottom-up (community-driven) signals.

Schedule:
  News fetch  — every 4h, active hours 10am–10pm only
                runs immediately on startup if within active hours
  Content brief — daily at configured hour (default 6am) → Telegram
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_IST = ZoneInfo("Asia/Kolkata")

import httpx

from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

# ── Categories (AI-focused: Products and Research only) ──────────────────────

CATEGORIES = ["Products", "Research"]

# Multi-dimensional search strategy (inspired by openclaw/skills ai-news-collector).
# Key insight: "Don't just search 'AI news today'" — generic searches return SEO
# aggregation pages and miss community viral phenomena (GitHub stars surging,
# HN frontpage, viral open-source tools). Must use 4 dimensions:
#
#   Dim A  Products   — Official launches, model releases, big lab announcements  (recency=day)
#   Dim B  Research   — Papers, benchmarks, scientific breakthroughs              (recency=day)
#   Dim C  Viral      — GitHub trending, HN, Reddit viral AI — bottom-up signals  (recency=week)
#   Dim D  Newsletter — The Batch, AI Weekly, Substack roundups (highest density) (recency=week)
#
# category=None → auto-assigned via _auto_category() so UI keeps Products/Research only.

_PERPLEXITY_SEARCHES: list[dict] = [
    {
        "label": "Products",
        "category": "Products",
        "recency": "day",
        "prompt": (
            "List the 8 most significant new AI product launches, model releases, tool "
            "announcements, and major updates from the last 24 hours. Everything must be "
            "AI-related. Include: company name, product name, key capability or benchmark. "
            "Cover: OpenAI, Anthropic, Google DeepMind, Meta AI, Mistral, xAI, Stability AI, "
            "Hugging Face, and any other AI companies or startups."
        ),
    },
    {
        "label": "Research",
        "category": "Research",
        "recency": "day",
        "prompt": (
            "List the 6 most significant AI and machine learning research breakthroughs, papers, "
            "and scientific findings from the last 24 hours. Everything must be AI/ML-related. "
            "For each: what was discovered or achieved, why it matters, who published it. "
            "Include arXiv papers, top conference findings, and industry research labs."
        ),
    },
    {
        "label": "Viral",
        "category": None,   # auto-categorized per article
        "recency": "week",
        "prompt": (
            "What AI open-source projects, tools, or models are going viral this week? Focus on: "
            "GitHub repositories suddenly gaining thousands of stars, AI topics trending on "
            "Hacker News or Reddit r/MachineLearning r/artificial, unexpected community-driven "
            "AI tools or agents developers are excited about, and controversial AI events "
            "generating buzz. List 6 items — include what sparked the viral moment and why "
            "developers care."
        ),
    },
    {
        "label": "Newsletter",
        "category": None,   # auto-categorized per article
        "recency": "week",
        "prompt": (
            "Summarize the top AI news from this week's major AI newsletters and roundups — "
            "including The Batch by deeplearning.ai, AI Weekly, ImportAI, and Substack AI "
            "newsletters. What are the 8 most important AI developments that newsletter editors "
            "and curators highlighted as must-know this week? These are high-signal items that "
            "experts consider the week's most important AI news."
        ),
    },
]

# Fallback queries when no API keys are set (uses the existing web_search)
_FALLBACK_QUERIES: list[tuple[str, str | None]] = [
    ("new AI product model release launch this week", "Products"),
    ("AI research breakthrough paper this week", "Research"),
    ("viral AI tool trending GitHub Hacker News this week", None),
    ("AI weekly roundup newsletter top stories", None),
]


# ── Database ──────────────────────────────────────────────────────────────────

class AiNewsDB:
    """SQLite-backed store for AI news articles and content ideas."""

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
                    category    TEXT DEFAULT 'Products',
                    source      TEXT DEFAULT '',
                    found_at    REAL NOT NULL,
                    expires_at  REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS content_ideas (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    title       TEXT NOT NULL,
                    url         TEXT DEFAULT '',
                    category    TEXT DEFAULT '',
                    source      TEXT DEFAULT '',
                    angle       TEXT NOT NULL,
                    why         TEXT DEFAULT '',
                    created_at  REAL NOT NULL,
                    expires_at  REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_art_expires ON articles(expires_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_art_found ON articles(found_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ideas_created ON content_ideas(created_at DESC)")

    # ── Articles ──────────────────────────────────────────────────────────────

    def purge_expired(self) -> int:
        now = time.time()
        with self._conn() as conn:
            r1 = conn.execute("DELETE FROM articles WHERE expires_at < ?", (now,)).rowcount
            r2 = conn.execute("DELETE FROM content_ideas WHERE expires_at < ?", (now,)).rowcount
        return r1 + r2

    def store_articles(self, articles: list[dict], retention_days: int = 3) -> int:
        if not articles:
            return 0
        now = time.time()
        expires_at = now + retention_days * 86400
        inserted = 0
        with self._conn() as conn:
            for art in articles:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO articles "
                        "(title, url, summary, category, source, found_at, expires_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            art.get("title", "")[:300],
                            art.get("url", ""),
                            art.get("summary", "")[:500],
                            art.get("category", "Products"),
                            art.get("source", ""),
                            now,
                            expires_at,
                        ),
                    )
                    inserted += conn.execute("SELECT changes()").fetchone()[0]
                except Exception:
                    pass
        return inserted

    def get_articles(self, category: str | None = None, limit: int = 100) -> list[dict]:
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
            r1 = conn.execute("DELETE FROM articles").rowcount
            r2 = conn.execute("DELETE FROM content_ideas").rowcount
        return r1 + r2

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
            idea_count = conn.execute(
                "SELECT COUNT(*) FROM content_ideas WHERE expires_at > ?", (now,)
            ).fetchone()[0]
        return {
            "total": total,
            "by_category": {r["category"]: r["cnt"] for r in by_cat},
            "content_ideas": idea_count,
        }

    # ── Content Ideas ─────────────────────────────────────────────────────────

    def store_content_ideas(self, ideas: list[dict], retention_days: int = 3) -> int:
        if not ideas:
            return 0
        now = time.time()
        expires_at = now + retention_days * 86400
        # Clear old ideas before storing fresh ones
        with self._conn() as conn:
            conn.execute("DELETE FROM content_ideas")
            inserted = 0
            for idea in ideas:
                try:
                    conn.execute(
                        "INSERT INTO content_ideas "
                        "(title, url, category, source, angle, why, created_at, expires_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            idea.get("title", "")[:300],
                            idea.get("url", ""),
                            idea.get("category", ""),
                            idea.get("source", ""),
                            idea.get("angle", "")[:400],
                            idea.get("why", "")[:400],
                            now,
                            expires_at,
                        ),
                    )
                    inserted += 1
                except Exception:
                    pass
        return inserted

    def get_content_ideas(self, limit: int = 10) -> list[dict]:
        now = time.time()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM content_ideas WHERE expires_at > ? "
                "ORDER BY created_at DESC LIMIT ?",
                (now, limit),
            ).fetchall()
        return [dict(r) for r in rows]


# ── Singleton ─────────────────────────────────────────────────────────────────

_db_instance: AiNewsDB | None = None
_refresh_lock: asyncio.Lock | None = None
_refresh_event: asyncio.Event | None = None


def get_db() -> AiNewsDB:
    global _db_instance
    if _db_instance is None:
        _db_instance = AiNewsDB()
    return _db_instance


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_domain(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url)
    if m:
        return m.group(1).replace("www.", "")
    return ""


def _fmt_age(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


# ── Fetch: Perplexity (news with citations) ───────────────────────────────────

async def _fetch_perplexity(
    label: str, prompt: str, api_key: str, category: str | None = None, recency: str = "day"
) -> list[dict]:
    """Fetch news from Perplexity sonar with citation URLs.

    category=None → each article is auto-categorized via _auto_category().
    recency       → Perplexity search_recency_filter: "day" | "week" | "month"
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "sonar",
                    "messages": [{"role": "user", "content": prompt}],
                    "search_recency_filter": recency,
                },
            )
            r.raise_for_status()
            data = r.json()

        content: str = data["choices"][0]["message"]["content"]
        citations: list[str] = data.get("citations", [])
        return _parse_perplexity(content, citations, category)

    except Exception as e:
        logger.warning("Perplexity fetch [%s] failed: %s", label, e)
        return []


def _parse_perplexity(content: str, citations: list[str], category: str | None) -> list[dict]:
    """Parse Perplexity's numbered list response + citation URLs into article dicts.

    category=None → auto-assign per article via _auto_category(title).
    """
    articles: list[dict] = []

    # Split on numbered list items: "1. ...", "2. ..."
    blocks = re.split(r"\n\d+\.\s+", "\n" + content.strip())
    items = [b.strip() for b in blocks[1:] if b.strip()]  # skip text before first item

    for i, item in enumerate(items):
        if not item:
            continue

        # Title: first bold phrase or first line — strip markdown before storing
        title_m = re.match(r"\*\*([^*\n]+)\*\*", item)
        if title_m:
            title = _strip_md(title_m.group(1)).rstrip(":").strip()
        else:
            title = _strip_md(item.split("\n")[0].split(".")[0])[:120]

        if not title or len(title) < 5:
            continue

        # Summary: full block cleaned of markdown and citation refs
        summary = _strip_md(item)[:400]

        # URL from citations array (index-aligned)
        url = citations[i] if i < len(citations) else ""

        # Auto-assign category if not explicitly set (Viral/Newsletter searches)
        art_category = category if category is not None else _auto_category(title)

        articles.append({
            "title": title,
            "url": url,
            "summary": summary,
            "category": art_category,
            "source": _extract_domain(url) if url else "perplexity",
        })

    return articles


# ── Fetch: Gemini + Google Search grounding (trending) ───────────────────────

async def _fetch_gemini_trending(api_key: str) -> list[dict]:
    """Use Gemini with Google Search grounding for real-time trending AI topics.

    Focuses on GitHub trending + Hacker News — sources that surface community-driven
    viral content that Perplexity product/research searches often miss.
    """
    query = (
        "What AI and machine learning repositories are trending on GitHub right now today? "
        "Also what AI topics are on Hacker News frontpage today? "
        "List the top 8 specific AI tools, models, or projects getting the most developer "
        "attention right now — include their names and links."
    )
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
                json={
                    "contents": [{"parts": [{"text": query}]}],
                    "tools": [{"google_search": {}}],
                },
            )
            r.raise_for_status()
            data = r.json()

        articles: list[dict] = []
        candidate = data.get("candidates", [{}])[0]

        # Extract from grounding metadata chunks (real URLs + titles)
        grounding = candidate.get("groundingMetadata", {})
        chunks = grounding.get("groundingChunks", [])
        for chunk in chunks:
            web = chunk.get("web", {})
            title = _strip_md(web.get("title", "").strip())
            url = web.get("uri", "").strip()
            if title and url and url.startswith("http"):
                # Auto-assign category based on title keywords
                cat = _auto_category(title)
                articles.append({
                    "title": title,
                    "url": url,
                    "summary": "",
                    "category": cat,
                    "source": _extract_domain(url),
                })

        return articles[:8]

    except Exception as e:
        logger.warning("Gemini trending fetch failed: %s", e)
        return []


def _auto_category(title: str) -> str:
    """Assign category based on title keywords — Products or Research only."""
    t = title.lower()
    if any(w in t for w in ["paper", "research", "study", "arxiv", "benchmark", "dataset", "breakthrough", "findings", "published"]):
        return "Research"
    return "Products"


def _strip_md(text: str) -> str:
    """Strip markdown formatting so plain text renders cleanly in the UI."""
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)   # **bold** → bold
    text = re.sub(r"\*([^*\n]+)\*", r"\1", text)        # *italic* → italic
    text = re.sub(r"`([^`]+)`", r"\1", text)            # `code` → code
    text = re.sub(r"\[\d+\]", "", text)                 # [1] citation refs
    text = re.sub(r"#{1,6}\s+", "", text)               # ## headings
    text = re.sub(r"\s{2,}", " ", text)                 # collapse extra spaces
    return text.strip()


# ── Content scoring: Gemini Flash ─────────────────────────────────────────────

async def _score_content_ideas(articles: list[dict], api_key: str) -> list[dict]:
    """Use Gemini Flash to pick top 3 reel-worthy content ideas from recent articles."""
    if not articles or not api_key:
        return _rule_based_score(articles)

    article_list = "\n".join(
        f"{i + 1}. [{a['category']}] {a['title']}"
        for i, a in enumerate(articles[:25])
    )

    prompt = f"""You help a tech content creator find the best AI topics for 60-second reaction/commentary reels.

Today's AI news (collected from product launches, research papers, GitHub trending, HN, and AI newsletters):
{article_list}

Select TOP 3 most reel-worthy topics. Prioritise articles with these HIGH-SIGNAL viral indicators:
- Multiple sources/outlets covering the same event (confirmed hot topic)
- Community viral proof: GitHub stars surging, HN frontpage, Reddit r/ML trending
- Surprising, controversial, or unexpected (challenges what people thought was true)
- Big lab official announcements (OpenAI, Anthropic, Google, Meta) — but only if genuinely impactful
- Easy to explain in 60 seconds with a strong "wait, what?!" reaction
- Has short-form video potential (visual demo possible, relatable to non-experts)

Return ONLY a valid JSON array, no other text:
[
  {{"index": 1, "title": "exact title from list", "angle": "Your content angle in one punchy sentence", "why": "One sentence on why this will perform well on short-form video"}}
]"""

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
                json={"contents": [{"parts": [{"text": prompt}]}]},
            )
            r.raise_for_status()
            data = r.json()

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        m = re.search(r"\[[\s\S]*?\]", text)
        if not m:
            return _rule_based_score(articles)

        ideas_raw = json.loads(m.group())
        result: list[dict] = []
        for idea in ideas_raw[:3]:
            idx = int(idea.get("index", 1)) - 1
            art = articles[idx] if 0 <= idx < len(articles) else {}
            result.append({
                "title": _strip_md(idea.get("title", art.get("title", ""))),
                "url": art.get("url", ""),
                "category": art.get("category", ""),
                "source": art.get("source", ""),
                "angle": _strip_md(idea.get("angle", "")),
                "why": _strip_md(idea.get("why", "")),
            })
        return result

    except Exception as e:
        logger.warning("Content scoring failed: %s — falling back to rule-based", e)
        return _rule_based_score(articles)


def _rule_based_score(articles: list[dict]) -> list[dict]:
    """Simple keyword-based fallback scorer when Gemini is unavailable."""
    hot_words = [
        "launches", "beats", "surpasses", "first ever", "breakthrough",
        "open source", "free", "gpt-5", "claude 4", "gemini ultra",
        "raises", "billion", "acquires", "shut down", "banned",
    ]
    scored = []
    for art in articles:
        t = art["title"].lower()
        score = sum(1 for w in hot_words if w in t)
        scored.append((score, art))
    scored.sort(key=lambda x: -x[0])
    return [
        {**art, "angle": "React to this — it's big news in AI.", "why": "High-signal keyword match"}
        for _, art in scored[:3]
    ]


# ── Active hours check ────────────────────────────────────────────────────────

def _in_active_hours(start: int = 10, end: int = 22) -> bool:
    hour = datetime.now().hour
    return start <= hour < end


# ── Core refresh ──────────────────────────────────────────────────────────────

async def _do_refresh_news(db: AiNewsDB, retention_days: int = 3) -> dict[str, int]:
    """Fetch AI news only — no content idea scoring. Called by watcher loop and manual refresh."""
    from config import get_config
    cfg = get_config()
    perplexity_key: str = getattr(cfg, "perplexity_api_key", "")
    gemini_key: str = getattr(cfg, "gemini_api_key", "")

    purged = db.purge_expired()
    total_new = 0

    # ── 1. Perplexity: 4-dimensional search (Products, Research, Viral, Newsletter) ──
    if perplexity_key:
        for search in _PERPLEXITY_SEARCHES:
            articles = await _fetch_perplexity(
                label=search["label"],
                prompt=search["prompt"],
                api_key=perplexity_key,
                category=search["category"],
                recency=search["recency"],
            )
            inserted = db.store_articles(articles, retention_days)
            total_new += inserted
            logger.debug(
                "Perplexity [%s/%s]: %d results, %d new",
                search["label"], search["recency"], len(articles), inserted,
            )
            await asyncio.sleep(0.5)
    else:
        # Fallback: generic web_search
        from tools.web_search import _web_search
        for query, category in _FALLBACK_QUERIES:
            try:
                result = await _web_search(query=query, count=6, freshness="pw")
                articles = _parse_fallback(result, category)
                inserted = db.store_articles(articles, retention_days)
                total_new += inserted
            except Exception as e:
                logger.warning("Fallback search [%s] failed: %s", category, e)
            await asyncio.sleep(1)

    # ── 2. Gemini: trending topics ────────────────────────────────────────
    if gemini_key:
        trending = await _fetch_gemini_trending(gemini_key)
        inserted = db.store_articles(trending, retention_days)
        total_new += inserted
        logger.debug("Gemini trending: %d results, %d new", len(trending), inserted)

    logger.info("AI news refresh done: +%d new articles, -%d expired", total_new, purged)
    return {"new": total_new, "purged": purged}


async def _do_score_ideas(db: AiNewsDB, retention_days: int = 3) -> int:
    """Score content ideas from recent articles. Called only at 6 PM IST daily."""
    from config import get_config
    gemini_key: str = getattr(get_config(), "gemini_api_key", "")
    recent = db.get_articles(limit=25)
    if not recent:
        return 0
    ideas = await _score_content_ideas(recent, gemini_key)
    if ideas:
        count = db.store_content_ideas(ideas, retention_days)
        logger.info("Content ideas scored at 6 PM IST: %d ideas stored", count)
        return count
    return 0


def _parse_fallback(text: str, category: str | None) -> list[dict]:
    """Parse generic _web_search() text output (DDG/Brave format)."""
    articles: list[dict] = []
    if not text or text.startswith("Error:"):
        return articles
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        title = lines[0].lstrip("*").rstrip("*").strip()
        if not title or title.startswith("[Note:"):
            continue
        url = ""
        summary = ""
        for line in lines[1:]:
            if not url and re.match(r"https?://", line):
                url = line.split()[0]
            elif url and not summary and not line.startswith("_"):
                summary = line[:300]
        if title and url and url.startswith("http"):
            art_category = category if category is not None else _auto_category(title)
            articles.append({
                "title": title, "url": url, "summary": summary,
                "category": art_category, "source": _extract_domain(url),
            })
    return articles


# ── Public refresh (gateway endpoint) ────────────────────────────────────────

async def refresh_news() -> dict[str, Any]:
    """Refresh AI news only — does NOT regenerate content ideas (those update at 6 PM IST)."""
    global _refresh_lock
    if _refresh_lock is None:
        _refresh_lock = asyncio.Lock()
    from config import get_config
    cfg = get_config()
    if _refresh_lock.locked():
        return {"new": 0, "purged": 0, "status": "already_running"}
    async with _refresh_lock:
        return await _do_refresh_news(get_db(), retention_days=getattr(cfg, "ai_news_retention_days", 3))


def trigger_early_refresh() -> None:
    if _refresh_event is not None:
        _refresh_event.set()


# ── Background loops ──────────────────────────────────────────────────────────

async def ai_news_watcher_loop(cfg: Any) -> None:
    """
    Fetch AI news every poll_interval_minutes, but only within active hours.
    Runs immediately on startup if within active hours.
    """
    global _refresh_lock, _refresh_event
    if _refresh_lock is None:
        _refresh_lock = asyncio.Lock()
    if _refresh_event is None:
        _refresh_event = asyncio.Event()

    poll_minutes: int = getattr(cfg, "ai_news_poll_interval_minutes", 240)
    retention_days: int = getattr(cfg, "ai_news_retention_days", 3)
    active_start: int = getattr(cfg, "ai_news_active_hours_start", 10)
    active_end: int = getattr(cfg, "ai_news_active_hours_end", 22)
    db = get_db()

    logger.info(
        "AI news watcher started — interval=%dm, active=%02d:00–%02d:00, retention=%dd",
        poll_minutes, active_start, active_end, retention_days,
    )

    while True:
        if _in_active_hours(active_start, active_end):
            try:
                async with _refresh_lock:
                    await _do_refresh_news(db, retention_days=retention_days)
            except Exception as e:
                logger.warning("AI news watcher error: %s", e)
        else:
            logger.debug(
                "AI news: outside active hours (now=%02d:00, window=%02d:00–%02d:00)",
                datetime.now().hour, active_start, active_end,
            )

        _refresh_event.clear()
        try:
            await asyncio.wait_for(_refresh_event.wait(), timeout=poll_minutes * 60)
        except asyncio.TimeoutError:
            pass


async def content_brief_loop(send_fn: Any, cfg: Any) -> None:
    """
    Daily at 6 PM IST:
    1. Score fresh content ideas from recent articles
    2. Send Telegram brief with top 3 ideas
    """
    retention_days: int = getattr(cfg, "ai_news_retention_days", 3)
    db = get_db()
    logger.info("Content brief loop started — fires daily at 18:00 IST (6 PM)")

    while True:
        # Calculate seconds until next 6 PM IST
        now = datetime.now(_IST)
        next_run = now.replace(hour=18, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        wait_secs = (next_run - now).total_seconds()
        logger.debug("Content brief: next run in %.0f seconds (%s IST)", wait_secs, next_run.strftime("%H:%M %d/%m"))
        await asyncio.sleep(wait_secs)

        try:
            # Score fresh content ideas at 6 PM
            await _do_score_ideas(db, retention_days)

            # Send Telegram brief
            ideas = db.get_content_ideas(limit=3)
            if ideas:
                msg = _format_content_brief(ideas, 18)
                await send_fn(msg)
                logger.info("Content brief sent at 6 PM IST (%d ideas)", len(ideas))
            else:
                logger.debug("Content brief: no ideas to send")
        except Exception as e:
            logger.warning("Content brief send failed: %s", e)


def _format_content_brief(ideas: list[dict], brief_hour: int = 6) -> str:
    greeting = "Good morning" if brief_hour < 12 else "Good evening"
    lines = [f"🎬 *{greeting}! Today's Top Content Ideas*\n"]
    emoji = ["🥇", "🥈", "🥉"]
    for i, idea in enumerate(ideas[:3]):
        lines.append(
            f"{emoji[i]} *{idea['title']}*\n"
            f"💡 _{idea['angle']}_\n"
            f"📈 {idea['why']}\n"
            + (f"🔗 {idea['url']}\n" if idea.get("url") else "")
        )
    lines.append("_Happy creating! 🚀_")
    return "\n".join(lines).strip()


# ── Tool definitions ──────────────────────────────────────────────────────────

async def _ai_news_list(category: str | None = None, limit: int = 20, **_: Any) -> str:
    db = get_db()
    articles = db.get_articles(category=category or None, limit=min(limit, 50))
    if not articles:
        return "No AI news cached yet. The watcher fetches every 4h between 10am–10pm."
    stats = db.stats()
    lines = [f"📰 *AI News* — {stats['total']} articles\n"]
    for art in articles:
        age_str = _fmt_age(time.time() - art["found_at"])
        lines.append(
            f"*{art['title']}*\n"
            f"[{art['source']}]({art['url']}) · {art['category']} · {age_str}\n"
            + (f"_{art['summary'][:180]}_\n" if art.get("summary") else "")
        )
    return "\n".join(lines).strip()


async def _ai_news_content_ideas(**_: Any) -> str:
    db = get_db()
    ideas = db.get_content_ideas(limit=3)
    if not ideas:
        return "No content ideas scored yet. They're generated after each news refresh cycle."
    lines = ["🎬 *Top Content Ideas*\n"]
    for i, idea in enumerate(ideas, 1):
        lines.append(
            f"*{i}. {idea['title']}*\n"
            f"💡 _{idea['angle']}_\n"
            f"📈 {idea['why']}\n"
            + (f"🔗 {idea['url']}\n" if idea.get("url") else "")
        )
    return "\n".join(lines).strip()


async def _ai_news_refresh(**_: Any) -> str:
    result = await refresh_news()
    if result.get("status") == "already_running":
        return "⏳ Refresh already in progress."
    return f"✅ +{result['new']} new articles, {result['purged']} expired removed."


async def _ai_news_clear(**_: Any) -> str:
    count = get_db().clear_all()
    return f"🗑 Cleared {count} AI news items from cache."


AI_NEWS_LIST_TOOL = ToolDefinition(
    name="ai_news_list",
    description=(
        "List cached AI news articles. "
        "Filter by category: Products or Research (all AI-focused). "
        "Updated every 4 hours between 10am–10pm."
    ),
    parameters={
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["Products", "Research"],
                "description": "Filter by category (optional)",
            },
            "limit": {"type": "integer", "description": "Max results (default 20)", "default": 20},
        },
        "required": [],
    },
    fn=_ai_news_list,
)

AI_NEWS_CONTENT_IDEAS_TOOL = ToolDefinition(
    name="ai_news_content_ideas",
    description="Get today's top 3 AI content ideas — reel-worthy topics scored by AI for short-form video potential.",
    parameters={"type": "object", "properties": {}, "required": []},
    fn=_ai_news_content_ideas,
)

AI_NEWS_REFRESH_TOOL = ToolDefinition(
    name="ai_news_refresh",
    description="Trigger an immediate AI news refresh cycle (normally runs every 4h).",
    parameters={"type": "object", "properties": {}, "required": []},
    fn=_ai_news_refresh,
    owner_only=True,
)

AI_NEWS_CLEAR_TOOL = ToolDefinition(
    name="ai_news_clear",
    description="Clear all cached AI news articles and content ideas.",
    parameters={"type": "object", "properties": {}, "required": []},
    fn=_ai_news_clear,
    owner_only=True,
)
