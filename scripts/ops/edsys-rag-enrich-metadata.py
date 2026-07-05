#!/usr/bin/env python3
"""Build local-only EdSys RAG metadata enrichment SQLite.

This script reads the generated clean RAG mirror and writes derived metadata to
runtime storage. It never rewrites Obsidian notes or Git-tracked docs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_MIRROR = Path("/mnt/ai-store/rag/docs/EdSysVault_RAG")
DEFAULT_DB = Path("/mnt/ai-store/rag/enrichment/metadata.sqlite")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build local-only RAG metadata enrichment index.")
    parser.add_argument("--mirror", default=str(DEFAULT_MIRROR))
    parser.add_argument("--db", default=str(DEFAULT_DB))
    return parser.parse_args()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_front_matter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    raw = text[4:end].strip().splitlines()
    body = text[end + 4 :].lstrip("\n")
    metadata: dict[str, object] = {}
    current_key = ""
    for line in raw:
        if not line.strip():
            continue
        if line.startswith("  - ") and current_key:
            metadata.setdefault(current_key, [])
            if isinstance(metadata[current_key], list):
                metadata[current_key].append(line.strip()[2:].strip().strip('"'))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        value = value.strip()
        if value == "":
            metadata[current_key] = []
        elif value.startswith("[") and value.endswith("]"):
            metadata[current_key] = [item.strip().strip('"\'') for item in value[1:-1].split(",") if item.strip()]
        else:
            metadata[current_key] = value.strip('"')
    return metadata, body


def title_for(path: Path, body: str, metadata: dict[str, object]) -> str:
    for key in ("name", "title"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for line in body.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return match.group(1).strip()
    return path.stem


def summary_for(body: str) -> str:
    cleaned = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("```") or stripped.startswith("---"):
            if cleaned:
                break
            continue
        cleaned.append(stripped)
        if sum(len(item) for item in cleaned) > 500:
            break
    return " ".join(cleaned)[:800]


def list_value(metadata: dict[str, object], *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value if str(item).strip())
        elif isinstance(value, str) and value.strip():
            values.append(value.strip())
    return sorted(set(values))


def build(mirror: Path, db_path: Path) -> dict[str, object]:
    mirror = mirror.expanduser().resolve()
    db_path = db_path.expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    built_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            create table if not exists documents(
              rel_path text primary key,
              title text not null,
              status text,
              last_audited text,
              confidence text,
              source_systems_json text not null,
              tags_json text not null,
              summary text not null,
              sha256 text not null,
              mtime_epoch integer not null,
              built_at text not null
            );
            create table if not exists metadata(key text primary key, value text not null);
            """
        )
        conn.execute("delete from documents")
        count = 0
        for path in sorted(mirror.rglob("*RAG Summary.md")):
            text = path.read_text(encoding="utf-8", errors="replace")
            metadata, body = split_front_matter(text)
            rel_path = str(path.relative_to(mirror))
            stat = path.stat()
            conn.execute(
                """
                insert into documents(
                  rel_path, title, status, last_audited, confidence,
                  source_systems_json, tags_json, summary, sha256, mtime_epoch, built_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rel_path,
                    title_for(path, body, metadata),
                    str(metadata.get("status") or ""),
                    str(metadata.get("last_audited") or metadata.get("last_verified") or ""),
                    str(metadata.get("confidence") or ""),
                    json.dumps(list_value(metadata, "source_systems"), separators=(",", ":")),
                    json.dumps(list_value(metadata, "tags", "role"), separators=(",", ":")),
                    summary_for(body),
                    sha256_text(text),
                    int(stat.st_mtime),
                    built_at,
                ),
            )
            count += 1
        conn.execute("delete from metadata")
        conn.executemany(
            "insert into metadata(key, value) values (?, ?)",
            [("built_at", built_at), ("mirror", str(mirror)), ("document_count", str(count))],
        )
        conn.commit()
    finally:
        conn.close()
    db_path.chmod(0o664)
    return {"db": str(db_path), "mirror": str(mirror), "document_count": count, "built_at": built_at}


def main() -> int:
    args = parse_args()
    result = build(Path(args.mirror), Path(args.db))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
