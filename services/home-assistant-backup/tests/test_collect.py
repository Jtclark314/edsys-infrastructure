import base64
import errno
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
from types import SimpleNamespace
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collect as collector
import guest_program as guest

NOW = datetime(2026, 10, 17, 1, 0, tzinfo=timezone.utc)
FILENAME = 'automatic_backup_2026_10_1_2026-10-16_20.46_13806909.tar'


class FakeClock:
    def __init__(self):
        self.now, self.delays = 0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.delays.append(seconds)
        self.now += seconds


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


def securetar_mock(monkeypatch, expected='private-test-recovery-key', on_validate=None):
    class FakeSecureTarArchive:
        def __init__(self, *, fileobj, password):
            self.tar = tarfile.open(fileobj=fileobj, mode='r:')
            self.password = password

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.tar.close()

        def validate(self, member):
            if on_validate:
                on_validate(member, self.password)
            return self.password == expected
    monkeypatch.setitem(sys.modules, 'securetar', SimpleNamespace(SecureTarArchive=FakeSecureTarArchive))


@pytest.fixture
def environment(tmp_path, monkeypatch):
    securetar_mock(monkeypatch)
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


def test_incoming_below_stage_publishes_new_generation(environment):
    environment.args['incoming'] = environment.args['stage'] / '.incoming'
    assert collector.INCOMING == collector.STAGE / '.incoming'
    collector.collect(**environment.args)
    last = environment.args['stage'] / 'last-success.json'
    meta = metadata()
    meta.update(slug='bbbbbbbb', date='2026-10-17T00:59:00+00:00')
    archive(environment.source / 'next.tar', meta)
    result = collector.collect(**environment.args)
    assert result['backup_id'] == 'bbbbbbbb'
    assert result['reused'] is False
    assert json.loads(last.read_text())['backup_id'] == 'bbbbbbbb'
    assert (environment.args['stage'] / 'ab270a93/native-backup.tar').is_file()
    assert not list(environment.args['incoming'].iterdir())


def test_publication_errno_is_safe_and_last_good_survives(environment, monkeypatch):
    environment.args['incoming'] = environment.args['stage'] / '.incoming'
    collector.collect(**environment.args)
    previous = (environment.args['stage'] / 'last-success.json').read_bytes()
    meta = metadata()
    meta.update(slug='bbbbbbbb', date='2026-10-17T00:59:00+00:00')
    archive(environment.source / 'next.tar', meta)
    def reject(*args):
        raise OSError(errno.EXDEV, 'private path and provider text must not escape')
    monkeypatch.setattr(collector.os, 'rename', reject)
    with pytest.raises(guest.BackupError, match='^collection_failed$'):
        collector.collect(**environment.args)
    current = json.loads((environment.args['stage'] / 'status.json').read_text())
    assert current['failure_type'] == 'OSError'
    assert current['failure_errno'] == errno.EXDEV
    assert 'private path' not in json.dumps(current)
    assert (environment.args['stage'] / 'last-success.json').read_bytes() == previous
    assert not (environment.args['stage'] / 'bbbbbbbb').exists()
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
    clock = FakeClock()
    with pytest.raises(guest.BackupError) as caught:
        collector.Transport(runner, clock, clock.sleep)({'action': 'inventory'})
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
    assert '\nReadWritePaths=/srv/edsys-backup/staging/home-assistant\n' in unit
    assert '/srv/edsys-backup/.home-assistant-incoming' not in unit


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


def response(value, returncode=0, **flags):
    return SimpleNamespace(returncode=returncode,
                           stdout=json.dumps({'exitcode': 0, 'out-data': json.dumps(value), **flags}),
                           stderr='private transport diagnostic')


@pytest.mark.parametrize('failure', ['nonzero', 'timeout'])
def test_transient_read_retries_same_request_then_exact_success(failure, capsys):
    clock, calls = FakeClock(), []
    data = b'bounded encrypted archive chunk'
    value = {'offset': 1024, 'length': len(data), 'data': base64.b64encode(data).decode(),
             'sha256': hashlib.sha256(data).hexdigest()}
    def runner(command, **kwargs):
        calls.append((command, kwargs['input']))
        if len(calls) == 1:
            if failure == 'timeout':
                clock.now += kwargs['timeout']
                raise subprocess.TimeoutExpired('private command', kwargs['timeout'],
                                                output='private archive', stderr='private credential')
            return SimpleNamespace(returncode=255, stdout='', stderr='private diagnostic')
        return response(value)
    payload = {'action': 'chunk', 'filename': FILENAME, 'fingerprint': [1, 2, 3, 4, 5],
               'offset': 1024, 'length': len(data)}
    actual = collector.Transport(runner, clock, clock.sleep)(payload)
    assert actual == value
    assert hashlib.sha256(base64.b64decode(actual['data'])).hexdigest() == actual['sha256']
    assert calls[0] == calls[1]
    assert clock.delays == [1]
    assert capsys.readouterr() == ('', '')


def test_persistent_nonzero_has_exact_retry_bound(capsys):
    clock, calls = FakeClock(), []
    def runner(command, **kwargs):
        calls.append(kwargs['input'])
        return SimpleNamespace(returncode=255, stdout='', stderr='private diagnostic')
    with pytest.raises(guest.BackupError, match='^guest_transport_failed$'):
        collector.Transport(runner, clock, clock.sleep)({'action': 'inventory'})
    assert len(calls) == 4 and len(set(calls)) == 1
    assert clock.delays == [1, 2, 4] and clock.now == 7
    assert capsys.readouterr() == ('', '')


def test_persistent_timeout_respects_total_call_deadline():
    clock, timeouts = FakeClock(), []
    def runner(command, **kwargs):
        timeouts.append(kwargs['timeout'])
        clock.now += kwargs['timeout']
        raise subprocess.TimeoutExpired('private command', kwargs['timeout'])
    with pytest.raises(guest.BackupError, match='^guest_transport_failed$'):
        collector.Transport(runner, clock, clock.sleep)({'action': 'inventory'})
    assert timeouts == [110, 110, 17]
    assert clock.now == 240


@pytest.mark.parametrize('value,flags,returncode', [
    ({'error': 'guest_archive_changed'}, {}, 0),
    ({'error': 'unsafe_guest_archive'}, {}, 1),
    ({}, {'out-truncated': True}, 0),
    ({}, {'out-truncated': True}, 1),
    ({}, {'err-truncated': True}, 1),
    ({}, {'exited': False}, 1),
    ({}, {'exitcode': 1}, 1),
    ({}, {'out-data': 'incomplete-json'}, 1),
])
def test_semantic_or_incomplete_proof_never_retries(value, flags, returncode):
    clock, calls = FakeClock(), []
    def runner(command, **kwargs):
        calls.append(command)
        return response(value, returncode=returncode, **flags)
    with pytest.raises(guest.BackupError):
        collector.Transport(runner, clock, clock.sleep)({'action': 'describe', 'filename': FILENAME})
    assert len(calls) == 1 and not clock.delays


def test_chunk_retry_does_not_restart_the_whole_export(environment):
    clock, actions = FakeClock(), []
    failed = False
    def runner(command, **kwargs):
        nonlocal failed
        payload = json.loads(kwargs['input'])
        actions.append(payload['action'])
        if payload['action'] == 'chunk' and not failed:
            failed = True
            return SimpleNamespace(returncode=255, stdout='', stderr='private transient')
        return response(guest.operation(payload, environment.source))
    environment.args['transport'] = collector.Transport(runner, clock, clock.sleep)
    result = collector.collect(**environment.args)
    assert result['status'] == 'success'
    assert actions.count('inventory') == 1
    assert actions.count('describe') == 2
    assert actions.count('chunk') == 2
    assert clock.delays == [1]


@pytest.mark.parametrize('correct', [True, False])
def test_guest_checks_key_on_bound_file_without_output(environment, correct, monkeypatch, capsys):
    seen = []
    def validator(member, password):
        seen.append(member.name)
        print('private library diagnostic ' + password)
        print('private stderr ' + password, file=sys.stderr)
    securetar_mock(monkeypatch, on_validate=validator)
    info = guest.describe(environment.source, FILENAME)
    result = guest.operation({'action': 'verify-key', 'filename': FILENAME,
                              'fingerprint': info['fingerprint'],
                              'password': 'private-test-recovery-key' if correct else 'wrong-private-key'},
                             environment.source)
    assert result == {'key_matches': correct, 'validated_members': 4}
    assert len(seen) == 4
    assert capsys.readouterr() == ('', '')


def test_guest_key_verification_refuses_source_change(environment):
    info = guest.describe(environment.source, FILENAME)
    with (environment.source / FILENAME).open('ab') as stream:
        stream.write(b'x' * 512)
    with pytest.raises(guest.BackupError, match='guest_archive_changed'):
        guest.operation({'action': 'verify-key', 'filename': FILENAME,
                         'fingerprint': info['fingerprint'], 'password': 'private-test-recovery-key'},
                        environment.source)


def test_guest_key_verification_rechecks_after_validation(environment, monkeypatch):
    changed = False
    def validator(member, password):
        nonlocal changed
        if not changed:
            changed = True
            with (environment.source / FILENAME).open('ab') as stream:
                stream.write(b'x' * 512)
    securetar_mock(monkeypatch, on_validate=validator)
    info = guest.describe(environment.source, FILENAME)
    with pytest.raises(guest.BackupError, match='guest_archive_changed'):
        guest.operation({'action': 'verify-key', 'filename': FILENAME,
                         'fingerprint': info['fingerprint'], 'password': 'private-test-recovery-key'},
                        environment.source)


def test_wrong_native_key_refuses_publication_and_sensitive_status(environment):
    environment.key.write_bytes(b'wrong-private-key')
    with pytest.raises(guest.BackupError, match='native_key_verification_failed'):
        collector.collect(**environment.args)
    assert not (environment.args['stage'] / 'ab270a93').exists()
    assert not any(call['action'] == 'chunk' for call in environment.transport.calls)
    status = (environment.args['stage'] / 'status.json').read_text()
    assert 'wrong-private-key' not in status


def test_key_drift_on_idempotent_reuse_is_reverified(environment):
    collector.collect(**environment.args)
    success = (environment.args['stage'] / 'last-success.json').read_bytes()
    environment.transport.calls.clear()
    environment.key.write_bytes(b'wrong-private-key')
    with pytest.raises(guest.BackupError, match='native_key_verification_failed'):
        collector.collect(**environment.args)
    assert any(call['action'] == 'verify-key' for call in environment.transport.calls)
    assert (environment.args['stage'] / 'last-success.json').read_bytes() == success
    assert (environment.args['stage'] / 'ab270a93').is_dir()


def test_pre_key_proof_generation_reused_only_after_fresh_key_proof(environment):
    collector.collect(**environment.args)
    path = environment.args['stage'] / 'ab270a93' / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest.pop('native_key_verification')
    path.write_text(json.dumps(manifest))
    before = path.read_bytes()
    environment.transport.calls.clear()
    result = collector.collect(**environment.args)
    assert result['reused'] is True and result['native_key_verified'] is True
    assert any(call['action'] == 'verify-key' for call in environment.transport.calls)
    assert not any(call['action'] == 'chunk' for call in environment.transport.calls)
    assert path.read_bytes() == before


def test_key_rotation_after_reuse_proof_refuses_success(environment):
    collector.collect(**environment.args)
    success = (environment.args['stage'] / 'last-success.json').read_bytes()
    def rotate(payload, result):
        if payload['action'] == 'verify-key':
            environment.key.write_bytes(b'rotated-after-proof')
        return result
    environment.transport.modify = rotate
    with pytest.raises(guest.BackupError, match='recovery_key_changed'):
        collector.collect(**environment.args)
    assert (environment.args['stage'] / 'last-success.json').read_bytes() == success


def test_reuse_rechecks_freshness_before_success(environment, monkeypatch):
    collector.collect(**environment.args)
    backup_date = datetime.fromisoformat(metadata()['date'])
    environment.args['now'] = backup_date + timedelta(hours=26) - timedelta(seconds=60)
    clock = iter([0, 130])
    monkeypatch.setattr(collector.time, 'monotonic', lambda: next(clock))
    with pytest.raises(guest.BackupError, match='no_fresh_eligible_backup'):
        collector.collect(**environment.args)


def test_key_never_enters_transport_command(environment):
    clock, commands = FakeClock(), []
    def runner(command, **kwargs):
        commands.append(command)
        payload = json.loads(kwargs['input'])
        return response(guest.operation(payload, environment.source))
    environment.args['transport'] = collector.Transport(runner, clock, clock.sleep)
    collector.collect(**environment.args)
    assert 'private-test-recovery-key' not in json.dumps(commands)


def test_collector_uses_bounded_shared_remote_lock():
    calls = []
    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return response([])
    assert collector.Transport(runner)({'action': 'inventory'}) == []
    command, options = calls[0]
    remote = shlex.split(command[-1])
    assert remote[:7] == ['flock', '--exclusive', '--wait', '10', '--conflict-exit-code',
                          '75', '/run/lock/edsys-ha-qga-300.lock']
    assert remote[7:11] == ['qm', 'guest', 'exec', '300']
    assert options['timeout'] <= 110


def test_securetar_unavailable_refuses_key_proof(environment, monkeypatch):
    monkeypatch.setitem(sys.modules, 'securetar', None)
    with pytest.raises(guest.BackupError, match='native_key_verification_failed'):
        collector.collect(**environment.args)
    assert not (environment.args['stage'] / 'ab270a93').exists()
