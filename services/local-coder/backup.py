#!/usr/bin/env python3
"""Stage consistent private local-coder state for the existing encrypted backup."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import uuid

RUNTIME = Path('/mnt/ai-store/local-coder')
STAGE = Path('/srv/edsys-backup/staging/local-coder')


def database_copy(source, target):
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Staged SQLite integrity check failed')
    target.chmod(0o600)


def verify(directory):
    manifest = json.loads((directory / 'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        path = directory / name
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError('Invalid restore manifest path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected['sha256']:
            raise ValueError('Restore checksum mismatch')
        if name.endswith('.sqlite'):
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Restored SQLite integrity failure')
    return {'verified_files': len(manifest['files']), 'bytes': sum(v['bytes'] for v in manifest['files'].values())}


def stage():
    if os.geteuid() != 0:
        raise SystemExit('Run staging as root for the existing encrypted backup')
    os.umask(0o077)
    if not Path('/mnt/ai-store').is_mount():
        raise SystemExit('AI Store is not mounted')
    subprocess.run(['runuser', '-u', 'jeremy', '--', '/usr/bin/python3', str(Path(__file__).with_name('history.py'))], check=True, stdout=subprocess.DEVNULL)
    STAGE.mkdir(parents=True, mode=0o700, exist_ok=True)
    name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    directory = STAGE / name
    directory.mkdir(mode=0o700)
    database_copy(RUNTIME / 'data/opencode/opencode.db', directory / 'opencode.sqlite')
    database_copy(RUNTIME / 'archive/history.sqlite', directory / 'history.sqlite')
    for folder in ('project-memory', 'desktop-notes', 'browser-output', 'web'):
        source = RUNTIME / folder
        if not source.exists():
            continue
        for path in source.rglob('*'):
            if path.is_symlink():
                raise ValueError('Unexpected symlink in private backup source')
            if not path.is_file() or path.name == '.lock':
                continue
            target = directory / folder / path.relative_to(source)
            target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            # Atomic checkpoint replacement and completed output files are copied by content.
            target.write_bytes(path.read_bytes())
            target.chmod(0o600)
    tools = RUNTIME / 'data/opencode/tool-output'
    if tools.exists():
        if any(path.is_symlink() for path in tools.rglob('*')):
            raise ValueError('Unexpected symlink in tool-output backup source')
        shutil.copytree(tools, directory / 'tool-output', symlinks=False)
    files = {}
    for path in directory.rglob('*'):
        if path.is_file():
            path.chmod(0o600)
            blob = path.read_bytes()
            files[str(path.relative_to(directory))] = {'sha256': hashlib.sha256(blob).hexdigest(), 'bytes': len(blob)}
    (directory / 'manifest.json').write_text(json.dumps({'created_at': name, 'files': files}, indent=2) + '\n')
    result = verify(directory)
    link = STAGE / ('.current-' + uuid.uuid4().hex)
    link.symlink_to(directory.name)
    os.replace(link, STAGE / 'current')
    return {'stage': str(directory), **result}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.verify.resolve()) if args.verify else stage()))
