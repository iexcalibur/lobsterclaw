"""
Memory index — persistent hybrid (FTS + optional vector) search over workspace memory files.

Mirrors OpenClaw's MemoryIndexManager (manager.ts) with:
  - Persistent SQLite index (mtime-based incremental updates, not rebuild-on-search)
  - BM25 FTS5 full-text search with query expansion (keyword extraction)
  - Optional vector embeddings with Reciprocal Rank Fusion (RRF) merge
  - Embedding providers: OpenAI text-embedding-3-small, Google Gemini text-embedding-004

Modes (MEMORY_EMBEDDING_PROVIDER env):
  "openai"  — OpenAI text-embedding-3-small  (requires OPENAI_API_KEY)
  "gemini"  — Google Gemini text-embedding-004 (requires GEMINI_API_KEY)
  "none"    — FTS only (default, no API key needed)
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import sqlite3
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Chunk size for splitting large files before embedding
CHUNK_SIZE = 500          # characters per chunk
EMBED_DIMS_OPENAI = 1536  # text-embedding-3-small dimension
EMBED_DIMS_GEMINI = 768   # text-embedding-004 dimension
_IN_MEMORY_DB = ":memory:"


@dataclass
class SearchResult:
    key: str
    snippet: str
    score: float
    source: str = "fts"  # "fts" | "semantic" | "hybrid"


# ---------------------------------------------------------------
# Persistent index database schema
# ---------------------------------------------------------------
_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS file_meta (
    path       TEXT PRIMARY KEY,
    mtime      REAL NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chunks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    path       TEXT NOT NULL,
    key        TEXT NOT NULL,
    chunk_idx  INTEGER NOT NULL,
    content    TEXT NOT NULL,
    embedding  BLOB,           -- packed float32 array (NULL if no embeddings)
    FOREIGN KEY (path) REFERENCES file_meta(path)
);

CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
CREATE INDEX IF NOT EXISTS idx_chunks_key  ON chunks(key);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    key UNINDEXED,
    content,
    content_rowid=id,
    tokenize='porter ascii'
);
"""


class MemoryIndex:
    """
    Persistent hybrid memory index.

    - Keeps a SQLite file at `index_db_path`; rebuilds only changed files.
    - FTS via SQLite FTS5 + BM25 ranking with query expansion.
    - Optional vector search (OpenAI or Gemini embeddings) with RRF merge.
    """

    def __init__(
        self,
        memory_dir: Path,
        memory_md: Path,
        *,
        index_db_path: Path | None = None,
        embedding_provider: str = "none",
        openai_api_key: str | None = None,
        gemini_api_key: str | None = None,
        persist: bool = True,
    ) -> None:
        self._memory_dir = memory_dir
        self._memory_md = memory_md
        self._embedding_provider = embedding_provider.lower()
        self._openai_api_key = openai_api_key
        self._gemini_api_key = gemini_api_key

        # Use persistent file or fallback to in-memory
        if persist and index_db_path is not None:
            index_db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db_path = str(index_db_path)
        else:
            self._db_path = _IN_MEMORY_DB

        self._init_db()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        limit: int = 5,
        semantic: bool = False,
        api_key: str | None = None,
    ) -> list[SearchResult]:
        """
        Search memory.
        - Always runs incremental index refresh first (only stale files are re-indexed).
        - If semantic=True (or embedding_provider != "none"), runs hybrid search.
        - Falls back to FTS-only on any embedding error.
        """
        self._refresh_index()

        fts_results = self._fts_search(query, limit * 2)

        use_semantic = semantic or self._embedding_provider not in ("none", "")
        effective_key = api_key or self._openai_api_key or self._gemini_api_key
        if use_semantic and effective_key:
            try:
                vec_results = self._vector_search(query, limit * 2, effective_key)
                if vec_results:
                    return self._rrf_merge(fts_results, vec_results, limit)
            except Exception as e:
                logger.warning("Semantic search failed, falling back to FTS: %s", e)

        return fts_results[:limit]

    def invalidate(self) -> None:
        """Clear all indexed chunks (force full re-index on next search)."""
        conn = self._connect()
        conn.executescript("""
            DELETE FROM chunks;
            DELETE FROM chunks_fts;
            DELETE FROM file_meta;
        """)
        conn.commit()
        conn.close()
        logger.debug("MemoryIndex: cleared all chunks")

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Incremental index refresh (mtime-based)
    # ------------------------------------------------------------------

    def _collect_source_files(self) -> list[tuple[str, Path]]:
        """Return [(key, path)] for all indexable memory files."""
        files: list[tuple[str, Path]] = []
        if self._memory_md.exists():
            files.append(("MEMORY", self._memory_md))
        if self._memory_dir.exists():
            for md in sorted(self._memory_dir.rglob("*.md")):
                if md.name != "README.md":
                    rel_key = str(md.relative_to(self._memory_dir).with_suffix("").as_posix())
                    files.append((rel_key, md))
        return files

    def _refresh_index(self) -> None:
        """Add/update index for changed files; remove entries for deleted files."""
        source_files = self._collect_source_files()
        source_paths = {str(p) for _, p in source_files}

        conn = self._connect()
        try:
            # Remove stale entries for deleted files
            indexed_paths = {row["path"] for row in conn.execute("SELECT path FROM file_meta").fetchall()}
            for stale in indexed_paths - source_paths:
                self._remove_file(conn, stale)

            # Index new/changed files
            for key, path in source_files:
                path_str = str(path)
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                row = conn.execute(
                    "SELECT mtime FROM file_meta WHERE path=?", (path_str,)
                ).fetchone()
                if row and abs(float(row["mtime"]) - mtime) < 0.001:
                    continue  # up-to-date
                self._index_file(conn, key, path, mtime)

            conn.commit()
        finally:
            conn.close()

    def _remove_file(self, conn: sqlite3.Connection, path: str) -> None:
        # Remove FTS entries for chunks belonging to this file
        rows = conn.execute("SELECT id FROM chunks WHERE path=?", (path,)).fetchall()
        for row in rows:
            conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (row["id"],))
        conn.execute("DELETE FROM chunks WHERE path=?", (path,))
        conn.execute("DELETE FROM file_meta WHERE path=?", (path,))

    def _index_file(
        self, conn: sqlite3.Connection, key: str, path: Path, mtime: float
    ) -> None:
        path_str = str(path)
        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError as e:
            logger.warning("MemoryIndex: cannot read %s: %s", path_str, e)
            return

        # Remove old entries
        self._remove_file(conn, path_str)

        text_chunks = _split_chunks(content, CHUNK_SIZE)
        embeddings: list[bytes | None] = [None] * len(text_chunks)

        # Compute embeddings if provider is configured
        if self._embedding_provider not in ("none", "") and text_chunks:
            try:
                emb_list = self._embed_texts(text_chunks)
                embeddings = [_pack_floats(e) for e in emb_list]
            except Exception as e:
                logger.warning("MemoryIndex: embedding failed for %s: %s", path.name, e)

        # Insert chunks
        for idx, (text, emb) in enumerate(zip(text_chunks, embeddings)):
            conn.execute(
                "INSERT INTO chunks (path, key, chunk_idx, content, embedding) VALUES (?,?,?,?,?)",
                (path_str, key, idx, text, emb),
            )
            rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("INSERT INTO chunks_fts(rowid, key, content) VALUES (?,?,?)",
                         (rowid, key, text))

        conn.execute(
            "INSERT OR REPLACE INTO file_meta (path, mtime, chunk_count) VALUES (?,?,?)",
            (path_str, mtime, len(text_chunks)),
        )
        logger.debug("MemoryIndex: indexed %s (%d chunks)", path.name, len(text_chunks))

    # ------------------------------------------------------------------
    # FTS search with query expansion
    # ------------------------------------------------------------------

    def _fts_search(self, query: str, limit: int) -> list[SearchResult]:
        # Expand query: add keywords extracted from the original query
        expanded = _expand_query(query)
        safe_query = _sanitize_fts(expanded)

        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT chunks.key, chunks.content, chunks_fts.rank
                FROM chunks_fts
                JOIN chunks ON chunks.id = chunks_fts.rowid
                WHERE chunks_fts MATCH ?
                ORDER BY chunks_fts.rank
                LIMIT ?
                """,
                (safe_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # Fallback to substring match over raw chunks
            ql = query.lower()
            rows_raw = conn.execute("SELECT key, content FROM chunks").fetchall()
            rows = [(r["key"], r["content"], 0.0) for r in rows_raw if ql in r["content"].lower()][:limit]
        finally:
            conn.close()

        if not rows:
            return []

        raw_ranks = [float(r[2] if not isinstance(r, sqlite3.Row) else r["rank"]) for r in rows]
        min_r, max_r = min(raw_ranks), max(raw_ranks)
        span = (max_r - min_r) or 1.0

        results: list[SearchResult] = []
        for row in rows:
            key, content, rank = (row[0], row[1], row[2]) if not isinstance(row, sqlite3.Row) else (row["key"], row["content"], row["rank"])
            norm = 1.0 - 0.9 * ((float(rank) - min_r) / span)
            snippet = content[:600] + ("…" if len(content) > 600 else "")
            results.append(SearchResult(key=key, snippet=snippet, score=round(norm, 3), source="fts"))

        # Deduplicate by key (keep highest score)
        return _deduplicate(results, limit)

    # ------------------------------------------------------------------
    # Vector search
    # ------------------------------------------------------------------

    def _vector_search(self, query: str, limit: int, api_key: str) -> list[SearchResult]:
        query_emb = self._embed_texts([query])[0]
        q_norm = math.sqrt(sum(x * x for x in query_emb)) or 1.0

        conn = self._connect()
        rows = conn.execute(
            "SELECT id, key, content, embedding FROM chunks WHERE embedding IS NOT NULL"
        ).fetchall()
        conn.close()

        if not rows:
            return []

        scored: list[tuple[float, str, str]] = []
        for row in rows:
            try:
                emb = _unpack_floats(bytes(row["embedding"]))
            except Exception:
                continue
            dot = sum(a * b for a, b in zip(query_emb, emb))
            e_norm = math.sqrt(sum(x * x for x in emb)) or 1.0
            score = dot / (q_norm * e_norm)
            scored.append((score, row["key"], row["content"]))

        # Best chunk per key
        best: dict[str, tuple[float, str]] = {}
        for score, key, text in scored:
            if key not in best or score > best[key][0]:
                best[key] = (score, text)

        top = sorted(best.items(), key=lambda x: x[1][0], reverse=True)[:limit]
        return [
            SearchResult(
                key=k,
                snippet=v[1][:600] + ("…" if len(v[1]) > 600 else ""),
                score=v[0],
                source="semantic",
            )
            for k, v in top
        ]

    # ------------------------------------------------------------------
    # Embedding dispatch
    # ------------------------------------------------------------------

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        if self._embedding_provider == "openai":
            return _embed_openai(texts, self._openai_api_key or "")
        if self._embedding_provider == "gemini":
            return _embed_gemini(texts, self._gemini_api_key or "")
        raise ValueError(f"Unknown embedding provider: {self._embedding_provider}")

    # ------------------------------------------------------------------
    # RRF merge
    # ------------------------------------------------------------------

    def _rrf_merge(
        self,
        fts: list[SearchResult],
        vec: list[SearchResult],
        limit: int,
        k: int = 60,
    ) -> list[SearchResult]:
        scores: dict[str, float] = {}
        snippets: dict[str, str] = {}

        for rank, r in enumerate(fts):
            scores[r.key] = scores.get(r.key, 0) + 1 / (k + rank + 1)
            snippets[r.key] = r.snippet

        for rank, r in enumerate(vec):
            scores[r.key] = scores.get(r.key, 0) + 1 / (k + rank + 1)
            if r.key not in snippets:
                snippets[r.key] = r.snippet

        merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [
            SearchResult(key=k, snippet=snippets[k], score=v, source="hybrid")
            for k, v in merged
        ]


# ---------------------------------------------------------------
# Embedding providers
# ---------------------------------------------------------------

def _embed_openai(texts: list[str], api_key: str) -> list[list[float]]:
    import httpx
    results: list[list[float]] = []
    batch_size = 100
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        resp = httpx.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "text-embedding-3-small", "input": batch},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        results.extend(item["embedding"] for item in data["data"])
    return results


def _embed_gemini(texts: list[str], api_key: str) -> list[list[float]]:
    """Batch embed via Gemini text-embedding-004 REST API."""
    import httpx

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"text-embedding-004:batchEmbedContents?key={api_key}"
    )
    results: list[list[float]] = []
    batch_size = 100
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        body = {
            "requests": [
                {"model": "models/text-embedding-004", "content": {"parts": [{"text": t}]}}
                for t in batch
            ]
        }
        resp = httpx.post(url, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for emb_obj in data.get("embeddings", []):
            results.append(emb_obj["values"])
    return results


# ---------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------

def _split_chunks(content: str, chunk_size: int) -> list[str]:
    """Split text into chunks at paragraph boundaries."""
    if len(content) <= chunk_size:
        return [content]
    paras = [p.strip() for p in content.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paras:
        if len(buf) + len(para) > chunk_size and buf:
            chunks.append(buf)
            buf = para
        else:
            buf = (buf + "\n\n" + para).strip() if buf else para
    if buf:
        chunks.append(buf)
    return chunks or [content[:chunk_size]]


def _expand_query(query: str) -> str:
    """
    Query expansion: extract keywords and add them alongside the original query.
    Mirrors OpenClaw's extractKeywords used before FTS.
    """
    # Remove punctuation and common stop words
    _STOPWORDS = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "shall",
        "should", "may", "might", "must", "can", "could", "to", "of", "in",
        "on", "at", "by", "for", "with", "about", "as", "into", "through",
        "and", "or", "but", "not", "no", "nor", "so", "yet", "both", "either",
        "neither", "each", "all", "any", "some", "such", "than", "too", "very",
        "just", "what", "which", "who", "whom", "this", "that", "these", "those",
        "i", "me", "my", "myself", "we", "our", "you", "your", "he", "she",
        "they", "it", "its", "his", "her", "their", "how", "when", "where", "why",
    }
    tokens = re.findall(r"[a-zA-Z0-9'_-]{2,}", query.lower())
    keywords = [t for t in tokens if t not in _STOPWORDS and len(t) > 2]
    if not keywords:
        return query
    # Combine original query words + keyword set (deduplicated, ≤ 8 terms)
    combined = list(dict.fromkeys(tokens[:4] + keywords[:4]))[:8]
    return " ".join(combined)


def _sanitize_fts(query: str) -> str:
    cleaned = re.sub(r'[^a-zA-Z0-9\s\-_\']', ' ', query).strip()
    return cleaned or query[:50]


def _deduplicate(results: list[SearchResult], limit: int) -> list[SearchResult]:
    """Keep best score per key."""
    best: dict[str, SearchResult] = {}
    for r in results:
        if r.key not in best or r.score > best[r.key].score:
            best[r.key] = r
    return sorted(best.values(), key=lambda x: x.score, reverse=True)[:limit]


def _pack_floats(values: list[float]) -> bytes:
    return struct.pack(f"{len(values)}f", *values)


def _unpack_floats(data: bytes) -> list[float]:
    n = len(data) // 4
    return list(struct.unpack(f"{n}f", data))
