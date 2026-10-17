import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collect as collector
import guest_program as guest

NOW = datetime(2026, 10, 17, 1, 0, tzinfo=timezone.utc)
FILENAME = 'automatic_backup_2026_10_1_2026-10-16_20.46_13806909.tar'


def metadata():
    return {'slug': 'ab270a93', 'date': '2026-10-17T00:46:13+00:00',
            'type': 'partial', 'version': 2, 'protected': True, 'compressed': True,
            'homeassistant': {'version': '2026.10.1', 'exclude_database': False},
            'addons': [{'slug': slug, 'version': '1.0'} for slug in sorted(guest.REQUIRED_APPS)]}


def archive(path, meta=None, extra=None, payload_size=128):
    meta = metadata() if meta is None else meta
    with tarfile.open(path, 'w') as tar:
        members = [(slug + '.tar.gz', b'x' * payload_size)
                   for slug in ['homeassistant', *sorted(guest.REQUIRED_APPS)]]
        members.append(('./backup.json', json.dumps(meta).encode()))
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if extra is not None:
            tar.addfile(extra, io.BytesIO(b'x' * extra.size) if extra.isfile() else None)
    return path


class FakeTransport:
    def __init__(self, root):
        self.root, self.calls, self.modify = root, [], None

    def __call__(self, payload):
        self.calls.append(deepcopy(payload))
        value = guest.operation(payload, self.root)
        return self.modify(payload, value) if self.modify else value


@pytest.fixture
def environment(tmp_path):
    tmp_path.chmod(0o700)
    source = tmp_path / 'guest'
    source.mkdir(mode=0o700)
    archive(source / FILENAME)
    key = tmp_path / 'key'
    key.write_bytes(b'private-test-recovery-key')
    key.chmod(0o600)
    transport = FakeTransport(source)
    args = dict(transport=transport, stage=tmp_path / 'stage', incoming=tmp_path / 'incoming',
                key_path=key, now=NOW, uid=os.geteuid(), floor=tmp_path)
    return SimpleNamespace(root=tmp_path, source=source, key=key, transport=transport, args=args)


def test_protected_partial_with_real_coverage_is_eligible(environment):
    result = collector.collect(**environment.args, backup_id='ab270a93')
    assert result['backup_id'] == 'ab270a93'
    stage = environment.args['stage']
    generation = stage / 'ab270a93'
    assert {p.name for p in generation.iterdir()} == {'manifest.json', 'native-backup.tar', 'recovery-key'}
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in generation.iterdir())
    assert generation.stat().st_mode & 0o777 == 0o700
    assert (generation / 'native-backup.tar').read_bytes() == (environment.source / FILENAME).read_bytes()
    assert (generation / 'recovery-key').read_bytes() == environment.key.read_bytes()
    assert not list(environment.args['incoming'].iterdir())
    assert 'private-test' not in json.dumps(result)


def test_idempotent_export_verifies_without_reading_chunks(environment):
    collector.collect(**environment.args)
    environment.transport.calls.clear()
    result = collector.collect(**environment.args)
    assert result['reused'] is True
    assert all(call['action'] != 'chunk' for call in environment.transport.calls)


@pytest.mark.parametrize('change', ['unprotected', 'no_database', 'no_ha', 'missing_app', 'stale', 'future'])
def test_coverage_and_freshness_fail_closed(environment, change):
    meta = metadata()
    if change == 'unprotected': meta['protected'] = False
    if change == 'no_database': meta['homeassistant']['exclude_database'] = True
    if change == 'no_ha': meta['homeassistant'] = None
    if change == 'missing_app': meta['addons'].pop()
    if change == 'stale': meta['date'] = (NOW - timedelta(hours=27)).isoformat()
    if change == 'future': meta['date'] = (NOW + timedelta(minutes=6)).isoformat()
    archive(environment.source / FILENAME, meta)
    with pytest.raises(guest.BackupError, match='no_fresh_eligible_backup'):
        collector.collect(**environment.args)
    assert json.loads((environment.args['stage'] / 'status.json').read_text())['status'] == 'failed'
    assert not (environment.args['stage'] / 'ab270a93').exists()


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'x/../../escape', 'x\\escape', './backup.json'])
def test_archive_rejects_traversal_and_duplicates(tmp_path, name):
    info = tarfile.TarInfo(name)
    info.size = 1
    path = archive(tmp_path / 'bad.tar', extra=info)
    with path.open('rb') as stream, pytest.raises(guest.BackupError, match='unsafe_archive_member'):
        guest.archive_metadata(stream)
    assert not (tmp_path.parent / 'escape').exists()


@pytest.mark.parametrize('kind', [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE])
def test_archive_rejects_links_and_specials(tmp_path, kind):
    info = tarfile.TarInfo('host-file')
    info.type = kind
    info.linkname = '/etc/shadow'
    path = archive(tmp_path / 'bad.tar', extra=info)
    with path.open('rb') as stream, pytest.raises(guest.BackupError):
        guest.archive_metadata(stream)


def test_guest_refuses_filename_path_and_symlink(environment):
    for name in ['../other.tar', '/data/backup/test.tar', 'x;id.tar']:
        with pytest.raises(guest.BackupError):
            guest.describe(environment.source, name)
    (environment.source / 'linked.tar').symlink_to(environment.source / FILENAME)
    with pytest.raises(guest.BackupError):
        guest.describe(environment.source, 'linked.tar')


@pytest.mark.parametrize('what', ['key', 'key_parent', 'stage', 'incoming', 'generation'])
def test_local_symlinks_are_rejected(environment, what):
    args = environment.args
    if what == 'key':
        target = environment.root / 'actual-key'
        environment.key.rename(target)
        environment.key.symlink_to(target)
    elif what == 'key_parent':
        target = environment.root / 'keys'
        target.mkdir(mode=0o700)
        environment.key.rename(target / 'key')
        (environment.root / 'key-link').symlink_to(target, target_is_directory=True)
        args['key_path'] = environment.root / 'key-link' / 'key'
    elif what in ['stage', 'incoming']:
        target = environment.root / 'other'
        target.mkdir(mode=0o700)
        args[what].symlink_to(target, target_is_directory=True)
    else:
        args['stage'].mkdir(mode=0o700)
        (args['stage'] / 'ab270a93').symlink_to(environment.root, target_is_directory=True)
    with pytest.raises(guest.BackupError):
        collector.collect(**args)


def test_chunking_and_aggregate_progress(environment):
    archive(environment.source / FILENAME, payload_size=guest.CHUNK_BYTES // 2)
    progress = []
    result = collector.collect(**environment.args, progress=progress.append)
    chunks = [c for c in environment.transport.calls if c['action'] == 'chunk']
    assert len(chunks) >= 3
    assert all(0 < c['length'] <= guest.CHUNK_BYTES for c in chunks)
    assert [c['offset'] for c in chunks] == list(range(0, result['bytes'], guest.CHUNK_BYTES))
    assert progress[-1]['exported_bytes'] == result['bytes']


@pytest.mark.parametrize('kind', ['bad_encoding', 'wrong_length', 'wrong_offset', 'bad_chunk_hash', 'bad_whole_hash'])
def test_corrupt_chunks_never_publish(environment, kind):
    def modify(payload, value):
        if payload['action'] == 'chunk':
            if kind == 'bad_encoding': value['data'] = '%private-not-base64%'
            if kind == 'wrong_length': value['length'] += 1
            if kind == 'wrong_offset': value['offset'] += 1
            if kind == 'bad_chunk_hash': value['sha256'] = '0' * 64
            if kind == 'bad_whole_hash':
                data = base64.b64decode(value['data'])
                data = b'!' + data[1:]
                value['data'] = base64.b64encode(data).decode()
                value['sha256'] = hashlib.sha256(data).hexdigest()
        return value
    environment.transport.modify = modify
    with pytest.raises(guest.BackupError):
        collector.collect(**environment.args)
    assert not (environment.args['stage'] / 'ab270a93').exists()
    assert not list(environment.args['incoming'].iterdir())


@pytest.mark.parametrize('when', ['after_inventory', 'after_chunk', 'key'])
def test_source_mutation_race_never_promotes(environment, when):
    changed = False
    def modify(payload, value):
        nonlocal changed
        action = 'inventory' if when == 'after_inventory' else 'chunk'
        if payload['action'] == action and not changed:
            changed = True
            if when == 'key': environment.key.write_bytes(b'replaced-private-test-key')
            else:
                with (environment.source / FILENAME).open('ab') as stream:
                    stream.write(b'x' * 512)
        return value
    environment.transport.modify = modify
    with pytest.raises(guest.BackupError):
        collector.collect(**environment.args)
    assert not (environment.args['stage'] / 'ab270a93').exists()


def test_stale_failure_preserves_last_good(environment):
    collector.collect(**environment.args)
    last = (environment.args['stage'] / 'last-success.json').read_bytes()
    environment.args['now'] = NOW + timedelta(hours=27)
    with pytest.raises(guest.BackupError, match='no_fresh_eligible_backup'):
        collector.collect(**environment.args)
    assert (environment.args['stage'] / 'last-success.json').read_bytes() == last
    assert (environment.args['stage'] / 'ab270a93' / 'native-backup.tar').is_file()
    assert json.loads((environment.args['stage'] / 'status.json').read_text())['status'] == 'failed'


def test_failed_promotion_preserves_last_good(environment, monkeypatch):
    collector.collect(**environment.args)
    last = (environment.args['stage'] / 'last-success.json').read_bytes()
    meta = metadata()
    meta['slug'] = 'bbbbbbbb'
    meta['date'] = '2026-10-17T00:59:00+00:00'
    archive(environment.source / 'next.tar', meta)
    monkeypatch.setattr(collector.os, 'rename', lambda *a: (_ for _ in ()).throw(OSError('private error')))
    with pytest.raises(guest.BackupError, match='collection_failed'):
        collector.collect(**environment.args)
    assert not (environment.args['stage'] / 'bbbbbbbb').exists()
    assert (environment.args['stage'] / 'last-success.json').read_bytes() == last
    assert not list(environment.args['incoming'].iterdir())


def test_existing_generation_tamper_fails_not_overwrites(environment):
    collector.collect(**environment.args)
    path = environment.args['stage'] / 'ab270a93' / 'native-backup.tar'
    path.write_bytes(b'tampered')
    with pytest.raises(guest.BackupError, match='generation_checksum_mismatch'):
        collector.collect(**environment.args)
    assert path.read_bytes() == b'tampered'


def test_duplicate_native_id_refused(environment):
    archive(environment.source / 'duplicate.tar')
    with pytest.raises(guest.BackupError, match='ambiguous_backup_id'):
        collector.collect(**environment.args)


def test_specific_id_cannot_select_different_native_backup(environment):
    with pytest.raises(guest.BackupError, match='no_fresh_eligible_backup'):
        collector.collect(**environment.args, backup_id='ffffffff')


@pytest.mark.parametrize('failure', ['exit', 'truncated', 'bad_json', 'guest_error'])
def test_transport_fixed_command_and_sanitized_errors(failure):
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        inner = json.dumps({'error': 'private detail'}) if failure == 'guest_error' else '[]'
        envelope = {'exitcode': 0, 'out-data': inner, 'out-truncated': failure == 'truncated'}
        return SimpleNamespace(returncode=1 if failure == 'exit' else 0,
                               stdout='private malformed' if failure == 'bad_json' else json.dumps(envelope),
                               stderr='private service credential')
    with pytest.raises(guest.BackupError) as caught:
        collector.Transport(runner)({'action': 'inventory'})
    assert 'private' not in str(caught.value)
    assert calls[0][:6] == ['runuser', '-u', 'jeremy', '--', 'ssh', '-o']
    assert 'pve-node3' in calls[0]
    assert 'hassio_supervisor' in calls[0][-1]


def test_native_schema_requires_component_members(tmp_path):
    path = tmp_path / 'missing.tar'
    raw = json.dumps(metadata()).encode()
    with tarfile.open(path, 'w') as tar:
        info = tarfile.TarInfo('./backup.json'); info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
    with path.open('rb') as stream, pytest.raises(guest.BackupError, match='missing_component_payload'):
        guest.archive_metadata(stream)


def test_service_is_independent_of_main_backup():
    root = Path(__file__).resolve().parents[1]
    unit = (root / 'edsys-home-assistant-backup.service').read_text()
    assert 'Requires=edsys-backup' not in unit
    assert 'ExecStartPre=' not in '\n'.join(l for l in unit.splitlines() if not l.startswith('#'))
    assert '01:40:00 America/New_York' in (root / 'edsys-home-assistant-backup.timer').read_text()


def deployment_paths(monkeypatch, changes=None):
    """Model verified ownership without changing any real host path/owner."""
    layout = {
        '/': (0, stat.S_IFDIR | 0o755),
        '/srv': (1000, stat.S_IFDIR | 0o755),
        '/srv/edsys-backup': (0, stat.S_IFDIR | 0o755),
        '/srv/edsys-backup/staging': (0, stat.S_IFDIR | 0o750),
        '/srv/edsys-backup/staging/home-assistant': (0, stat.S_IFDIR | 0o700),
        '/srv/other': (1000, stat.S_IFDIR | 0o700),
    }
    layout.update(changes or {})
    def inspect(path):
        uid, mode = layout[str(path)]
        return SimpleNamespace(st_uid=uid, st_mode=mode)
    monkeypatch.setattr(Path, 'lstat', inspect)
    monkeypatch.setattr(Path, 'stat', inspect)


def test_verified_operator_owned_srv_ancestor_allowed(monkeypatch):
    deployment_paths(monkeypatch)
    collector.secure_directory(Path('/srv/edsys-backup/staging/home-assistant'), create=False)


def test_root_owned_srv_remains_allowed(monkeypatch):
    deployment_paths(monkeypatch, {'/srv': (0, stat.S_IFDIR | 0o755)})
    collector.secure_directory(Path('/srv/edsys-backup/staging/home-assistant'), create=False)


@pytest.mark.parametrize('changes', [
    {'/srv': (1001, stat.S_IFDIR | 0o755)},
    {'/srv': (1000, stat.S_IFDIR | 0o775)},
    {'/srv': (1000, stat.S_IFLNK | 0o777)},
    {'/srv/edsys-backup': (1000, stat.S_IFDIR | 0o755)},
    {'/srv/edsys-backup/staging': (1000, stat.S_IFDIR | 0o750)},
    {'/srv/edsys-backup/staging/home-assistant': (1000, stat.S_IFDIR | 0o700)},
    {'/srv/edsys-backup/staging/home-assistant': (0, stat.S_IFDIR | 0o750)},
])
def test_srv_exception_does_not_weaken_private_children(monkeypatch, changes):
    deployment_paths(monkeypatch, changes)
    with pytest.raises(guest.BackupError):
        collector.secure_directory(Path('/srv/edsys-backup/staging/home-assistant'), create=False)


@pytest.mark.parametrize('path', ['/srv', '/srv/other'])
def test_operator_exception_is_not_a_general_stage_owner(monkeypatch, path):
    deployment_paths(monkeypatch)
    with pytest.raises(guest.BackupError):
        collector.secure_directory(Path(path), private=False, create=False)
