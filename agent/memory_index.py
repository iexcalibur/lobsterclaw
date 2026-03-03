"""
Memory index — optional semantic (vector) search over workspace memory files.

Mirrors OpenClaw's MemoryIndexManager (manager.ts) with hybrid BM25+vector search.

Modes:
  1. Semantic (MEMORY_SEMANTIC=true + OPENAI_API_KEY set):
     Uses OpenAI text-embedding-3-small to embed all memory chunks.
     Hybrid search: cosine similarity + FTS BM25, merged via Reciprocal Rank Fusion (RRF).
  2. FTS only (default):
     SQLite FTS5 with Porter stemmer. Always available, no API key needed.

The index rebuilds from markdown files on each search (stateless, always fresh).
For large memory collections a persistent index would be faster, but for personal
use (tens of files) rebuild-on-search is negligible.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

# Chunk size for splitting large files before embedding
CHUNK_SIZE = 500  # characters per chunk


@dataclass
class SearchResult:
    key: str
    snippet: str
    score: float
    source: str = "fts"  # "fts" | "semantic" | "hybrid"


class MemoryIndex:
    """
    Hybrid memory index — FTS + optional vector search.
    Rebuilt from disk files on every search call.
    """

    def __init__(self, memory_dir: Path, memory_md: Path) -> None:
        self._memory_dir = memory_dir
        self._memory_md = memory_md

    def search(
        self,
        query: str,
        limit: int = 5,
        semantic: bool = False,
        api_key: str | None = None,
    ) -> list[SearchResult]:
        """
        Search memory.
        If semantic=True and api_key is available, use hybrid search.
        Otherwise fall back to FTS only.
        """
        chunks = self._load_chunks()
        if not chunks:
            return []

        fts_results = self._fts_search(chunks, query, limit * 2)

        if semantic and api_key:
            try:
                vec_results = self._vector_search(chunks, query, limit * 2, api_key)
                return self._rrf_merge(fts_results, vec_results, limit)
            except Exception as e:
                logger.warning("Semantic search failed, falling back to FTS: %s", e)

        return fts_results[:limit]

    # ------------------------------------------------------------------
    # Load memory chunks
    # ------------------------------------------------------------------

    def _load_chunks(self) -> list[tuple[str, str, str]]:
        """Returns list of (key, content, source_path)."""
        chunks: list[tuple[str, str, str]] = []

        if self._memory_md.exists():
            content = self._memory_md.read_text(encoding="utf-8").strip()
            if content:
                chunks.append(("MEMORY", content, str(self._memory_md)))

        if self._memory_dir.exists():
            for md_file in sorted(self._memory_dir.glob("*.md")):
                if md_file.name == "README.md":
                    continue
                content = md_file.read_text(encoding="utf-8").strip()
                if content:
                    chunks.append((md_file.stem, content, str(md_file)))

        return chunks

    # ------------------------------------------------------------------
    # FTS search (SQLite FTS5)
    # ------------------------------------------------------------------

    def _fts_search(
        self,
        chunks: list[tuple[str, str, str]],
        query: str,
        limit: int,
    ) -> list[SearchResult]:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE VIRTUAL TABLE m USING fts5(key UNINDEXED, content, tokenize='porter ascii')"
        )
        conn.executemany(
            "INSERT INTO m(key, content) VALUES (?,?)",
            [(k, c) for k, c, _ in chunks],
        )
        conn.commit()

        safe_query = _sanitize_fts(query)
        try:
            rows = conn.execute(
                "SELECT key, content FROM m WHERE m MATCH ? ORDER BY rank LIMIT ?",
                (safe_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # Fallback: substring match
            ql = query.lower()
            rows = [(k, c) for k, c, _ in chunks if ql in c.lower()][:limit]

        conn.close()

        results = []
        for key, content in rows:
            snippet = content[:600] + ("…" if len(content) > 600 else "")
            results.append(SearchResult(key=key, snippet=snippet, score=1.0, source="fts"))
        return results

    # ------------------------------------------------------------------
    # Vector search (OpenAI text-embedding-3-small)
    # ------------------------------------------------------------------

    def _vector_search(
        self,
        chunks: list[tuple[str, str, str]],
        query: str,
        limit: int,
        api_key: str,
    ) -> list[SearchResult]:
        import httpx
        import json
        import math

        def _embed(texts: list[str]) -> list[list[float]]:
            response = httpx.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "text-embedding-3-small", "input": texts},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            return [item["embedding"] for item in data["data"]]

        def _cosine(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            norm_a = math.sqrt(sum(x * x for x in a))
            norm_b = math.sqrt(sum(x * x for x in b))
            return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0

        # Split large chunks before embedding
        all_chunk_texts: list[tuple[str, str]] = []  # (key, text_chunk)
        for key, content, _ in chunks:
            if len(content) <= CHUNK_SIZE:
                all_chunk_texts.append((key, content))
            else:
                # Split at paragraph boundaries
                paras = [p.strip() for p in content.split("\n\n") if p.strip()]
                buf = ""
                for para in paras:
                    if len(buf) + len(para) > CHUNK_SIZE and buf:
                        all_chunk_texts.append((key, buf))
                        buf = para
                    else:
                        buf = (buf + "\n\n" + para).strip()
                if buf:
                    all_chunk_texts.append((key, buf))

        texts = [t for _, t in all_chunk_texts]
        # Embed all chunks + query in one call (max 100 inputs per call)
        batch_size = 100
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            all_embeddings.extend(_embed(batch))

        query_embedding = _embed([query])[0]

        # Score chunks
        scored: list[tuple[float, str, str]] = []
        for (key, text), embedding in zip(all_chunk_texts, all_embeddings):
            score = _cosine(query_embedding, embedding)
            scored.append((score, key, text))

        # Best chunk per key
        best_per_key: dict[str, tuple[float, str]] = {}
        for score, key, text in scored:
            if key not in best_per_key or score > best_per_key[key][0]:
                best_per_key[key] = (score, text)

        top = sorted(best_per_key.items(), key=lambda x: x[1][0], reverse=True)[:limit]
        results = []
        for key, (score, text) in top:
            snippet = text[:600] + ("…" if len(text) > 600 else "")
            results.append(SearchResult(key=key, snippet=snippet, score=score, source="semantic"))
        return results

    # ------------------------------------------------------------------
    # RRF merge (Reciprocal Rank Fusion)
    # ------------------------------------------------------------------

    def _rrf_merge(
        self,
        fts: list[SearchResult],
        vec: list[SearchResult],
        limit: int,
        k: int = 60,
    ) -> list[SearchResult]:
        """Merge FTS and vector result lists using Reciprocal Rank Fusion."""
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


def _sanitize_fts(query: str) -> str:
    return re.sub(r'[^a-zA-Z0-9\s\-_\']', ' ', query).strip() or query[:50]
