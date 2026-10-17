"""Fixed read-only HAOS guest operations; also supplies local tar validation.

The controller sends this file to Python in hassio_supervisor through the existing
QEMU guest-agent route. No provider credential or arbitrary path is accepted.
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile

ROOT = Path('/data/backup')
CHUNK_BYTES = 1024 * 1024
MAX_BYTES = 8 * 1024**3
REQUIRED_APPS = {'core_matter_server', 'core_mosquitto', 'd5369777_music_assistant'}
SLUG = re.compile(r'[0-9a-f]{8}')
BASENAME = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,190}\.tar')


class BackupError(RuntimeError):
    """Messages are fixed codes suitable for private status, never raw payloads."""


def fingerprint(value):
    return [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns]


def archive_metadata(stream):
    """Read only bounded outer metadata; never extract an archive member."""
    try:
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode='r:') as archive:
            seen = {}
            for index, member in enumerate(archive):
                name = member.name[2:] if member.name.startswith('./') else member.name
                path = PurePosixPath(name)
                if (index >= 128 or member.size < 0 or '\\' in name or path.is_absolute()
                        or len(path.parts) != 1 or '..' in path.parts
                        or not member.isfile() or name in seen):
                    raise BackupError('unsafe_archive_member')
                seen[name] = member
            member = seen.get('backup.json')
            if member is None or not 0 < member.size <= 65536:
                raise BackupError('missing_backup_metadata')
            raw = json.load(archive.extractfile(member))
            if not isinstance(raw, dict) or raw.get('version') != 2:
                raise BackupError('unsupported_metadata')
            slug = raw.get('slug')
            if not isinstance(slug, str) or not SLUG.fullmatch(slug):
                raise BackupError('invalid_backup_id')
            date = datetime.fromisoformat(raw['date'])
            if date.tzinfo is None:
                raise BackupError('invalid_backup_date')
            home = raw.get('homeassistant')
            apps = raw.get('addons')
            if not isinstance(apps, list):
                raise BackupError('invalid_app_metadata')
            app_versions = {}
            for app in apps:
                if (not isinstance(app, dict) or not isinstance(app.get('slug'), str)
                        or not re.fullmatch(r'[a-z0-9_-]+', app['slug'])
                        or app['slug'] in app_versions
                        or not isinstance(app.get('version'), str)):
                    raise BackupError('invalid_app_metadata')
                app_versions[app['slug']] = app['version']
            # New native managed backups are called partial even when all current
            # apps are selected. Coverage, not that label, determines eligibility.
            protected = raw.get('protected') is True
            database = isinstance(home, dict) and home.get('exclude_database') is False
            ha_version = home.get('version') if isinstance(home, dict) else None
            if not isinstance(ha_version, str):
                ha_version = None
            suffix = '.tar.gz' if raw.get('compressed') is True else '.tar'
            payloads = ({'homeassistant' + suffix} if home else set())
            payloads.update(slug + suffix for slug in app_versions)
            if not payloads.issubset(seen) or any(seen[name].size <= 0 for name in payloads):
                raise BackupError('missing_component_payload')
            return {'slug': slug, 'date': date.astimezone(timezone.utc).isoformat(),
                    'protected': protected, 'database_included': database,
                    'ha_version': ha_version, 'apps': app_versions}
    except BackupError:
        raise
    except (ValueError, KeyError, TypeError, OSError, tarfile.TarError, UnicodeError):
        raise BackupError('invalid_archive') from None


def eligible(metadata):
    return (metadata.get('protected') is True and metadata.get('database_included') is True
            and bool(metadata.get('ha_version'))
            and REQUIRED_APPS.issubset(metadata.get('apps', {})))


def open_archive(root, filename):
    if (not isinstance(filename, str) or not BASENAME.fullmatch(filename)
            or '..' in filename):
        raise BackupError('invalid_archive_name')
    if root.is_symlink() or not root.is_dir():
        raise BackupError('unsafe_guest_root')
    try:
        fd = os.open(root / filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stream = os.fdopen(fd, 'rb')
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
            stream.close()
            raise BackupError('unsafe_guest_archive')
        return stream
    except OSError:
        raise BackupError('guest_archive_unavailable') from None


def unchanged(root, filename, stream, expected):
    if fingerprint(os.fstat(stream.fileno())) != expected:
        raise BackupError('guest_archive_changed')
    try:
        current = (root / filename).lstat()
    except OSError:
        raise BackupError('guest_archive_changed') from None
    if not stat.S_ISREG(current.st_mode) or fingerprint(current) != expected:
        raise BackupError('guest_archive_changed')


def describe(root, filename, with_hash=False):
    with open_archive(root, filename) as stream:
        token = fingerprint(os.fstat(stream.fileno()))
        metadata = archive_metadata(stream)
        result = {'filename': filename, 'fingerprint': token,
                  'size': token[2], 'metadata': metadata}
        if with_hash:
            stream.seek(0)
            digest = hashlib.sha256()
            while chunk := stream.read(CHUNK_BYTES):
                digest.update(chunk)
            result['sha256'] = digest.hexdigest()
        unchanged(root, filename, stream, token)
        return result


def operation(payload, root=ROOT):
    if not isinstance(payload, dict):
        raise BackupError('invalid_guest_request')
    action = payload.get('action')
    if action == 'inventory' and set(payload) == {'action'}:
        if root.is_symlink() or not root.is_dir():
            raise BackupError('unsafe_guest_root')
        items = []
        for entry in root.iterdir():
            if BASENAME.fullmatch(entry.name) and not entry.is_symlink():
                try:
                    items.append(describe(root, entry.name))
                except BackupError:
                    continue  # Unfinished/unsupported files cannot become candidates.
        return items
    if action == 'describe' and set(payload) == {'action', 'filename'}:
        return describe(root, payload['filename'], with_hash=True)
    if action == 'chunk' and set(payload) == {'action', 'filename', 'fingerprint', 'offset', 'length'}:
        offset, length = payload['offset'], payload['length']
        if (type(offset) is not int or type(length) is not int or offset < 0
                or not 0 < length <= CHUNK_BYTES):
            raise BackupError('invalid_chunk_range')
        with open_archive(root, payload['filename']) as stream:
            unchanged(root, payload['filename'], stream, payload['fingerprint'])
            if offset + length > os.fstat(stream.fileno()).st_size:
                raise BackupError('invalid_chunk_range')
            stream.seek(offset)
            data = stream.read(length)
            if len(data) != length:
                raise BackupError('short_guest_chunk')
            unchanged(root, payload['filename'], stream, payload['fingerprint'])
            return {'offset': offset, 'length': length,
                    'sha256': hashlib.sha256(data).hexdigest(),
                    'data': base64.b64encode(data).decode('ascii')}
    raise BackupError('invalid_guest_request')


if __name__ == '__main__':
    try:
        request = sys.stdin.read(4097)
        if len(request) > 4096:
            raise BackupError('oversized_guest_request')
        print(json.dumps(operation(json.loads(request))))
    except Exception:
        # Never reflect exception text, file bytes or a private payload.
        print('{"error":"guest_operation_failed"}')
        raise SystemExit(1)
