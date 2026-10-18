#!/usr/bin/env python3
"""Collect completed encrypted native HA backups into existing private Restic staging."""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid

import guest_program as guest
from guest_program import BackupError

STAGE = Path('/srv/edsys-backup/staging/home-assistant')
# Both paths must live below the unit's single writable bind mount. Equal
# st_dev alone does not permit rename across separate systemd bind mounts.
# The backup runner must explicitly exclude this whole incoming subtree.
INCOMING = STAGE / '.incoming'
KEY = Path('/etc/edsys-backup/home-assistant-recovery-key')
# Verified 9950x deployment boundary: /srv is owned by Jeremy (UID 1000).
# This trusts only that existing ancestor, not operator-owned backup children.
TRUSTED_ANCESTOR_UIDS = {Path('/srv'): 1000}


def secure_directory(path, uid=0, floor=Path('/'), private=True, create=True):
    path, floor = Path(path).absolute(), Path(floor).absolute()
    if not path.is_relative_to(floor):
        raise BackupError('unsafe_local_path')
    # Inspect every ancestor rather than trusting resolve(), which follows links.
    chain = [floor]
    for part in path.relative_to(floor).parts:
        chain.append(chain[-1] / part)
    for current in chain:
        try:
            info = current.lstat()
        except FileNotFoundError:
            if not create:
                raise BackupError('private_parent_unavailable') from None
            current.mkdir(mode=0o700)
            info = current.lstat()
        allowed_uids = {uid}
        if uid == 0 and floor == Path('/') and current != path:
            allowed_uids.add(TRUSTED_ANCESTOR_UIDS.get(current, uid))
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in allowed_uids
                or info.st_mode & 0o022):
            raise BackupError('unsafe_local_directory')
    if private and path.stat().st_mode & 0o077:
        raise BackupError('nonprivate_stage_directory')


def read_private(path, uid=0, maximum=4096):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != uid
                    or before.st_mode & 0o077 or not 0 < before.st_size <= maximum):
                raise BackupError('unsafe_private_file')
            data = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
            if (guest.fingerprint(before) != guest.fingerprint(after)
                    or guest.fingerprint(Path(path).lstat()) != guest.fingerprint(before)
                    or len(data) != before.st_size):
                raise BackupError('private_file_changed')
            return data
    except OSError:
        raise BackupError('private_file_unavailable') from None


def write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value):
    temporary = path.with_name('.status-' + uuid.uuid4().hex)
    try:
        write_private(temporary, (json.dumps(value, sort_keys=True) + '\n').encode())
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise BackupError('unsafe_status_path')
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


class Transport:
    def __init__(self, runner=subprocess.run, clock=time.monotonic, sleeper=time.sleep):
        self.runner = runner
        self.clock = clock
        self.sleeper = sleeper
        self.code = Path(guest.__file__).read_text()

    @staticmethod
    def reject_failure_proof(stdout):
        """A failed invocation may still carry a complete semantic rejection."""
        try:
            envelope = json.loads(stdout)
        except (ValueError, TypeError):
            return
        if not isinstance(envelope, dict):
            return
        if (envelope.get('out-truncated') or envelope.get('err-truncated')
                or envelope.get('exited') is False
                or ('exitcode' in envelope and envelope['exitcode'] != 0)):
            raise BackupError('guest_transport_incomplete')
        if 'out-data' not in envelope:
            return
        output = envelope['out-data']
        if not isinstance(output, str) or len(output) > 2 * guest.CHUNK_BYTES:
            raise BackupError('guest_response_oversized')
        try:
            value = json.loads(output)
        except (ValueError, TypeError):
            raise BackupError('guest_transport_incomplete') from None
        if isinstance(value, dict) and 'error' in value:
            raise BackupError('guest_operation_failed')

    @staticmethod
    def decode(stdout):
        try:
            envelope = json.loads(stdout)
            if (not isinstance(envelope, dict) or envelope.get('exitcode') != 0
                    or envelope.get('exited') is False
                    or envelope.get('out-truncated') or envelope.get('err-truncated')):
                raise BackupError('guest_transport_incomplete')
            output = envelope.get('out-data', '')
            if not isinstance(output, str) or len(output) > 2 * guest.CHUNK_BYTES:
                raise BackupError('guest_response_oversized')
            value = json.loads(output)
            if isinstance(value, dict) and 'error' in value:
                raise BackupError('guest_operation_failed')
            return value
        except (ValueError, TypeError):
            raise BackupError('guest_transport_failed') from None

    def __call__(self, payload):
        if not isinstance(payload, dict) or payload.get('action') not in {'inventory', 'describe', 'chunk', 'verify-key'}:
            raise BackupError('invalid_guest_request')
        args = ['flock', '--exclusive', '--wait', '10', '--conflict-exit-code', '75',
                '/run/lock/edsys-ha-qga-300.lock',
                'qm', 'guest', 'exec', '300', '--pass-stdin', '1', '--timeout', '90',
                '--', 'docker', 'exec', '-i', 'hassio_supervisor', 'python3', '-c', self.code]
        command = ['runuser', '-u', 'jeremy', '--', 'ssh', '-o', 'BatchMode=yes',
                   '-o', 'ClearAllForwardings=yes', '-o', 'ConnectTimeout=10',
                   '-o', 'StrictHostKeyChecking=yes',
                   'pve-node3', shlex.join(args)]
        # Same frozen, fixed read request: at most three retries, with an overall
        # four-minute per-call budget. Semantic/truncation proofs never retry.
        request = json.dumps(payload)
        deadline = self.clock() + 240
        delays = (1, 2, 4)
        for attempt in range(4):
            remaining = deadline - self.clock()
            if remaining <= 0:
                break
            try:
                result = self.runner(command, input=request, capture_output=True,
                                     text=True, timeout=min(110, remaining), check=False)
            except subprocess.TimeoutExpired:
                result = None
            except (subprocess.SubprocessError, OSError, ValueError, TypeError):
                raise BackupError('guest_transport_failed') from None
            if result is not None:
                if result.returncode == 0:
                    return self.decode(result.stdout)
                self.reject_failure_proof(result.stdout)
            if attempt == len(delays):
                break
            delay = delays[attempt]
            if deadline - self.clock() <= delay:
                break
            self.sleeper(delay)
        raise BackupError('guest_transport_failed')


def choose(items, now, maximum_age, backup_id=None):
    if not isinstance(items, list) or len(items) > 512:
        raise BackupError('invalid_guest_inventory')
    candidates = []
    for item in items:
        metadata = item['metadata']
        if not guest.eligible(metadata) or (backup_id and metadata['slug'] != backup_id):
            continue
        age = now - datetime.fromisoformat(metadata['date'])
        if -timedelta(minutes=5) <= age <= maximum_age:
            candidates.append(item)
    if not candidates:
        raise BackupError('no_fresh_eligible_backup')
    candidate = max(candidates, key=lambda item: item['metadata']['date'])
    if sum(item['metadata']['slug'] == candidate['metadata']['slug'] for item in candidates) != 1:
        raise BackupError('ambiguous_backup_id')
    return candidate


def file_digest(path, uid):
    digest = hashlib.sha256()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                    or info.st_mode & 0o077 or info.st_size > guest.MAX_BYTES):
                raise BackupError('unsafe_staged_file')
            while data := stream.read(guest.CHUNK_BYTES):
                digest.update(data)
            if (guest.fingerprint(info) != guest.fingerprint(os.fstat(stream.fileno()))
                    or guest.fingerprint(info) != guest.fingerprint(Path(path).lstat())):
                raise BackupError('staged_file_changed')
            return {'bytes': info.st_size, 'sha256': digest.hexdigest()}
    except OSError:
        raise BackupError('staged_file_unavailable') from None


def verify_generation(directory, description, key, uid, floor):
    secure_directory(directory, uid, floor)
    if {p.name for p in directory.iterdir()} != {'native-backup.tar', 'recovery-key', 'manifest.json'}:
        raise BackupError('unexpected_generation_files')
    manifest = json.loads(read_private(directory / 'manifest.json', uid, 65536))
    if (manifest.get('schema') != 1 or manifest.get('metadata') != description['metadata']
            or manifest.get('guest') != description):
        raise BackupError('generation_source_mismatch')
    for name in ('native-backup.tar', 'recovery-key'):
        if file_digest(directory / name, uid) != manifest['files'].get(name):
            raise BackupError('generation_checksum_mismatch')
    if (manifest['files']['native-backup.tar']['sha256'] != description['sha256']
            or manifest['files']['native-backup.tar']['bytes'] != description['size']
            or read_private(directory / 'recovery-key', uid) != key):
        raise BackupError('generation_content_mismatch')
    with (directory / 'native-backup.tar').open('rb') as stream:
        if guest.archive_metadata(stream) != description['metadata']:
            raise BackupError('generation_metadata_mismatch')
    return manifest


def collect(transport, stage=STAGE, incoming=INCOMING, key_path=KEY, now=None,
            maximum_age=timedelta(hours=26), backup_id=None, uid=0, floor=Path('/'),
            progress=None):
    now = now or datetime.now(timezone.utc)
    secure_directory(stage, uid, floor)
    secure_directory(incoming, uid, floor)
    if stage.stat().st_dev != incoming.stat().st_dev:
        raise BackupError('incoming_stage_filesystem_mismatch')
    started = time.monotonic()
    temporary = None
    try:
        secure_directory(Path(key_path).parent, uid, floor, private=False, create=False)
        key = read_private(key_path, uid)
        candidate = choose(transport({'action': 'inventory'}), now, maximum_age, backup_id)
        description = transport({'action': 'describe', 'filename': candidate['filename']})
        if any(description.get(k) != candidate.get(k) for k in candidate):
            raise BackupError('guest_archive_changed')
        if not guest.SLUG.fullmatch(description['metadata']['slug']):
            raise BackupError('invalid_backup_id')
        try:
            password = key.decode('utf-8').rstrip('\r\n')
        except UnicodeError:
            raise BackupError('invalid_recovery_key') from None
        key_proof = transport({'action': 'verify-key', 'filename': description['filename'],
                               'fingerprint': description['fingerprint'], 'password': password})
        if (not isinstance(key_proof, dict) or key_proof.get('key_matches') is not True
                or type(key_proof.get('validated_members')) is not int
                or key_proof['validated_members'] < 4):
            raise BackupError('native_key_verification_failed')
        target = stage / description['metadata']['slug']
        reused = target.exists() or target.is_symlink()
        if reused:
            manifest = verify_generation(target, description, key, uid, floor)
            choose([description], now + timedelta(seconds=time.monotonic() - started),
                   maximum_age, backup_id)
            if read_private(key_path, uid) != key:
                raise BackupError('recovery_key_changed')
        else:
            temporary = Path(tempfile.mkdtemp(prefix='incomplete-', dir=incoming))
            output = temporary / 'native-backup.tar'
            digest = hashlib.sha256()
            with output.open('xb') as stream:
                output.chmod(0o600)
                offset = 0
                while offset < description['size']:
                    if time.monotonic() - started > 1800:
                        raise BackupError('collection_deadline_exceeded')
                    length = min(guest.CHUNK_BYTES, description['size'] - offset)
                    chunk = transport({'action': 'chunk', 'filename': description['filename'],
                                       'fingerprint': description['fingerprint'],
                                       'offset': offset, 'length': length})
                    try:
                        data = base64.b64decode(chunk['data'], validate=True)
                    except (ValueError, KeyError, TypeError):
                        raise BackupError('invalid_chunk_encoding') from None
                    if (chunk.get('offset') != offset or chunk.get('length') != length
                            or len(data) != length
                            or hashlib.sha256(data).hexdigest() != chunk.get('sha256')):
                        raise BackupError('chunk_integrity_failed')
                    stream.write(data)
                    digest.update(data)
                    offset += length
                    if progress and (offset == description['size'] or offset % (16 * guest.CHUNK_BYTES) == 0):
                        progress({'exported_bytes': offset, 'total_bytes': description['size']})
                stream.flush()
                os.fsync(stream.fileno())
            if digest.hexdigest() != description['sha256']:
                raise BackupError('archive_integrity_failed')
            if transport({'action': 'describe', 'filename': description['filename']}) != description:
                raise BackupError('guest_archive_changed')
            choose([description], now + timedelta(seconds=time.monotonic() - started),
                   maximum_age, backup_id)
            if read_private(key_path, uid) != key:
                raise BackupError('recovery_key_changed')
            with output.open('rb') as stream:
                if guest.archive_metadata(stream) != description['metadata']:
                    raise BackupError('archive_metadata_mismatch')
            write_private(temporary / 'recovery-key', key)
            manifest = {'schema': 1, 'collected_at': now.isoformat(),
                        'metadata': description['metadata'], 'guest': description,
                        'native_key_verification': key_proof,
                        'files': {'native-backup.tar': {'bytes': description['size'],
                                                       'sha256': description['sha256']},
                                  'recovery-key': {'bytes': len(key),
                                                   'sha256': hashlib.sha256(key).hexdigest()}}}
            write_private(temporary / 'manifest.json', (json.dumps(manifest, sort_keys=True) + '\n').encode())
            verify_generation(temporary, description, key, uid, floor)
            sync_directory(temporary)
            os.rename(temporary, target)
            temporary = None
            sync_directory(stage)
        result = {'status': 'success', 'checked_at': now.isoformat(), 'backup_id': target.name,
                  'backup_date': description['metadata']['date'], 'bytes': description['size'],
                  'reused': reused, 'native_protected': True, 'required_apps': 3,
                  'database_included': True, 'native_key_verified': True,
                  'validated_encrypted_members': key_proof['validated_members']}
        atomic_json(stage / 'last-success.json', result)
        atomic_json(stage / 'status.json', result)
        return result
    except Exception as error:
        code = str(error) if isinstance(error, BackupError) else 'collection_failed'
        failure = {'status': 'failed', 'checked_at': now.isoformat(), 'error': code}
        if isinstance(error, OSError):
            # Fixed type and bounded errno only, never exception text or paths.
            failure['failure_type'] = 'OSError'
            if type(error.errno) is int and 0 < error.errno < 4096:
                failure['failure_errno'] = error.errno
        atomic_json(stage / 'status.json', failure)
        if isinstance(error, BackupError):
            raise
        raise BackupError(code) from None
    finally:
        if temporary is not None:
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup-id', help='Require this native metadata ID for a specific initial export')
    parser.add_argument('--max-age-hours', type=float, default=26)
    parser.add_argument('--progress', action='store_true', help='Print aggregate byte progress only')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise BackupError('root_required')
    if (not 0 < args.max_age_hours <= 48
            or (args.backup_id and not guest.SLUG.fullmatch(args.backup_id))):
        raise BackupError('invalid_collector_options')
    os.umask(0o077)
    lock_root = Path('/run/edsys-home-assistant-backup')
    secure_directory(lock_root)
    lock = os.open(lock_root / 'collector.lock',
                   os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock, 'w') as stream:
        if os.fstat(stream.fileno()).st_uid != 0:
            raise BackupError('unsafe_collector_lock')
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError('collector_already_running') from None
        progress = (lambda value: print(json.dumps(value), file=sys.stderr, flush=True)) if args.progress else None
        print(json.dumps(collect(Transport(), maximum_age=timedelta(hours=args.max_age_hours),
                                 backup_id=args.backup_id, progress=progress)))


if __name__ == '__main__':
    try:
        main()
    except BackupError as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}), file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        print('{"status":"failed","error":"collector_failed"}', file=sys.stderr)
        raise SystemExit(1)
