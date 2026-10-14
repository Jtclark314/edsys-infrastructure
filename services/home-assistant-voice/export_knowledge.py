#!/usr/bin/env python3
"""Export only EdSys documentation from the existing current grounding index."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3
import time
from datetime import datetime

SOURCE = Path("/mnt/ai-store/rag/grounding/edsys-grounding.sqlite")
ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("voice_knowledge", ROOT / "custom_components/edsys_voice_knowledge/knowledge.py")
knowledge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(knowledge)

SCHEMA = """
create table metadata(key text primary key,value text not null);
create table sources(id integer primary key,path text not null,rel_path text not null,
 title text not null,source_group text not null,mtime_epoch integer not null,size_bytes integer not null,
 sha256 text not null,current_priority integer not null,audited_at text not null);
create table chunks(id integer primary key,source_id integer not null,chunk_index integer not null,text text not null);
create virtual table chunks_fts using fts5(source_id unindexed,chunk_index unindexed,title,path unindexed,text,
 tokenize='unicode61 remove_diacritics 2');
"""


def export(destination: Path, source: Path = SOURCE) -> dict:
    if source.is_symlink() or not source.is_file():
        raise RuntimeError("Canonical grounding index unavailable")
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as origin:
        origin.row_factory = sqlite3.Row
        metadata = dict(origin.execute("select key,value from metadata"))
        if metadata.get("schema_version") != "2":
            raise RuntimeError("Unsupported canonical grounding schema")
        built = datetime.fromisoformat(metadata["built_at"].replace("Z", "+00:00"))
        if built.tzinfo is None or not -60 <= time.time() - built.timestamp() <= knowledge.MAX_AGE_SECONDS:
            raise RuntimeError("Canonical grounding index is stale")
        sources = [dict(row) for row in origin.execute("select * from sources") if knowledge.admitted_source(dict(row))]
        if not sources or len(sources) > 500:
            raise RuntimeError("Unexpected knowledge source count")
        selected = {row["id"] for row in sources}
        chunks = [dict(row) for row in origin.execute("select * from chunks") if row["source_id"] in selected]
    if not chunks:
        raise RuntimeError("No admitted current knowledge chunks")
    if destination.exists():
        raise RuntimeError("Export target already exists; use private temporary storage")
    with sqlite3.connect(destination) as target:
        target.executescript(SCHEMA)
        safe_metadata = {key: metadata[key] for key in ("schema_version", "built_at", "manifest_sha256") if key in metadata}
        safe_metadata.update(voice_scope="edsys-only", source_count=str(len(sources)), chunk_count=str(len(chunks)))
        target.executemany("insert into metadata values(?,?)", safe_metadata.items())
        columns = ("id", "path", "rel_path", "title", "source_group", "mtime_epoch", "size_bytes", "sha256", "current_priority", "audited_at")
        target.executemany("insert into sources values(?,?,?,?,?,?,?,?,?,?)", [tuple(s[key] for key in columns) for s in sources])
        target.executemany("insert into chunks values(?,?,?,?)", [(c["id"], c["source_id"], c["chunk_index"], c["text"]) for c in chunks])
        source_map = {s["id"]: s for s in sources}
        target.executemany("insert into chunks_fts values(?,?,?,?,?)", [(c["source_id"], c["chunk_index"], source_map[c["source_id"]]["title"], source_map[c["source_id"]]["path"], c["text"]) for c in chunks])
        if target.execute("pragma quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Export integrity check failed")
    destination.chmod(0o600)
    return {"sources": len(sources), "chunks": len(chunks), "built_at": metadata["built_at"]}
