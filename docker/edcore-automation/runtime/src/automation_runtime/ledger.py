"""Durable duplicate-ID ledger for the command gate."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3


class DuplicateCommandError(RuntimeError):
    pass


class Ledger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The gate constructs its durable state on the main thread and has one
        # serialized command worker. Cross-thread use is therefore deliberate;
        # run() joins that sole worker before close().
        self.connection = sqlite3.connect(
            self.path,
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
        )
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS command_ids (
                command_id TEXT PRIMARY KEY,
                expires_at TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('pending', 'published')),
                first_seen_at TEXT NOT NULL
            ) STRICT
            """
        )

    def claim(self, command_id: str, expires_at: datetime, now: datetime) -> None:
        cutoff = (now.astimezone(timezone.utc) - timedelta(days=1)).isoformat()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute("DELETE FROM command_ids WHERE expires_at < ?", (cutoff,))
            self.connection.execute(
                "INSERT INTO command_ids(command_id, expires_at, state, first_seen_at) VALUES (?, ?, 'pending', ?)",
                (
                    command_id,
                    expires_at.astimezone(timezone.utc).isoformat(),
                    now.astimezone(timezone.utc).isoformat(),
                ),
            )
            self.connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            self.connection.execute("ROLLBACK")
            raise DuplicateCommandError(command_id) from exc
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def mark_published(self, command_id: str) -> None:
        cursor = self.connection.execute(
            "UPDATE command_ids SET state='published' WHERE command_id=? AND state='pending'",
            (command_id,),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("command ledger transition failed")

    def close(self) -> None:
        self.connection.close()
