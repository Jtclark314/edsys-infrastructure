#!/usr/bin/env python3
"""Disposable full-collector publication probe; no HA, network or real keys.

Run ``prepare ROOT`` outside the service, then ``run ROOT`` under the unit's
filesystem restrictions with only ROOT/stage writable. ROOT must be a private
``/var/lib/edsys-codex-reports/ha-export-fix-sandbox-*`` directory. Crypto is deliberately mocked;
the real export separately qualifies native encrypted-member validation.
"""
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collect as collector
import guest_program as guest


def checked_root(value):
    root = Path(value)
    if (not root.is_absolute() or root.parent != Path('/var/lib/edsys-codex-reports')
            or not root.name.startswith('ha-export-fix-sandbox-')
            or root.is_symlink()):
        raise ValueError('disposable sandbox root required')
    info = root.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError('private owned sandbox root required')
    return root


def prepare(root):
    if list(root.iterdir()):
        raise ValueError('empty sandbox root required')
    (root / 'source').mkdir(mode=0o700)
    (root / 'stage').mkdir(mode=0o700)
    key = root / 'test-key'
    key.write_text('synthetic-test-key\n')
    key.chmod(0o600)
    now = datetime.now(timezone.utc)
    for index, slug in enumerate(('11111111', '22222222')):
        metadata = {'version': 2, 'slug': slug,
                    'date': (now - timedelta(minutes=2-index)).isoformat(),
                    'protected': True, 'compressed': True,
                    'homeassistant': {'version': '2026.10.2', 'exclude_database': False},
                    'addons': [{'slug': app, 'version': 'synthetic'}
                               for app in sorted(guest.REQUIRED_APPS)]}
        with tarfile.open(root / 'source' / (slug + '.tar'), 'w') as archive:
            for name in ['homeassistant', *sorted(guest.REQUIRED_APPS)]:
                info = tarfile.TarInfo(name + '.tar.gz')
                data = b'synthetic payload'
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            data = json.dumps(metadata).encode()
            info = tarfile.TarInfo('backup.json'); info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    print(json.dumps({'prepared': True, 'synthetic_only': True}))


class SyntheticSecureTar:
    def __init__(self, *, fileobj, password):
        self.tar = tarfile.open(fileobj=fileobj, mode='r:')
        self.password = password

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.tar.close()

    def validate(self, entry):
        return self.password == 'synthetic-test-key'


def run(root):
    sys.modules['securetar'] = SimpleNamespace(SecureTarArchive=SyntheticSecureTar)
    stage = root / 'stage'
    incoming = stage / '.incoming'
    selected = '11111111.tar'
    calls = []

    def transport(payload):
        calls.append(payload['action'])
        result = guest.operation(payload, root / 'source')
        if payload['action'] == 'inventory':
            result = [item for item in result if item['filename'] == selected]
        return result

    args = dict(transport=transport, stage=stage, incoming=incoming,
                key_path=root / 'test-key', uid=os.geteuid(), floor=root)
    first = collector.collect(**args)
    assert first['backup_id'] == '11111111' and first['reused'] is False
    selected = '22222222.tar'
    second = collector.collect(**args)
    assert second['backup_id'] == '22222222' and second['reused'] is False
    calls.clear()
    reused = collector.collect(**args)
    assert reused['reused'] is True and 'chunk' not in calls
    assert (stage / '11111111/native-backup.tar').is_file()
    assert not list(incoming.iterdir())
    assert json.loads((stage / 'last-success.json').read_text())['backup_id'] == '22222222'
    try:
        (root / 'outside-writable-stage').write_text('must be refused')
    except OSError:
        pass
    else:
        raise AssertionError('sandbox did not refuse writes outside the stage')
    print(json.dumps({'synthetic_only': True, 'crypto_mocked': True,
                      'new_generations_published': 2, 'idempotent_reuse': True,
                      'last_good_advanced': True, 'incoming_empty': True,
                      'outside_stage_write_refused': True}))


if __name__ == '__main__':
    os.umask(0o077)
    if len(sys.argv) != 3 or sys.argv[1] not in {'prepare', 'run'}:
        raise SystemExit('usage: sandbox_publication.py prepare|run ROOT')
    root = checked_root(sys.argv[2])
    (prepare if sys.argv[1] == 'prepare' else run)(root)
