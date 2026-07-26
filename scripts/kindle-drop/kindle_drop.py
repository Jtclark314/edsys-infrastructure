#!/usr/bin/env python3
"""Fail-closed Basecamp Kindle Drop dispatcher.

Tracked source contains no mailbox addresses, OAuth material, or Amazon host
patterns. Those values live in one DPAPI LocalMachine-encrypted runtime blob.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import email
import email.policy
import hashlib
import html
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable

APP_VERSION = "1.0.0"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
UTC = timezone.utc


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def safe_name(value: str, *, fallback: str = "document.pdf") -> str:
    value = Path(value).name.replace("\x00", "")
    value = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "_", value).strip(" .")
    return (value[:180] or fallback)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def is_reparse_point(path: Path) -> bool:
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse)


def load_dpapi_json(path: Path) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("DPAPI private data can only be opened on Windows")
    import win32crypt  # type: ignore

    encrypted = base64.b64decode(path.read_bytes(), validate=True)
    clear = win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)[1]
    try:
        return json.loads(clear.decode("utf-8"))
    finally:
        clear = b"\0" * len(clear)


def save_dpapi_json(path: Path, value: dict[str, Any]) -> None:
    if os.name != "nt":
        raise RuntimeError("DPAPI private data can only be written on Windows")
    import win32crypt  # type: ignore

    clear = json.dumps(value, sort_keys=True).encode("utf-8")
    encrypted = win32crypt.CryptProtectData(
        clear, "EdSys Kindle Drop", None, None, None, 0x4
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(base64.b64encode(encrypted))
    os.replace(temporary, path)


@dataclass(frozen=True)
class Settings:
    share_root: Path
    state_root: Path
    private_blob_path: Path
    listen_ip: str
    listen_port: int
    automatic_limit_bytes: int
    web_limit_bytes: int
    stable_seconds: int
    poll_seconds: int
    gmail_poll_seconds: int
    gmail_health_seconds: int
    max_send_attempts: int
    max_pages: int
    return_download_limit_bytes: int

    @classmethod
    def load(cls, path: Path) -> "Settings":
        value = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            share_root=Path(value["share_root"]),
            state_root=Path(value["state_root"]),
            private_blob_path=Path(value["private_blob_path"]),
            listen_ip=str(value["listen_ip"]),
            listen_port=int(value["listen_port"]),
            automatic_limit_bytes=int(value["automatic_limit_bytes"]),
            web_limit_bytes=int(value["web_limit_bytes"]),
            stable_seconds=int(value["stable_seconds"]),
            poll_seconds=int(value["poll_seconds"]),
            gmail_poll_seconds=int(value["gmail_poll_seconds"]),
            gmail_health_seconds=int(value["gmail_health_seconds"]),
            max_send_attempts=int(value["max_send_attempts"]),
            max_pages=int(value["max_pages"]),
            return_download_limit_bytes=int(value["return_download_limit_bytes"]),
        )

    @property
    def inbox(self) -> Path:
        return self.share_root / "00-Drop-PDF-Here"

    @property
    def resend(self) -> Path:
        return self.share_root / "01-Resend"

    @property
    def oversize(self) -> Path:
        return self.share_root / "10-Needs-Web-Upload"

    @property
    def failed(self) -> Path:
        return self.share_root / "20-Failed"

    @property
    def returned(self) -> Path:
        return self.share_root / "30-Returned-Annotated"

    @property
    def receipts(self) -> Path:
        return self.share_root / "90-Receipts"

    @property
    def processing(self) -> Path:
        return self.state_root / "processing"

    @property
    def database(self) -> Path:
        return self.state_root / "state" / "kindle-drop.sqlite3"


class Ledger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS submissions (
              id TEXT PRIMARY KEY,
              work_name TEXT NOT NULL UNIQUE,
              original_name TEXT NOT NULL,
              source_queue TEXT NOT NULL,
              sha256 TEXT,
              size_bytes INTEGER,
              page_count INTEGER,
              state TEXT NOT NULL,
              reason TEXT,
              gmail_message_id TEXT,
              rfc822_message_id TEXT NOT NULL UNIQUE,
              attempts INTEGER NOT NULL DEFAULT 0,
              next_attempt_at REAL NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS submissions_hash_state
              ON submissions(sha256, state);
            CREATE TABLE IF NOT EXISTS returns (
              gmail_message_id TEXT PRIMARY KEY,
              stored_name TEXT,
              sha256 TEXT,
              size_bytes INTEGER,
              page_count INTEGER,
              state TEXT NOT NULL,
              reason TEXT,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runtime (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            """
        )
        self.connection.commit()
        self.lock = threading.RLock()

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self.lock:
            cursor = self.connection.execute(sql, parameters)
            self.connection.commit()
            return cursor

    def claim(self, work_name: str, original_name: str, source_queue: str) -> str:
        submission_id = uuid.uuid4().hex
        message_id = make_msgid(
            idstring=f"kindledrop-{submission_id}", domain="basecamp.local"
        )
        now = utc_now()
        self.execute(
            """
            INSERT INTO submissions
              (id, work_name, original_name, source_queue, state,
               rfc822_message_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'claimed', ?, ?, ?)
            """,
            (
                submission_id,
                work_name,
                original_name,
                source_queue,
                message_id,
                now,
                now,
            ),
        )
        return submission_id

    def submission_for_work(self, work_name: str) -> sqlite3.Row | None:
        return self.execute(
            "SELECT * FROM submissions WHERE work_name = ?", (work_name,)
        ).fetchone()

    def update(self, submission_id: str, **fields: Any) -> None:
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        self.execute(
            f"UPDATE submissions SET {assignments} WHERE id = ?",
            tuple(fields.values()) + (submission_id,),
        )

    def prior_submitted_hash(self, digest: str, exclude_id: str) -> bool:
        return (
            self.execute(
                """
                SELECT 1 FROM submissions
                WHERE sha256 = ? AND id <> ? AND state = 'submitted'
                LIMIT 1
                """,
                (digest, exclude_id),
            ).fetchone()
            is not None
        )

    def return_seen(self, gmail_message_id: str) -> bool:
        return (
            self.execute(
                "SELECT 1 FROM returns WHERE gmail_message_id = ?",
                (gmail_message_id,),
            ).fetchone()
            is not None
        )

    def add_return(self, gmail_message_id: str, **fields: Any) -> None:
        self.execute(
            """
            INSERT OR REPLACE INTO returns
              (gmail_message_id, stored_name, sha256, size_bytes, page_count,
               state, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gmail_message_id,
                fields.get("stored_name"),
                fields.get("sha256"),
                fields.get("size_bytes"),
                fields.get("page_count"),
                fields["state"],
                fields.get("reason"),
                utc_now(),
            ),
        )

    def set_runtime(self, key: str, value: str) -> None:
        self.execute(
            """
            INSERT INTO runtime(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def runtime(self) -> dict[str, str]:
        return {
            row["key"]: row["value"]
            for row in self.execute("SELECT key, value FROM runtime").fetchall()
        }

    def snapshot(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        with self.lock:
            output = sqlite3.connect(temporary)
            try:
                self.connection.backup(output)
                result = output.execute("PRAGMA integrity_check").fetchone()[0]
                if result != "ok":
                    raise RuntimeError(f"SQLite snapshot integrity check: {result}")
            finally:
                output.close()
        os.replace(temporary, destination)


def validate_pdf(path: Path, max_pages: int) -> tuple[str, int, int]:
    from pypdf import PdfReader

    if is_reparse_point(path):
        raise ValueError("reparse-point")
    size = path.stat().st_size
    if size == 0:
        raise ValueError("zero-byte")
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise ValueError("content-is-not-pdf")
    try:
        reader = PdfReader(str(path), strict=True)
        if reader.is_encrypted:
            raise ValueError("encrypted-pdf")
        page_count = len(reader.pages)
        if page_count < 1 or page_count > max_pages:
            raise ValueError("unsupported-page-count")
        for page in reader.pages:
            _ = page.mediabox
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("corrupt-or-unsupported-pdf") from exc
    return sha256_file(path), size, page_count


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.links.append(html.unescape(value))


def allowed_host(host: str | None, allowlist: Iterable[str]) -> bool:
    host = (host or "").rstrip(".").lower()
    for rule in allowlist:
        rule = rule.rstrip(".").lower()
        if rule.startswith("*."):
            suffix = rule[1:]
            if host.endswith(suffix) and host != suffix[1:]:
                return True
        elif host == rule:
            return True
    return False


def extract_urls(message: email.message.Message) -> list[str]:
    links: list[str] = []
    for part in message.walk():
        if part.get_content_maintype() != "text":
            continue
        try:
            text = part.get_content()
        except Exception:
            continue
        if part.get_content_subtype() == "html":
            parser = LinkParser()
            parser.feed(text)
            links.extend(parser.links)
        links.extend(re.findall(r"https://[^\s<>\"']+", text))
    return list(dict.fromkeys(link.rstrip(").,;") for link in links))


class AllowlistRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts: list[str]):
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or not allowed_host(parsed.hostname, self.hosts):
            raise ValueError("download-redirect-host-not-allowlisted")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GmailTransport:
    def __init__(self, private_path: Path, private: dict[str, Any]):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        self.private_path = private_path
        self.private = private
        self.credentials = Credentials.from_authorized_user_info(
            private["oauth"], scopes=[GMAIL_SCOPE]
        )
        self.service = build(
            "gmail", "v1", credentials=self.credentials, cache_discovery=False
        )
        self.lock = threading.Lock()

    def persist_credentials(self) -> None:
        self.private["oauth"] = json.loads(self.credentials.to_json())
        save_dpapi_json(self.private_path, self.private)

    def health(self) -> None:
        with self.lock:
            self.service.users().getProfile(userId="me").execute()
            self.persist_credentials()

    def send_pdf(
        self, path: Path, original_name: str, rfc822_message_id: str
    ) -> str:
        query = f"in:sent rfc822msgid:{rfc822_message_id}"
        with self.lock:
            existing = (
                self.service.users()
                .messages()
                .list(userId="me", q=query, maxResults=1)
                .execute()
                .get("messages", [])
            )
            if existing:
                return str(existing[0]["id"])

            message = EmailMessage()
            message["To"] = self.private["kindle_address"]
            message["From"] = self.private["gmail_address"]
            message["Subject"] = "Kindle Drop"
            message["Message-ID"] = rfc822_message_id
            message.set_content(
                "Submitted by the private Basecamp Kindle Drop service."
            )
            message.add_attachment(
                path.read_bytes(),
                maintype="application",
                subtype="pdf",
                filename=safe_name(original_name),
            )
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
            result = (
                self.service.users()
                .messages()
                .send(userId="me", body={"raw": raw})
                .execute()
            )
            self.persist_credentials()
            return str(result["id"])

    def unread(self) -> list[dict[str, str]]:
        with self.lock:
            result = (
                self.service.users()
                .messages()
                .list(
                    userId="me",
                    q="is:unread newer_than:14d",
                    maxResults=25,
                )
                .execute()
            )
            return list(result.get("messages", []))

    def raw_message(self, message_id: str) -> email.message.Message:
        with self.lock:
            value = (
                self.service.users()
                .messages()
                .get(userId="me", id=message_id, format="raw")
                .execute()
            )
        raw = base64.urlsafe_b64decode(value["raw"] + "===")
        return email.message_from_bytes(raw, policy=email.policy.default)

    def mark_read(self, message_id: str) -> None:
        with self.lock:
            self.service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()


class Dispatcher:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.ledger = Ledger(settings.database)
        self.private: dict[str, Any] | None = None
        self.gmail: GmailTransport | None = None
        self.stability: dict[str, tuple[int, int, float]] = {}
        self.stop_event = threading.Event()
        self.next_gmail_poll = 0.0
        self.next_gmail_health = 0.0
        self.gmail_state = "commissioning"
        self.last_error = ""
        self.prepare_paths()
        self.load_private()

    def prepare_paths(self) -> None:
        for path in (
            self.settings.inbox,
            self.settings.resend,
            self.settings.oversize,
            self.settings.failed,
            self.settings.returned,
            self.settings.receipts,
            self.settings.processing,
            self.settings.state_root / "logs",
            self.settings.state_root / "backups",
        ):
            path.mkdir(parents=True, exist_ok=True)

    def load_private(self) -> None:
        if not self.settings.private_blob_path.exists():
            self.ledger.set_runtime("commissioning", "private-config-missing")
            return
        value = load_dpapi_json(self.settings.private_blob_path)
        required = {
            "gmail_address",
            "kindle_address",
            "oauth",
            "amazon_sender_domains",
            "amazon_download_hosts",
        }
        missing = sorted(required - set(value))
        if missing or not value["amazon_sender_domains"] or not value[
            "amazon_download_hosts"
        ]:
            raise RuntimeError("private configuration incomplete")
        self.private = value
        self.gmail = GmailTransport(self.settings.private_blob_path, value)
        self.ledger.set_runtime("commissioning", "")

    def receipt(
        self,
        kind: str,
        *,
        submission_id: str | None = None,
        original_name: str | None = None,
        digest: str | None = None,
        size: int | None = None,
        pages: int | None = None,
        reason: str | None = None,
    ) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        suffix = submission_id[:10] if submission_id else uuid.uuid4().hex[:10]
        atomic_json(
            self.settings.receipts / f"{stamp}-{kind}-{suffix}.json",
            {
                "schema": 1,
                "state": kind,
                "timestamp": utc_now(),
                "submission_id": submission_id,
                "filename": original_name,
                "sha256": digest,
                "size_bytes": size,
                "page_count": pages,
                "reason": reason,
            },
        )

    def move_unique(self, source: Path, destination: Path, name: str) -> Path:
        destination.mkdir(parents=True, exist_ok=True)
        safe = safe_name(name)
        target = destination / safe
        if target.exists():
            target = destination / f"{target.stem}-{uuid.uuid4().hex[:8]}{target.suffix}"
        os.replace(source, target)
        return target

    def stable_candidates(self) -> list[tuple[Path, str]]:
        ready: list[tuple[Path, str]] = []
        now = time.time()
        seen: set[str] = set()
        for queue, source_name in (
            (self.settings.inbox, "normal"),
            (self.settings.resend, "resend"),
        ):
            for path in queue.iterdir():
                key = str(path)
                seen.add(key)
                try:
                    if is_reparse_point(path):
                        self.move_unique(path, self.settings.failed, path.name)
                        self.receipt(
                            "failed",
                            original_name=path.name,
                            reason="reparse-point",
                        )
                        self.stability.pop(key, None)
                        continue
                    if not path.is_file():
                        continue
                    info = path.stat()
                    fingerprint = (info.st_size, info.st_mtime_ns)
                    previous = self.stability.get(key)
                    if previous and previous[:2] == fingerprint:
                        if now - previous[2] >= self.settings.stable_seconds:
                            ready.append((path, source_name))
                    else:
                        self.stability[key] = (*fingerprint, now)
                except FileNotFoundError:
                    continue
        for key in set(self.stability) - seen:
            self.stability.pop(key, None)
        return ready

    def claim_ready(self) -> None:
        for source, queue_name in self.stable_candidates():
            work_name = f"{uuid.uuid4().hex}-{safe_name(source.name)}"
            target = self.settings.processing / work_name
            try:
                os.replace(source, target)
                self.ledger.claim(work_name, source.name, queue_name)
                self.stability.pop(str(source), None)
            except FileNotFoundError:
                continue

    def process_claimed(self) -> None:
        now = time.time()
        for path in sorted(self.settings.processing.iterdir()):
            row = self.ledger.submission_for_work(path.name)
            if row is None:
                self.move_unique(path, self.settings.failed, path.name)
                self.receipt("failed", reason="untracked-processing-file")
                continue
            if row["next_attempt_at"] > now:
                continue
            self.process_one(path, row)

    def process_one(self, path: Path, row: sqlite3.Row) -> None:
        submission_id = str(row["id"])
        original_name = str(row["original_name"])
        digest = row["sha256"]
        size = row["size_bytes"]
        pages = row["page_count"]
        try:
            if digest is None:
                if Path(original_name).suffix.lower() != ".pdf":
                    raise ValueError("unsupported-extension")
                digest, size, pages = validate_pdf(path, self.settings.max_pages)
                self.ledger.update(
                    submission_id,
                    sha256=digest,
                    size_bytes=size,
                    page_count=pages,
                    state="validated",
                )

            if size > self.settings.web_limit_bytes:
                self.move_unique(path, self.settings.failed, original_name)
                self.ledger.update(
                    submission_id,
                    state="failed",
                    reason="over-send-to-kindle-web-limit",
                )
                self.receipt(
                    "failed",
                    submission_id=submission_id,
                    original_name=original_name,
                    digest=digest,
                    size=size,
                    pages=pages,
                    reason="over-send-to-kindle-web-limit",
                )
                return

            if size > self.settings.automatic_limit_bytes:
                self.move_unique(path, self.settings.oversize, original_name)
                self.ledger.update(
                    submission_id,
                    state="needs-web-upload",
                    reason="over-automatic-limit",
                )
                self.receipt(
                    "needs-web-upload",
                    submission_id=submission_id,
                    original_name=original_name,
                    digest=digest,
                    size=size,
                    pages=pages,
                    reason="over-automatic-limit",
                )
                return

            if (
                row["source_queue"] != "resend"
                and self.ledger.prior_submitted_hash(digest, submission_id)
            ):
                path.unlink()
                self.ledger.update(
                    submission_id,
                    state="duplicate-suppressed",
                    reason="hash-already-submitted",
                )
                self.receipt(
                    "duplicate-suppressed",
                    submission_id=submission_id,
                    original_name=original_name,
                    digest=digest,
                    size=size,
                    pages=pages,
                    reason="hash-already-submitted",
                )
                return

            if self.gmail is None:
                self.ledger.update(
                    submission_id,
                    state="waiting-for-commissioning",
                    next_attempt_at=time.time() + 300,
                )
                return

            gmail_id = self.gmail.send_pdf(
                path, original_name, str(row["rfc822_message_id"])
            )
            path.unlink()
            self.ledger.update(
                submission_id,
                state="submitted",
                gmail_message_id=gmail_id,
                reason=None,
                next_attempt_at=0,
            )
            self.ledger.set_runtime("last_submission", utc_now())
            self.receipt(
                "submitted",
                submission_id=submission_id,
                original_name=original_name,
                digest=digest,
                size=size,
                pages=pages,
            )
        except ValueError as exc:
            reason = str(exc)
            with contextlib.suppress(FileNotFoundError):
                self.move_unique(path, self.settings.failed, original_name)
            self.ledger.update(submission_id, state="failed", reason=reason)
            self.receipt(
                "failed",
                submission_id=submission_id,
                original_name=original_name,
                digest=digest,
                size=size,
                pages=pages,
                reason=reason,
            )
        except Exception as exc:
            attempts = int(row["attempts"]) + 1
            reason = type(exc).__name__
            delay = min(3600, 30 * (2 ** min(attempts - 1, 7)))
            if attempts >= self.settings.max_send_attempts:
                with contextlib.suppress(FileNotFoundError):
                    self.move_unique(path, self.settings.failed, original_name)
                self.ledger.update(
                    submission_id,
                    state="failed",
                    attempts=attempts,
                    reason="submission-retries-exhausted",
                )
                self.receipt(
                    "failed",
                    submission_id=submission_id,
                    original_name=original_name,
                    digest=digest,
                    size=size,
                    pages=pages,
                    reason="submission-retries-exhausted",
                )
            else:
                self.ledger.update(
                    submission_id,
                    state="retrying",
                    attempts=attempts,
                    reason=reason,
                    next_attempt_at=time.time() + delay,
                )
            self.last_error = reason
            logging.exception("Submission attempt failed")

    def authenticated_amazon(self, message: email.message.Message) -> bool:
        if self.private is None:
            return False
        from_addresses = getaddresses(message.get_all("from", []))
        domains = {
            address.rsplit("@", 1)[-1].lower()
            for _, address in from_addresses
            if "@" in address
        }
        allowed_domains = [
            str(item).lower() for item in self.private["amazon_sender_domains"]
        ]
        sender_ok = any(allowed_host(domain, allowed_domains) for domain in domains)
        authentication = " ".join(message.get_all("authentication-results", []))
        authenticated = bool(
            re.search(r"\b(dkim|spf)=pass\b", authentication, re.IGNORECASE)
        )
        return sender_ok and authenticated

    def download_return(self, url: str) -> bytes:
        assert self.private is not None
        hosts = [str(item) for item in self.private["amazon_download_hosts"]]
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not allowed_host(parsed.hostname, hosts):
            raise ValueError("download-host-not-allowlisted")
        opener = urllib.request.build_opener(AllowlistRedirect(hosts))
        request = urllib.request.Request(
            url,
            headers={"User-Agent": f"EdSys-Kindle-Drop/{APP_VERSION}"},
        )
        maximum = self.settings.return_download_limit_bytes
        with opener.open(request, timeout=60) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > maximum:
                raise ValueError("returned-pdf-too-large")
            buffer = io.BytesIO()
            while True:
                block = response.read(min(1024 * 1024, maximum + 1 - buffer.tell()))
                if not block:
                    break
                buffer.write(block)
                if buffer.tell() > maximum:
                    raise ValueError("returned-pdf-too-large")
        return buffer.getvalue()

    def poll_returns(self) -> None:
        if self.gmail is None:
            return
        for item in self.gmail.unread():
            message_id = str(item["id"])
            if self.ledger.return_seen(message_id):
                self.gmail.mark_read(message_id)
                continue
            message = self.gmail.raw_message(message_id)
            if not self.authenticated_amazon(message):
                continue
            links = [
                url
                for url in extract_urls(message)
                if allowed_host(
                    urllib.parse.urlparse(url).hostname,
                    self.private["amazon_download_hosts"],  # type: ignore[index]
                )
            ]
            if not links:
                self.ledger.add_return(
                    message_id, state="amazon-notice", reason="no-allowlisted-pdf-link"
                )
                self.gmail.mark_read(message_id)
                self.receipt("amazon-notice", reason="no-allowlisted-pdf-link")
                continue
            try:
                temporary: Path | None = None
                last_link_error: Exception | None = None
                for link in links:
                    candidate = (
                        self.settings.processing / f"return-{uuid.uuid4().hex}.pdf"
                    )
                    try:
                        candidate.write_bytes(self.download_return(link))
                        digest, size, pages = validate_pdf(
                            candidate, self.settings.max_pages
                        )
                        temporary = candidate
                        break
                    except Exception as exc:
                        last_link_error = exc
                        with contextlib.suppress(FileNotFoundError):
                            candidate.unlink()
                if temporary is None:
                    raise last_link_error or ValueError("no-valid-return-pdf")
                subject = safe_name(str(message.get("subject", "Annotated return")))
                if not subject.lower().endswith(".pdf"):
                    subject += ".pdf"
                stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
                stored = self.move_unique(
                    temporary, self.settings.returned, f"{stamp}-{subject}"
                )
                self.ledger.add_return(
                    message_id,
                    stored_name=stored.name,
                    sha256=digest,
                    size_bytes=size,
                    page_count=pages,
                    state="captured",
                )
                self.ledger.set_runtime("last_return_capture", utc_now())
                self.receipt(
                    "return-captured",
                    original_name=stored.name,
                    digest=digest,
                    size=size,
                    pages=pages,
                )
                self.gmail.mark_read(message_id)
            except Exception as exc:
                self.last_error = type(exc).__name__
                logging.exception("Return capture failed")

    def gmail_maintenance(self) -> None:
        now = time.monotonic()
        if self.gmail is None:
            return
        if now >= self.next_gmail_health:
            try:
                self.gmail.health()
                self.gmail_state = "ready"
                self.ledger.set_runtime("last_gmail_health", utc_now())
                self.last_error = ""
            except Exception as exc:
                self.gmail_state = "error"
                self.last_error = type(exc).__name__
                logging.exception("Gmail health check failed")
            self.next_gmail_health = now + self.settings.gmail_health_seconds
        if now >= self.next_gmail_poll and self.gmail_state == "ready":
            try:
                self.poll_returns()
            except Exception as exc:
                self.last_error = type(exc).__name__
                logging.exception("Gmail return poll failed")
            self.next_gmail_poll = now + self.settings.gmail_poll_seconds

    def queue_health(self) -> tuple[int, int]:
        count = 0
        oldest = 0.0
        now = time.time()
        for folder in (
            self.settings.inbox,
            self.settings.resend,
            self.settings.processing,
        ):
            for path in folder.iterdir():
                try:
                    if not is_reparse_point(path) and path.is_file():
                        count += 1
                        oldest = max(oldest, now - path.stat().st_mtime)
                except OSError:
                    continue
        return count, int(oldest)

    def health(self) -> tuple[int, dict[str, Any]]:
        count, oldest = self.queue_health()
        runtime = self.ledger.runtime()
        commissioned = self.gmail is not None
        ready = commissioned and self.gmail_state == "ready"
        status = "ready" if ready else (
            "commissioning" if not commissioned else "degraded"
        )
        code = 200 if ready else 503
        return code, {
            "schema": 1,
            "service": "kindle-drop",
            "version": APP_VERSION,
            "status": status,
            "dispatcher_heartbeat": runtime.get("dispatcher_heartbeat"),
            "gmail_authentication": self.gmail_state,
            "queue_count": count,
            "oldest_queue_age_seconds": oldest,
            "last_submission": runtime.get("last_submission"),
            "last_return_capture": runtime.get("last_return_capture"),
            "last_gmail_health": runtime.get("last_gmail_health"),
            "last_error_class": self.last_error or None,
        }

    def serve_health(self) -> ThreadingHTTPServer:
        dispatcher = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path != "/healthz":
                    self.send_error(404)
                    return
                code, value = dispatcher.health()
                body = json.dumps(value, sort_keys=True).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                return

        server = ThreadingHTTPServer(
            (self.settings.listen_ip, self.settings.listen_port), Handler
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def run(self) -> None:
        server = self.serve_health()
        logging.info("Kindle Drop dispatcher started")
        try:
            while not self.stop_event.is_set():
                self.ledger.set_runtime("dispatcher_heartbeat", utc_now())
                self.gmail_maintenance()
                self.claim_ready()
                self.process_claimed()
                self.stop_event.wait(self.settings.poll_seconds)
        finally:
            server.shutdown()
            server.server_close()


def create_backup(settings: Settings, output_root: Path) -> Path:
    ledger = Ledger(settings.database)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    staging = settings.state_root / "backup-staging" / uuid.uuid4().hex
    snapshot = staging / "kindle-drop.sqlite3"
    try:
        ledger.snapshot(snapshot)
    finally:
        ledger.connection.close()
    output_root.mkdir(parents=True, exist_ok=True)
    final = output_root / f"kindle-drop-daily-{timestamp}.zip"
    temporary = final.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            archive.write(snapshot, "state/kindle-drop.sqlite3")
            for root, prefix in (
                (settings.receipts, "receipts"),
                (settings.returned, "returned-annotated"),
            ):
                for path in sorted(root.rglob("*")):
                    if not is_reparse_point(path) and path.is_file():
                        archive.write(path, f"{prefix}/{path.relative_to(root)}")
        os.replace(temporary, final)
        digest = sha256_file(final)
        final.with_suffix(".zip.sha256").write_text(
            f"{digest}  {final.name}\n", encoding="ascii"
        )
        return final
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def configure_logging(settings: Settings) -> None:
    log_path = settings.state_root / "logs" / "dispatcher.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--config", type=Path, required=True)
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--config", type=Path, required=True)
    backup_parser.add_argument("--output-root", type=Path, required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("pdf", type=Path)
    validate_parser.add_argument("--max-pages", type=int, default=8000)
    args = parser.parse_args(argv)

    if args.command == "validate":
        digest, size, pages = validate_pdf(args.pdf, args.max_pages)
        print(json.dumps({"sha256": digest, "size_bytes": size, "pages": pages}))
        return 0

    settings = Settings.load(args.config)
    configure_logging(settings)
    if args.command == "backup":
        print(create_backup(settings, args.output_root))
        return 0
    Dispatcher(settings).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
