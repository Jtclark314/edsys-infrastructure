"""Bounded, local, read-only search of an EdSys-only grounding snapshot."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
import time

MAX_AGE_SECONDS = 1800
MAX_QUERY_CHARS = 512
MAX_EXCERPT_CHARS = 1200
MAX_RESULTS = 4
EXCLUDED = ("foothills", "procore", "excel", "homeowner", "finance", "speakr", "kindle", "basecamp operator")
STOP = frozenset("a an the and for with what where which current role does from about describe please in no more than words how do is are it its give state our my system used tell me of on as to can has have explain".split())
SCOPE = re.compile(r"\b(?:edsys|9950x|nimo|nemo|edcore|workhorse|pve[ -]?node\d|node[ -]?\d|pi5|pi[ -]?5|pi[ -]?hole|plex|frigate|litellm|ollama|anythingllm|qdrant|netbox|edrouter|tailscale|codex|home assistant)\b", re.I)


def admitted_source(source: dict) -> bool:
    label = (source["rel_path"] + " " + source["title"]).casefold()
    return source["source_group"] in ("EdSys-Master", "EdSysVault_RAG") and not any(word in label for word in EXCLUDED)


def tokens(query: str) -> list[str]:
    query = re.sub(r"\bnode\s*(\d)\b", r"pve-node\1", query, flags=re.I)
    query = re.sub(r"\bnemo\b", "nimo", query, flags=re.I)
    result = []
    for word in re.findall(r"[A-Za-z0-9]{2,}", query):
        word = word.casefold()
        if word not in STOP and word not in result:
            result.append(word)
    # Scope words alone must not make an unknown host look supported.
    subjects = [word for word in result if word not in ("edsys", "pve")]
    return (subjects or result)[:16]


def excerpt(text: str, query_tokens: list[str]) -> str:
    if len(text) <= MAX_EXCERPT_CHARS:
        return text
    lowered = text.casefold()
    starts = {0}
    for token in query_tokens:
        position = lowered.find(token)
        while position >= 0:
            starts.add(max(0, min(len(text) - MAX_EXCERPT_CHARS, position - MAX_EXCERPT_CHARS // 3)))
            position = lowered.find(token, position + len(token))
    start = max(starts, key=lambda i: sum(t in lowered[i:i + MAX_EXCERPT_CHARS] for t in query_tokens))
    return ("…" if start else "") + text[start:start + MAX_EXCERPT_CHARS] + "…"


def search(path: Path, query: str, *, now: float | None = None) -> dict:
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= MAX_QUERY_CHARS:
        return {"status": "invalid_query", "sources": []}
    if any(term in query.casefold() for term in EXCLUDED):
        return {"status": "out_of_scope", "sources": []}
    if not SCOPE.search(query):
        return {"status": "not_an_edsys_query", "sources": []}
    terms = tokens(query)
    if not terms:
        return {"status": "no_matching_sources", "sources": []}
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            return {"status": "unavailable", "sources": []}
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1) as connection:
            connection.execute("pragma query_only=on")
            metadata = dict(connection.execute("select key,value from metadata"))
            if metadata.get("schema_version") != "2" or metadata.get("voice_scope") != "edsys-only":
                return {"status": "invalid_catalog", "sources": []}
            built = datetime.fromisoformat(metadata["built_at"].replace("Z", "+00:00"))
            if built.tzinfo is None:
                return {"status": "invalid_catalog", "sources": []}
            age = (time.time() if now is None else now) - built.timestamp()
            if age < -60 or age > MAX_AGE_SECONDS:
                return {"status": "stale", "sources": []}
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "select s.title,s.rel_path,s.source_group,s.audited_at,s.sha256,f.text "
                "from chunks_fts f join sources s on s.id=f.source_id "
                "where chunks_fts match ? "
                "order by bm25(chunks_fts,0,0,4,0,1)*(1+0.5*s.current_priority) limit 32",
                (" OR ".join('"' + term + '"' for term in terms),),
            ).fetchall()
        sources, per_source = [], {}
        for row in rows:
            source = dict(row)
            if not admitted_source(source) or per_source.get(row["rel_path"], 0) >= 2:
                continue
            per_source[row["rel_path"]] = per_source.get(row["rel_path"], 0) + 1
            sources.append({"title": row["title"], "document": row["rel_path"],
                            "audited_at": row["audited_at"] or "undated",
                            "sha256": row["sha256"], "excerpt": excerpt(row["text"], terms)})
            if len(sources) >= MAX_RESULTS:
                break
        return {"status": "ok" if sources else "no_matching_sources", "built_at": metadata["built_at"],
                "evidence_type": "reviewed_documentation_not_live_telemetry", "sources": sources}
    except (OSError, sqlite3.Error, KeyError, ValueError):
        return {"status": "unavailable", "sources": []}
