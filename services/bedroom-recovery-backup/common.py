"""Private consistent captures; shared with the fixed Pi SSH worker."""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import tarfile
import time

MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024


def regular(path: Path) -> Path:
    """Reject every symlink component, including an existing source root."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('unsafe_path')
    for part in [*reversed(path.parents), path]:
        s = part.lstat()
        if stat.S_ISLNK(s.st_mode):
            raise ValueError('symlink_path')
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('not_regular')
    return path


def read_stable(path: Path, maximum=MAX_FILE) -> bytes:
    regular(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        a = os.fstat(fd)
        if not stat.S_ISREG(a.st_mode) or a.st_size > maximum:
            raise ValueError('source_size')
        with os.fdopen(fd, 'rb', closefd=False) as f:
            raw = f.read(maximum + 1)
        b = os.fstat(fd)
        if (a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns) != (b.st_dev, b.st_ino, b.st_size, b.st_mtime_ns) or len(raw) != a.st_size:
            raise ValueError('source_changed')
        return raw
    finally:
        os.close(fd)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def logical_digest(db, deadline=None):
    """Hash schema and row multisets; publish no rows, SQL or account content."""
    result = hashlib.sha256()
    tables = db.execute("SELECT name,sql FROM sqlite_schema WHERE type='table' ORDER BY name").fetchall()
    count = 0
    for name, sql in tables:
        if deadline is not None and time.monotonic()>deadline:raise TimeoutError('sqlite_logical_deadline')
        result.update(json.dumps([name, sql], ensure_ascii=True).encode())
        quoted = '"' + name.replace('"', '""') + '"'
        hashes = []
        for row in db.execute('SELECT * FROM ' + quoted):
            if deadline is not None and time.monotonic()>deadline:raise TimeoutError('sqlite_logical_deadline')
            values = [[type(v).__name__, v.hex() if isinstance(v, bytes) else v] for v in row]
            encoded = json.dumps(values, ensure_ascii=True, separators=(',', ':')).encode()
            if len(encoded) > MAX_FILE:
                raise ValueError('sqlite_row_bound')
            hashes.append(hashlib.sha256(encoded).digest())
            count += 1
            if count > 1_000_000:
                raise ValueError('sqlite_row_count')
        for h in sorted(hashes):
            result.update(h)
    return result.hexdigest()


def sqlite_capture(path: Path):
    regular(path)
    if path.stat().st_size > MAX_FILE:
        raise ValueError('sqlite_size')
    deadline = time.monotonic() + 15
    def progress(*_):
        if time.monotonic() > deadline:
            raise TimeoutError('sqlite_deadline')
    source = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)
    target = sqlite3.connect(':memory:')
    try:
        source.execute('PRAGMA query_only=ON')
        source.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        # A read transaction pins the exact source snapshot for both fingerprints
        # and SQLite's online backup API. It never issues a source checkpoint.
        source.execute('BEGIN')
        if source.execute('PRAGMA page_count').fetchone()[0] * source.execute('PRAGMA page_size').fetchone()[0] > MAX_FILE:
            raise ValueError('sqlite_logical_size')
        before = logical_digest(source,deadline)
        source.backup(target, pages=64, progress=progress, sleep=.05)
        after = logical_digest(source,deadline)
        source.rollback()
        target.execute('PRAGMA query_only=ON')
        target.set_progress_handler(lambda: int(time.monotonic() > deadline),10000)
        if target.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('sqlite_integrity')
        if target.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('sqlite_foreign_keys')
        copied = logical_digest(target,deadline)
        if before != after or copied != before:
            raise ValueError('sqlite_logical_parity')
        # SQLite backup into :memory: can retain WAL-format header flags from
        # its source although the serialized copy has no WAL sidecar. Normalize
        # only this standalone copy; schema+rows remain verified below/on hub.
        raw = bytearray(target.serialize())
        if raw[:16] != b'SQLite format 3\x00' or raw[18] not in (1,2) or raw[19] not in (1,2):
            raise ValueError('sqlite_header')
        raw[18]=raw[19]=1
        raw=bytes(raw)
        if len(raw) > MAX_FILE:
            raise ValueError('sqlite_copy_size')
        return raw, {'integrity': 'ok', 'foreign_key_violations': 0,
                     'logical_sha256': copied, 'source_logical_sha256': before,
                     'source_snapshot_parity': True}
    finally:
        source.close()
        target.close()


class Archive:
    def __init__(self, stream):
        self.tar = tarfile.open(fileobj=stream, mode='w|')
        self.files = {}
        self.total = 0

    def add(self, name, raw, **metadata):
        if name in self.files or name.startswith('/') or '..' in Path(name).parts:
            raise ValueError('archive_name')
        self.total += len(raw)
        if len(raw) > MAX_FILE or self.total > MAX_TOTAL:
            raise ValueError('archive_bound')
        self.files[name] = {'bytes': len(raw), 'sha256': sha(raw), **metadata}
        item = tarfile.TarInfo(name)
        item.size = len(raw)
        item.mode = 0o600
        self.tar.addfile(item, io.BytesIO(raw))

    def finish(self, metadata):
        self.add('manifest.json', json.dumps({**metadata, 'files': self.files}, sort_keys=True).encode())
        self.tar.close()
