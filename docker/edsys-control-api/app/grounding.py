"""Read-only search over the shared current EdSys grounding index."""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,}")


class GroundingIndex:
    """Query the existing immutable-at-read-time SQLite/FTS index."""

    def __init__(self, path: Path, freshness_seconds: int) -> None:
        self.path = path
        self.freshness_seconds = freshness_seconds

    def status(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "available": False,
                "fresh": False,
                "reason": "grounding index is unavailable",
            }
        try:
            with self._connect() as connection:
                metadata = dict(
                    connection.execute("select key, value from metadata").fetchall()
                )
        except sqlite3.Error:
            return {
                "available": False,
                "fresh": False,
                "reason": "grounding index is unreadable",
            }
        source_count = int(metadata.get("source_count", "0"))
        chunk_count = int(metadata.get("chunk_count", "0"))
        age_seconds = _age_seconds(metadata.get("built_at", ""))
        fresh = age_seconds is not None and age_seconds <= self.freshness_seconds
        return {
            "available": source_count > 0 and chunk_count > 0,
            "fresh": fresh,
            "reason": "ok" if fresh else "grounding index is stale",
            "built_at": metadata.get("built_at"),
            "age_seconds": age_seconds,
            "freshness_seconds": self.freshness_seconds,
            "source_count": source_count,
            "chunk_count": chunk_count,
            "manifest_sha256": metadata.get("manifest_sha256"),
        }

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(limit, 12))
        fts_query = _fts_query(query)
        if not fts_query:
            return []
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            try:
                rows = list(
                    connection.execute(
                        """
                        select s.rel_path, s.title, s.sha256, f.chunk_index, f.text,
                               bm25(chunks_fts) as score
                          from chunks_fts f
                          join sources s on s.id = f.source_id
                         where chunks_fts match ?
                         order by score
                         limit ?
                        """,
                        (fts_query, bounded_limit),
                    )
                )
            except sqlite3.Error:
                return []
        return [
            {
                "rel_path": str(row["rel_path"]),
                "title": str(row["title"]),
                "sha256": str(row["sha256"]),
                "chunk_index": int(row["chunk_index"]),
                "score": float(row["score"]) if row["score"] is not None else None,
                "excerpt": _excerpt(str(row["text"]), query),
            }
            for row in rows
        ]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)


def _tokens(query: str) -> list[str]:
    stop = {
        "a",
        "an",
        "the",
        "and",
        "for",
        "with",
        "what",
        "where",
        "which",
        "current",
        "does",
        "from",
        "about",
        "please",
        "how",
        "are",
        "is",
        "our",
        "my",
    }
    result: list[str] = []
    seen: set[str] = set()
    for match in TOKEN_RE.finditer(query):
        token = match.group(0).strip("._-")
        lowered = token.lower()
        if len(token) < 2 or lowered in stop or lowered in seen:
            continue
        seen.add(lowered)
        result.append(token)
    return result


def _fts_query(query: str) -> str:
    quoted: list[str] = []
    for token in _tokens(query)[:12]:
        safe = re.sub(r"[^A-Za-z0-9_]", " ", token).strip()
        quoted.extend(f'"{part}"' for part in safe.split() if len(part) >= 2)
    return " OR ".join(quoted)


def _excerpt(text: str, query: str, max_chars: int = 1200) -> str:
    compact = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(compact) <= max_chars:
        return compact
    lowered = compact.casefold()
    positions = [
        lowered.find(token.casefold()) for token in _tokens(query) if len(token) >= 3
    ]
    positions = [position for position in positions if position >= 0]
    start = max(0, min(positions or [0]) - max_chars // 3)
    end = min(len(compact), start + max_chars)
    excerpt = compact[start:end].strip()
    return ("…" if start else "") + excerpt + ("…" if end < len(compact) else "")


def _age_seconds(value: str) -> int | None:
    if not value:
        return None
    try:
        built = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int(time.time() - built.timestamp()))
