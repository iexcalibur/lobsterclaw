"""
Trending Topic Engine — multi-source topic discovery

Architecture (3-tier):
  Tier 1 — News (World)
    ├── Google News RSS        → raw headlines (free, no API key)
    ├── Gemini + Google Search → summarize + ground breaking news
    └── Perplexity sonar       → deep web news with citations

  Tier 2 — Community Signals
    ├── Reddit .json           → subreddit hot posts + top comments (free)
    ├── Perplexity sonar       → "site:reddit.com [topic]" summarized discussion
    └── HackerNews Algolia API → tech trending (free)

  Tier 3 — Social Signals
    └── Grok API               → X/Twitter trending (fetches + summarizes)

  Merge: Gemini → picks top 3 trending topic cards from all signals

Features:
  - Topic-configurable: AI, Finance, Finance+AI, Marketing, etc.
  - Auto-refresh toggle (enable/disable scheduler)
  - Daily 6 PM digest (always runs)
  - SQLite cache with TTL
  - Manual refresh via gateway button

Schedule:
  Auto-refresh — every 4-12h (configurable), only when enabled
  Daily digest — 6 PM IST daily (always runs)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import httpx

from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")

# ── Default topics ────────────────────────────────────────────────────────────

DEFAULT_TOPICS: dict[str, dict[str, Any]] = {
    "AI": {
        "label": "AI",
        "queries": ["artificial intelligence", "AI", "machine learning", "LLM"],
        "subreddits": ["artificial", "MachineLearning", "ChatGPT", "LocalLLaMA"],
        "hn_tags": ["ai", "machine-learning", "llm", "gpt"],
    },
    "Finance": {
        "label": "Finance",
        "queries": ["finance", "stock market", "investing", "fintech"],
        "subreddits": ["finance", "investing", "stocks", "fintech"],
        "hn_tags": ["finance", "fintech", "investing"],
    },
    "Finance+AI": {
        "label": "Finance + AI",
        "queries": ["AI in finance", "fintech AI", "algorithmic trading AI", "AI stock market"],
        "subreddits": ["algotrading", "fintech", "artificial", "MachineLearning"],
        "hn_tags": ["fintech", "ai", "trading"],
    },
    "Marketing": {
        "label": "Marketing",
        "queries": ["digital marketing trends", "marketing AI", "content marketing", "SEO trends"],
        "subreddits": ["marketing", "digital_marketing", "SEO", "socialmedia"],
        "hn_tags": ["marketing", "growth", "seo"],
    },
}


# ── Database ──────────────────────────────────────────────────────────────────

class TrendingDB:
    """SQLite store for trending signals and topic cards."""

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            from config import get_config
            db_path = Path(get_config().data_dir).expanduser() / "trending_topics.db"
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
                CREATE TABLE IF NOT EXISTS signals (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic       TEXT NOT NULL,
                    tier        TEXT NOT NULL,
                    source      TEXT NOT NULL,
                    title       TEXT NOT NULL,
                    url         TEXT DEFAULT '',
                    summary     TEXT DEFAULT '',
                    score       REAL DEFAULT 0,
                    metadata    TEXT DEFAULT '{}',
                    found_at    REAL NOT NULL,
                    expires_at  REAL NOT NULL,
                    UNIQUE(topic, url)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS topic_cards (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic       TEXT NOT NULL,
                    rank        INTEGER NOT NULL,
                    title       TEXT NOT NULL,
                    summary     TEXT NOT NULL,
                    sources     TEXT DEFAULT '[]',
                    why_trending TEXT DEFAULT '',
                    created_at  REAL NOT NULL,
                    expires_at  REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sig_topic ON signals(topic, found_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_topic ON topic_cards(topic, created_at DESC)")

    def purge_expired(self) -> int:
        now = time.time()
        with self._conn() as conn:
            r1 = conn.execute("DELETE FROM signals WHERE expires_at < ?", (now,)).rowcount
            r2 = conn.execute("DELETE FROM topic_cards WHERE expires_at < ?", (now,)).rowcount
        return r1 + r2

    def store_signals(self, signals: list[dict], topic: str, retention_days: int = 3) -> int:
        if not signals:
            return 0
        now = time.time()
        expires_at = now + retention_days * 86400
        inserted = 0
        with self._conn() as conn:
            for sig in signals:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO signals "
                        "(topic, tier, source, title, url, summary, score, metadata, found_at, expires_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            topic,
                            sig.get("tier", ""),
                            sig.get("source", ""),
                            sig.get("title", "")[:300],
                            sig.get("url", ""),
                            sig.get("summary", "")[:500],
                            sig.get("score", 0),
                            json.dumps(sig.get("metadata", {})),
                            now,
                            expires_at,
                        ),
                    )
                    inserted += conn.execute("SELECT changes()").fetchone()[0]
                except Exception:
                    pass
        return inserted

    def get_signals(self, topic: str, tier: str | None = None, limit: int = 50) -> list[dict]:
        now = time.time()
        with self._conn() as conn:
            if tier:
                rows = conn.execute(
                    "SELECT * FROM signals WHERE topic=? AND tier=? AND expires_at>? "
                    "ORDER BY found_at DESC LIMIT ?",
                    (topic, tier, now, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM signals WHERE topic=? AND expires_at>? "
                    "ORDER BY found_at DESC LIMIT ?",
                    (topic, now, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def store_topic_cards(self, cards: list[dict], topic: str, retention_days: int = 3) -> int:
        if not cards:
            return 0
        now = time.time()
        expires_at = now + retention_days * 86400
        with self._conn() as conn:
            # Clear old cards for this topic
            conn.execute("DELETE FROM topic_cards WHERE topic=?", (topic,))
            for i, card in enumerate(cards[:3]):
                conn.execute(
                    "INSERT INTO topic_cards "
                    "(topic, rank, title, summary, sources, why_trending, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        topic,
                        i + 1,
                        card.get("title", "")[:300],
                        card.get("summary", "")[:500],
                        json.dumps(card.get("sources", [])),
                        card.get("why_trending", "")[:400],
                        now,
                        expires_at,
                    ),
                )
        return len(cards[:3])

    def get_topic_cards(self, topic: str) -> list[dict]:
        now = time.time()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM topic_cards WHERE topic=? AND expires_at>? ORDER BY rank",
                (topic, now),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["sources"] = json.loads(d.get("sources", "[]"))
            except Exception:
                d["sources"] = []
            result.append(d)
        return result

    def clear_topic(self, topic: str) -> int:
        with self._conn() as conn:
            r1 = conn.execute("DELETE FROM signals WHERE topic=?", (topic,)).rowcount
            r2 = conn.execute("DELETE FROM topic_cards WHERE topic=?", (topic,)).rowcount
        return r1 + r2

    def clear_all(self) -> int:
        with self._conn() as conn:
            r1 = conn.execute("DELETE FROM signals").rowcount
            r2 = conn.execute("DELETE FROM topic_cards").rowcount
        return r1 + r2

    def stats(self, topic: str | None = None) -> dict:
        now = time.time()
        with self._conn() as conn:
            if topic:
                total = conn.execute(
                    "SELECT COUNT(*) FROM signals WHERE topic=? AND expires_at>?", (topic, now)
                ).fetchone()[0]
                by_tier = conn.execute(
                    "SELECT tier, COUNT(*) as cnt FROM signals "
                    "WHERE topic=? AND expires_at>? GROUP BY tier",
                    (topic, now),
                ).fetchall()
                cards = conn.execute(
                    "SELECT COUNT(*) FROM topic_cards WHERE topic=? AND expires_at>?", (topic, now)
                ).fetchone()[0]
            else:
                total = conn.execute(
                    "SELECT COUNT(*) FROM signals WHERE expires_at>?", (now,)
                ).fetchone()[0]
                by_tier = conn.execute(
                    "SELECT tier, COUNT(*) as cnt FROM signals "
                    "WHERE expires_at>? GROUP BY tier",
                    (now,),
                ).fetchall()
                cards = conn.execute(
                    "SELECT COUNT(*) FROM topic_cards WHERE expires_at>?", (now,)
                ).fetchone()[0]
        return {
            "total_signals": total,
            "by_tier": {r["tier"]: r["cnt"] for r in by_tier},
            "topic_cards": cards,
        }

    # ── Settings (auto-refresh toggle) ───────────────────────────────────────

    def get_setting(self, key: str, default: str = "") -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )


# ── Singleton ─────────────────────────────────────────────────────────────────

_db_instance: TrendingDB | None = None
_refresh_lock: asyncio.Lock | None = None


def get_trending_db() -> TrendingDB:
    global _db_instance
    if _db_instance is None:
        _db_instance = TrendingDB()
    return _db_instance


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_md(text: str) -> str:
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*\n]+)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[\d+\]", "", text)
    text = re.sub(r"#{1,6}\s+", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _extract_domain(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url)
    return m.group(1).replace("www.", "") if m else ""


def _fmt_age(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


# ── Tier 1: News ──────────────────────────────────────────────────────────────

async def _fetch_google_news_rss(topic_queries: list[str]) -> list[dict]:
    """Fetch headlines from Google News RSS — free, no API key."""
    signals: list[dict] = []
    query = " OR ".join(topic_queries[:3])
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en&gl=US&ceid=US:en"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()

        root = ET.fromstring(r.content)
        items = root.findall(".//item")

        for item in items[:15]:
            title_el = item.find("title")
            link_el = item.find("link")
            pub_date_el = item.find("pubDate")
            source_el = item.find("source")

            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            link = link_el.text.strip() if link_el is not None and link_el.text else ""
            source = source_el.text.strip() if source_el is not None and source_el.text else _extract_domain(link)

            if not title or not link:
                continue

            signals.append({
                "tier": "news",
                "source": f"google_news:{source}",
                "title": title,
                "url": link,
                "summary": "",
                "score": 0,
                "metadata": {"pub_date": pub_date_el.text if pub_date_el is not None else ""},
            })

    except Exception as e:
        logger.warning("Google News RSS fetch failed: %s", e)

    return signals


async def _fetch_gemini_news(topic_queries: list[str], api_key: str) -> list[dict]:
    """Gemini + Google Search grounding for breaking news."""
    query = ", ".join(topic_queries[:3])
    prompt = (
        f"What are the most significant breaking news and developments about {query} "
        f"in the last 24 hours? List the top 8 most important stories with their titles "
        f"and brief descriptions. Focus on major announcements, launches, and events."
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "tools": [{"google_search": {}}],
                },
            )
            r.raise_for_status()
            data = r.json()

        signals: list[dict] = []
        candidate = data.get("candidates", [{}])[0]

        # Extract text content
        parts = candidate.get("content", {}).get("parts", [])
        text = " ".join(p.get("text", "") for p in parts if "text" in p)

        # Extract grounding URLs
        grounding = candidate.get("groundingMetadata", {})
        chunks = grounding.get("groundingChunks", [])
        for chunk in chunks:
            web = chunk.get("web", {})
            title = _strip_md(web.get("title", "").strip())
            url = web.get("uri", "").strip()
            if title and url and url.startswith("http"):
                signals.append({
                    "tier": "news",
                    "source": f"gemini:{_extract_domain(url)}",
                    "title": title,
                    "url": url,
                    "summary": "",
                    "score": 0,
                })

        # Also parse numbered items from text
        blocks = re.split(r"\n\d+\.\s+", "\n" + text)
        for block in blocks[1:]:
            block = block.strip()
            if not block:
                continue
            title_m = re.match(r"\*\*([^*\n]+)\*\*", block)
            title = _strip_md(title_m.group(1)).rstrip(":") if title_m else _strip_md(block.split("\n")[0])[:120]
            if title and len(title) >= 10:
                signals.append({
                    "tier": "news",
                    "source": "gemini",
                    "title": title,
                    "url": "",
                    "summary": _strip_md(block)[:300],
                    "score": 0,
                })

        return signals[:10]

    except Exception as e:
        logger.warning("Gemini news fetch failed: %s", e)
        return []


async def _fetch_perplexity_news(topic_queries: list[str], api_key: str) -> list[dict]:
    """Perplexity sonar for cited web news."""
    query = ", ".join(topic_queries[:3])
    prompt = (
        f"What are the top 8 most important and trending news stories about {query} "
        f"from the last 24 hours? Include official announcements, major developments, "
        f"and breaking news. For each: title, one-line summary, and source."
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "sonar",
                    "messages": [{"role": "user", "content": prompt}],
                    "search_recency_filter": "day",
                },
            )
            r.raise_for_status()
            data = r.json()

        content = data["choices"][0]["message"]["content"]
        citations = data.get("citations", [])

        signals: list[dict] = []
        blocks = re.split(r"\n\d+\.\s+", "\n" + content.strip())
        for i, block in enumerate(blocks[1:]):
            block = block.strip()
            if not block:
                continue
            title_m = re.match(r"\*\*([^*\n]+)\*\*", block)
            title = _strip_md(title_m.group(1)).rstrip(":") if title_m else _strip_md(block.split("\n")[0])[:120]
            url = citations[i] if i < len(citations) else ""
            if title and len(title) >= 5:
                signals.append({
                    "tier": "news",
                    "source": f"perplexity:{_extract_domain(url)}" if url else "perplexity",
                    "title": title,
                    "url": url,
                    "summary": _strip_md(block)[:300],
                    "score": 0,
                })

        return signals

    except Exception as e:
        logger.warning("Perplexity news fetch failed: %s", e)
        return []


# ── Tier 2: Community Signals ─────────────────────────────────────────────────

async def _fetch_reddit_json(subreddits: list[str], topic_queries: list[str]) -> list[dict]:
    """Fetch hot posts from subreddits via .json API — free, no auth."""
    signals: list[dict] = []

    for sub in subreddits[:4]:
        try:
            url = f"https://www.reddit.com/r/{sub}/hot.json?limit=10"
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    url,
                    headers={"User-Agent": "TrendingTopicBot/1.0"},
                )
                r.raise_for_status()
                data = r.json()

            posts = data.get("data", {}).get("children", [])
            for post in posts:
                pd = post.get("data", {})
                if pd.get("stickied"):
                    continue

                title = pd.get("title", "").strip()
                post_url = f"https://reddit.com{pd.get('permalink', '')}"
                upvotes = pd.get("ups", 0)
                num_comments = pd.get("num_comments", 0)
                selftext = pd.get("selftext", "")[:200]
                flair = pd.get("link_flair_text", "")

                if not title:
                    continue

                # Fetch top comments for this post
                top_comments = []
                try:
                    comments_url = f"https://www.reddit.com{pd.get('permalink', '')}.json?limit=10&sort=top"
                    async with httpx.AsyncClient(timeout=10) as cc:
                        cr = await cc.get(
                            comments_url,
                            headers={"User-Agent": "TrendingTopicBot/1.0"},
                        )
                        if cr.status_code == 200:
                            cdata = cr.json()
                            if len(cdata) > 1:
                                comment_children = cdata[1].get("data", {}).get("children", [])
                                for c in comment_children[:5]:
                                    ctext = c.get("data", {}).get("body", "").strip()
                                    if ctext and len(ctext) > 10:
                                        top_comments.append(ctext[:200])
                except Exception:
                    pass  # comments are optional

                signals.append({
                    "tier": "community",
                    "source": f"reddit:r/{sub}",
                    "title": title,
                    "url": post_url,
                    "summary": selftext,
                    "score": upvotes + num_comments * 2,
                    "metadata": {
                        "upvotes": upvotes,
                        "comments": num_comments,
                        "flair": flair,
                        "subreddit": sub,
                        "top_comments": top_comments[:3],
                    },
                })

            await asyncio.sleep(0.5)  # rate limit politeness

        except Exception as e:
            logger.warning("Reddit r/%s fetch failed: %s", sub, e)

    # Sort by score (upvotes + comments)
    signals.sort(key=lambda s: s.get("score", 0), reverse=True)
    return signals[:15]


async def _fetch_perplexity_reddit(topic_queries: list[str], api_key: str) -> list[dict]:
    """Perplexity for summarized Reddit discussion — backup + broader coverage."""
    query = ", ".join(topic_queries[:2])
    prompt = (
        f"What are people saying on Reddit about {query} in the last 24-48 hours? "
        f"Find the most discussed posts and threads. For each: what subreddit, "
        f"what's the main discussion about, and what's the dominant sentiment or opinion."
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "sonar",
                    "messages": [{"role": "user", "content": f"site:reddit.com {prompt}"}],
                    "search_recency_filter": "week",
                },
            )
            r.raise_for_status()
            data = r.json()

        content = data["choices"][0]["message"]["content"]
        citations = data.get("citations", [])

        signals: list[dict] = []
        blocks = re.split(r"\n\d+\.\s+", "\n" + content.strip())
        for i, block in enumerate(blocks[1:]):
            block = block.strip()
            if not block:
                continue
            title_m = re.match(r"\*\*([^*\n]+)\*\*", block)
            title = _strip_md(title_m.group(1)).rstrip(":") if title_m else _strip_md(block.split("\n")[0])[:120]
            url = citations[i] if i < len(citations) else ""
            if title and len(title) >= 5:
                signals.append({
                    "tier": "community",
                    "source": "perplexity:reddit",
                    "title": title,
                    "url": url,
                    "summary": _strip_md(block)[:300],
                    "score": 0,
                })

        return signals

    except Exception as e:
        logger.warning("Perplexity Reddit fetch failed: %s", e)
        return []


async def _fetch_hackernews(hn_tags: list[str]) -> list[dict]:
    """Fetch trending stories from HackerNews Algolia API — free, no auth."""
    signals: list[dict] = []
    tags_query = " OR ".join(hn_tags[:3])

    try:
        url = (
            f"https://hn.algolia.com/api/v1/search?"
            f"query={quote_plus(tags_query)}&tags=story&hitsPerPage=10"
            f"&numericFilters=points>50"
        )
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()

        for hit in data.get("hits", []):
            title = hit.get("title", "").strip()
            hit_url = hit.get("url", "") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
            points = hit.get("points", 0)
            num_comments = hit.get("num_comments", 0)

            if not title:
                continue

            signals.append({
                "tier": "community",
                "source": f"hackernews:{_extract_domain(hit_url) if hit_url.startswith('http') else 'hn'}",
                "title": title,
                "url": hit_url,
                "summary": "",
                "score": points + num_comments,
                "metadata": {
                    "points": points,
                    "comments": num_comments,
                    "hn_id": hit.get("objectID", ""),
                },
            })

    except Exception as e:
        logger.warning("HackerNews fetch failed: %s", e)

    return signals


# ── Tier 3: Social Signals ────────────────────────────────────────────────────

async def _fetch_grok_twitter(topic_queries: list[str], api_key: str) -> list[dict]:
    """Grok API for X/Twitter trending — fetches + summarizes in one call."""
    query = ", ".join(topic_queries[:2])
    prompt = (
        f"What are the most trending and viral posts on X/Twitter about {query} "
        f"in the last 24 hours? List the top 8 most discussed tweets, threads, "
        f"or conversations. For each: who posted, what they said, and how much "
        f"engagement it got (likes, retweets, replies)."
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.x.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "grok-3",
                    "messages": [{"role": "user", "content": prompt}],
                    "search_parameters": {"mode": "auto"},
                },
            )
            r.raise_for_status()
            data = r.json()

        content = data["choices"][0]["message"]["content"]

        signals: list[dict] = []
        blocks = re.split(r"\n\d+\.\s+", "\n" + content.strip())
        for block in blocks[1:]:
            block = block.strip()
            if not block:
                continue
            title_m = re.match(r"\*\*([^*\n]+)\*\*", block)
            title = _strip_md(title_m.group(1)).rstrip(":") if title_m else _strip_md(block.split("\n")[0])[:120]
            if title and len(title) >= 5:
                signals.append({
                    "tier": "social",
                    "source": "grok:x.com",
                    "title": title,
                    "url": "",
                    "summary": _strip_md(block)[:300],
                    "score": 0,
                })

        return signals

    except Exception as e:
        logger.warning("Grok Twitter fetch failed: %s", e)
        return []


# ── Merge: Gemini → top 3 topic cards ─────────────────────────────────────────

async def _merge_top3(signals: list[dict], topic: str, api_key: str) -> list[dict]:
    """Use Gemini to merge all signals into top 3 trending topic cards."""
    if not signals:
        return []

    if not api_key:
        return _fallback_top3(signals)

    signal_text = "\n".join(
        f"- [{s['tier']}:{s['source']}] {s['title']}"
        + (f" (score:{s['score']})" if s.get('score') else "")
        + (f" — {s['summary'][:100]}" if s.get('summary') else "")
        for s in signals[:40]
    )

    prompt = f"""You are a trending topic analyst. Below are signals collected from news, Reddit, HackerNews, and X/Twitter about "{topic}".

{signal_text}

Analyze ALL signals and identify the TOP 3 most trending topics RIGHT NOW.
Look for:
- Topics appearing across MULTIPLE sources (news + reddit + twitter = very hot)
- High engagement signals (high upvotes, many comments, lots of retweets)
- Breaking/fresh developments (newer = better)
- Topics with strong community reaction or controversy

Return ONLY a valid JSON array, no other text:
[
  {{
    "title": "Clear, concise topic title",
    "summary": "2-3 sentence summary of what's happening and why it matters",
    "sources": ["source1", "source2"],
    "why_trending": "One sentence on why this is trending right now"
  }}
]"""

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
                json={"contents": [{"parts": [{"text": prompt}]}]},
            )
            r.raise_for_status()
            data = r.json()

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        m = re.search(r"\[[\s\S]*?\]", text)
        if not m:
            return _fallback_top3(signals)

        cards = json.loads(m.group())
        return [
            {
                "title": _strip_md(c.get("title", "")),
                "summary": _strip_md(c.get("summary", "")),
                "sources": c.get("sources", []),
                "why_trending": _strip_md(c.get("why_trending", "")),
            }
            for c in cards[:3]
        ]

    except Exception as e:
        logger.warning("Gemini merge failed: %s — falling back", e)
        return _fallback_top3(signals)


def _fallback_top3(signals: list[dict]) -> list[dict]:
    """Simple fallback: pick top 3 by score."""
    scored = sorted(signals, key=lambda s: s.get("score", 0), reverse=True)
    seen_titles: set[str] = set()
    result: list[dict] = []
    for s in scored:
        key = s["title"].lower()[:50]
        if key in seen_titles:
            continue
        seen_titles.add(key)
        result.append({
            "title": s["title"],
            "summary": s.get("summary", ""),
            "sources": [s.get("source", "")],
            "why_trending": f"Score: {s.get('score', 0)} from {s.get('source', '')}",
        })
        if len(result) >= 3:
            break
    return result


# ── Core refresh engine ───────────────────────────────────────────────────────

async def refresh_trending(topic: str = "AI", retention_days: int = 3, force_merge: bool = True) -> dict[str, Any]:
    """Fetch all tiers for a topic and merge into top 3 cards.

    force_merge=True (manual refresh) — always runs Gemini merge.
    force_merge=False (auto-refresh) — skips merge if auto_top3 is disabled.
    """
    global _refresh_lock
    if _refresh_lock is None:
        _refresh_lock = asyncio.Lock()

    if _refresh_lock.locked():
        return {"status": "already_running", "new_signals": 0, "cards": 0}

    async with _refresh_lock:
        return await _do_refresh(topic, retention_days, force_merge=force_merge)


async def _do_refresh(topic: str, retention_days: int = 3, force_merge: bool = True) -> dict[str, Any]:
    from config import get_config
    cfg = get_config()
    perplexity_key = getattr(cfg, "perplexity_api_key", "")
    gemini_key = getattr(cfg, "gemini_api_key", "")
    grok_key = getattr(cfg, "grok_api_key", "")

    topic_config = DEFAULT_TOPICS.get(topic, DEFAULT_TOPICS.get("AI", {}))
    queries = topic_config.get("queries", [topic])
    subreddits = topic_config.get("subreddits", [])
    hn_tags = topic_config.get("hn_tags", [topic.lower()])

    db = get_trending_db()
    db.purge_expired()

    all_signals: list[dict] = []
    total_new = 0

    # ── Tier 1: News (run in parallel) ────────────────────────────────────
    tier1_tasks = [_fetch_google_news_rss(queries)]
    if gemini_key:
        tier1_tasks.append(_fetch_gemini_news(queries, gemini_key))
    if perplexity_key:
        tier1_tasks.append(_fetch_perplexity_news(queries, perplexity_key))

    tier1_results = await asyncio.gather(*tier1_tasks, return_exceptions=True)
    for result in tier1_results:
        if isinstance(result, list):
            all_signals.extend(result)

    # ── Tier 2: Community (run in parallel) ───────────────────────────────
    tier2_tasks = [
        _fetch_reddit_json(subreddits, queries),
        _fetch_hackernews(hn_tags),
    ]
    if perplexity_key:
        tier2_tasks.append(_fetch_perplexity_reddit(queries, perplexity_key))

    tier2_results = await asyncio.gather(*tier2_tasks, return_exceptions=True)
    for result in tier2_results:
        if isinstance(result, list):
            all_signals.extend(result)

    # ── Tier 3: Social ────────────────────────────────────────────────────
    if grok_key:
        try:
            social = await _fetch_grok_twitter(queries, grok_key)
            all_signals.extend(social)
        except Exception as e:
            logger.warning("Tier 3 social failed: %s", e)

    # Store signals
    total_new = db.store_signals(all_signals, topic, retention_days)
    logger.info(
        "Trending [%s]: %d signals fetched, %d new stored",
        topic, len(all_signals), total_new,
    )

    # ── Merge → top 3 cards ───────────────────────────────────────────────
    # force_merge=True (manual refresh) always merges.
    # force_merge=False (auto-refresh) checks the auto_top3 toggle.
    should_merge = force_merge or is_auto_top3_enabled()
    cards_stored = 0
    if should_merge:
        cards = await _merge_top3(all_signals, topic, gemini_key)
        cards_stored = db.store_topic_cards(cards, topic, retention_days)

    return {
        "status": "ok",
        "topic": topic,
        "new_signals": total_new,
        "total_signals": len(all_signals),
        "cards": cards_stored,
        "merge_skipped": not should_merge,
    }


# ── Auto-refresh toggle ──────────────────────────────────────────────────────

def is_auto_refresh_enabled() -> bool:
    db = get_trending_db()
    return db.get_setting("auto_refresh_enabled", "true") == "true"


def set_auto_refresh_enabled(enabled: bool) -> None:
    db = get_trending_db()
    db.set_setting("auto_refresh_enabled", "true" if enabled else "false")


def get_active_topic() -> str:
    db = get_trending_db()
    return db.get_setting("active_topic", "AI")


def set_active_topic(topic: str) -> None:
    db = get_trending_db()
    db.set_setting("active_topic", topic)


def is_auto_top3_enabled() -> bool:
    """When disabled, auto-refresh fetches signals but skips Gemini merge.
    Manual refresh always merges regardless."""
    db = get_trending_db()
    return db.get_setting("auto_top3_enabled", "true") == "true"


def set_auto_top3_enabled(enabled: bool) -> None:
    db = get_trending_db()
    db.set_setting("auto_top3_enabled", "true" if enabled else "false")


# ── Background loops ──────────────────────────────────────────────────────────

async def trending_watcher_loop(cfg: Any) -> None:
    """Auto-refresh trending topics on schedule. Respects toggle."""
    poll_minutes = getattr(cfg, "ai_news_poll_interval_minutes", 240)
    retention_days = getattr(cfg, "ai_news_retention_days", 3)

    logger.info("Trending topic watcher started — interval=%dm", poll_minutes)

    while True:
        if is_auto_refresh_enabled():
            try:
                topic = get_active_topic()
                await _do_refresh(topic, retention_days, force_merge=False)
            except Exception as e:
                logger.warning("Trending watcher error: %s", e)
        else:
            logger.debug("Trending auto-refresh disabled — skipping")

        await asyncio.sleep(poll_minutes * 60)


async def trending_daily_digest_loop(send_fn: Any, cfg: Any) -> None:
    """Daily at 6 PM IST — always runs regardless of auto-refresh toggle."""
    logger.info("Trending daily digest loop started — fires at 18:00 IST")

    while True:
        now = datetime.now(_IST)
        next_run = now.replace(hour=18, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        wait_secs = (next_run - now).total_seconds()
        await asyncio.sleep(wait_secs)

        try:
            # Refresh all configured topics
            for topic_key in DEFAULT_TOPICS:
                await _do_refresh(topic_key)

            # Send digest for active topic
            active_topic = get_active_topic()
            db = get_trending_db()
            cards = db.get_topic_cards(active_topic)

            if cards and send_fn:
                msg = _format_digest(cards, active_topic)
                await send_fn(msg)
                logger.info("Trending daily digest sent: %d cards for %s", len(cards), active_topic)
        except Exception as e:
            logger.warning("Trending daily digest failed: %s", e)


def _format_digest(cards: list[dict], topic: str) -> str:
    lines = [f"🔥 *Trending in {topic} — Daily Digest*\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, card in enumerate(cards[:3]):
        sources_str = ", ".join(card.get("sources", [])[:3])
        lines.append(
            f"{medals[i]} *{card['title']}*\n"
            f"_{card['summary']}_\n"
            f"📡 {sources_str}\n"
            f"📈 {card.get('why_trending', '')}\n"
        )
    return "\n".join(lines).strip()


# ── Tool definitions (for agent use) ──────────────────────────────────────────

async def _trending_list(topic: str = "AI", **_: Any) -> str:
    db = get_trending_db()
    cards = db.get_topic_cards(topic)
    if not cards:
        return f"No trending topics for '{topic}'. Press refresh or wait for next cycle."
    stats = db.stats(topic)
    lines = [f"🔥 *Trending in {topic}* — {stats['total_signals']} signals\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, card in enumerate(cards[:3]):
        sources_str = ", ".join(card.get("sources", [])[:3])
        lines.append(
            f"{medals[i]} *{card['title']}*\n"
            f"_{card['summary']}_\n"
            f"📡 {sources_str}\n"
            f"📈 {card.get('why_trending', '')}\n"
        )
    return "\n".join(lines).strip()


async def _trending_refresh(topic: str = "AI", **_: Any) -> str:
    result = await refresh_trending(topic)
    if result.get("status") == "already_running":
        return "⏳ Refresh already in progress."
    return (
        f"✅ Trending [{result['topic']}]: "
        f"{result['total_signals']} signals → {result['cards']} topic cards"
    )


async def _trending_clear(topic: str | None = None, **_: Any) -> str:
    db = get_trending_db()
    if topic:
        count = db.clear_topic(topic)
        return f"🗑 Cleared {count} items for topic '{topic}'."
    count = db.clear_all()
    return f"🗑 Cleared {count} total trending items."


TRENDING_LIST_TOOL = ToolDefinition(
    name="trending_topics",
    description=(
        "List top 3 trending topic cards for a given topic. "
        "Topics: AI, Finance, Finance+AI, Marketing. "
        "Multi-source: Google News, Reddit, HackerNews, X/Twitter."
    ),
    parameters={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "enum": list(DEFAULT_TOPICS.keys()),
                "description": "Topic to show trending cards for (default: AI)",
                "default": "AI",
            },
        },
        "required": [],
    },
    fn=_trending_list,
)

TRENDING_REFRESH_TOOL = ToolDefinition(
    name="trending_refresh",
    description="Trigger an immediate trending topic refresh for a given topic.",
    parameters={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "enum": list(DEFAULT_TOPICS.keys()),
                "description": "Topic to refresh (default: AI)",
                "default": "AI",
            },
        },
        "required": [],
    },
    fn=_trending_refresh,
    owner_only=True,
)

TRENDING_CLEAR_TOOL = ToolDefinition(
    name="trending_clear",
    description="Clear all cached trending signals and topic cards.",
    parameters={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Topic to clear (omit to clear all)",
            },
        },
        "required": [],
    },
    fn=_trending_clear,
    owner_only=True,
)
